# elite_signals_bot.py — Bloomberg-style Telegram Bot
import asyncio
import os
import requests
import yfinance as yf
import feedparser
from telegram import Bot, ParseMode
from openai import OpenAI
from datetime import datetime
import pandas as pd

# ---------------- Environment Variables ----------------
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")  # Optional

bot = Bot(token=BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------- Config ----------------
STOCKS = ["AAPL", "TSLA", "AMZN"]
FOREX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]
SIGNAL_INTERVAL = 900  # 15 minutes
OPTIONS_UNUSUAL_VOLUME_MULTIPLIER = 1.5

# ---------------- Helper Functions ----------------
def bold(text): return f"*{text}*"

async def send_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=ParseMode.MARKDOWN)

def get_stock_data(ticker):
    t = yf.Ticker(ticker)
    info = t.info
    price = info.get("regularMarketPrice")
    volume = info.get("volume")
    return price, volume

def get_options_data(ticker):
    t = yf.Ticker(ticker)
    chains = []
    try:
        for exp in t.options[:2]:
            opt = t.option_chain(exp)
            chains.append({"expiration": exp, "calls": opt.calls, "puts": opt.puts})
    except Exception as e:
        print(f"Options fetch error for {ticker}: {e}")
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

def get_news(ticker):
    if not NEWSAPI_KEY:
        return []
    url = f"https://newsapi.org/v2/everything?q={ticker}&language=en&sortBy=publishedAt&pageSize=3&apiKey={NEWSAPI_KEY}"
    try:
        r = requests.get(url).json()
        return [(n['title'], n['url']) for n in r.get("articles", [])]
    except:
        return []

def get_sec_filings(ticker):
    # Free SEC EDGAR scraping (latest insider buys/sells)
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={ticker}&owner=include&action=getcompany&count=5&output=atom"
    feed = feedparser.parse(url)
    filings = []
    for entry in feed.entries:
        filings.append({"title": entry.title, "link": entry.link})
    return filings

async def ai_signal(prompt):
    try:
        resp = client.responses.create(model="gpt-4.1-mini", input=prompt)
        return resp.output[0].content[0].text
    except Exception as e:
        print(f"AI error: {e}")
        return "AI analysis failed"

# ---------------- Autonomous Signal Loop ----------------
async def auto_signals():
    while True:
        messages = []
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        messages.append(f"🚀 {bold('Market Signal Update')} — {now}\n")

        for s in STOCKS:
            price, volume = get_stock_data(s)
            # Options
            chains = get_options_data(s)
            unusual = check_unusual_volume(chains)

            # News
            news_items = get_news(s)
            news_str = "\n".join([f"• {n[0]} ({n[1]})" for n in news_items]) or "No major headlines"

            # SEC filings
            filings = get_sec_filings(s)
            filings_str = "\n".join([f"• {f['title']} ({f['link']})" for f in filings]) or "No recent filings"

            # AI analysis
            prompt = f"""
Analyze stock {s} with:
- Price: {price}
- Volume: {volume}
- Options unusual activity: {unusual}
- News headlines: {news_items}
- SEC filings: {filings}
Generate a concise Buy/Sell/Hold signal and confidence (0-100%). Provide short reasoning.
"""
            ai_msg = await ai_signal(prompt)

            msg = f"{bold(s)}\nPrice: ${price}\nVolume: {volume}\nSignal: {ai_msg}\nOptions Unusual: {unusual[:3]}\nNews:\n{news_str}\nFilings:\n{filings_str}\n"
            messages.append(msg)

        for f in FOREX_PAIRS:
            rate = get_forex_rate(f)
            if rate:
                ai_msg = await ai_signal(f"Generate Buy/Sell/Hold signal for forex pair {f} at rate {rate}. Include confidence 0-100%.")
                messages.append(f"{bold(f)}\nRate: {rate}\nSignal: {ai_msg}\n")
        
        for m in messages:
            await send_message(m)

        await asyncio.sleep(SIGNAL_INTERVAL)

# ---------------- Manual Commands ----------------
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

async def stock_command(update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper()
    price, volume = get_stock_data(ticker)
    ai_msg = await ai_signal(f"Generate Buy/Sell/Hold signal for stock {ticker} at price {price} USD with volume {volume}. Include confidence 0-100%.")
    await update.message.reply_text(f"{bold(ticker)}\nPrice: ${price}\nVolume: {volume}\nSignal: {ai_msg}", parse_mode=ParseMode.MARKDOWN)

async def options_command(update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper()
    chains = get_options_data(ticker)
    unusual = check_unusual_volume(chains)
    if unusual:
        msg = f"📈 {ticker} Options Unusual Activity:\n"
        for u in unusual[:10]:
            msg += f"{u['type']} {u['strike']} exp {u['expiration']} vol {u['volume']} last ${u['lastPrice']}\n"
    else:
        msg = f"No unusual options activity for {ticker}."
    await update.message.reply_text(msg)

async def forex_command(update, context: ContextTypes.DEFAULT_TYPE):
    pair = context.args[0].upper()
    rate = get_forex_rate(pair)
    ai_msg = await ai_signal(f"Generate Buy/Sell/Hold signal for forex pair {pair} at rate {rate}. Include confidence 0-100%.")
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
