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
SIGNAL_INTERVAL = int(os.environ.get("SIGNAL_INTERVAL", 900))  # default 15 min

# =======================
# AI CLIENT
# =======================
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# =======================
# MARKETS CONFIG
# =======================
STOCKS = ["AAPL", "TSLA", "AMZN"]  # Add more tickers as needed
FOREX_PAIRS = ["EURUSD", "GBPUSD", "XAUUSD"]
OPTIONS_TICKERS = ["AAPL", "TSLA"]  # Can expand

# =======================
# TELEGRAM SETUP
# =======================
telegram_bot = TelegramBot(token=TELEGRAM_BOT_TOKEN)
telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

async def handle_stock(update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper() if context.args else "AAPL"
    price = yf.Ticker(ticker).info.get("regularMarketPrice")
    ai_response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=f"Generate a Buy/Sell/Hold signal for {ticker} at price {price}."
    )
    signal = ai_response.output[0].content[0].text
    await update.message.reply_text(f"{ticker}: {price}\nSignal: {signal}")

async def handle_forex(update, context: ContextTypes.DEFAULT_TYPE):
    pair = context.args[0].upper() if context.args else "EURUSD"
    base, quote = pair[:3], pair[3:]
    r = requests.get(f"https://open.er-api.com/v6/latest/{base}").json()
    rate = r["rates"].get(quote, "N/A")
    ai_response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=f"Generate a Buy/Sell/Hold signal for {pair} at rate {rate}."
    )
    signal = ai_response.output[0].content[0].text
    await update.message.reply_text(f"{pair}: {rate}\nSignal: {signal}")

# Add handlers
telegram_app.add_handler(CommandHandler("stock", handle_stock))
telegram_app.add_handler(CommandHandler("forex", handle_forex))

# =======================
# DISCORD SETUP
# =======================
intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)

async def discord_auto_signals():
    await discord_client.wait_until_ready()
    channel = discord_client.get_channel(DISCORD_CHANNEL_ID)
    while True:
        messages = []

        # Stocks
        for s in STOCKS:
            t = yf.Ticker(s)
            price = t.info.get("regularMarketPrice")
            ai_response = openai_client.responses.create(
                model="gpt-4.1-mini",
                input=f"Generate a Buy/Sell/Hold signal for {s} at price {price}."
            )
            signal = ai_response.output[0].content[0].text
            messages.append(f"{s}: {price}\nSignal: {signal}")

        # Forex & Commodities
        for f in FOREX_PAIRS:
            base, quote = f[:3], f[3:]
            try:
                r = requests.get(f"https://open.er-api.com/v6/latest/{base}").json()
                rate = r["rates"].get(quote, "N/A")
            except:
                rate = "N/A"
            ai_response = openai_client.responses.create(
                model="gpt-4.1-mini",
                input=f"Generate a Buy/Sell/Hold signal for {f} at rate {rate}."
            )
            signal = ai_response.output[0].content[0].text
            messages.append(f"{f}: {rate}\nSignal: {signal}")

        # Send to Discord
        for m in messages:
            await channel.send(m)

        # Send to Telegram
        for m in messages:
            await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=m)

        await asyncio.sleep(SIGNAL_INTERVAL)

# =======================
# MAIN
# =======================
async def main():
    # Start Telegram polling
    asyncio.create_task(telegram_app.run_polling())

    # Start Discord auto signals
    asyncio.create_task(discord_auto_signals())

    # Start Discord client
    await discord_client.start(DISCORD_BOT_TOKEN)

# Run everything
asyncio.run(main())
