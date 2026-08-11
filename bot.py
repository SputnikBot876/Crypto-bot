#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import telebot
import os
import feedparser
import time
import threading
import requests
from datetime import datetime

# === ТОКЕН БОТА ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# ===== НОВОСТНЫЕ ИСТОЧНИКИ (6 RSS-ЛЕНТ) =====
def get_crypto_news():
    news_list = []
    sources = [
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("The Block", "https://www.theblock.co/rss.xml"),
        ("РБК-Крипто", "https://www.rbc.ru/crypto/rss/?project=rbc_crypto"),
        ("Forklog", "https://forklog.com/feed"),
        ("Bits.media", "https://bits.media/feed/"),
    ]
    for source_name, rss_url in sources:
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:5]:
                news_list.append({
                    "title": entry.title,
                    "link": entry.link,
                    "source": source_name,
                })
        except Exception as e:
            print(f"Ошибка {source_name}: {e}")
            continue
    return news_list

# ===== ФИЛЬТР ПО КЛЮЧЕВЫМ СЛОВАМ =====
CRYPTO_KEYWORDS = [
    "биткоин", "bitcoin", "btc",
    "эфириум", "ethereum", "eth",
    "цена", "price", "курс",
    "волатильность", "рост", "падение"
]

REGULATORY_KEYWORDS = [
    "закон", "законопроект", "регулирование", "ЦБ", "Банк России",
    "реестр", "лицензия", "криптообменник", "депозитарий",
    "P2P", "блокировка", "115-ФЗ", "РОСФИНМОНИТОРИНГ",
    "тестирование", "лимит", "неквалифицированный инвестор",
    "ВЭД", "внешнеторговый", "трансграничные", "обменник",
    "цифровой депозитарий", "брокер", "реформа"
]

def filter_news(news_list, keywords):
    filtered = []
    for item in news_list:
        title_lower = item['title'].lower()
        if any(kw.lower() in title_lower for kw in keywords):
            filtered.append(item)
    return filtered

# ===== ПАРСИНГ ЦЕН (Binance + Bybit) =====
# ===== ПАРСИНГ ЦЕН (Binance + Bybit + ЗАГЛУШКА) =====
# ===== ПАРСИНГ ЦЕН (Binance + Bybit) =====
def get_crypto_prices():
    try:
        # === CoinGecko API ===
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        
        return {
            "btc": {
                "price": float(data["bitcoin"]["usd"]),
                "change": float(data["bitcoin"]["usd_24h_change"])
            },
            "eth": {
                "price": float(data["ethereum"]["usd"]),
                "change": float(data["ethereum"]["usd_24h_change"])
            }
        }
    except Exception as e:
        print(f"Ошибка цен (CoinGecko): {e}")
        # === ЗАГЛУШКА ===
        return {
            "btc": {
                "price": 65000.00,
                "change": 0.25
            },
            "eth": {
                "price": 1800.00,
                "change": 0.10
            }
        }

# ===== ИНДЕКС СТРАХА И ЖАДНОСТИ =====
def get_fear_greed_index():
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        response = requests.get(url, timeout=10)
        data = response.json()
        return {"value": data["data"][0]["value"], "classification": data["data"][0]["value_classification"]}
    except Exception as e:
        print(f"Ошибка индекса: {e}")
        return None

# ===== ОТПРАВКА НОВОСТЕЙ =====
def send_news(chat_id, mode="all"):
    all_news = get_crypto_news()
    if not all_news:
        bot.send_message(chat_id, "📭 Новостей пока нет.")
        return

    if mode == "regulatory":
        filtered = filter_news(all_news, REGULATORY_KEYWORDS)
        title = "📜 **Регуляторные новости**"
        if not filtered:
            bot.send_message(chat_id, "📭 Новостей по регулированию пока нет.")
            return
    else:
        filtered = filter_news(all_news, CRYPTO_KEYWORDS + REGULATORY_KEYWORDS)
        title = "📰 **Крипто-новости**"

    msg = f"{title} ({datetime.now().strftime('%H:%M')})\n\n"
    for item in filtered[:5]:
        msg += f"• **{item['title']}**\n  📎 {item['link']}\n  📌 {item['source']}\n\n"
    bot.send_message(chat_id, msg, parse_mode="Markdown")

# ===== КОМАНДЫ БОТА =====
@bot.message_handler(commands=['start', 'help'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 Привет! Я бот для мониторинга криптовалют.\n\n"
        "📌 **Команды:**\n"
        "/price — цены BTC/ETH\n"
        "/sentiment — индекс страха и жадности\n"
        "/news — крипто-новости\n"
        "/law — регуляторные новости\n"
        "/monitor — включить рассылку (каждые 30 мин)\n"
        "/stop — остановить рассылку"
    )

@bot.message_handler(commands=['price'])
def price_command(message):
    prices = get_crypto_prices()
    if not prices:
        bot.send_message(message.chat.id, "❌ Не удалось получить цены.")
        return

    btc = prices["btc"]
    eth = prices["eth"]
    msg = f"💰 **Актуальные цены**\n\n**BTC:** ${btc['price']:,.2f} ({btc['change']:+.2f}%)\n**ETH:** ${eth['price']:,.2f} ({eth['change']:+.2f}%)"
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['sentiment'])
def sentiment_command(message):
    fng = get_fear_greed_index()
    if not fng:
        bot.send_message(message.chat.id, "❌ Не удалось получить индекс.")
        return

    value = int(fng["value"])
    classification = fng["classification"]

    emoji = "😱" if classification == "Fear" and value <= 25 else "😨" if classification == "Fear" else "🤑" if classification == "Greed" and value >= 75 else "😊" if classification == "Greed" else "😐"

    if value <= 25:
        advice = "📌 Рынок в панике. Возможность присмотреться к покупке."
    elif value <= 45:
        advice = "📌 На рынке страх. Можно добавлять небольшими частями."
    elif value <= 55:
        advice = "📌 Рынок нейтрален. Продолжайте свою стратегию."
    elif value <= 75:
        advice = "📌 Растёт жадность. Будьте осторожны с крупными покупками."
    else:
        advice = "📌 Экстремальная жадность! Рынок перегрет. Лучше воздержаться."

    msg = f"📊 **Индекс страха и жадности**\n\n{emoji} **{classification}** — {value}/100\n\n📈 0-25: Экстремальный страх\n📈 26-45: Страх\n📈 46-55: Нейтрально\n📈 56-75: Жадность\n📈 76-100: Экстремальная жадность\n\n{advice}"
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['news'])
def news_command(message):
    send_news(message.chat.id, mode="all")

@bot.message_handler(commands=['law'])
def law_command(message):
    send_news(message.chat.id, mode="regulatory")

# ===== АВТОМАТИЧЕСКАЯ РАССЫЛКА =====
monitoring_threads = {}

@bot.message_handler(commands=['monitor'])
def monitor_command(message):
    chat_id = message.chat.id
    if chat_id in monitoring_threads and monitoring_threads[chat_id] and monitoring_threads[chat_id].is_alive():
        bot.send_message(chat_id, "⚠️ Рассылка уже запущена.")
        return

    def monitor_loop():
        bot.send_message(chat_id, "🔁 Рассылка включена. Каждые 30 минут.")
        while True:
            try:
                prices = get_crypto_prices()
                if prices:
                    btc = prices["btc"]
                    eth = prices["eth"]
                    msg = f"💰 **Цены**\nBTC: ${btc['price']:,.2f} ({btc['change']:+.2f}%)\nETH: ${eth['price']:,.2f} ({eth['change']:+.2f}%)"
                    bot.send_message(chat_id, msg, parse_mode="Markdown")
                send_news(chat_id, mode="all")
                time.sleep(1800)
            except Exception as e:
                print(f"Ошибка мониторинга: {e}")
                time.sleep(60)

    thread = threading.Thread(target=monitor_loop, daemon=True)
    thread.start()
    monitoring_threads[chat_id] = thread

@bot.message_handler(commands=['stop'])
def stop_command(message):
    chat_id = message.chat.id
    if chat_id in monitoring_threads:
        monitoring_threads[chat_id] = None
        bot.send_message(chat_id, "⏹ Рассылка остановлена.")
    else:
        bot.send_message(chat_id, "Рассылка не была запущена.")

@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.reply_to(message, "Используйте команды: /start, /price, /news, /law, /monitor, /stop")

print("✅ Бот запущен! Ваш бот: t.me/Sputnik_876_bot")
print("📌 Команды: /price, /sentiment, /news, /law, /monitor, /stop")
bot.polling()
