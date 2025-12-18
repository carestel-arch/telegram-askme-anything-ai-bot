import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# ========================
# SETUP LOGGING
# ========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========================
# GET API KEYS
# ========================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# Check if keys exist
if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN not found! Set it in Heroku Config Vars")
if not GROQ_API_KEY:
    logger.error("❌ GROQ_API_KEY not found! Set it in Heroku Config Vars")

# ========================
# INITIALIZE GROQ CLIENT
# ========================
client = Groq(api_key=GROQ_API_KEY)

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when /start is issued."""
    user = update.effective_user
    welcome_text = f"""
👋 Hello {user.first_name}! I'm your AI Assistant powered by Groq.

⚡ **Features:**
• Fast & free AI responses
• Can answer questions
• Help with writing, coding, ideas

📝 **Try asking me:**
• "Explain quantum computing simply"
• "Write a poem about cats"
• "Help me plan a trip to Paris"
• "How do I learn Python?"

Just type your question below! 😊
    """
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message."""
    help_text = """
🤖 **Available Commands:**
/start - Start the bot
/help - Show this help message
/about - About this bot

💡 **Just type any question** and I'll answer it!

⚡ Powered by Groq (Llama 3.1) - Fast & Free AI
    """
    await update.message.reply_text(help_text)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send about message."""
    about_text = """
🤖 **Groq AI Telegram Bot**
Version 1.0

⚡ **Powered by:** Groq Cloud & Llama 3.1
🎯 **Features:** Fast, free AI responses
📊 **Limits:** 5,000 requests/day (free)

🔧 **Created with:** Python, python-telegram-bot, Groq API

💝 **Enjoy chatting!**
    """
    await update.message.reply_text(about_text)

# ========================
# HANDLE MESSAGES
# ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
    try:
        user_message = update.message.text
        user_id = update.effective_user.id
        
        logger.info(f"📨 User {user_id}: {user_message[:50]}...")
        
        # Show "typing..." indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # Get response from Groq
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful Telegram bot assistant. Keep responses concise, friendly, and helpful. If asked who you are, say you're a Telegram bot powered by Groq's AI."
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            model="llama-3.1-8b-instant",  # FREE and super fast!
            temperature=0.7,
            max_tokens=500,
            top_p=1,
            stream=False
        )
        
        # Get the response text
        bot_reply = chat_completion.choices[0].message.content
        
        # Send the response
        await update.message.reply_text(bot_reply)
        
        logger.info(f"✅ Replied to user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        error_message = "Sorry, I encountered an error. Please try again in a moment."
        await update.message.reply_text(error_message)

# ========================
# ERROR HANDLER
# ========================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}")

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start the bot."""
    print("=" * 50)
    print("🤖 Starting Groq Telegram Bot...")
    print("=" * 50)
    
    # Check for API keys
    if not TELEGRAM_TOKEN or not GROQ_API_KEY:
        print("❌ ERROR: API keys not found!")
        print("Please set TELEGRAM_TOKEN and GROQ_API_KEY environment variables")
        print("On Heroku: Settings → Reveal Config Vars")
        return
    
    print("✅ API keys loaded successfully")
    print("📱 Connecting to Telegram...")
    
    # Create Application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    print("✅ Bot is running! Press Ctrl+C to stop")
    print("=" * 50)
    
    application.run_polling()

if __name__ == '__main__':
    main()
