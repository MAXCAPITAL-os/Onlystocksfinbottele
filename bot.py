import os
import asyncio
import requests
import yfinance as yf
import discord
from telegram import Bot as TelegramBot
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import OpenAI

# =======================
# ENV VARIABLES
# =======================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", 0))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")
SIGNAL_INTERVAL = int(os.environ.get("SIGNAL_INTERVAL", 900))

# =======================
# AI CLIENT
# =======================
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# =======================
# MARKETS CONFIG
# =======================
STOCKS = ["AAPL", "TSLA", "AMZN", "NVDA", "GOOGL"]  # expand as needed
FOREX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
OPTIONS_TICKERS = STOCKS  # Track options for all stocks

# =======================
# TELEGRAM SETUP
# =======================
telegram_bot = TelegramBot(token=TELEGRAM_BOT_TOKEN)
telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

async def ai_signal_for_stock(ticker):
    t = yf.Ticker(ticker)
    price = t.info.get("regularMarketPrice")
    # Simple options unusual activity
    try:
        options_chain = t.option_chain(t.options[0])
        calls = options_chain.calls
        puts = options_chain.puts
        unusual_activity = f"Calls: {calls['volume'].sum()}, Puts: {puts['volume'].sum()}"
    except:
        unusual_activity = "No options data"
    prompt = (
        f"Generate Buy/Sell/Hold signal for {ticker} at {price} USD.\n"
        f"Include confidence %, reasoning, and options flow summary: {unusual_activity}"
    )
    response = openai_client.responses.create(model="gpt-4.1-mini", input=prompt)
    return f"{ticker}: {price}\nSignal: {response.output[0].content[0].text}"

async def handle_stock(update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper() if context.args else "AAPL"
    signal = await ai_signal_for_stock(ticker)
    await update.message.reply_text(signal)

telegram_app.add_handler(CommandHandler("stock", handle_stock))

# =======================
# DISCORD SETUP
# =======================
intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)

async def auto_signals():
    await discord_client.wait_until_ready()
    channel = discord_client.get_channel(DISCORD_CHANNEL_ID)
    while True:
        messages = []

        # Stocks
        for s in STOCKS:
            try:
                msg = await ai_signal_for_stock(s)
                messages.append(msg)
            except Exception as e:
                messages.append(f"{s}: Error fetching signal - {e}")

        # Forex & Commodities
        for f in FOREX_PAIRS:
            base, quote = f[:3], f[3:]
            try:
                r = requests.get(f"https://open.er-api.com/v6/latest/{base}").json()
                rate = r["rates"].get(quote, "N/A")
            except:
                rate = "N/A"
            prompt = f"Generate Buy/Sell/Hold signal for {f} at rate {rate}, include reasoning and confidence %."
            response = openai_client.responses.create(model="gpt-4.1-mini", input=prompt)
            msg = f"{f}: {rate}\nSignal: {response.output[0].content[0].text}"
            messages.append(msg)

        # Send to Discord & Telegram
        for m in messages:
            await channel.send(m)
            await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=m)

        await asyncio.sleep(SIGNAL_INTERVAL)

# =======================
# MAIN
# =======================
async def main():
    # Telegram polling
    asyncio.create_task(telegram_app.run_polling())

    # Auto signals
    asyncio.create_task(auto_signals())

    # Discord client
    await discord_client.start(DISCORD_BOT_TOKEN)

asyncio.run(main())
