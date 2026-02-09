import asyncio
import os
import requests
import yfinance as yf
import pandas as pd
import feedparser
import yaml
from datetime import datetime
from telegram import Bot, ParseMode
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- Load Config ----------------
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

# ---------------- Environment Variables ----------------
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")

bot = Bot(token=BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)

SIGNAL_INTERVAL = config["signal_interval"]
OPTIONS_UNUSUAL_VOLUME_MULTIPLIER = config["options"]["unusual_volume_multiplier"]
MAX_OPTIONS_ALERTS = config["options"]["max_alerts_per_stock"]
MAX_MESSAGES_PER_UPDATE = config["telegram"]["max_messages_per_update"]

# ---------------- Helper Functions ----------------
def bold(text): return f"*{text}*"

async def send_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=ParseMode.MARKDOWN)

def get_stock_data(ticker):
    t = yf.Ticker(ticker)
    info = t.info
    return info.get("regularMarketPrice"), info.get("volume")

def get_options_data(ticker):
    t = yf.Ticker(ticker)
    chains = []
    try:
        for exp in t.options[:2]:
            opt = t.option_chain(exp)
            chains.append({"expiration": exp, "calls": opt.calls, "puts": opt.puts})
    except:
        pass
    return chains

def check_unusual_volume(chains):
    unusual = []
    for chain in chains:
        for df, kind in [(chain["calls"], "CALL"), (chain["puts"], "PUT")]:
            avg_vol = df['volume'].mean() or 1
            df_unusual = df[df['volume'] > avg_vol * OPTIONS_UNUSUAL_VOLUME_MULTIPLIER]
            for _, row in df_unusual.iterrows():
                unusual.append({
                    "type": kind,
                    "strike": row['strike'],
                    "volume": row['volume'],
                    "expiration": chain["expiration"],
                    "lastPrice": row['lastPrice']
                })
    return unusual

def get_forex_rate(pair):
    base, quote = pair[:3], pair[3:]
    try:
        r = requests.get(f"https://open.er-api.com/v6/latest/{base}").json()
        return r["rates"].get(quote)
    except:
        return None

def get_commodity_rate(pair):
    try:
        if pair in ["XAUUSD","XAGUSD"]:
            r = requests.get(f"https://api.exchangerate.host/convert?from={pair[:3]}&to={pair[3:]}&amount=1").json()
            return r.get("result")
        elif pair == "WTIUSD":
            r = requests.get("https://www.quandl.com/api/v3/datasets/OPEC/ORB.json").json()
            return r['dataset']['data'][0][1]
    except:
        return None

def get_news(ticker):
    if not NEWSAPI_KEY:
        return []
    try:
        url = f"https://newsapi.org/v2/everything?q={ticker}&language=en&sortBy=publishedAt&pageSize=3&apiKey={NEWSAPI_KEY}"
        r = requests.get(url).json()
        return [(n['title'], n['url']) for n in r.get("articles",[])]
    except:
        return []

def get_sec_filings(ticker):
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={ticker}&owner=include&action=getcompany&count=5&output=atom"
    feed = feedparser.parse(url)
    filings = [{"title":e.title,"link":e.link} for e in feed.entries]
    return filings

async def ai_signal(prompt):
    try:
        resp = client.responses.create(model="gpt-4.1-mini", input=prompt)
        return resp.output[0].content[0].text
    except:
        return "AI analysis failed"

# ---------------- Autonomous Signal Loop ----------------
last_price = {}

async def auto_signals():
    while True:
        messages = []
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        messages.append(f"🚀 {bold('Market Signal Update')} — {now}\n")

        # Stocks
        for s in config["stocks"]:
            ticker = s["ticker"]
            threshold = s["alert_threshold"]
            price, volume = get_stock_data(ticker)
            last_p = last_price.get(ticker, price)
            last_price[ticker] = price
            price_change = abs((price-last_p)/last_p) if last_p else 0
            if price_change < threshold:
                continue

            chains = get_options_data(ticker)
            unusual = check_unusual_volume(chains)[:MAX_OPTIONS_ALERTS]
            news_items = get_news(ticker)
            news_str = "\n".join([f"• {n[0]} ({n[1]})" for n in news_items]) or "No major headlines"
            filings = get_sec_filings(ticker)
            filings_str = "\n".join([f"• {f['title']} ({f['link']})" for f in filings]) or "No recent filings"

            prompt = f"""
Analyze stock {ticker}:
- Price: {price}
- Volume: {volume}
- Options unusual: {unusual}
- News: {news_items}
- SEC filings: {filings}
Generate {config['ai_settings']['style']} Buy/Sell/Hold signal with confidence 0-100%. Max length {config['ai_settings']['max_summary_length']} chars.
"""
            ai_msg = await ai_signal(prompt)
            msg = f"{bold(ticker)}\nPrice: ${price}\nVolume: {volume}\nSignal: {ai_msg}\nOptions Unusual: {unusual}\nNews:\n{news_str}\nFilings:\n{filings_str}\n"
            messages.append(msg)
            if len(messages) >= MAX_MESSAGES_PER_UPDATE:
                break

        # Forex
        for f in config["forex_pairs"]:
            pair = f["pair"]
            threshold = f["alert_threshold"]
            rate = get_forex_rate(pair)
            if rate:
                last_r = last_price.get(pair, rate)
                last_price[pair] = rate
                if abs((rate-last_r)/last_r) < threshold:
                    continue
                ai_msg = await ai_signal(f"Buy/Sell/Hold signal for forex pair {pair} at rate {rate}. Confidence 0-100%.")
                messages.append(f"{bold(pair)}\nRate: {rate}\nSignal: {ai_msg}\n")
                if len(messages) >= MAX_MESSAGES_PER_UPDATE:
                    break

        # Commodities
        for c in config["commodities"]:
            pair = c["pair"]
            threshold = c["alert_threshold"]
            rate = get_commodity_rate(pair)
            if rate:
                last_r = last_price.get(pair, rate)
                last_price[pair] = rate
                if abs((rate-last_r)/last_r) < threshold:
                    continue
                ai_msg = await ai_signal(f"Buy/Sell/Hold signal for commodity {pair} at rate {rate}. Confidence 0-100%.")
                messages.append(f"{bold(pair)}\nRate: {rate}\nSignal: {ai_msg}\n")
                if len(messages) >= MAX_MESSAGES_PER_UPDATE:
                    break

        # Send messages
        for m in messages[:MAX_MESSAGES_PER_UPDATE]:
            await send_message(m)

        await asyncio.sleep(SIGNAL_INTERVAL)

# ---------------- Manual Commands ----------------
async def stock_command(update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper()
    price, volume = get_stock_data(ticker)
    ai_msg = await ai_signal(f"Buy/Sell/Hold for {ticker} at ${price} volume {volume}. Confidence 0-100%.")
    await update.message.reply_text(f"{bold(ticker)}\nPrice: ${price}\nVolume: {volume}\nSignal: {ai_msg}", parse_mode=ParseMode.MARKDOWN)

async def options_command(update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper()
    chains = get_options_data(ticker)
    unusual = check_unusual_volume(chains)
    if unusual:
        msg = f"📈 {ticker} Options Unusual Activity:\n"
        for u in unusual[:MAX_OPTIONS_ALERTS]:
            msg += f"{u['type']} {u['strike']} exp {u['expiration']} vol {u['volume']} last ${u['lastPrice']}\n"
    else:
        msg = "No unusual options activity."
    await update.message.reply_text(msg)

async def forex_command(update, context: ContextTypes.DEFAULT_TYPE):
    pair = context.args[0].upper()
    rate = get_forex_rate(pair)
    ai_msg = await ai_signal(f"Buy/Sell/Hold for {pair} at rate {rate}. Confidence 0-100%.")
    await update.message.reply_text(f"{bold(pair)}\nRate: {rate}\nSignal: {ai_msg}", parse_mode=ParseMode.MARKDOWN)

# ---------------- App Setup ----------------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("stock", stock_command))
app.add_handler(CommandHandler("options", options_command))
app.add_handler(CommandHandler("forex", forex_command))

async def main():
    asyncio.create_task(auto_signals())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
