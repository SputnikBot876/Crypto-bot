#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import telebot
import os
import feedparser
import time
import threading
import requests
from datetime import datetime
from html import escape
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Переменная окружения BOT_TOKEN не задана!")

bot = telebot.TeleBot(BOT_TOKEN)

# Хранилище состояния мониторинга {chat_id: stop_event}
monitoring_states = {}
# Хранилище отправленных новостей для защиты от дублей {chat_id: OrderedDict(link: timestamp)}
sent_news_cache = {}
CACHE_MAX_SIZE = 100

# Защита от race condition в кэше
cache_lock = threading.Lock()

# ===== НОВОСТНЫЕ ИСТОЧНИКИ =====
RSS_SOURCES = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("РБК-Крипто", "https://www.rbc.ru/crypto/rss/?project=rbc_crypto"),
    ("Forklog", "https://forklog.com/feed"),
    ("Bits.media", "https://bits.media/feed/"),
]

# Глобальный пул потоков для RSS (создается один раз)
news_executor = ThreadPoolExecutor(
    max_workers=len(RSS_SOURCES),
    thread_name_prefix="rss"
)

# ===== КЛЮЧЕВЫЕ СЛОВА =====
CRYPTO_KEYWORDS = [
    "биткоин", "bitcoin", "btc",
    "эфириум", "ethereum", "eth",
    "цена", "price", "курс",
    "волатильность", "рост", "падение"
]

REGULATORY_KEYWORDS = [
    # Базовые ключевые слова
    "закон", "законопроект", "регулирование", "цб", "банк россии",
    "реестр", "лицензия", "криптообменник", "депозитарий",
    "p2p", "блокировка", "115-фз", "росфинмониторинг",
    "тестирование", "лимит", "неквалифицированный инвестор",
    "вэд", "внешнеторговый", "трансграничные", "обменник",
    "цифровой депозитарий", "брокер", "реформа",
    
    # Расширенные ключевые слова из статьи РБК
    "цифровая валюта", "криптовалюта", "крипта",
    "уголовная ответственность", "идентификация",
    "комплаенс", "kyc", "aml",
    "валютный контроль", "операции с цифровой валютой",
    "цифровые права", "криптоуслуги", "криптоплощадки",
    "криптобиржа", "криптобиржи", "цифровые активы",
    "токены", "токен", "стейблкоин", "стейблкоины",
    "майнинг", "майнеры", "криптокошелек", "криптокошельки",
    "некастодиальный", "кастодиальный", "фиат",
    "обмен цифровой валюты", "организация обращения",
    "цифровой рубль", "санкции", "ограничения",
    "запрет", "разрешение", "контроль", "надзор"
]

# Высокоприоритетные ключевые слова (точное соответствие)
HIGH_PRIORITY_KEYWORDS = [
    "законопроект о цифровой валюте",
    "регулирование криптовалют",
    "криптообменник",
    "цифровой депозитарий",
    "115-фз",
    "росфинмониторинг",
    "неквалифицированный инвестор",
    "внешнеторговый",
    "трансграничные расчеты",
    "уголовная ответственность",
    "криптобиржа",
    "криптоплощадка"
]

# ===== ФУНКЦИИ ПОЛУЧЕНИЯ НОВОСТЕЙ =====
def fetch_single_source(source_name, rss_url):
    """Получение новостей из одного источника (для параллельного выполнения)"""
    headers = {"User-Agent": "Mozilla/5.0 (CryptoBot/1.0)"}
    try:
        feed = feedparser.parse(rss_url, request_headers=headers)
        news = []
        for entry in feed.entries[:5]:
            news.append({
                "title": entry.get("title", "Без заголовка"),
                "link": entry.get("link", ""),
                "source": source_name,
                "published": entry.get("published", entry.get("updated", "")),
                "published_parsed": entry.get("published_parsed", entry.get("updated_parsed")),
            })
        return news
    except Exception as e:
        print(f"[WARN] Ошибка парсинга {source_name}: {e}")
        return []

def get_date(item):
    """Надежное получение даты из разных форматов RSS"""
    # 1. Пробуем стандартный парсер email.utils
    try:
        return parsedate_to_datetime(item.get("published", ""))
    except (TypeError, ValueError, Exception):
        pass
    
    # 2. Фоллбэк: feedparser уже распарсил дату в time.struct_time
    published_parsed = item.get("published_parsed")
    if published_parsed:
        try:
            return datetime(*published_parsed[:6])
        except (TypeError, ValueError, Exception):
            pass
    
    # 3. Если ничего не сработало - возвращаем минимальную дату
    return datetime.min

def get_crypto_news():
    """Параллельное получение новостей из всех источников"""
    all_news = []
    
    # Используем глобальный executor (не создаем новый)
    futures = []
    for source_name, rss_url in RSS_SOURCES:
        future = news_executor.submit(fetch_single_source, source_name, rss_url)
        futures.append(future)
    
    # Собираем результаты по мере завершения
    for future in as_completed(futures, timeout=15):
        try:
            news = future.result(timeout=5)
            all_news.extend(news)
        except Exception as e:
            print(f"[ERROR] Ошибка получения новостей: {e}")
    
    # Сортируем по дате публикации (с надежным парсингом)
    all_news.sort(key=get_date, reverse=True)
    return all_news

def filter_news(news_list, keywords):
    """Обычная фильтрация по ключевым словам"""
    filtered = []
    for item in news_list:
        title_lower = item['title'].lower()
        if any(kw.lower() in title_lower for kw in keywords):
            filtered.append(item)
    return filtered

def filter_news_with_priority(news_list, keywords, high_priority_keywords=None):
    """Фильтрация с приоритетами"""
    high_priority = []
    normal_priority = []
    
    for item in news_list:
        title_lower = item['title'].lower()
        matched = False
        
        # Проверяем высокоприоритетные слова
        if high_priority_keywords:
            for keyword in high_priority_keywords:
                if keyword.lower() in title_lower:
                    high_priority.append(item)
                    matched = True
                    break
        
        # Если не высокий приоритет, проверяем обычные
        if not matched:
            if any(kw.lower() in title_lower for kw in keywords):
                normal_priority.append(item)
    
    # Высокоприоритетные новости идут первыми
    return high_priority + normal_priority

def get_high_priority_news(news_list):
    """Получение только высокоприоритетных новостей"""
    return filter_news_with_priority(news_list, [], HIGH_PRIORITY_KEYWORDS)

# ===== ПАРСИНГ ЦЕН (С ЗАЩИТОЙ) =====
def get_crypto_prices():
    headers = {"User-Agent": "Mozilla/5.0 (CryptoBot/1.0)"}
    apis = [
        {
            "name": "Binance",
            "btc": "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
            "eth": "https://api.binance.com/api/v3/ticker/24hr?symbol=ETHUSDT",
            "parse": lambda d: {"price": float(d["lastPrice"]), "change": float(d["priceChangePercent"])}
        },
        {
            "name": "Bybit",
            "btc": "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT",
            "eth": "https://api.bybit.com/v5/market/tickers?category=spot&symbol=ETHUSDT",
            "parse": lambda d: {"price": float(d["result"]["list"][0]["lastPrice"]), "change": float(d["result"]["list"][0]["price24hPcnt"]) * 100}
        }
    ]
    
    for api in apis:
        try:
            btc_raw = requests.get(api["btc"], timeout=7, headers=headers).json()
            eth_raw = requests.get(api["eth"], timeout=7, headers=headers).json()
            return {"btc": api["parse"](btc_raw), "eth": api["parse"](eth_raw)}
        except Exception as e:
            print(f"[WARN] {api['name']} недоступен: {e}")
    return None

def get_fear_greed_index():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10).json()
        return {"value": int(r["data"][0]["value"]), "classification": r["data"][0]["value_classification"]}
    except Exception as e:
        print(f"[WARN] Fear&Greed API: {e}")
        return None

# ===== БЕЗОПАСНАЯ ОТПРАВКА СООБЩЕНИЙ (HTML) =====
def safe_send(chat_id, text):
    """Отправка с автоматическим экранированием HTML и обработкой ошибок"""
    try:
        bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
    except telebot.apihelper.ApiTelegramException as e:
        # Если HTML сломан, пробуем отправить как обычный текст
        try:
            bot.send_message(chat_id, f"(Сообщение без форматирования)\n{text}", disable_web_page_preview=True)
        except Exception:
            print(f"[ERROR] Не удалось отправить сообщение в {chat_id}: {e}")

# ===== УПРАВЛЕНИЕ КЭШЕМ НОВОСТЕЙ (С ЗАЩИТОЙ ОТ ГОНОК) =====
def add_to_cache(chat_id, link):
    """Добавление ссылки в кэш с сохранением порядка (thread-safe)"""
    with cache_lock:
        if chat_id not in sent_news_cache:
            sent_news_cache[chat_id] = OrderedDict()
        
        # Добавляем или обновляем ссылку
        sent_news_cache[chat_id][link] = time.time()
        
        # Ограничиваем размер кэша (удаляем самые старые)
        while len(sent_news_cache[chat_id]) > CACHE_MAX_SIZE:
            # Удаляем первый (самый старый) элемент
            sent_news_cache[chat_id].popitem(last=False)

def is_news_sent(chat_id, link):
    """Проверка, была ли новость уже отправлена (thread-safe)"""
    with cache_lock:
        return chat_id in sent_news_cache and link in sent_news_cache[chat_id]

def get_new_news(chat_id, news_list):
    """Получение только новых новостей (не отправленных ранее)"""
    return [item for item in news_list if not is_news_sent(chat_id, item["link"])]

# ===== ЛОГИКА РАССЫЛКИ =====
def monitor_loop(chat_id, stop_event):
    safe_send(chat_id, "🔁 <b>Мониторинг запущен!</b>\nРассылка каждые 30 минут.\nДля остановки: /stop")
    
    while not stop_event.is_set():
        try:
            # 1. Цены
            prices = get_crypto_prices()
            if prices:
                btc, eth = prices["btc"], prices["eth"]
                price_msg = (
                    f"💰 <b>Актуальные цены</b>\n"
                    f"BTC: ${btc['price']:,.2f} ({btc['change']:+.2f}%)\n"
                    f"ETH: ${eth['price']:,.2f} ({eth['change']:+.2f}%)"
                )
                safe_send(chat_id, price_msg)
            
            # 2. Новости (без дублей)
            all_news = get_crypto_news()
            filtered = filter_news(all_news, CRYPTO_KEYWORDS + REGULATORY_KEYWORDS)
            
            # Получаем только новые новости
            new_items = get_new_news(chat_id, filtered)
            
            if new_items:
                msg = f"📰 <b>Крипто-новости</b> ({datetime.now().strftime('%H:%M')})\n\n"
                for item in new_items[:5]:
                    title = escape(item['title'])
                    msg += f"• <a href=\"{item['link']}\">{title}</a>\n  📌 {escape(item['source'])}\n\n"
                    add_to_cache(chat_id, item["link"])
                
                safe_send(chat_id, msg)
                
        except Exception as e:
            print(f"[ERROR] Мониторинг {chat_id}: {e}")
        
        # Ждем 30 минут, но прерываемся мгновенно если stop_event установлен
        stop_event.wait(timeout=1800)
    
    safe_send(chat_id, "⏹ <b>Мониторинг остановлен.</b>")

# ===== КОМАНДЫ БОТА =====
@bot.message_handler(commands=['start', 'help'])
def start(message):
    text = (
        "👋 Привет! Я бот для мониторинга криптовалют.\n\n"
        "<b>📌 Основные команды:</b>\n"
        "/price — цены BTC/ETH\n"
        "/sentiment — индекс страха и жадности\n"
        "/news — крипто-новости\n"
        "/law — регуляторные новости\n"
        "/top_law — важные регуляторные новости\n\n"
        "<b>📡 Мониторинг:</b>\n"
        "/monitor — включить рассылку (30 мин)\n"
        "/stop — остановить рассылку"
    )
    safe_send(message.chat.id, text)

@bot.message_handler(commands=['price'])
def price_command(message):
    prices = get_crypto_prices()
    if not prices:
        safe_send(message.chat.id, "❌ Не удалось получить цены. Попробуйте позже.")
        return
    btc, eth = prices["btc"], prices["eth"]
    msg = f"💰 <b>Актуальные цены</b>\n\n<b>BTC:</b> ${btc['price']:,.2f} ({btc['change']:+.2f}%)\n<b>ETH:</b> ${eth['price']:,.2f} ({eth['change']:+.2f}%)"
    safe_send(message.chat.id, msg)

@bot.message_handler(commands=['sentiment'])
def sentiment_command(message):
    fng = get_fear_greed_index()
    if not fng:
        safe_send(message.chat.id, "❌ Не удалось получить индекс.")
        return
    
    v = fng["value"]
    c = fng["classification"]
    
    emoji_map = {"Extreme Fear": "😱", "Fear": "😨", "Neutral": "😐", "Greed": "😊", "Extreme Greed": "🤑"}
    emoji = emoji_map.get(c, "😐")
    
    advice = ""
    if v <= 25: advice = "📌 Рынок в панике. Возможность присмотреться к покупке."
    elif v <= 45: advice = "📌 На рынке страх. Можно добавлять небольшими частями."
    elif v <= 55: advice = "📌 Рынок нейтрален. Продолжайте свою стратегию."
    elif v <= 75: advice = "📌 Растёт жадность. Будьте осторожны с крупными покупками."
    else: advice = "📌 Экстремальная жадность! Рынок перегрет."

    msg = (
        f"📊 <b>Индекс страха и жадности</b>\n\n"
        f"{emoji} <b>{c}</b> — {v}/100\n\n"
        f"📈 0-25: Экстремальный страх\n"
        f"📈 26-45: Страх\n"
        f"📈 46-55: Нейтрально\n"
        f"📈 56-75: Жадность\n"
        f"📈 76-100: Экстремальная жадность\n\n"
        f"{advice}"
    )
    safe_send(message.chat.id, msg)

@bot.message_handler(commands=['news'])
def news_command(message):
    all_news = get_crypto_news()
    filtered = filter_news(all_news, CRYPTO_KEYWORDS + REGULATORY_KEYWORDS)
    if not filtered:
        safe_send(message.chat.id, "📭 Новостей пока нет.")
        return
    
    msg = f"📰 <b>Крипто-новости</b> ({datetime.now().strftime('%H:%M')})\n\n"
    for item in filtered[:5]:
        title = escape(item['title'])
        msg += f"• <a href=\"{item['link']}\">{title}</a>\n  📌 {escape(item['source'])}\n\n"
    safe_send(message.chat.id, msg)

@bot.message_handler(commands=['law'])
def law_command(message):
    all_news = get_crypto_news()
    if not all_news:
        safe_send(message.chat.id, "📭 Новостей пока нет.")
        return
    
    # Используем фильтр с приоритетами
    filtered = filter_news_with_priority(
        all_news, 
        REGULATORY_KEYWORDS,
        HIGH_PRIORITY_KEYWORDS
    )
    
    if not filtered:
        safe_send(message.chat.id, "📭 Регуляторных новостей пока нет.")
        return
    
    msg = f"📜 <b>Регуляторные новости</b> ({datetime.now().strftime('%H:%M')})\n\n"
    
    # Показываем до 10 новостей
    for item in filtered[:10]:
        title = escape(item['title'])
        msg += f"• <a href=\"{item['link']}\">{title}</a>\n  📌 {escape(item['source'])}\n\n"
    
    safe_send(message.chat.id, msg)

@bot.message_handler(commands=['top_law'])
def top_law_command(message):
    """Показать только высокоприоритетные регуляторные новости"""
    all_news = get_crypto_news()
    if not all_news:
        safe_send(message.chat.id, "📭 Новостей пока нет.")
        return
    
    # Используем общую функцию фильтрации
    high_priority_news = get_high_priority_news(all_news)
    
    if not high_priority_news:
        safe_send(message.chat.id, "📭 Важных регуляторных новостей нет.")
        return
    
    msg = f"🚨 <b>Важные регуляторные новости</b> ({datetime.now().strftime('%H:%M')})\n\n"
    for item in high_priority_news[:10]:
        title = escape(item['title'])
        msg += f"• <a href=\"{item['link']}\">{title}</a>\n  📌 {escape(item['source'])}\n\n"
    
    safe_send(message.chat.id, msg)

@bot.message_handler(commands=['monitor'])
def monitor_command(message):
    chat_id = message.chat.id
    if chat_id in monitoring_states and not monitoring_states[chat_id].is_set():
        safe_send(chat_id, "⚠️ Рассылка уже запущена. Используйте /stop для остановки.")
        return
    
    stop_event = threading.Event()
    monitoring_states[chat_id] = stop_event
    thread = threading.Thread(target=monitor_loop, args=(chat_id, stop_event), daemon=True)
    thread.start()

@bot.message_handler(commands=['stop'])
def stop_command(message):
    chat_id = message.chat.id
    if chat_id in monitoring_states and not monitoring_states[chat_id].is_set():
        monitoring_states[chat_id].set()  # Сигнализируем потоку остановиться
        del monitoring_states[chat_id]
    else:
        safe_send(chat_id, "ℹ️ Рассылка не была запущена.")

@bot.message_handler(func=lambda m: True)
def fallback(message):
    safe_send(message.chat.id, "Используйте команды: /start, /price, /news, /law, /top_law, /monitor, /stop")

# ===== ЗАПУСК =====
if __name__ == "__main__":
    print("✅ Бот запущен! Ваш бот: t.me/Sputnik_876_bot")
    print("📌 Команды: /price, /sentiment, /news, /law, /top_law, /monitor, /stop")
    
    try:
        bot.infinity_polling(skip_pending=True)
    finally:
        # Корректное завершение пула потоков при остановке бота
        news_executor.shutdown(wait=True)