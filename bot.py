import os
import requests
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get token
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')

# ========================
# SIMPLE SEARCH THAT WORKS
# ========================
def search_simple(query):
    """Simple search that ALWAYS returns something"""
    try:
        # Try Wikipedia first (always works)
        wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(query)}"
        wiki_resp = requests.get(wiki_url, timeout=5)
        
        if wiki_resp.status_code == 200:
            data = wiki_resp.json()
            if 'extract' in data:
                return f"📚 *Wikipedia:*\n{data['extract'][:800]}"
        
        # Try DuckDuckGo Instant Answer
        ddg_url = f"https://api.duckduckgo.com/?q={requests.utils.quote(query)}&format=json&no_html=1"
        ddg_resp = requests.get(ddg_url, timeout=5)
        
        if ddg_resp.status_code == 200:
            data = ddg_resp.json()
            if data.get('AbstractText'):
                return f"🔍 *Search Result:*\n{data['AbstractText']}"
        
        # Return helpful knowledge base
        return get_knowledge(query)
        
    except:
        return get_knowledge(query)

def get_knowledge(query):
    """Knowledge base for common questions"""
    knowledge = {
        # Technology
        "ai": "🤖 *Artificial Intelligence*\nAI is computer systems that can perform tasks normally requiring human intelligence.",
        "artificial intelligence": "🤖 *Artificial Intelligence*\nThe simulation of human intelligence in machines.",
        "machine learning": "🧠 *Machine Learning*\nA subset of AI where computers learn from data without explicit programming.",
        "python": "🐍 *Python*\nA popular programming language used for web development, AI, and data science.",
        
        # Science
        "space": "🚀 *Space Exploration*\nThe discovery and exploration of celestial structures in outer space.",
        "climate change": "🌍 *Climate Change*\nLong-term shifts in temperatures and weather patterns, mainly caused by human activities.",
        "quantum computing": "⚛️ *Quantum Computing*\nComputers that use quantum-mechanical phenomena like superposition to perform operations.",
        
        # Current Affairs
        "president": "🇺🇸 *US President*\nThe President is elected every 4 years. The most recent election was in 2024.",
        "current president": "🇺🇸 *Current US President*\nCheck official government websites or recent news for the most current information.",
        "election": "🗳️ *Elections*\nDemocratic process where people vote to choose their leaders.",
        
        # General
        "weather": "☁️ *Weather*\nFor current weather, check weather.com or your local weather service.",
        "news": "📰 *News*\nFor latest news, check BBC, CNN, Reuters, or other reliable news sources.",
        "stock": "📈 *Stocks*\nFor current stock prices, check financial websites like Yahoo Finance or Bloomberg.",
        
        # How-tos
        "learn python": "📚 *Learn Python*\n1. Start with Python.org tutorial\n2. Try Codecademy or Coursera\n3. Practice with small projects\n4. Join Python communities",
        "cook": "👨‍🍳 *Cooking*\nI can help with recipes! Try asking: 'How to cook pasta' or 'Easy dinner recipes'",
        "travel": "✈️ *Travel*\nFor travel information, check travel guides, booking websites, or tourism boards.",
    }
    
    query_lower = query.lower()
    
    # Check for exact matches
    for key in knowledge:
        if key in query_lower:
            return knowledge[key]
    
    # General answer for anything else
    return f"""🔍 *I can help with:* {query}

💡 *Try asking more specifically:*
• "What is [topic]?"
• "How does [thing] work?"
• "Explain [concept] simply"
• "Latest news about [topic]"

📚 *For detailed information, I recommend:*
1. Searching on Google/Wikipedia
2. Checking official websites
3. Reading recent articles

*Or ask me about:* AI, Technology, Science, Learning, News, etc."""

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context):
    """StarAI Welcome"""
    welcome = """
🌟 *WELCOME TO STARAI* 🌟

*Your Personal AI Assistant*

⚡ **I Can Help With:**
• Answering questions
• Explaining concepts
• Providing information
• Learning resources

🔍 **Try Asking:**
• "What is artificial intelligence?"
• "How does blockchain work?"
• "Explain quantum physics"
• "Latest technology news"

💡 **Examples:**
• "Teach me Python"
• "Climate change explained"
• "Space exploration updates"
• "How to learn coding"

*Ask me anything! I'll do my best to help.* 🚀
    """
    await update.message.reply_text(welcome, parse_mode="Markdown")

async def help_cmd(update: Update, context):
    """Help"""
    help_text = """
🆘 *StarAI Help*

💬 **Just type your question!**

📝 **Example Questions:**
• "What is machine learning?"
• "How to start programming?"
• "Explain global warming"
• "Current tech trends"

⚡ **Tips:**
• Be specific
• Ask one question at a time
• I work best with factual topics

*Ready to learn? Ask away!*
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

# ========================
# MESSAGE HANDLER
# ========================
async def handle_message(update: Update, context):
    """Handle all messages"""
    try:
        user_msg = update.message.text
        
        # Show typing
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # Get response
        response = search_simple(user_msg)
        
        # Send response
        final_response = f"✨ *StarAI Response:*\n\n{response}\n\n💫 *Powered by StarAI*"
        
        await update.message.reply_text(final_response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "❌ *Error occurred.*\nPlease try again or ask a different question.",
            parse_mode="Markdown"
        )

# ========================
# MAIN
# ========================
def main():
    """Start bot"""
    print("=" * 50)
    print("🌟 STARAI - SIMPLE WORKING VERSION")
    print("=" * 50)
    
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN not set!")
        print("Add to Heroku Config Vars")
        return
    
    print("✅ Telegram token found")
    print("🤖 Starting StarAI...")
    
    # Create bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ StarAI is running!")
    print("📱 Send /start to test")
    print("=" * 50)
    
    app.run_polling()

if __name__ == '__main__':
    main()
