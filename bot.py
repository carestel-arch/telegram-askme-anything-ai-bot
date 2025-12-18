import os
import json
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
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

# Conversation memory (simple in-memory store)
user_conversations = {}

# ========================
# CONVERSATION MANAGEMENT
# ========================
def get_user_conversation(user_id):
    """Get or create conversation history for user"""
    if user_id not in user_conversations:
        user_conversations[user_id] = [
            {
                "role": "system",
                "content": """You are StarAI, a friendly, intelligent AI assistant.
                
                Personality: Warm, helpful, empathetic, knowledgeable, and engaging.
                
                Capabilities:
                1. Answer any question with depth and insight
                2. Engage in natural, human-like conversations
                3. Show empathy and understanding
                4. Provide thoughtful explanations
                5. Remember conversation context
                6. Be creative and engaging
                
                Guidelines:
                - Be conversational, not robotic
                - Use emojis appropriately 😊
                - Show genuine interest in the user
                - Provide detailed, helpful responses
                - Admit when you don't know something
                - Keep responses under 500 words
                
                Current Date: December 2024"""
            }
        ]
    return user_conversations[user_id]

def update_conversation(user_id, role, content):
    """Update conversation history"""
    conversation = get_user_conversation(user_id)
    conversation.append({"role": role, "content": content})
    
    # Keep only last 10 messages to manage memory
    if len(conversation) > 20:
        conversation = [conversation[0]] + conversation[-19:]
        user_conversations[user_id] = conversation

def clear_conversation(user_id):
    """Clear user's conversation history"""
    if user_id in user_conversations:
        del user_conversations[user_id]

# ========================
# AI RESPONSE GENERATION
# ========================
def generate_ai_response(user_id, user_message):
    """Generate AI response using Groq"""
    try:
        if not client:
            return "AI service is currently unavailable. Please try again later."
        
        # Get conversation history
        conversation = get_user_conversation(user_id)
        
        # Add user message to history
        conversation.append({"role": "user", "content": user_message})
        
        # Generate response
        response = client.chat.completions.create(
            messages=conversation,
            model="llama-3.1-8b-instant",
            temperature=0.8,
            max_tokens=800,
            top_p=0.9,
            frequency_penalty=0.1,
            presence_penalty=0.1
        )
        
        ai_response = response.choices[0].message.content
        
        # Add AI response to conversation history
        conversation.append({"role": "assistant", "content": ai_response})
        
        return ai_response
        
    except Exception as e:
        logger.error(f"AI generation error: {e}")
        return get_fallback_response(user_message)

def get_fallback_response(user_message):
    """Fallback responses for when AI fails"""
    user_lower = user_message.lower()
    
    # Greetings
    greetings = ["hi", "hello", "hey", "hola", "greetings"]
    if any(greet in user_lower for greet in greetings):
        return "👋 Hello! I'm StarAI! How can I help you today?"
    
    # Common questions
    if "love" in user_lower:
        return """💖 Love is a complex mix of emotions, behaviors, and beliefs associated with strong feelings of affection, protectiveness, warmth, and respect for another person.

It can be:
• Romantic love (between partners)
• Familial love (family bonds)
• Platonic love (friendship)
• Self-love (caring for oneself)

Love involves care, intimacy, protection, attraction, and trust. It's one of the most profound human experiences! 😊"""
    
    if "president" in user_lower:
        return """🇺🇸 For current political leadership information:
• US President: Check official whitehouse.gov
• Other countries: Visit their government websites
• Latest elections: Follow reliable news sources

I can help explain political systems or how elections work!"""
    
    if "how are you" in user_lower:
        return "🌟 I'm doing great, thank you for asking! I'm here and ready to help you with anything. How about you?"
    
    if "your name" in user_lower:
        return "✨ I'm StarAI! Your friendly AI assistant. Nice to meet you! 😊"
    
    if "help" in user_lower:
        return "🤝 I'm here to help! You can ask me about anything: science, technology, relationships, philosophy, current events, or just chat!"
    
    # Default fallback
    return """✨ I'd love to help with that! Could you please rephrase your question or provide more details?

💡 **Examples of what I can help with:**
• "Explain quantum physics simply"
• "What are the latest space discoveries?"
• "How do I deal with stress?"
• "Tell me a fun fact!"
• "Can you help me plan my day?"

Ask me anything! I'm here for you. 😊"""

# ========================
# SPECIAL FEATURES
# ========================
def get_current_info(query):
    """Get current information when needed"""
    import requests
    
    query_lower = query.lower()
    
    # Weather requests
    weather_words = ["weather", "temperature", "forecast", "rain", "sunny"]
    if any(word in query_lower for word in weather_words):
        return "🌤️ For current weather, I recommend checking:\n• weather.com\n• accuweather.com\n• your local weather service\n\nI can explain weather patterns or climate science though!"
    
    # News requests
    news_words = ["news", "current events", "latest", "today", "update"]
    if any(word in query_lower for word in news_words):
        return "📰 For latest news, check:\n• BBC News\n• Reuters\n• Associated Press\n• CNN\n• Al Jazeera\n\nI can discuss news topics or explain current events!"
    
    # Time requests
    if "time" in query_lower:
        current_time = datetime.now().strftime("%I:%M %p")
        return f"🕰️ My system time is: {current_time} (UTC)\n\nFor your local time, check your device clock! 😊"
    
    # Date requests
    if "date" in query_lower or "day today" in query_lower:
        current_date = datetime.now().strftime("%B %d, %Y")
        return f"📅 Today is: {current_date}"
    
    return None

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command with beautiful welcome"""
    user = update.effective_user
    
    welcome = f"""
🌟 *WELCOME TO STARAI, {user.first_name}!* 🌟

✨ *Your Intelligent Companion for Everything*

💬 **I'm not just a bot - I'm your AI friend who can:**
• Have meaningful conversations
• Answer ANY question thoughtfully
• Provide emotional support
• Explain complex concepts simply
• Help with decisions and ideas
• Share knowledge and insights

🎭 **Talk to me about:**
• Life, love, and relationships 💖
• Science and technology 🔬
• Philosophy and meaning 🤔
• Current events and news 📰
• Personal growth and goals 🌱
• Fun facts and trivia 🎯

🤝 **How to interact:**
• Just talk naturally - like texting a friend!
• Ask deep or simple questions
• Share your thoughts and feelings
• Request explanations or advice
• Say "help" if you're unsure

💡 **Try saying:**
"Hi StarAI!"
"What is the meaning of life?"
"Can you explain quantum physics?"
"I'm feeling stressed, any advice?"
"Tell me something interesting!"

*I'm here to listen, help, and engage. Let's have a wonderful conversation!* 💫

*Type anything to begin...* 😊
    """
    
    # Clear previous conversation
    clear_conversation(user.id)
    
    await update.message.reply_text(welcome, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 *StarAI Help Center*

💬 **How to use me:**
Just talk to me like a human friend! I understand natural language.

🌟 **What I can do:**
• Answer questions on any topic
• Provide emotional support
• Explain complex ideas simply
• Help with problem-solving
• Engage in deep conversations
• Share knowledge and insights

🎯 **Conversation starters:**
"Hi! How are you?"
"What's your opinion on AI?"
"Can you help me understand something?"
"Tell me a story"
"What should I learn today?"

⚡ **Commands:**
/start - Begin fresh conversation
/help - This help message
/clear - Clear our conversation memory
/about - Learn about StarAI

🔍 **For current information:**
I'll guide you to reliable sources for news, weather, stock prices, etc.

*Remember: I'm here for YOU. Don't hesitate to ask anything!* 💝
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """About StarAI"""
    about_text = """
🤖 *About StarAI*

✨ **Version:** Human-like AI Assistant

💝 **My Purpose:**
To be a compassionate, intelligent companion who makes knowledge accessible and conversations meaningful.

🧠 **Powered by:**
• Groq AI (Llama 3.1) for intelligent responses
• Natural language understanding
• Conversational memory
• Emotional intelligence algorithms

🌟 **My Personality:**
Warm, empathetic, curious, knowledgeable, and genuinely interested in helping you.

🎯 **What Makes Me Different:**
1. *Human-like conversations* - I don't just answer, I engage
2. *Emotional intelligence* - I understand feelings
3. *Depth over speed* - Quality responses matter most
4. *Continuous learning* - I adapt to your style
5. *No judgment zone* - You can ask anything

🔧 **Technology:**
Built with Python, Telegram Bot API, and advanced AI models.

💫 **Philosophy:**
"To illuminate minds and touch hearts through conversation."

*StarAI - More than an assistant, a companion in your journey.* 🌟
    """
    await update.message.reply_text(about_text, parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation memory"""
    user = update.effective_user
    clear_conversation(user.id)
    
    await update.message.reply_text(
        "🧹 *Conversation cleared!*\n\n"
        "I've forgotten our previous chat. Let's start fresh! 😊\n\n"
        "Say hi or ask me anything!",
        parse_mode="Markdown"
    )

# ========================
# MESSAGE HANDLER - THE MAIN BRAIN
# ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all incoming messages"""
    try:
        user = update.effective_user
        user_message = update.message.text
        
        logger.info(f"User {user.id}: {user_message}")
        
        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # Check for current info needs
        current_info = get_current_info(user_message)
        if current_info:
            await update.message.reply_text(current_info, parse_mode="Markdown")
            return
        
        # Generate AI response
        ai_response = generate_ai_response(user.id, user_message)
        
        # Send response
        await update.message.reply_text(ai_response, parse_mode="Markdown")
        
        logger.info(f"Replied to user {user.id}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        error_response = """⚠️ *Oops! Something went wrong.*

I encountered an error processing your message. Please:

1. Try rephrasing your question
2. Wait a moment and try again
3. Use /clear to reset our conversation

I'm still learning and appreciate your patience! 💫

*In the meantime, here's a fun fact:* 
Did you know honey never spoils? Archaeologists have found pots of honey in ancient Egyptian tombs that are over 3,000 years old and still perfectly good to eat! 🍯"""
        
        await update.message.reply_text(error_response, parse_mode="Markdown")

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start StarAI"""
    print("=" * 50)
    print("🤖 STARAI - HUMAN-LIKE AI STARTING")
    print("=" * 50)
    
    # Check API keys
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN not found!")
        print("Add to Heroku Config Vars")
        return
    
    if not GROQ_API_KEY:
        print("❌ ERROR: GROQ_API_KEY not found!")
        print("Get FREE key: https://console.groq.com")
        print("Add to Heroku Config Vars")
        return
    
    print("✅ Telegram token: Found")
    print("✅ Groq API key: Found")
    print("🤖 Initializing StarAI with conversation memory...")
    
    # Create application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("clear", clear_command))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ StarAI is running with human-like intelligence!")
    print("📱 Send /start to begin a conversation")
    print("=" * 50)
    
    # Start bot
    application.run_polling()

if __name__ == '__main__':
    main()
