import os
import re
import requests
import json
import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from groq import Groq

# ========================
# SETUP
# ========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# API Keys
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# Initialize Groq
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ========================
# WEB SEARCH FUNCTION
# ========================
def search_web(query: str) -> dict:
    """
    Search the web for information.
    Returns: {'answer': str, 'source': str, 'has_data': bool}
    """
    try:
        # Clean query
        clean_query = query.strip()
        
        # Try multiple search methods
        methods = [
            search_duckduckgo,
            search_bing,
            search_wikipedia,
            search_brave  # Alternative
        ]
        
        for method in methods:
            try:
                result = method(clean_query)
                if result['has_data']:
                    logger.info(f"Found data using {method.__name__}")
                    return result
            except:
                continue
        
        return {
            'answer': 'No current information found online.',
            'source': 'Web Search',
            'has_data': False
        }
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        return {
            'answer': f'Search error: {str(e)[:100]}',
            'source': '',
            'has_data': False
        }

def search_duckduckgo(query: str) -> dict:
    """Search DuckDuckGo (free, no API key)"""
    try:
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        answer = ""
        source = ""
        
        if data.get("AbstractText"):
            answer = data["AbstractText"]
            source = data.get("AbstractURL", "DuckDuckGo")
        elif data.get("RelatedTopics"):
            for topic in data["RelatedTopics"][:3]:
                if "Text" in topic:
                    answer += topic["Text"] + "\n\n"
            source = "DuckDuckGo"
        
        return {
            'answer': answer[:2000] if answer else "",
            'source': source,
            'has_data': bool(answer)
        }
    except:
        return {'answer': '', 'source': '', 'has_data': False}

def search_bing(query: str) -> dict:
    """Alternative search method"""
    try:
        # Using Bing's quick answer (no API needed)
        url = f"https://www.bing.com/search?q={query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        
        # Extract text (simplified)
        content = response.text[:5000]
        
        # Look for answer patterns
        patterns = [
            r'<div[^>]*class="[^"]*b_ans[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*b_factrow[^"]*"[^>]*>(.*?)</div>',
            r'<p[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</p>'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, content, re.DOTALL)
            if matches:
                answer = re.sub('<[^>]+>', '', matches[0])[:1000]
                return {
                    'answer': answer,
                    'source': 'Bing',
                    'has_data': bool(answer.strip())
                }
        
        return {'answer': '', 'source': '', 'has_data': False}
    except:
        return {'answer': '', 'source': '', 'has_data': False}

def search_wikipedia(query: str) -> dict:
    """Search Wikipedia for factual information"""
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json"
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data["query"]["search"]:
            title = data["query"]["search"][0]["title"]
            
            # Get summary
            params2 = {
                "action": "query",
                "prop": "extracts",
                "exintro": True,
                "explaintext": True,
                "titles": title,
                "format": "json"
            }
            
            response2 = requests.get(url, params=params2)
            data2 = response2.json()
            page = next(iter(data2["query"]["pages"].values()))
            
            if "extract" in page:
                return {
                    'answer': page["extract"][:1500],
                    'source': f"Wikipedia: {title}",
                    'has_data': True
                }
        
        return {'answer': '', 'source': '', 'has_data': False}
    except:
        return {'answer': '', 'source': '', 'has_data': False}

def search_brave(query: str) -> dict:
    """Alternative search engine"""
    try:
        url = f"https://search.brave.com/search?q={query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        
        # Extract snippets (simplified)
        content = response.text[:8000]
        
        # Look for snippets
        snippet_pattern = r'<div[^>]*class="[^"]*snippet-content[^"]*"[^>]*>(.*?)</div>'
        snippets = re.findall(snippet_pattern, content, re.DOTALL)
        
        if snippets:
            answer = re.sub('<[^>]+>', '', snippets[0])[:1000]
            return {
                'answer': answer,
                'source': 'Brave Search',
                'has_data': bool(answer.strip())
            }
        
        return {'answer': '', 'source': '', 'has_data': False}
    except:
        return {'answer': '', 'source': '', 'has_data': False}

# ========================
# AI PROCESSING
# ========================
def process_with_ai(question: str, web_data: dict) -> str:
    """Process question with AI, using web data as context"""
    try:
        if not client:
            return web_data['answer'] if web_data['has_data'] else "AI service unavailable."
        
        # Prepare context
        context = ""
        if web_data['has_data']:
            context = f"Web search information: {web_data['answer'][:1500]}"
        
        # Create AI prompt
        system_prompt = """You are StarAI, a helpful AI assistant. You have access to web search information.
        
        Guidelines:
        1. Provide accurate, helpful information
        2. If web data is available, use it
        3. If no web data, use your knowledge
        4. Be clear about what's from web vs your knowledge
        5. Keep responses conversational but informative
        6. Format nicely with emojis where appropriate
        """
        
        user_prompt = f"""
        Question: {question}
        
        {context}
        
        Please provide a comprehensive answer. Include sources if available.
        """
        
        # Get AI response
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="llama-3.1-8b-instant",
            temperature=0.7,
            max_tokens=800,
            top_p=0.9
        )
        
        ai_answer = response.choices[0].message.content
        
        # Add source attribution
        if web_data['has_data'] and web_data['source']:
            ai_answer += f"\n\n🌐 *Source reference:* {web_data['source']}"
        
        return ai_answer
        
    except Exception as e:
        logger.error(f"AI processing error: {e}")
        return web_data['answer'] if web_data['has_data'] else "Error processing request."

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message for StarAI"""
    user = update.effective_user
    welcome_message = f"""
✨ *Welcome to StarAI, {user.first_name}!* ✨

I'm your personal AI assistant powered by advanced AI and real-time web search.

🌟 *What I can do:*
• Answer any question with up-to-date information
• Search the web for current facts
• Help with research and learning
• Provide explanations and summaries
• Assist with coding, writing, and creativity

🔍 *I search everywhere:*
✓ Current events & news
✓ Science & technology  
✓ History & facts
✓ Weather & geography
✓ Sports & entertainment
✓ And much more!

📝 *Try asking me:*
• "Explain quantum computing"
• "Latest space discoveries"
• "How to learn Python"
• "Best places to visit in Japan"
• "Current cryptocurrency trends"

💬 *Commands:*
/start - Show this welcome
/help - Get help
/search - Quick web search
/about - About StarAI

*Ready to explore the world of knowledge? Ask me anything!* 🚀
    """
    
    # Send welcome with image-like formatting
    await update.message.reply_text(welcome_message, parse_mode="Markdown")
    
    # Send a follow-up message
    await update.message.reply_text(
        "⚡ *StarAI is online and ready!*\n\n"
        "Type your question below or use /search for direct web search.",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 *StarAI Help Center*

💡 *How to use:*
1. Simply type your question
2. I'll search the web and use AI to answer
3. For quick web results, use /search

🔧 *Commands:*
/start - Welcome message
/help - This help message
/search <query> - Direct web search
/about - About StarAI

🌐 *Search Capabilities:*
• Real-time web search
• Multiple search engines
• Wikipedia integration
• AI-powered summaries

📞 *Need more help?*
Just ask! I'm here to assist with anything.
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """About StarAI"""
    about_text = """
🤖 *About StarAI*

✨ *Version:* 2.0 (Advanced)

⚡ *Powered by:*
• Groq AI (Llama 3.1) for intelligent responses
• Real-time web search for current information
• Multiple search engines for comprehensive results

🎯 *Mission:*
To provide accurate, up-to-date information to everyone, for free.

🔧 *Technology Stack:*
• Python & Telegram Bot API
• Groq Cloud AI
• Web search APIs
• Real-time data processing

🌟 *Features:*
✓ Natural language understanding
✓ Web search integration  
✓ Real-time information
✓ Multi-source verification
✓ User-friendly interface

💝 *Created with:* Passion for AI and knowledge sharing

*StarAI - Illuminating knowledge across the universe!* 🌟
    """
    await update.message.reply_text(about_text, parse_mode="Markdown")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct web search command"""
    query = ' '.join(context.args)
    
    if not query:
        await update.message.reply_text(
            "🔍 *Usage:* /search <your query>\n\n"
            "Example: /search current Mars missions",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text(
        f"🔍 *Searching for:* {query}\n\n"
        "Please wait while I gather information...",
        parse_mode="Markdown"
    )
    
    # Perform search
    search_result = search_web(query)
    
    if search_result['has_data']:
        response = f"✅ *Search Results:*\n\n{search_result['answer']}\n\n"
        if search_result['source']:
            response += f"📚 *Source:* {search_result['source']}"
    else:
        response = "❌ No results found. Try a different query."
    
    await update.message.reply_text(response, parse_mode="Markdown")

# ========================
# MESSAGE HANDLER
# ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all incoming messages"""
    try:
        user_message = update.message.text
        user_id = update.effective_user.id
        
        logger.info(f"User {user_id}: {user_message}")
        
        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # Step 1: Search the web
        await update.message.reply_text(
            "🌐 *StarAI is searching for information...*",
            parse_mode="Markdown"
        )
        
        web_data = search_web(user_message)
        
        # Step 2: Process with AI
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        if web_data['has_data']:
            await update.message.reply_text(
                "🤖 *Processing information with AI...*",
                parse_mode="Markdown"
            )
        
        # Get AI response
        answer = process_with_ai(user_message, web_data)
        
        # Step 3: Send response
        response_header = "✨ *StarAI Response:* ✨\n\n"
        final_response = response_header + answer
        
        # Split if too long (Telegram limit)
        if len(final_response) > 4000:
            parts = [final_response[i:i+4000] for i in range(0, len(final_response), 4000)]
            for part in parts:
                await update.message.reply_text(part, parse_mode="Markdown")
        else:
            await update.message.reply_text(final_response, parse_mode="Markdown")
        
        logger.info(f"Replied to user {user_id}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "❌ *Error:* Could not process your request.\n"
            "Please try again or rephrase your question.",
            parse_mode="Markdown"
        )

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start StarAI"""
    print("=" * 50)
    print("🌟 STARAI BOT STARTING 🌟")
    print("=" * 50)
    
    # Check API keys
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN not found!")
        print("Set it in Heroku Config Vars")
        return
    
    if not GROQ_API_KEY:
        print("⚠️ WARNING: GROQ_API_KEY not found!")
        print("Web search will work, but AI responses limited")
        print("Get free key: https://console.groq.com")
    
    print("✅ Telegram token: Found")
    print("✅ Groq API key: Found" if GROQ_API_KEY else "⚠️ Groq API key: Missing")
    print("🤖 Initializing StarAI...")
    
    # Create application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("search", search_command))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ StarAI is running!")
    print("📱 Send /start to your bot on Telegram")
    print("=" * 50)
    
    # Start bot
    application.run_polling()

if __name__ == '__main__':
    main()
