import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bs4 import BeautifulSoup

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get API keys
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# ========================
# REAL-TIME WEB SEARCH
# ========================
def search_duckduckgo(query):
    """Get real-time answers from DuckDuckGo (FREE, no API key needed)"""
    try:
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        result = {
            "answer": "",
            "source": "",
            "summary": ""
        }
        
        # Get instant answer
        if data.get("AbstractText"):
            result["answer"] = data["AbstractText"]
            result["source"] = data.get("AbstractURL", "")
        
        # Get related topics
        elif data.get("RelatedTopics"):
            for topic in data["RelatedTopics"][:3]:  # Get top 3
                if "Text" in topic:
                    result["answer"] += topic["Text"] + "\n\n"
        
        # If no answer from DDG, do a simple web search
        if not result["answer"]:
            result = simple_web_search(query)
        
        return result
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        return {"answer": "", "source": "", "summary": "Search failed"}

def simple_web_search(query):
    """Fallback web search using DuckDuckGo HTML"""
    try:
        url = f"https://html.duckduckgo.com/html/?q={query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract first few results
        results = []
        for result in soup.find_all("a", class_="result__url", limit=5):
            text = result.get_text(strip=True)
            if text and len(text) > 20:
                results.append(text)
        
        return {
            "answer": "\n".join(results[:3]) if results else "No results found",
            "source": "DuckDuckGo Web Search",
            "summary": "Web search results"
        }
    except:
        return {"answer": "Search unavailable", "source": "", "summary": ""}

def get_ai_summary(search_result, query):
    """Use Groq to summarize search results"""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        
        prompt = f"""
        Question: {query}
        
        Search Results: {search_result['answer'][:2000]}
        
        Please provide a concise, accurate answer based on the search results.
        Include key facts and mention if information seems current or outdated.
        """
        
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=400
        )
        
        return response.choices[0].message.content
    except:
        return search_result['answer']

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context):
    welcome = """
🔍 *REAL-TIME AI BOT*

*Now with Web Search!* 🌐

⚡ **Features:**
• Real-time web search for current information
• AI-powered summaries
• Free & fast responses

📝 **Try asking:**
• "Current president of USA"
• "Latest news today"
• "Weather in London"
• "Stock price of Apple"

I'll search the web and give you current answers! 🚀
    """
    await update.message.reply_text(welcome, parse_mode="Markdown")

async def handle_message(update: Update, context):
    try:
        user_message = update.message.text
        logger.info(f"Question: {user_message}")
        
        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # ========================
        # STEP 1: REAL-TIME SEARCH
        # ========================
        await update.message.reply_text("🔍 Searching for current information...")
        
        search_result = search_duckduckgo(user_message)
        
        # ========================
        # STEP 2: AI SUMMARY
        # ========================
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        if search_result['answer']:
            # Get AI summary
            ai_summary = get_ai_summary(search_result, user_message)
            
            # Format response
            response = f"🤖 *AI Summary:*\n\n{ai_summary}\n\n"
            
            if search_result['source']:
                response += f"📚 *Source:* {search_result['source']}\n\n"
            
            response += "💡 *Note:* Information from real-time web search"
            
            await update.message.reply_text(response, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ No current information found. Try rephrasing your question.")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("Sorry, an error occurred. Please try again.")

# ========================
# MAIN FUNCTION
# ========================
def main():
    print("=" * 50)
    print("🔍 REAL-TIME SEARCH BOT STARTING...")
    print("=" * 50)
    
    # Check API keys
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN missing!")
        return
    
    print("✅ Telegram token found")
    
    if not GROQ_API_KEY:
        print("⚠️ GROQ_API_KEY missing - AI summaries disabled")
        print("⚠️ Will use raw search results only")
    
    print("🤖 Bot starting with real-time web search...")
    
    # Create bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Bot is running! Try asking current questions!")
    print("=" * 50)
    
    app.run_polling()

if __name__ == '__main__':
    main()
