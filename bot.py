import os
import io
import json
import requests
import logging
import random
import tempfile
import base64
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from groq import Groq
from PIL import Image, ImageDraw, ImageFont
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

# ========================
# PERSISTENT DATABASE
# ========================
class MemoryDatabase:
    def __init__(self):
        self.db_name = "starai_memory.db"
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Create users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create conversations table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Create user_data table for custom preferences
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_data (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT,
                    favorite_color TEXT,
                    interests TEXT,
                    personality_type TEXT,
                    custom_instructions TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
            
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
    
    def save_user(self, user_id, username, first_name, last_name):
        """Save or update user information"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_seen)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, username, first_name, last_name))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Save user error: {e}")
    
    def save_message(self, user_id, role, content):
        """Save a message to conversation history"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO conversations (user_id, role, content)
                VALUES (?, ?, ?)
            ''', (user_id, role, content))
            
            # Keep only last 20 messages per user
            cursor.execute('''
                DELETE FROM conversations 
                WHERE id IN (
                    SELECT id FROM conversations 
                    WHERE user_id = ? 
                    ORDER BY timestamp ASC 
                    LIMIT (SELECT COUNT(*) - 20 FROM conversations WHERE user_id = ?)
                )
            ''', (user_id, user_id))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Save message error: {e}")
    
    def get_conversation_history(self, user_id, limit=15):
        """Get conversation history for a user"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT role, content FROM conversations 
                WHERE user_id = ? 
                ORDER BY timestamp ASC 
                LIMIT ?
            ''', (user_id, limit))
            
            rows = cursor.fetchall()
            conn.close()
            
            # Format as list of dictionaries
            history = [{"role": row[0], "content": row[1]} for row in rows]
            return history
            
        except Exception as e:
            logger.error(f"Get conversation error: {e}")
            return []
    
    def clear_conversation(self, user_id):
        """Clear conversation history for a user"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM conversations WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Clear conversation error: {e}")
    
    def save_user_data(self, user_id, name=None, favorite_color=None, interests=None, 
                      personality_type=None, custom_instructions=None):
        """Save custom user data"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Get existing data
            cursor.execute('SELECT * FROM user_data WHERE user_id = ?', (user_id,))
            existing = cursor.fetchone()
            
            if existing:
                # Update existing
                cursor.execute('''
                    UPDATE user_data 
                    SET name = COALESCE(?, name),
                        favorite_color = COALESCE(?, favorite_color),
                        interests = COALESCE(?, interests),
                        personality_type = COALESCE(?, personality_type),
                        custom_instructions = COALESCE(?, custom_instructions)
                    WHERE user_id = ?
                ''', (name, favorite_color, interests, personality_type, custom_instructions, user_id))
            else:
                # Insert new
                cursor.execute('''
                    INSERT INTO user_data (user_id, name, favorite_color, interests, personality_type, custom_instructions)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (user_id, name, favorite_color, interests, personality_type, custom_instructions))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Save user data error: {e}")
    
    def get_user_data(self, user_id):
        """Get user's custom data"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM user_data WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "name": row[1],
                    "favorite_color": row[2],
                    "interests": row[3],
                    "personality_type": row[4],
                    "custom_instructions": row[5]
                }
            return None
            
        except Exception as e:
            logger.error(f"Get user data error: {e}")
            return None
    
    def get_all_users(self):
        """Get all registered users"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM users')
            count = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT u.user_id, u.username, u.first_name, 
                       COUNT(c.id) as message_count
                FROM users u
                LEFT JOIN conversations c ON u.user_id = c.user_id
                GROUP BY u.user_id
                ORDER BY u.last_seen DESC
                LIMIT 10
            ''')
            
            users = cursor.fetchall()
            conn.close()
            
            return {
                "total_users": count,
                "recent_users": users
            }
            
        except Exception as e:
            logger.error(f"Get all users error: {e}")
            return {"total_users": 0, "recent_users": []}

# Initialize database
memory_db = MemoryDatabase()

# ========================
# CONVERSATION MANAGEMENT
# ========================
def get_user_conversation(user_id, username="", first_name="", last_name=""):
    """Get or create conversation history with persistent memory"""
    # Save user info to database
    if username or first_name or last_name:
        memory_db.save_user(user_id, username, first_name, last_name)
    
    # Get conversation history from database
    history = memory_db.get_conversation_history(user_id, limit=15)
    
    # Get user's custom data
    user_data = memory_db.get_user_data(user_id)
    
    # Create system prompt with user info
    user_info = ""
    if user_data and user_data.get("name"):
        user_info += f"\n\nUSER INFORMATION:\n- Name: {user_data.get('name')}"
    if user_data and user_data.get("favorite_color"):
        user_info += f"\n- Favorite Color: {user_data.get('favorite_color')}"
    if user_data and user_data.get("interests"):
        user_info += f"\n- Interests: {user_data.get('interests')}"
    if user_data and user_data.get("custom_instructions"):
        user_info += f"\n- Custom Instructions: {user_data.get('custom_instructions')}"
    
    # Check if this is a new conversation or has history
    if not history:
        system_message = {
            "role": "system",
            "content": f"""You are StarAI, a friendly, intelligent AI assistant with personality.
                
PERSONALITY: Warm, empathetic, knowledgeable, engaging, supportive.

CAPABILITIES:
1. Have natural human-like conversations
2. Answer any question thoughtfully
3. Provide emotional support
4. Explain complex concepts simply
5. Generate creative content
6. Remember conversation context
7. Remember user preferences and details

SPECIAL FEATURES:
- Can create images (/image command)
- Can find music (/music command)
- Can tell jokes, facts, quotes
- Engages naturally with users
- Has memory across sessions
- Can learn about users{user_info}

IMPORTANT: You should remember user details from previous conversations. 
If the user told you their name, favorite things, or any personal information, 
you should remember and reference it naturally in conversation.

RESPONSE STYLE:
- Use natural language with emojis 😊
- Be warm and engaging
- Show genuine interest
- Keep responses under 500 words
- Reference user details when appropriate

Current Date: {datetime.now().strftime('%B %Y')}"""
        }
        
        # Save system message to database
        memory_db.save_message(user_id, "system", system_message["content"])
        
        return [system_message]
    
    return history

def update_conversation(user_id, role, content):
    """Update conversation history in database"""
    memory_db.save_message(user_id, role, content)

def clear_conversation(user_id):
    """Clear conversation memory"""
    memory_db.clear_conversation(user_id)

# ========================
# IMAGE GENERATION FUNCTIONS
# ========================
def create_fallback_image(prompt):
    """Create a fallback image with text"""
    try:
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            # Create image
            img = Image.new('RGB', (512, 512), color=(40, 44, 52))
            draw = ImageDraw.Draw(img)
            
            # Load font
            try:
                font = ImageFont.truetype("arial.ttf", 32) if os.path.exists("arial.ttf") else ImageFont.load_default()
            except:
                font = ImageFont.load_default()
            
            # Format text
            lines = []
            words = prompt.split()
            current_line = ""
            
            for word in words:
                if len(current_line + " " + word) <= 20:
                    current_line = current_line + " " + word if current_line else word
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            
            # Draw main text
            text = "\n".join(lines[:4])
            if len(lines) > 4:
                text += "\n..."
            
            # Calculate text position
            text_width = len(max(text.split('\n'), key=len)) * 20
            text_height = len(text.split('\n')) * 40
            
            x = (512 - text_width) // 2
            y = (512 - text_height) // 2
            
            # Draw text
            draw.text((x, y), text, fill=(255, 215, 0), font=font, align="center")
            
            # Add watermark
            draw.text((10, 480), "✨ StarAI Image", fill=(100, 200, 255), font=font)
            
            img.save(tmp.name, 'PNG')
            return tmp.name
            
    except Exception as e:
        logger.error(f"Fallback image error: {e}")
        return None

def generate_image(prompt):
    """Generate images using Pollinations.ai"""
    try:
        logger.info(f"Generating image for: {prompt}")
        
        # Method 1: Pollinations.ai
        try:
            clean_prompt = prompt.strip().replace(" ", "%20")
            poll_url = f"https://image.pollinations.ai/prompt/{clean_prompt}"
            
            params = {
                "width": "512",
                "height": "512",
                "seed": str(random.randint(1, 1000000)),
                "nofilter": "true"
            }
            
            response = requests.get(poll_url, params=params, timeout=30)
            
            if response.status_code == 200 and len(response.content) > 1000:
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                    tmp.write(response.content)
                    return tmp.name
                    
        except Exception as e:
            logger.error(f"Pollinations.ai error: {e}")
        
        # Method 2: Craiyon API
        try:
            craiyon_url = "https://api.craiyon.com/v3"
            response = requests.post(craiyon_url, json={"prompt": prompt}, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("images") and len(data["images"]) > 0:
                    image_data = data["images"][0]
                    if image_data.startswith('data:image'):
                        image_data = image_data.split(',')[1]
                    
                    image_bytes = base64.b64decode(image_data)
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        tmp.write(image_bytes)
                        return tmp.name
                        
        except Exception as e:
            logger.error(f"Craiyon API error: {e}")
        
        # Final fallback
        return create_fallback_image(prompt)
            
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        return create_fallback_image(prompt)

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
            title = video['title'][:50] + "..." if len(video['title']) > 50 else video['title']
            url = video['link']
            duration = video.get('duration', 'N/A')
            views = video.get('viewCount', {}).get('short', 'N/A')
            music_list.append(f"{i}. 🎵 {title}\n   ⏱️ {duration} | 👁️ {views}\n   🔗 {url}")
        
        return music_list
    except Exception as e:
        logger.error(f"Music search error: {e}")
        return ["🎵 Use: `/music <song or artist>`", "Example: `/music Bohemian Rhapsody`"]

# ========================
# FUN CONTENT
# ========================
JOKES = [
    "😂 Why don't scientists trust atoms? Because they make up everything!",
    "😄 Why did the scarecrow win an award? Because he was outstanding in his field!",
    "🤣 What do you call a fake noodle? An impasta!",
    "😆 Why did the math book look so sad? Because it had too many problems!",
    "😊 How does the moon cut his hair? Eclipse it!",
    "😁 Why did the computer go to the doctor? It had a virus!",
]

FACTS = [
    "🐝 Honey never spoils! Archaeologists have found 3000-year-old honey that's still edible.",
    "🧠 Octopuses have three hearts! Two pump blood to gills, one to the body.",
    "🌊 The shortest war was Britain-Zanzibar in 1896. It lasted 38 minutes!",
    "🐌 Snails can sleep for up to three years when hibernating.",
    "🦒 A giraffe's neck has the same number of vertebrae as humans: seven!",
    "🐧 Penguins propose to their mates with pebbles!",
]

QUOTES = [
    "🌟 'The only way to do great work is to love what you do.' - Steve Jobs",
    "💫 'Your time is limited, don't waste it living someone else's life.' - Steve Jobs",
    "🚀 'The future belongs to those who believe in the beauty of their dreams.' - Eleanor Roosevelt",
    "🌱 'The only impossible journey is the one you never begin.' - Tony Robbins",
    "💖 'Be yourself; everyone else is already taken.' - Oscar Wilde",
    "✨ 'Success is not final, failure is not fatal: it is the courage to continue that counts.' - Winston Churchill",
]

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command with interactive buttons"""
    user = update.effective_user
    
    # Save user to database
    memory_db.save_user(user.id, user.username, user.first_name, user.last_name)
    
    # Get user data for personalized welcome
    user_data = memory_db.get_user_data(user.id)
    user_name = user_data.get("name") if user_data and user_data.get("name") else user.first_name
    
    welcome = f"""
🌟 *WELCOME BACK, {user_name}!* 🌟

✨ *Your AI Companion with Memory!*

🎨 **CREATE:**
• Images from text
• Art and designs
• Visual content

🎵 **MUSIC:**
• Find songs & artists
• Get YouTube links
• Discover new music

💬 **CHAT WITH MEMORY:**
• I remember our conversations
• Know your preferences
• Personalized responses
• Learning about you

🎭 **FUN:**
• Jokes & humor
• Cool facts
• Inspiring quotes
• Entertainment

🔧 **COMMANDS:**
`/image <text>` - Generate images
`/music <song>` - Find music
`/joke` - Get a joke
`/fact` - Learn a fact
`/quote` - Inspiration
`/clear` - Reset chat
`/help` - All commands
`/remember` - Set preferences
`/mystats` - See your data

*I remember you! Tell me more about yourself!* 😊
    """
    
    # Create buttons
    keyboard = [
        [InlineKeyboardButton("🎨 Create Image", callback_data='create_image'),
         InlineKeyboardButton("🎵 Find Music", callback_data='find_music')],
        [InlineKeyboardButton("😂 Get Joke", callback_data='get_joke'),
         InlineKeyboardButton("💡 Get Fact", callback_data='get_fact')],
        [InlineKeyboardButton("📜 Get Quote", callback_data='get_quote'),
         InlineKeyboardButton("💾 My Memory", callback_data='my_memory')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=reply_markup)

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Let user set preferences to remember"""
    user = update.effective_user
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "💾 *Set Your Preferences*\n\n"
            "I can remember things about you! Use:\n"
            "`/remember name John` - Remember your name\n"
            "`/remember color blue` - Remember favorite color\n"
            "`/remember interests music,reading` - Remember interests\n"
            "`/remember instructions Be more concise` - Custom instructions\n\n"
            "*I'll remember these for our future conversations!* 🧠",
            parse_mode="Markdown"
        )
        return
    
    category = args[0].lower()
    value = ' '.join(args[1:]) if len(args) > 1 else ""
    
    user_data = {}
    
    if category == "name":
        user_data["name"] = value
        response = f"✅ I'll remember your name is *{value}*! Nice to meet you! 😊"
    elif category == "color" or category == "favorite":
        user_data["favorite_color"] = value
        response = f"✅ I'll remember your favorite color is *{value}*! 🎨"
    elif category == "interests":
        user_data["interests"] = value
        response = f"✅ I'll remember your interests: *{value}*! 🎭"
    elif category == "instructions":
        user_data["custom_instructions"] = value
        response = f"✅ I'll remember your instructions: *{value}*! 📝"
    else:
        response = "❌ Invalid category. Use: name, color, interests, or instructions"
    
    # Save to database
    if user_data:
        memory_db.save_user_data(user.id, **user_data)
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def mystats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's stored information"""
    user = update.effective_user
    
    # Get user data
    user_data = memory_db.get_user_data(user.id)
    
    # Get conversation stats
    history = memory_db.get_conversation_history(user.id)
    message_count = len([msg for msg in history if msg["role"] != "system"])
    
    if user_data:
        stats = f"📊 *Your Profile*\n\n"
        if user_data.get("name"):
            stats += f"• *Name:* {user_data['name']}\n"
        if user_data.get("favorite_color"):
            stats += f"• *Favorite Color:* {user_data['favorite_color']}\n"
        if user_data.get("interests"):
            stats += f"• *Interests:* {user_data['interests']}\n"
        if user_data.get("personality_type"):
            stats += f"• *Personality:* {user_data['personality_type']}\n"
        if user_data.get("custom_instructions"):
            stats += f"• *Instructions:* {user_data['custom_instructions']}\n"
        
        stats += f"\n• *Messages with me:* {message_count}\n"
        stats += f"• *User ID:* {user.id}\n"
        
        if user.username:
            stats += f"• *Username:* @{user.username}\n"
        
        stats += f"\n*I remember you!* 🧠\nUse `/remember` to update your info."
    else:
        stats = (
            "📊 *Your Profile*\n\n"
            "I don't have much information about you yet!\n\n"
            "Tell me about yourself:\n"
            "• `/remember name [your name]`\n"
            "• `/remember color [favorite color]`\n"
            "• `/remember interests [your interests]`\n\n"
            "*I'll remember for our future conversations!* 😊"
        )
    
    await update.message.reply_text(stats, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 *STARAI HELP CENTER - WITH MEMORY!*

🎨 **MEDIA COMMANDS:**
`/image <description>` - Generate AI image
`/music <song/artist>` - Find music links
`/meme` - Get fun images

💬 **CHAT COMMANDS:**
`/start` - Welcome message
`/help` - This help
`/clear` - Reset conversation
`/about` - About StarAI

🧠 **MEMORY COMMANDS:**
`/remember <type> <value>` - Set preferences
`/mystats` - See your stored info
`/forgetme` - Delete your data (coming soon)

🎭 **FUN COMMANDS:**
`/joke` - Get a joke
`/fact` - Learn a fact  
`/quote` - Inspiring quote

🤖 **NATURAL LANGUAGE:**
You can also say:
• "Create an image of a dragon"
• "Find music by Taylor Swift"
• "Tell me a joke"
• "Explain quantum physics"
• "I need advice"
• "My name is John" (I'll remember!)

*I can remember our conversations across sessions!* 🧠😊
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """About StarAI"""
    about_text = """
🤖 *ABOUT STARAI v3.0*

✨ **Version:** AI Assistant with Persistent Memory

💝 **Mission:**
To be your intelligent companion that remembers you and our conversations.

🧠 **NEW: Persistent Memory**
✅ Remembers conversations across sessions
✅ Stores user preferences
✅ Personalized responses
✅ SQLite database storage

🌟 **Features:**
✅ Human-like conversations with memory
✅ Image generation
✅ Music discovery
✅ Emotional intelligence
✅ Learning & teaching
✅ Fun & entertainment
✅ User profiles

🔧 **Technology:**
• Python & Telegram Bot API
• SQLite for persistent memory
• Groq AI for conversations
• Multiple image APIs

*StarAI - Now with memory that lasts!* 💾✨
    """
    await update.message.reply_text(about_text, parse_mode="Markdown")

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate image from text"""
    prompt = ' '.join(context.args)
    
    if not prompt:
        await update.message.reply_text(
            "🎨 *Usage:* `/image <description>`\n\n"
            "*Examples:*\n• `/image sunset over mountains`\n• `/image cute cat in space`\n• `/image futuristic city`\n\n"
            "*Tip:* Be descriptive for better results!",
            parse_mode="Markdown"
        )
        return
    
    # Send initial message
    msg = await update.message.reply_text(
        f"✨ *Creating Image:*\n`{prompt}`\n\n⏳ Please wait... This may take 10-30 seconds.",
        parse_mode="Markdown"
    )
    
    # Generate image
    image_path = generate_image(prompt)
    
    if image_path and os.path.exists(image_path):
        try:
            # Check if file is valid
            if os.path.getsize(image_path) > 1000:
                # Send the image
                with open(image_path, 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=f"🎨 *Generated:* `{prompt}`\n\n✨ Created by StarAI",
                        parse_mode="Markdown"
                    )
                
                # Delete the waiting message
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=msg.message_id
                    )
                except:
                    pass
                    
            else:
                await msg.edit_text(
                    "❌ *Image file is too small or invalid.*\n\nTry a different prompt or try again later.",
                    parse_mode="Markdown"
                )
            
        except Exception as e:
            logger.error(f"Send image error: {e}")
            await msg.edit_text(
                "❌ *Error sending image.*\n\nThe image was created but couldn't be sent. Try again!",
                parse_mode="Markdown"
            )
        finally:
            # Clean up temp file
            try:
                if os.path.exists(image_path):
                    os.unlink(image_path)
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    else:
        await msg.edit_text(
            "❌ *Image creation failed.*\n\nTry:\n• A simpler description\n• Different keywords\n• Wait a moment and try again\n\nExample: `/image simple landscape`",
            parse_mode="Markdown"
        )

async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for music"""
    query = ' '.join(context.args)
    
    if not query:
        await update.message.reply_text(
            "🎵 *Usage:* `/music <song or artist>`\n\n"
            "*Examples:*\n• `/music Bohemian Rhapsody`\n• `/music Taylor Swift`\n• `/music classical music`",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text(f"🔍 *Searching:* `{query}`", parse_mode="Markdown")
    
    results = search_music(query)
    
    if len(results) > 0 and "Use:" not in results[0]:
        response = "🎶 *Music Results:*\n\n"
        for result in results:
            response += f"{result}\n\n"
        response += "💡 *Note:* These are YouTube links for listening."
    else:
        response = "❌ *No results found.*\n\nTry:\n• Different search terms\n• Check spelling\n• Example: `/music Shape of You`"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tell a joke"""
    joke = random.choice(JOKES)
    await update.message.reply_text(f"😂 *Joke of the Day:*\n\n{joke}", parse_mode="Markdown")

async def fact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Share a fun fact"""
    fact = random.choice(FACTS)
    await update.message.reply_text(f"💡 *Did You Know?*\n\n{fact}", parse_mode="Markdown")

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Share inspirational quote"""
    quote = random.choice(QUOTES)
    await update.message.reply_text(f"📜 *Inspirational Quote:*\n\n{quote}", parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation memory"""
    user = update.effective_user
    clear_conversation(user.id)
    await update.message.reply_text(
        "🧹 *Conversation cleared!*\n\n"
        "Note: Your profile data (name, preferences) is still saved.\n"
        "Use `/mystats` to see your data.\n\n"
        "Let's start fresh! 😊",
        parse_mode="Markdown"
    )

async def meme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get a fun image"""
    try:
        meme_topics = ["funny", "meme", "comedy", "cat", "dog", "dank", "wholesome"]
        topic = random.choice(meme_topics)
        response = requests.get(f"https://source.unsplash.com/400x400/?{topic}", timeout=10)
        
        if response.status_code == 200:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
            
            with open(tmp_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"😄 *Random {topic.capitalize()} Image!*\nUse `/image` to create your own!",
                    parse_mode="Markdown"
                )
            
            try:
                os.unlink(tmp_path)
            except:
                pass
        else:
            await joke_command(update, context)
            
    except Exception as e:
        logger.error(f"Meme error: {e}")
        await update.message.reply_text(
            "🎭 Need fun? Try:\n• `/joke` - For laughs\n• `/image` - Create your own memes\n• Just chat with me! 😊",
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
            "🎨 *Image Creation*\n\nSend: `/image <description>`\n\n*Examples:*\n• `/image dragon in forest`\n• `/image cyberpunk city`\n• `/image cute puppy`",
            parse_mode="Markdown"
        )
    elif query.data == 'find_music':
        await query.edit_message_text(
            "🎵 *Music Search*\n\nSend: `/music <song or artist>`\n\n*Examples:*\n• `/music Imagine Dragons`\n• `/music chill lofi`\n• `/music 80s hits`",
            parse_mode="Markdown"
        )
    elif query.data == 'get_joke':
        await query.edit_message_text(f"😂 *Joke:*\n\n{random.choice(JOKES)}", parse_mode="Markdown")
    elif query.data == 'get_fact':
        await query.edit_message_text(f"💡 *Fact:*\n\n{random.choice(FACTS)}", parse_mode="Markdown")
    elif query.data == 'get_quote':
        await query.edit_message_text(f"📜 *Quote:*\n\n{random.choice(QUOTES)}", parse_mode="Markdown")
    elif query.data == 'my_memory':
        user = query.from_user
        user_data = memory_db.get_user_data(user.id)
        
        if user_data and user_data.get("name"):
            response = f"🧠 *I Remember You!*\n\nHi *{user_data['name']}*! 😊\n\n"
            if user_data.get("favorite_color"):
                response += f"Your favorite color is *{user_data['favorite_color']}*! 🎨\n"
            if user_data.get("interests"):
                response += f"You're interested in *{user_data['interests']}*! 🎭\n\n"
            response += "Use `/remember` to update your info!"
        else:
            response = (
                "🧠 *My Memory*\n\n"
                "I don't have much info about you yet!\n\n"
                "Tell me about yourself:\n"
                "• `/remember name [your name]`\n"
                "• `/remember color [favorite color]`\n"
                "• `/remember interests [your interests]`\n\n"
                "*I'll remember for next time!* 😊"
            )
        
        await query.edit_message_text(response, parse_mode="Markdown")
    elif query.data == 'help':
        await help_command(update, context)

# ========================
# AI RESPONSE GENERATOR WITH MEMORY
# ========================
def generate_ai_response(user_id, user_message, username="", first_name="", last_name=""):
    """Generate intelligent AI response with memory"""
    try:
        if not client:
            return "🤖 *AI Service:* Currently unavailable. Try commands like `/image` or `/music`!"
        
        # Get conversation with user info
        conversation = get_user_conversation(user_id, username, first_name, last_name)
        
        # Check if user is telling us their name or info
        lower_msg = user_message.lower()
        name_keywords = ["my name is", "i am called", "call me", "i'm", "im "]
        
        for keyword in name_keywords:
            if keyword in lower_msg:
                parts = user_message.lower().split(keyword)
                if len(parts) > 1 and len(parts[1].strip()) > 1:
                    name = parts[1].strip().split()[0].capitalize()
                    memory_db.save_user_data(user_id, name=name)
                    break
        
        # Add user message to conversation
        conversation.append({"role": "user", "content": user_message})
        
        # Get AI response
        response = client.chat.completions.create(
            messages=conversation,
            model="llama-3.1-8b-instant",
            temperature=0.8,
            max_tokens=600
        )
        
        ai_response = response.choices[0].message.content
        
        # Save both messages to database
        update_conversation(user_id, "user", user_message)
        update_conversation(user_id, "assistant", ai_response)
        
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
    
    # Memory-related
    if "remember" in user_lower and ("my name" in user_lower or "i am" in user_lower):
        return "💾 Tell me: `/remember name [your name]` and I'll remember it forever! 😊"
    
    if "what do you know about me" in user_lower or "do you remember me" in user_lower:
        return "🧠 Use `/mystats` to see what I remember about you! Or tell me about yourself! 😊"
    
    # Default
    return """✨ I'd love to help! You can:

🎨 *Create images:* "Make an image of a sunset"
🎵 *Find music:* "Play some jazz music"
💬 *Chat naturally:* "Explain quantum physics"
🎭 *Have fun:* "Tell me a joke"
🧠 *Tell me about yourself:* "My name is John"

I'll remember what you tell me! Use `/remember` to set preferences. 😊"""

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
        
        # Check for image requests in natural language
        image_keywords = ["create image", "generate image", "draw", "paint", "picture of", "image of", "make a picture", "generate a picture"]
        if any(keyword in user_message.lower() for keyword in image_keywords):
            prompt = user_message
            
            for keyword in image_keywords:
                if keyword in user_message.lower():
                    parts = user_message.lower().split(keyword)
                    if len(parts) > 1:
                        prompt = parts[1].strip()
                        break
            
            if not prompt or len(prompt) < 2:
                prompt = "a beautiful artwork"
            
            msg = await update.message.reply_text(f"🎨 *Creating:* `{prompt}`...", parse_mode="Markdown")
            image_path = generate_image(prompt)
            
            if image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 1000:
                try:
                    with open(image_path, 'rb') as photo:
                        await update.message.reply_photo(
                            photo=photo,
                            caption=f"✨ *Generated:* `{prompt}`\n*By StarAI* 🎨",
                            parse_mode="Markdown"
                        )
                    
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=msg.message_id
                        )
                    except:
                        pass
                except Exception as e:
                    logger.error(f"Error sending image: {e}")
                    await msg.edit_text("❌ Couldn't send the image. Try `/image` command instead.")
                finally:
                    try:
                        if os.path.exists(image_path):
                            os.unlink(image_path)
                    except:
                        pass
            else:
                await msg.edit_text("❌ Image creation failed. Try: `/image <description>`")
            return
        
        # Check for music requests in natural language
        music_keywords = ["play music", "find song", "music by", "listen to", "song by", "find music", "search music"]
        if any(keyword in user_message.lower() for keyword in music_keywords):
            query = user_message
            
            for keyword in music_keywords:
                if keyword in user_message.lower():
                    parts = user_message.lower().split(keyword)
                    if len(parts) > 1:
                        query = parts[1].strip()
                        break
            
            if not query:
                query = "popular music"
            
            msg = await update.message.reply_text(f"🎵 *Searching:* `{query}`...", parse_mode="Markdown")
            results = search_music(query)
            
            if len(results) > 0 and "Use:" not in results[0]:
                response = "🎶 *Music Results:*\n\n"
                for result in results:
                    response += f"{result}\n\n"
                response += "💡 *Note:* YouTube links for listening."
            else:
                response = "❌ *No results found.* Try: `/music <song name>`"
            
            await msg.edit_text(response, parse_mode="Markdown")
            return
        
        # Generate AI response for other messages
        ai_response = generate_ai_response(
            user.id, 
            user_message, 
            user.username, 
            user.first_name, 
            user.last_name
        )
        
        # Send response
        await update.message.reply_text(ai_response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text(
            "❌ *Error occurred.*\n\nTry:\n• `/help` for commands\n• Rephrase your message\n• I'm still learning! 😊",
            parse_mode="Markdown"
        )

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start the bot"""
    print("=" * 50)
    print("🌟 STARAI v3.0 - AI ASSISTANT WITH PERSISTENT MEMORY")
    print("=" * 50)
    
    # Check API keys
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN missing!")
        print("Add to Heroku: Settings → Config Vars")
        print("Or set: export TELEGRAM_TOKEN='your_token'")
        return
    
    if not GROQ_API_KEY:
        print("⚠️ WARNING: GROQ_API_KEY missing")
        print("Get FREE key: https://console.groq.com")
        print("Chat features limited without it")
    
    print("✅ Starting StarAI with PERSISTENT MEMORY...")
    print("💾 Database: SQLite (starai_memory.db)")
    print("📸 Image generation: Pollinations.ai + Craiyon")
    print("🎵 Music search: YouTube")
    print("💬 AI chat: Groq LLaMA 3.1 with memory")
    
    # Show database stats
    stats = memory_db.get_all_users()
    print(f"📊 Database stats: {stats['total_users']} total users")
    
    # Create application
    try:
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
            ("remember", remember_command),
            ("mystats", mystats_command),
        ]
        
        for command, handler in commands:
            app.add_handler(CommandHandler(command, handler))
        
        # Add button handler
        app.add_handler(CallbackQueryHandler(button_callback))
        
        # Add message handler
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ StarAI v3.0 is running WITH MEMORY!")
        print("📱 Features: Persistent Memory, AI Chat, Image Generation, Music Search")
        print("🔧 Send /start to begin")
        print("=" * 50)
        
        # Start bot
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Failed to start: {e}")
        print("Check your TELEGRAM_TOKEN")

if __name__ == '__main__':
    main()
