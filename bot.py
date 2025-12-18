import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")

# ---------- Hugging Face Inference API URLs ----------
# Small, hosted models for free usage
HF_TEXT_API = "https://api-inference.huggingface.co/models/google/flan-t5-small"
HF_IMAGE_API = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2"  # hosted model

headers = {"Authorization": f"Bearer {HF_TOKEN}"}

# ---------- /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I’m StarAI Bot ✨\n"
        "I can answer questions and create images.\n\n"
        "🧠 Just type a question\n"
        "🖼️ Use /image <describe the image>"
    )

# ---------- Text AI ----------
async def chat_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    payload = {"inputs": user_text}
    try:
        r = requests.post(HF_TEXT_API, headers=headers, json=payload, timeout=20)
        data = r.json()
        answer = data[0].get("generated_text", "⚠️ I couldn't generate an answer.")
        await update.message.reply_text(answer)
    except Exception as e:
        logger.error(f"Text AI error: {e}")
        await update.message.reply_text("⚠️ I couldn’t answer that right now.")

# ---------- Image AI ----------
async def image_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("Use it like: /image a cat wearing sunglasses")
        return
    payload = {"inputs": prompt}
    try:
        r = requests.post(HF_IMAGE_API, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            with open("image.png", "wb") as f:
                f.write(r.content)
            await update.message.reply_photo(photo=open("image.png", "rb"))
        else:
            logger.error(f"Image API error: {r.status_code} {r.text}")
            await update.message.reply_text("⚠️ Could not generate the image right now.")
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await update.message.reply_text("⚠️ Image generation failed.")

# ---------- Build bot ----------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("image", image_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_reply))

logger.info("Starting StarAI Bot...")
app.run_polling()
