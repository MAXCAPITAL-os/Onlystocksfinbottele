# elite_signals_bot.py — Full Bloomberg-style God-tier Signals Bot
import asyncio
import os
import requests
import yfinance as yf
import pandas as pd
import feedparser
from datetime import datetime
from telegram import Bot, ParseMode
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- Environment Variables ----------------
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")  # Optional

bot = Bot(token=BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------- Config ----------------
SIGNAL_INTERVAL = 900  # seconds (15 min)
OPTIONS_UNUSUAL_VOLUME_MULTIPLIER = 1.5
MAX_OPTIONS_ALERTS = 5
MAX_MESSAGES_PER_UPDATE = 10

# ---------------- Load S&P 500 tickers dynamically ----------------
def load_sp500_tickers():
    url = "https://datahub.io/core/s-and-p-500-companies/r/constituents.csv"
    df = pd.read_csv(url)
    return df['Symbol'].tolist()

ALL_STOCKS = load_sp500_tickers()

# ---------------- Forex & Commodities ----------------
FOREX_PAIRS = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","NZDUSD"]
COMMODITIES = ["XAUUSD","XAGUSD","WTIUSD"]

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

def get_commodity_rate(pair):
    try:
        if pair in ["XAUUSD", "XAGUSD"]:
            r = requests.get(f"https://api.exchangerate.host/convert?from={pair[:3]}&to={pair[3:]}&amount=1").json()
            return r.get("result")
        elif pair == "WTIUSD":
            # Placeholder crude oil price API
            r = requests.get("https://www.quandl.com/api/v3/datasets/OPEC/ORB.json").json()
            return r['dataset']['data'][0][1]
        else:
            return None
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
last_price = {}

async def auto_signals():
    while True:
        messages = []
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        messages.append(f"🚀 {bold('Market Signal Update')} — {now}\n")

        # ---------------- Stocks ----------------
        for ticker in ALL_STOCKS:
            price, volume = get_stock_data(ticker)
            last_p = last_price.get(ticker, price)
            last_price[ticker] = price
            price_change = abs((price - last_p)/last_p) if last_p else 0

            if price_change < 0.01:  # skip minor moves <1%
                continue

            # Options
            chains = get_options_data(ticker)
            unusual = check_unusual_volume(chains)[:MAX_OPTIONS_ALERTS]

            # News + SEC filings
            news_items = get_news(ticker)
            news_str = "\n".join([f"• {n[0]} ({n[1]})" for n in news_items]) or "No major headlines"
            filings = get_sec_filings(ticker)
            filings_str = "\n".join([f"• {f['title']} ({f['link']})" for f in filings]) or "No recent filings"

            # AI Analysis
            prompt = f"""
Analyze stock {ticker}:
- Price: {price}
- Volume: {volume}
- Options unusual: {unusual}
- News headlines: {news_items}
- SEC filings: {filings}
Generate a concise Buy/Sell/Hold signal and confidence (0-100%). Provide reasoning.
"""
            ai_msg = await ai_signal(prompt)

            msg = f"{bold(ticker)}\nPrice: ${price}\nVolume: {volume}\nSignal: {ai_msg}\nOptions Unusual: {unusual}\nNews:\n{news_str}\nFilings:\n{filings_str}\n"
            messages.append(msg)
            if len(messages) >= MAX_MESSAGES_PER_UPDATE:
                break

        # ---------------- Forex ----------------
        for pair in FOREX_PAIRS:
            rate = get_forex_rate(pair)
            if rate:
                ai_msg = await ai_signal(f"Generate Buy/Sell/Hold signal for forex pair {pair} at rate {rate}. Include confidence 0-100%.")
                messages.append(f"{bold(pair)}\nRate: {rate}\nSignal: {ai_msg}\n")
                if len(messages) >= MAX_MESSAGES_PER_UPDATE:
                    break

        # ---------------- Commodities ----------------
        for c in COMMODITIES:
            rate = get_commodity_rate(c)
            if rate:
                ai_msg = await ai_signal(f"Generate Buy/Sell/Hold signal for commodity {c} at rate {rate}. Include confidence 0-100%.")
                messages.append(f"{bold(c)}\nRate: {rate}\nSignal: {ai_msg}\n")
                if len(messages) >= MAX_MESSAGES_PER_UPDATE:
                    break

        # ---------------- Send Messages ----------------
        for m in messages[:MAX_MESSAGES_PER_UPDATE]:
            await send_message(m)

        await asyncio.sleep(SIGNAL_INTERVAL)

# ---------------- Manual Commands ----------------
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
        for u in unusual[:MAX_OPTIONS_ALERTS]:
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
