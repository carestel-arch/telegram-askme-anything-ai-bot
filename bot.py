import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get API keys
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# Commands
async def start(update: Update, context):
    await update.message.reply_text("🤖 Hello! I'm your AI assistant powered by Groq. Ask me anything!")

async def handle_message(update: Update, context):
    try:
        user_message = update.message.text
        
        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # IMPORTANT: New Groq import method
        from groq import Groq
        
        # Initialize Groq client
        client = Groq(api_key=GROQ_API_KEY)
        
        # Get response
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "user", "content": user_message}
            ],
            model="llama-3.1-8b-instant",  # Free model
            max_tokens=500
        )
        
        reply = chat_completion.choices[0].message.content
        await update.message.reply_text(reply)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        error_msg = str(e)
        if "API key" in error_msg:
            await update.message.reply_text("❌ API key error. Please check your Groq API key.")
        elif "rate limit" in error_msg.lower():
            await update.message.reply_text("⚠️ Rate limit reached. Please try again later.")
        else:
            await update.message.reply_text(f"Error: {error_msg[:100]}")

def main():
    print("🚀 Starting Groq Telegram Bot...")
    print(f"Telegram Token: {'✅ Set' if TELEGRAM_TOKEN else '❌ Missing'}")
    print(f"Groq API Key: {'✅ Set' if GROQ_API_KEY else '❌ Missing'}")
    
    if not TELEGRAM_TOKEN or not GROQ_API_KEY:
        print("❌ ERROR: Missing API keys!")
        print("Please set TELEGRAM_TOKEN and GROQ_API_KEY in Heroku Config Vars")
        return
    
    # Create bot application
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Bot is running! Waiting for messages...")
    app.run_polling()

if __name__ == '__main__':
    main()
