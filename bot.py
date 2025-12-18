import os
import io
import json
import requests
import logging
import random
import tempfile
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from groq import Groq
from PIL import Image
from youtubesearchpython import VideosSearch

# ========================
# SETUP & CONFIGURATION
# ========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# API Keys (set these in Heroku Config Vars)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# Initialize Groq AI
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Conversation memory
user_conversations = {}

# ========================
# CONVERSATION MANAGEMENT
# ========================
def get_user_conversation(user_id):
    """Get or create conversation history"""
    if user_id not in user_conversations:
        user_conversations[user_id] = [
            {
                "role": "system",
                "content": """You are StarAI, a friendly, intelligent AI assistant with personality.
                
                PERSONALITY: Warm, empathetic, knowledgeable, engaging, supportive.
                
                CAPABILITIES:
                1. Have natural human-like conversations
                2. Answer any question thoughtfully
                3. Provide emotional support
                4. Explain complex concepts simply
                5. Generate creative content
                6. Remember conversation context
                
                SPECIAL FEATURES:
                - Can create images (/image command)
                - Can find music (/music command)
                - Can tell jokes, facts, quotes
                - Engages naturally with users
                
                RESPONSE STYLE:
                - Use natural language with emojis 😊
                - Be warm and engaging
                - Show genuine interest
                - Keep responses under 500 words
                
                Current Date: December 2024"""
            }
        ]
    return user_conversations[user_id]

def update_conversation(user_id, role, content):
    """Update conversation history"""
    conversation = get_user_conversation(user_id)
    conversation.append({"role": role, "content": content})
    
    # Keep only last 15 messages
    if len(conversation) > 16:
        conversation = [conversation[0]] + conversation[-15:]

def clear_conversation(user_id):
    """Clear conversation memory"""
    if user_id in user_conversations:
        del user_conversations[user_id]

# ========================
# IMAGE GENERATION
# ========================
def generate_image(prompt):
    """Generate images using free APIs"""
    try:
        # Method 1: Pollinations.ai (free)
        poll_url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=512&height=512"
        response = requests.get(poll_url, timeout=15)
        
        if response.status_code == 200:
            # Save to temp file
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp.write(response.content)
                return tmp.name
        
        # Method 2: Placeholder with text
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            # Create simple image
            img = Image.new('RGB', (400, 400), color=(73, 109, 137))
            
            # Add text (simplified)
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)
            
            # Simple text
            text = prompt[:30] if len(prompt) > 30 else prompt
            draw.text((50, 180), f"StarAI:\n{text}", fill=(255, 255, 255))
            
            img.save(tmp.name, 'PNG')
            return tmp.name
            
    except Exception as e:
        logger.error(f"Image error: {e}")
        return None

# ========================
# MUSIC SEARCH
# ========================
def search_music(query):
    """Search for music on YouTube"""
    try:
        videos_search = VideosSearch(query, limit=3)
        results = videos_search.result()['result']
        
        music_list = []
        for i, video in enumerate(results[:3], 1):
            title = video['title'][:50]
            url = video['link']
            duration = video.get('duration', 'N/A')
            music_list.append(f"{i}. 🎵 {title}\n   ⏱️ {duration}\n   🔗 {url}")
        
        return music_list
    except:
        return ["Use: /music <song or artist>"]

# ========================
# FUN CONTENT
# ========================
JOKES = [
    "😂 Why don't scientists trust atoms? Because they make up everything!",
    "😄 Why did the scarecrow win an award? Because he was outstanding in his field!",
    "🤣 What do you call a fake noodle? An impasta!",
    "😆 Why did the math book look so sad? Because it had too many problems!",
    "😊 How does the moon cut his hair? Eclipse it!",
]

FACTS = [
    "🐝 Honey never spoils! Archaeologists have found 3000-year-old honey that's still edible.",
    "🧠 Octopuses have three hearts! Two pump blood to gills, one to the body.",
    "🌊 The shortest war was Britain-Zanzibar in 1896. It lasted 38 minutes!",
    "🐌 Snails can sleep for up to three years when hibernating.",
    "🦒 A giraffe's neck has the same number of vertebrae as humans: seven!",
]

QUOTES = [
    "🌟 'The only way to do great work is to love what you do.' - Steve Jobs",
    "💫 'Your time is limited, don't waste it living someone else's life.' - Steve Jobs",
    "🚀 'The future belongs to those who believe in the beauty of their dreams.' - Eleanor Roosevelt",
    "🌱 'The only impossible journey is the one you never begin.' - Tony Robbins",
    "💖 'Be yourself; everyone else is already taken.' - Oscar Wilde",
]

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command with interactive buttons"""
    user = update.effective_user
    
    welcome = f"""
🌟 *WELCOME TO STARAI v2.0, {user.first_name}!* 🌟

✨ *Your Complete AI Companion*

🎨 **CREATE:**
• Images from text
• Art and designs
• Visual content

🎵 **MUSIC:**
• Find songs & artists
• Get YouTube links
• Discover new music

💬 **CHAT:**
• Natural conversations
• Emotional support
• Learning & knowledge
• Deep discussions

🎭 **FUN:**
• Jokes & humor
• Cool facts
• Inspiring quotes
• Entertainment

🔧 **COMMANDS:**
/image <text> - Generate images
/music <song> - Find music
/joke - Get a joke
/fact - Learn a fact
/quote - Inspiration
/clear - Reset chat
/help - All commands

*Tap buttons below or type commands!* 🚀
    """
    
    # Clear old conversation
    clear_conversation(user.id)
    
    # Create buttons
    keyboard = [
        [InlineKeyboardButton("🎨 Create Image", callback_data='create_image'),
         InlineKeyboardButton("🎵 Find Music", callback_data='find_music')],
        [InlineKeyboardButton("😂 Get Joke", callback_data='get_joke'),
         InlineKeyboardButton("💡 Get Fact", callback_data='get_fact')],
        [InlineKeyboardButton("📜 Get Quote", callback_data='get_quote'),
         InlineKeyboardButton("🆘 Help", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 *STARAI HELP CENTER*

🎨 **MEDIA COMMANDS:**
/image <description> - Generate AI image
/music <song/artist> - Find music links
/meme - Get fun images

💬 **CHAT COMMANDS:**
/start - Welcome message
/help - This help
/clear - Reset conversation
/about - About StarAI

🎭 **FUN COMMANDS:**
/joke - Get a joke
/fact - Learn a fact  
/quote - Inspiring quote

🤖 **NATURAL LANGUAGE:**
You can also say:
• "Create an image of a dragon"
• "Find music by Taylor Swift"
• "Tell me a joke"
• "Explain quantum physics"
• "I need advice"

*Just talk to me naturally!* 😊
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """About StarAI"""
    about_text = """
🤖 *ABOUT STARAI v2.0*

✨ **Version:** Complete AI Assistant

💝 **Mission:**
To be your intelligent companion for creativity, knowledge, and conversation.

🧠 **Powered by:**
• Groq AI for intelligent conversations
• Multiple APIs for media creation
• Natural language understanding

🌟 **Features:**
✅ Human-like conversations
✅ Image generation
✅ Music discovery
✅ Emotional intelligence
✅ Learning & teaching
✅ Fun & entertainment

🔧 **Technology:**
• Python & Telegram Bot API
• Advanced AI models
• Real-time processing
• Cloud deployment

*StarAI - More than a bot, a companion!* 💫
    """
    await update.message.reply_text(about_text, parse_mode="Markdown")

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate image from text"""
    prompt = ' '.join(context.args)
    
    if not prompt:
        await update.message.reply_text(
            "🎨 *Usage:* /image <description>\n\n"
            "Examples:\n• /image sunset over mountains\n• /image cute cat in space\n• /image futuristic city",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text(f"🎨 *Creating:* {prompt}\n\nPlease wait...", parse_mode="Markdown")
    
    image_path = generate_image(prompt)
    
    if image_path:
        try:
            with open(image_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"✨ *Generated:* {prompt}\n*Created by StarAI* 🎨",
                    parse_mode="Markdown"
                )
        finally:
            # Clean up temp file
            try:
                os.unlink(image_path)
            except:
                pass
    else:
        await update.message.reply_text(
            "❌ *Image creation failed.*\n\nTry:\n• Simpler description\n• Different keywords\n• Or try again later",
            parse_mode="Markdown"
        )

async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for music"""
    query = ' '.join(context.args)
    
    if not query:
        await update.message.reply_text(
            "🎵 *Usage:* /music <song or artist>\n\n"
            "Examples:\n• /music Bohemian Rhapsody\n• /music Taylor Swift\n• /music classical music",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text(f"🔍 *Searching:* {query}", parse_mode="Markdown")
    
    results = search_music(query)
    
    response = "🎶 *Music Results:*\n\n"
    for result in results:
        response += f"{result}\n\n"
    
    response += "💡 *Note:* These are YouTube links for legal listening."
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tell a joke"""
    joke = random.choice(JOKES)
    await update.message.reply_text(f"😂 *Joke:*\n\n{joke}", parse_mode="Markdown")

async def fact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Share a fun fact"""
    fact = random.choice(FACTS)
    await update.message.reply_text(f"💡 *Did you know?*\n\n{fact}", parse_mode="Markdown")

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Share inspirational quote"""
    quote = random.choice(QUOTES)
    await update.message.reply_text(f"📜 *Quote:*\n\n{quote}", parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation memory"""
    user = update.effective_user
    clear_conversation(user.id)
    await update.message.reply_text(
        "🧹 *Conversation cleared!*\n\nLet's start fresh! 😊\nSay hi or ask me anything!",
        parse_mode="Markdown"
    )

async def meme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get a fun image"""
    try:
        # Get random image from Unsplash
        response = requests.get("https://source.unsplash.com/random/400x400/?funny,meme,comedy", timeout=10)
        
        if response.status_code == 200:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
            
            with open(tmp_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption="😄 *Fun Image!*\nUse /image to create your own!",
                    parse_mode="Markdown"
                )
            
            # Clean up
            try:
                os.unlink(tmp_path)
            except:
                pass
        else:
            await joke_command(update, context)
            
    except:
        await update.message.reply_text(
            "🎭 Need fun? Try:\n• /joke - For laughs\n• /image - Create memes\n• Just chat with me! 😊",
            parse_mode="Markdown"
        )

# ========================
# BUTTON HANDLERS
# ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'create_image':
        await query.edit_message_text(
            "🎨 *Image Creation*\n\nSend: /image <description>\n\nExamples:\n• /image dragon in forest\n• /image cyberpunk city\n• /image cute puppy",
            parse_mode="Markdown"
        )
    elif query.data == 'find_music':
        await query.edit_message_text(
            "🎵 *Music Search*\n\nSend: /music <song or artist>\n\nExamples:\n• /music Imagine Dragons\n• /music chill lofi\n• /music 80s hits",
            parse_mode="Markdown"
        )
    elif query.data == 'get_joke':
        await query.edit_message_text(f"😂 *Joke:*\n\n{random.choice(JOKES)}", parse_mode="Markdown")
    elif query.data == 'get_fact':
        await query.edit_message_text(f"💡 *Fact:*\n\n{random.choice(FACTS)}", parse_mode="Markdown")
    elif query.data == 'get_quote':
        await query.edit_message_text(f"📜 *Quote:*\n\n{random.choice(QUOTES)}", parse_mode="Markdown")
    elif query.data == 'help':
        await help_command(update, context)

# ========================
# AI RESPONSE GENERATOR
# ========================
def generate_ai_response(user_id, user_message):
    """Generate intelligent AI response"""
    try:
        if not client:
            return "🤖 *AI Service:* Currently unavailable. Try commands like /image or /music!"
        
        conversation = get_user_conversation(user_id)
        conversation.append({"role": "user", "content": user_message})
        
        response = client.chat.completions.create(
            messages=conversation,
            model="llama-3.1-8b-instant",
            temperature=0.8,
            max_tokens=600
        )
        
        ai_response = response.choices[0].message.content
        conversation.append({"role": "assistant", "content": ai_response})
        
        return ai_response
        
    except Exception as e:
        logger.error(f"AI error: {e}")
        return get_fallback_response(user_message)

def get_fallback_response(user_message):
    """Fallback responses"""
    user_lower = user_message.lower()
    
    # Greetings
    greetings = {
        "hi": "👋 Hello! I'm StarAI! How can I help you today? 😊",
        "hello": "🌟 Hello there! Great to meet you! What would you like to chat about?",
        "hey": "😄 Hey! I'm here and ready to help! Ask me anything!",
        "how are you": "✨ I'm doing great, thanks for asking! Ready to assist you. How about you?",
    }
    
    for key, response in greetings.items():
        if key in user_lower:
            return response
    
    # Common questions
    if "love" in user_lower:
        return """💖 *Love* is a complex mix of emotions including care, intimacy, protectiveness, and trust.

Types of love:
• Romantic ❤️
• Familial 👨‍👩‍👧‍👦  
• Platonic (friendship) 👫
• Self-love 💝

It's one of the most beautiful human experiences!"""
    
    if "president" in user_lower:
        return """🇺🇸 For current leaders:
• Check official government websites
• Follow reliable news sources
• I can explain political systems!"""
    
    # Default
    return """✨ I'd love to help! You can:

🎨 *Create images:* "Make an image of a sunset"
🎵 *Find music:* "Play some jazz music"
💬 *Chat naturally:* "Explain quantum physics"
🎭 *Have fun:* "Tell me a joke"

Or use commands: /image, /music, /joke, /help 😊"""

# ========================
# MAIN MESSAGE HANDLER
# ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all incoming messages"""
    try:
        user = update.effective_user
        user_message = update.message.text
        
        logger.info(f"User {user.id}: {user_message[:50]}")
        
        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # Check for image requests
        image_words = ["create image", "generate image", "draw", "paint", "picture of", "image of"]
        if any(word in user_message.lower() for word in image_words):
            prompt = user_message
            for word in image_words:
                if word in user_message.lower():
                    prompt = user_message.lower().split(word)[-1].strip()
                    break
            
            if not prompt or len(prompt) < 3:
                prompt = "a beautiful artwork"
            
            await update.message.reply_text(f"🎨 Creating: {prompt}...")
            image_path = generate_image(prompt)
            
            if image_path:
                with open(image_path, 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=f"✨ *Created:* {prompt}\n*By StarAI* 🎨",
                        parse_mode="Markdown"
                    )
                try:
                    os.unlink(image_path)
                except:
                    pass
            else:
                await update.message.reply_text("Try: /image <description>")
            return
        
        # Check for music requests
        music_words = ["play music", "find song", "music by", "listen to", "song by"]
        if any(word in user_message.lower() for word in music_words):
            query = user_message
            for word in music_words:
                if word in user_message.lower():
                    query = user_message.lower().split(word)[-1].strip()
                    break
            
            if not query:
                query = "popular music"
            
            await update.message.reply_text(f"🎵 Searching: {query}...")
            results = search_music(query)
            
            response = "🎶 *Results:*\n\n"
            for result in results:
                response += f"{result}\n\n"
            
            await update.message.reply_text(response, parse_mode="Markdown")
            return
        
        # Generate AI response
        ai_response = generate_ai_response(user.id, user_message)
        
        # Send response
        await update.message.reply_text(ai_response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "❌ *Error occurred.*\n\nTry:\n• /help for commands\n• Rephrase your message\n• I'm still learning! 😊",
            parse_mode="Markdown"
        )

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start the bot"""
    print("=" * 50)
    print("🌟 STARAI v2.0 - COMPLETE AI ASSISTANT")
    print("=" * 50)
    
    # Check API keys
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN missing!")
        print("Add to Heroku: Settings → Config Vars")
        return
    
    if not GROQ_API_KEY:
        print("⚠️ WARNING: GROQ_API_KEY missing")
        print("Get FREE key: https://console.groq.com")
        print("Chat features limited without it")
    
    print("✅ Starting StarAI with all features...")
    
    # Create application
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add command handlers
    commands = [
        ("start", start),
        ("help", help_command),
        ("about", about_command),
        ("image", image_command),
        ("music", music_command),
        ("joke", joke_command),
        ("fact", fact_command),
        ("quote", quote_command),
        ("clear", clear_command),
        ("meme", meme_command),
    ]
    
    for command, handler in commands:
        app.add_handler(CommandHandler(command, handler))
    
    # Add button handler
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Add message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ StarAI v2.0 is running!")
    print("📱 Features: AI Chat, Image Generation, Music Search, Fun Commands")
    print("🔧 Send /start to begin")
    print("=" * 50)
    
    # Start bot
    app.run_polling()

if __name__ == '__main__':
    main()
