import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
import openai

# ---------- Logging ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Telegram token ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found! Set it in Heroku Config Vars.")

# ---------- OpenAI API ----------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY not found! Set it in Heroku Config Vars.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

# ---------- /start command ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hi 👋 I’m your AI bot. Ask me anything!")
    logger.info(f"Replied to /start from {update.effective_user.username}")

# ---------- AI reply ----------
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful Telegram bot."},
                {"role": "user", "content": user_text}
            ]
        )
        answer = response.choices[0].message.content
        await update.message.reply_text(answer)
        logger.info(f"Replied to message: {user_text}")
    except Exception as e:
        await update.message.reply_text("⚠️ Sorry, I couldn’t answer that.")
        logger.error(f"AI Error: {e}")

# ---------- Build bot ----------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

logger.info("Starting the bot...")
app.run_polling()
