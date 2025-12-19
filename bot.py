import os
import io
import json
import requests
import logging
import random
import tempfile
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

# ========================
# SECURE API KEY CONFIGURATION
# ========================
# IMPORTANT: Set these in Heroku Config Vars or .env file locally
# Never commit actual keys to GitHub!

# Get from environment variables (Heroku Config Vars)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# Validation - Will show clear error if missing
if not TELEGRAM_TOKEN:
    error_msg = """
❌ CRITICAL ERROR: TELEGRAM_TOKEN not found!

HOW TO FIX:
1. Go to Heroku Dashboard → Your App → Settings
2. Scroll to 'Config Vars'
3. Add: TELEGRAM_TOKEN = your_bot_token_here

Or locally, create .env file with:
TELEGRAM_TOKEN=your_token_here
"""
    logger.error(error_msg)
    raise ValueError("TELEGRAM_TOKEN not set in environment variables")

if not GROQ_API_KEY:
    logger.warning("⚠️ GROQ_API_KEY not found - AI chat features will be limited")
    logger.info("Get FREE API key: https://console.groq.com/keys")
    client = None
else:
    # Initialize Groq AI only if key is available
    client = Groq(api_key=GROQ_API_KEY)

# ========================
# ADDITIONAL CONFIG (Optional)
# ========================
# For payment verification or admin features
ADMIN_IDS = os.environ.get('ADMIN_IDS', '').split(',')  # Comma-separated Telegram IDs
PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID', '')
PAYPAL_SECRET = os.environ.get('PAYPAL_SECRET', '')

# Conversation memory (for human-like conversations)
user_conversations = {}

# ========================
# SIMPLE DONATION DATABASE
# ========================
class DonationDB:
    def __init__(self):
        # On Heroku, use /tmp directory for writeable storage
        if 'DYNO' in os.environ:  # Detect if running on Heroku
            self.db_file = "/tmp/starai_donations.db"
        else:
            self.db_file = "starai_donations.db"
        self.init_db()
    
    def init_db(self):
        """Initialize SQLite database"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    amount REAL,
                    status TEXT DEFAULT 'pending',
                    transaction_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    verified_at TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS supporters (
                    user_id INTEGER PRIMARY KEY,
                    total_donated REAL DEFAULT 0,
                    first_donation TIMESTAMP,
                    last_donation TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"✅ Donation database initialized: {self.db_file}")
            
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
    
    def add_donation(self, user_id, username, first_name, amount, transaction_id=""):
        """Add a donation record"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO donations (user_id, username, first_name, amount, transaction_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, amount, transaction_id))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            logger.error(f"❌ Add donation error: {e}")
            return False
    
    def verify_donation(self, transaction_id):
        """Mark donation as verified"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Get donation details
            cursor.execute('SELECT user_id, amount FROM donations WHERE transaction_id = ?', (transaction_id,))
            donation = cursor.fetchone()
            
            if donation:
                user_id, amount = donation
                
                # Update donation status
                cursor.execute('UPDATE donations SET status = "verified", verified_at = CURRENT_TIMESTAMP WHERE transaction_id = ?', (transaction_id,))
                
                # Update or create supporter record
                cursor.execute('SELECT * FROM supporters WHERE user_id = ?', (user_id,))
                supporter = cursor.fetchone()
                
                if supporter:
                    cursor.execute('''
                        UPDATE supporters 
                        SET total_donated = total_donated + ?, last_donation = CURRENT_TIMESTAMP
                        WHERE user_id = ?
                    ''', (amount, user_id))
                else:
                    cursor.execute('''
                        INSERT INTO supporters (user_id, total_donated, first_donation, last_donation)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ''', (user_id, amount))
                
                conn.commit()
                conn.close()
                return True
                
        except Exception as e:
            logger.error(f"❌ Verify donation error: {e}")
        
        return False
    
    def get_user_donations(self, user_id):
        """Get user's donations"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM donations 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            ''', (user_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            donations = []
            for row in rows:
                donations.append({
                    "id": row[0],
                    "amount": row[4],
                    "status": row[5],
                    "transaction_id": row[6],
                    "created_at": row[7],
                    "verified_at": row[8]
                })
            
            return donations
            
        except Exception as e:
            logger.error(f"❌ Get donations error: {e}")
            return []
    
    def get_user_total(self, user_id):
        """Get user's total verified donations"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT total_donated FROM supporters WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            
            return result[0] if result else 0
            
        except Exception as e:
            logger.error(f"❌ Get total error: {e}")
            return 0
    
    def get_stats(self):
        """Get donation statistics"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Total verified donations
            cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "verified"')
            total_verified = cursor.fetchone()[0] or 0
            
            # Total pending
            cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "pending"')
            total_pending = cursor.fetchone()[0] or 0
            
            # Supporters count
            cursor.execute('SELECT COUNT(*) FROM supporters WHERE total_donated > 0')
            supporters = cursor.fetchone()[0] or 0
            
            conn.close()
            
            return {
                "total_verified": total_verified,
                "total_pending": total_pending,
                "supporters": supporters
            }
            
        except Exception as e:
            logger.error(f"❌ Get stats error: {e}")
            return {"total_verified": 0, "total_pending": 0, "supporters": 0}

# Initialize donation database
donation_db = DonationDB()

# ========================
# CONVERSATION MANAGEMENT (FOR HUMAN-LIKE CHAT)
# ========================
def get_user_conversation(user_id):
    """Get or create conversation history for human-like chat"""
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
def create_fallback_image(prompt):
    """Create a fallback image when APIs fail"""
    try:
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            # Create image
            img = Image.new('RGB', (512, 512), color=(60, 60, 100))
            draw = ImageDraw.Draw(img)
            
            # Simple text drawing
            font = ImageFont.load_default()
            
            # Format text
            lines = []
            words = prompt.split()
            current_line = ""
            
            for word in words:
                if len(current_line + " " + word) <= 30:
                    current_line = current_line + " " + word if current_line else word
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            
            # Draw main text
            text = "\n".join(lines[:5])
            if len(lines) > 5:
                text += "\n..."
            
            # Simple positioning
            draw.text((50, 200), f"StarAI:\n{text}", fill=(255, 255, 255), font=font)
            draw.text((10, 480), "✨ Created by StarAI", fill=(200, 200, 255))
            
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
                    logger.info(f"✅ Image generated: {tmp.name}")
                    return tmp.name
                    
        except Exception as e:
            logger.error(f"Pollinations.ai error: {e}")
        
        # Method 2: Craiyon API (backup)
        try:
            craiyon_url = "https://api.craiyon.com/v3"
            response = requests.post(craiyon_url, json={"prompt": prompt}, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("images") and len(data["images"]) > 0:
                    import base64
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
    user_name = user.first_name
    
    # Check if user is a supporter
    total_donated = donation_db.get_user_total(user.id)
    is_supporter = total_donated > 0
    
    welcome = f"""
🌟 *WELCOME TO STARAI, {user_name}!* 🌟

✨ *Your Complete AI Companion*

🎨 **CREATE:**
• Images from text
• Art and designs
• Visual content

🎵 **MUSIC:**
• Find songs & artists
• Get YouTube links
• Discover new music

💬 **HUMAN-LIKE CHAT:**
• Natural conversations
• Emotional support
• Learning & knowledge
• Deep discussions

🎭 **FUN:**
• Jokes & humor
• Cool facts
• Inspiring quotes
• Entertainment

💰 **SUPPORT (Optional):**
• Help keep StarAI running
• Get supporter status
• Support development

🔧 **COMMANDS:**
`/image <text>` - Generate images
`/music <song>` - Find music
`/joke` - Get a joke
`/fact` - Learn a fact
`/quote` - Inspiration
`/clear` - Reset chat
`/donate` - Support StarAI
`/mydonations` - Your donations
`/help` - All commands

*Just talk to me naturally for human-like conversation!* 😊
"""
    
    # Add supporter badge if applicable
    if is_supporter:
        supporter_badge = f"\n\n🎖️ *SUPPORTER STATUS:*"
        supporter_badge += f"\n💝 Total Donated: ${total_donated:.2f}"
        supporter_badge += f"\n❤️ Thank you for your support!"
        welcome = welcome.replace("*Just talk to me", supporter_badge + "\n\n*Just talk to me")
    
    # Create buttons
    keyboard = [
        [InlineKeyboardButton("🎨 Create Image", callback_data='create_image'),
         InlineKeyboardButton("🎵 Find Music", callback_data='find_music')],
        [InlineKeyboardButton("😂 Get Joke", callback_data='get_joke'),
         InlineKeyboardButton("💡 Get Fact", callback_data='get_fact')],
        [InlineKeyboardButton("💰 Donate", callback_data='donate'),
         InlineKeyboardButton("📜 Get Quote", callback_data='get_quote')],
        [InlineKeyboardButton("💬 Chat with me", callback_data='chat'),
         InlineKeyboardButton("🆘 Help", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 *STARAI HELP CENTER*

🎨 **MEDIA COMMANDS:**
`/image <description>` - Generate AI image
`/music <song/artist>` - Find music links
`/meme` - Get fun images

💬 **CHAT COMMANDS:**
`/start` - Welcome message
`/help` - This help
`/clear` - Reset conversation

💰 **SUPPORT COMMANDS:**
`/donate` - Support StarAI development
`/mydonations` - Check your donation status

🎭 **FUN COMMANDS:**
`/joke` - Get a joke
`/fact` - Learn a fact  
`/quote` - Inspiring quote

🤖 **HUMAN-LIKE CONVERSATION:**
You can also say:
• "Create an image of a dragon"
• "Find music by Taylor Swift"
• "Tell me a joke"
• "Explain quantum physics"
• "I need advice"
• "How are you feeling?"
• "Let's have a conversation"

*Just talk to me naturally! I understand human language!* 😊
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Donation command"""
    user = update.effective_user
    
    # Get donation stats
    stats = donation_db.get_stats()
    user_total = donation_db.get_user_total(user.id)
    
    donate_text = f"""
💰 *SUPPORT STARAI DEVELOPMENT* 💰

Running StarAI costs money for:
• API keys and AI services
• Server hosting
• Development time
• Maintenance

✨ *Why Support?*
• Keep StarAI free for everyone
• Enable new features
• Help cover operational costs
• Get supporter perks

*Community Stats:*
👥 Total Supporters: {stats['supporters']}
💰 Total Raised: ${stats['total_verified']:.2f}
⏳ Pending: ${stats['total_pending']:.2f}

*Your Donations:* ${user_total:.2f}

*How to donate:*
1. **PayPal:** https://www.paypal.com/ncp/payment/HCPVDSSXRL4K8
2. **Buy Me Coffee:** https://www.buymeacoffee.com/StarAI

*After donating:*
1. Click *✅ I've Donated* below
2. Send your transaction ID
3. Get supporter status! 🎖️
"""
    
    # Add supporter message if applicable
    if user_total > 0:
        donate_text += f"\n\n🎖️ *You're already a supporter!* Thank you! 💝"
    
    keyboard = [
        [InlineKeyboardButton("✅ I've Donated", callback_data='i_donated'),
         InlineKeyboardButton("📊 My Donations", callback_data='my_donations')],
        [InlineKeyboardButton("🔗 PayPal", url='https://www.paypal.com/ncp/payment/HCPVDSSXRL4K8'),
         InlineKeyboardButton("☕ Buy Coffee", url='https://www.buymeacoffee.com/StarAI')],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(donate_text, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=False)

async def mydonations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user's donation status"""
    user = update.effective_user
    
    donations = donation_db.get_user_donations(user.id)
    total = donation_db.get_user_total(user.id)
    
    if donations:
        response = f"""
📊 *YOUR DONATIONS*

*Total Verified:* ${total:.2f}
*Total Transactions:* {len(donations)}

*Recent Donations:*
"""
        for i, donation in enumerate(donations[:5], 1):
            status_icon = "✅" if donation["status"] == "verified" else "⏳"
            response += f"\n{i}. {status_icon} ${donation['amount']:.2f} - {donation['created_at'][:10]}"
            if donation["transaction_id"]:
                response += f"\n   📎 {donation['transaction_id'][:20]}..."
        
        if total > 0:
            response += f"\n\n🎖️ *Supporter Level:* "
            if total >= 50:
                response += "Platinum 🏆"
            elif total >= 20:
                response += "Gold 🥇"
            elif total >= 10:
                response += "Silver 🥈"
            elif total >= 5:
                response += "Bronze 🥉"
            else:
                response += "Supporter 💝"
            
            response += f"\n❤️ Thank you for your support!"
    else:
        response = """
💸 *NO DONATIONS YET*

You haven't made any donations yet.

*Want to support StarAI?*
Use `/donate` to see how you can help!

*Even without donating, you can:*
• Share StarAI with friends
• Give feedback for improvements
• Keep using and enjoying the bot!

*Thank you for being part of the community!* 😊
"""
    
    await update.message.reply_text(response, parse_mode="Markdown")

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
        "🧹 *Conversation cleared!*\n\nLet's start fresh! 😊\nSay hi or ask me anything!",
        parse_mode="Markdown"
    )

async def meme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get a fun image"""
    try:
        # Get random meme image
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
            
            # Clean up
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
# AI RESPONSE GENERATOR (HUMAN-LIKE CONVERSATIONS)
# ========================
def generate_ai_response(user_id, user_message):
    """Generate intelligent AI response for human-like conversation"""
    try:
        if not client:
            return """🤖 *AI Chat Currently Unavailable*

I can still help you with:
🎨 `/image <description>` - Create amazing images
🎵 `/music <song>` - Find music on YouTube
😂 `/joke` - Get a laugh
💡 `/fact` - Learn something new
💰 `/donate` - Support this bot

*Get AI Chat back:* Add `GROQ_API_KEY` to Heroku Config Vars
(Free key: https://console.groq.com)"""
        
        # Get conversation history
        conversation = get_user_conversation(user_id)
        
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
        
        # Update conversation history
        update_conversation(user_id, "assistant", ai_response)
        
        return ai_response
        
    except Exception as e:
        logger.error(f"AI error: {e}")
        return get_fallback_response(user_message)

def get_fallback_response(user_message):
    """Fallback responses when AI is unavailable"""
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
        return """💖 *Love* is a beautiful emotion! It can be romantic, familial, platonic, or self-love. 
What aspect of love are you curious about? ❤️"""
    
    if "your name" in user_lower:
        return "🤖 I'm StarAI! Your friendly AI companion! 😊"
    
    # Default
    return """✨ I'd love to help! You can:

🎨 *Create images:* `/image sunset over mountains`
🎵 *Find music:* `/music Taylor Swift`
💬 *Chat naturally:* Just talk to me!
🎭 *Have fun:* `/joke`, `/fact`, `/quote`

*Need help?* Try `/help` for all commands! 😊"""

# ========================
# BUTTON HANDLERS (FIXED VERSION - ALL BUTTONS WORK!)
# ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ALL inline button presses"""
    query = update.callback_query
    await query.answer()
    
    # Debug logging
    logger.info(f"Button pressed: {query.data}")
    
    if query.data == 'create_image':
        await query.edit_message_text(
            "🎨 *Image Creation*\n\nSend: `/image <description>`\n\n*Examples:*\n• `/image dragon in forest`\n• `/image cyberpunk city`\n• `/image cute puppy`\n\n*Or just type:* \"Create an image of a sunset\"",
            parse_mode="Markdown"
        )
    elif query.data == 'find_music':
        await query.edit_message_text(
            "🎵 *Music Search*\n\nSend: `/music <song or artist>`\n\n*Examples:*\n• `/music Imagine Dragons`\n• `/music chill lofi`\n• `/music 80s hits`\n\n*Or just type:* \"Find music by Taylor Swift\"",
            parse_mode="Markdown"
        )
    elif query.data == 'get_joke':
        joke = random.choice(JOKES)
        await query.edit_message_text(f"😂 *Joke of the Day:*\n\n{joke}", parse_mode="Markdown")
    elif query.data == 'get_fact':
        fact = random.choice(FACTS)
        await query.edit_message_text(f"💡 *Did You Know?*\n\n{fact}", parse_mode="Markdown")
    elif query.data == 'get_quote':
        quote = random.choice(QUOTES)
        await query.edit_message_text(f"📜 *Inspirational Quote:*\n\n{quote}", parse_mode="Markdown")
    elif query.data == 'donate':
        # Call donate_command but for button
        user = query.from_user
        
        # Get donation stats
        stats = donation_db.get_stats()
        user_total = donation_db.get_user_total(user.id)
        
        donate_text = f"""
💰 *SUPPORT STARAI DEVELOPMENT* 💰

Running StarAI costs money for:
• API keys and AI services
• Server hosting
• Development time
• Maintenance

✨ *Why Support?*
• Keep StarAI free for everyone
• Enable new features
• Help cover operational costs
• Get supporter perks

*Community Stats:*
👥 Total Supporters: {stats['supporters']}
💰 Total Raised: ${stats['total_verified']:.2f}
⏳ Pending: ${stats['total_pending']:.2f}

*Your Donations:* ${user_total:.2f}

*How to donate:*
1. **PayPal:** https://www.paypal.com/ncp/payment/HCPVDSSXRL4K8
2. **Buy Me Coffee:** https://www.buymeacoffee.com/StarAI

*After donating:*
1. Click *✅ I've Donated* below
2. Send your transaction ID
3. Get supporter status! 🎖️
"""
        
        # Add supporter message if applicable
        if user_total > 0:
            donate_text += f"\n\n🎖️ *You're already a supporter!* Thank you! 💝"
        
        keyboard = [
            [InlineKeyboardButton("✅ I've Donated", callback_data='i_donated'),
             InlineKeyboardButton("📊 My Donations", callback_data='my_donations')],
            [InlineKeyboardButton("🔗 PayPal", url='https://www.paypal.com/ncp/payment/HCPVDSSXRL4K8'),
             InlineKeyboardButton("☕ Buy Coffee", url='https://www.buymeacoffee.com/StarAI')],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(donate_text, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=False)
    
    elif query.data == 'i_donated':
        user = query.from_user
        context.user_data[f"waiting_proof_{user.id}"] = True
        
        await query.edit_message_text(
            "✅ *PAYMENT CONFIRMATION*\n\n"
            "Please send your **Transaction ID** or **Payment Reference**:\n\n"
            "*Format:* `TXID123456789`\n\n"
            "Or send a screenshot of your payment confirmation.\n\n"
            "*Note:* Verification may take some time.\n"
            "Thank you! 🙏",
            parse_mode="Markdown"
        )
    
    elif query.data == 'my_donations':
        # Call mydonations_command but for button
        user = query.from_user
        donations = donation_db.get_user_donations(user.id)
        total = donation_db.get_user_total(user.id)
        
        if donations:
            response = f"""
📊 *YOUR DONATIONS*

*Total Verified:* ${total:.2f}
*Total Transactions:* {len(donations)}

*Recent Donations:*
"""
            for i, donation in enumerate(donations[:5], 1):
                status_icon = "✅" if donation["status"] == "verified" else "⏳"
                response += f"\n{i}. {status_icon} ${donation['amount']:.2f} - {donation['created_at'][:10]}"
                if donation["transaction_id"]:
                    response += f"\n   📎 {donation['transaction_id'][:20]}..."
            
            if total > 0:
                response += f"\n\n🎖️ *Supporter Level:* "
                if total >= 50:
                    response += "Platinum 🏆"
                elif total >= 20:
                    response += "Gold 🥇"
                elif total >= 10:
                    response += "Silver 🥈"
                elif total >= 5:
                    response += "Bronze 🥉"
                else:
                    response += "Supporter 💝"
                
                response += f"\n❤️ Thank you for your support!"
        else:
            response = """
💸 *NO DONATIONS YET*

You haven't made any donations yet.

*Want to support StarAI?*
Use `/donate` to see how you can help!

*Even without donating, you can:*
• Share StarAI with friends
• Give feedback for improvements
• Keep using and enjoying the bot!

*Thank you for being part of the community!* 😊
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(response, parse_mode="Markdown", reply_markup=reply_markup)
    
    elif query.data == 'back_to_menu':
        # Re-send start menu
        await start(update, context)
    
    elif query.data == 'chat':
        await query.edit_message_text(
            "💬 *Let's Chat!*\n\n"
            "I'm here to talk about anything! 😊\n\n"
            "*You can ask me:*\n"
            "• About science, history, or philosophy\n"
            "• For advice or emotional support\n"
            "• To explain complex concepts\n"
            "• Or just have a friendly chat!\n\n"
            "*Just type your message and I'll respond naturally!* 🎭\n\n"
            "*Try saying:*\n"
            "• \"Hello! How are you?\"\n"
            "• \"Tell me about space\"\n"
            "• \"I need some advice\"\n"
            "• \"What's your favorite thing?\"",
            parse_mode="Markdown"
        )
    
    elif query.data == 'help':
        await query.edit_message_text(
            "🆘 *STARAI HELP CENTER*\n\n"
            "🎨 **MEDIA COMMANDS:**\n"
            "`/image <description>` - Generate AI image\n"
            "`/music <song/artist>` - Find music links\n"
            "`/meme` - Get fun images\n\n"
            "💬 **CHAT COMMANDS:**\n"
            "`/start` - Welcome message\n"
            "`/help` - This help\n"
            "`/clear` - Reset conversation\n\n"
            "💰 **SUPPORT COMMANDS:**\n"
            "`/donate` - Support StarAI development\n"
            "`/mydonations` - Check your donation status\n\n"
            "🎭 **FUN COMMANDS:**\n"
            "`/joke` - Get a joke\n"
            "`/fact` - Learn a fact\n"
            "`/quote` - Inspiring quote\n\n"
            "🤖 **NATURAL LANGUAGE:**\n"
            "You can also say:\n"
            "• \"Create an image of a dragon\"\n"
            "• \"Find music by Taylor Swift\"\n"
            "• \"Tell me a joke\"\n"
            "• \"Explain quantum physics\"\n"
            "• \"I need advice\"\n\n"
            "*Just talk to me naturally!* 😊",
            parse_mode="Markdown"
        )
    
    else:
        # Fallback for any unhandled button
        await query.edit_message_text(
            "🤔 *Not sure what you clicked!*\n\n"
            "Try these commands:\n"
            "• `/image` - Create images\n"
            "• `/music` - Find songs\n"
            "• `/joke` - Get a laugh\n"
            "• `/donate` - Support bot\n\n"
            "Or just chat with me! 💬",
            parse_mode="Markdown"
        )

# ========================
# MAIN MESSAGE HANDLER (HANDLES HUMAN-LIKE CONVERSATIONS)
# ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all incoming messages for human-like conversations"""
    try:
        user = update.effective_user
        user_message = update.message.text
        
        logger.info(f"User {user.id}: {user_message[:50]}")
        
        # Check if user is sending donation proof
        if context.user_data.get(f"waiting_proof_{user.id}"):
            context.user_data.pop(f"waiting_proof_{user.id}", None)
            
            # Extract proof
            transaction_id = user_message
            if user_message.lower().startswith("transaction:") or "txid" in user_message.lower():
                if ":" in user_message:
                    transaction_id = user_message.split(":")[1].strip()
            
            # Ask for amount
            context.user_data[f"waiting_amount_{user.id}"] = transaction_id
            
            await update.message.reply_text(
                "💰 *DONATION AMOUNT*\n\n"
                "How much did you donate? (in USD)\n\n"
                "*Examples:*\n"
                "• `5` (for $5)\n"
                "• `10.50` (for $10.50)\n"
                "• `20` (for $20)\n\n"
                "Please enter the amount:",
                parse_mode="Markdown"
            )
            return
        
        # Check if user is sending donation amount
        if context.user_data.get(f"waiting_amount_{user.id}"):
            transaction_id = context.user_data.pop(f"waiting_amount_{user.id}")
            
            try:
                amount = float(user_message)
                
                # Save donation to database
                success = donation_db.add_donation(
                    user_id=user.id,
                    username=user.username or "No username",
                    first_name=user.first_name,
                    amount=amount,
                    transaction_id=transaction_id
                )
                
                if success:
                    response = f"""
✅ *DONATION RECORDED!*

*Amount:* ${amount:.2f}
*Transaction ID:* {transaction_id}
*Date:* {datetime.now().strftime('%Y-%m-%d %H:%M')}

*Status:* ⏳ **Pending Verification**

*What's next:*
1. Your donation is now recorded
2. It will be verified manually
3. You'll get supporter status once verified

*Your new total:* ${donation_db.get_user_total(user.id):.2f}

*Thank you for supporting StarAI!* 💝

Use `/mydonations` to check your status.
"""
                else:
                    response = "❌ Error recording donation. Please try again."
                
            except ValueError:
                response = "❌ Invalid amount. Please enter a number (like 5 or 10.50)."
            
            await update.message.reply_text(response, parse_mode="Markdown")
            return
        
        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        
        # Check for image requests in natural language
        image_keywords = ["create image", "generate image", "draw", "paint", "picture of", "image of", "make a picture", "generate a picture"]
        if any(keyword in user_message.lower() for keyword in image_keywords):
            prompt = user_message
            
            # Extract prompt from request
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
        
        # Check for joke/fact/quote requests
        if "joke" in user_message.lower() and ("tell" in user_message.lower() or "give" in user_message.lower()):
            await joke_command(update, context)
            return
        
        if "fact" in user_message.lower():
            await fact_command(update, context)
            return
        
        if "quote" in user_message.lower():
            await quote_command(update, context)
            return
        
        # Generate AI response for human-like conversation
        ai_response = generate_ai_response(user.id, user_message)
        
        # Send response
        await update.message.reply_text(ai_response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text(
            "❌ *Error occurred.*\n\nTry:\n• `/help` for commands\n• Rephrase your message\n• I'm still learning! 😊",
            parse_mode="Markdown"
        )

# ========================
# ADMIN COMMANDS (Optional)
# ========================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin commands for donation verification"""
    user = update.effective_user
    
    # Get admin IDs from environment variable (comma-separated)
    admin_ids = [admin_id.strip() for admin_id in ADMIN_IDS if admin_id.strip()]
    
    if str(user.id) not in admin_ids and admin_ids:  # Only check if ADMIN_IDS is set
        await update.message.reply_text("❌ Admin only.", parse_mode="Markdown")
        return
    
    args = context.args
    
    if not args:
        help_text = """
🔧 *ADMIN COMMANDS*

`/admin stats` - Show donation statistics
`/admin pending` - Show pending donations
`/admin verify <txid>` - Verify a donation
`/admin users` - List all supporters
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return
    
    cmd = args[0].lower()
    
    if cmd == "stats":
        stats = donation_db.get_stats()
        
        response = f"""
📊 *ADMIN STATS*

*Total Verified:* ${stats['total_verified']:.2f}
*Total Pending:* ${stats['total_pending']:.2f}
*Total Supporters:* {stats['supporters']}
"""
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "pending":
        # Get pending donations
        conn = sqlite3.connect(donation_db.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM donations WHERE status = "pending" ORDER BY created_at DESC')
        pending = cursor.fetchall()
        conn.close()
        
        if not pending:
            await update.message.reply_text("✅ No pending donations.", parse_mode="Markdown")
            return
        
        response = "⏳ *PENDING DONATIONS*\n\n"
        for i, donation in enumerate(pending):
            response += f"{i+1}. User {donation[1]} ({donation[3]})\n"
            response += f"   Amount: ${donation[4]:.2f}\n"
            response += f"   TXID: {donation[6]}\n"
            response += f"   Date: {donation[7][:16]}\n\n"
        
        response += "*To verify:* `/admin verify TXID`"
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "verify":
        if len(args) < 2:
            await update.message.reply_text("❌ Usage: `/admin verify TXID`", parse_mode="Markdown")
            return
        
        transaction_id = args[1]
        success = donation_db.verify_donation(transaction_id)
        
        if success:
            await update.message.reply_text(f"✅ Donation `{transaction_id}` verified!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Could not verify donation `{transaction_id}`", parse_mode="Markdown")
    
    elif cmd == "users":
        # List top supporters
        conn = sqlite3.connect(donation_db.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, total_donated, first_donation FROM supporters WHERE total_donated > 0 ORDER BY total_donated DESC LIMIT 20')
        supporters = cursor.fetchall()
        conn.close()
        
        if not supporters:
            await update.message.reply_text("❌ No supporters yet.", parse_mode="Markdown")
            return
        
        response = "🏆 *TOP SUPPORTERS*\n\n"
        for i, supporter in enumerate(supporters):
            user_id, total, first_date = supporter
            response += f"{i+1}. User {user_id}\n"
            response += f"   Total: ${total:.2f}\n"
            response += f"   Since: {first_date[:10] if first_date else 'N/A'}\n\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start the bot"""
    print("=" * 50)
    print("🌟 STARAI - COMPLETE AI ASSISTANT")
    print("=" * 50)
    
    # Check API keys
    if not TELEGRAM_TOKEN:
        print("❌ CRITICAL ERROR: TELEGRAM_TOKEN not found!")
        print("\nHOW TO FIX:")
        print("1. Heroku: Settings → Config Vars → Add TELEGRAM_TOKEN")
        print("2. Locally: Create .env file with TELEGRAM_TOKEN=your_token")
        print("\nGet token from @BotFather on Telegram")
        return
    
    if not GROQ_API_KEY:
        print("⚠️ WARNING: GROQ_API_KEY missing")
        print("AI chat features will be limited")
        print("Get FREE key: https://console.groq.com/keys")
        print("Add to Heroku: GROQ_API_KEY=your_key")
    else:
        print("✅ Groq AI: Enabled (Human-like chat available)")
    
    print("✅ Telegram Bot: Ready")
    print("🎨 Image generation: Pollinations.ai + Craiyon")
    print("🎵 Music search: YouTube")
    print("💰 Donation system: PayPal + Buy Me Coffee")
    print("🎭 Fun commands: Jokes, Facts, Quotes")
    print(f"💾 Database: {donation_db.db_file}")
    print("=" * 50)
    
    # Create application
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Add command handlers
        commands = [
            ("start", start),
            ("help", help_command),
            ("image", image_command),
            ("music", music_command),
            ("joke", joke_command),
            ("fact", fact_command),
            ("quote", quote_command),
            ("clear", clear_command),
            ("meme", meme_command),
            ("donate", donate_command),
            ("mydonations", mydonations_command),
            ("admin", admin_command),  # Optional admin commands
        ]
        
        for command, handler in commands:
            app.add_handler(CommandHandler(command, handler))
        
        # Add button handler
        app.add_handler(CallbackQueryHandler(button_callback))
        
        # Add message handler (for human-like conversations)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ StarAI is running with ALL FEATURES!")
        print("📱 Features: Human-like Chat, Image Generation, Music Search, Donations")
        print("🔧 Send /start to begin")
        print("💰 Send /donate to support development")
        print("=" * 50)
        
        # Start bot
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Failed to start: {e}")

if __name__ == '__main__':
    main()
