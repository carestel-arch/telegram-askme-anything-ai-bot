import os
import io
import json
import requests
import logging
import random
import tempfile
import sqlite3
import hashlib
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
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# PayPal Configuration
PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID')
PAYPAL_SECRET = os.environ.get('PAYPAL_SECRET')
PAYPAL_WEBHOOK_ID = os.environ.get('PAYPAL_WEBHOOK_ID')
PAYPAL_ENVIRONMENT = os.environ.get('PAYPAL_ENVIRONMENT', 'sandbox')

# Determine PayPal API URLs
if PAYPAL_ENVIRONMENT == 'live':
    PAYPAL_API_BASE = 'https://api.paypal.com'
    PAYPAL_WEBHOOK_URL = 'https://api.paypal.com'
else:
    PAYPAL_API_BASE = 'https://api.sandbox.paypal.com'
    PAYPAL_WEBHOOK_URL = 'https://api.sandbox.paypal.com'

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set in environment variables")

if not GROQ_API_KEY:
    logger.warning("⚠️ GROQ_API_KEY not found - AI chat features limited")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

ADMIN_IDS = os.environ.get('ADMIN_IDS', '').split(',')
user_conversations = {}

# ========================
# PAYPAL HELPER FUNCTIONS
# ========================
def get_paypal_access_token():
    """Get PayPal access token for API calls"""
    try:
        if not PAYPAL_CLIENT_ID or not PAYPAL_SECRET:
            logger.error("PayPal credentials not configured")
            return None
        
        auth_url = f"{PAYPAL_API_BASE}/v1/oauth2/token"
        auth = (PAYPAL_CLIENT_ID, PAYPAL_SECRET)
        headers = {"Accept": "application/json", "Accept-Language": "en_US"}
        data = {"grant_type": "client_credentials"}
        
        response = requests.post(auth_url, auth=auth, headers=headers, data=data, timeout=10)
        
        if response.status_code == 200:
            return response.json().get('access_token')
        else:
            logger.error(f"PayPal auth failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"PayPal token error: {e}")
        return None

def create_paypal_order(amount, user_id, description="StarAI Donation"):
    """Create a PayPal order and return approval URL"""
    try:
        access_token = get_paypal_access_token()
        if not access_token:
            return None, "PayPal service unavailable"
        
        order_url = f"{PAYPAL_API_BASE}/v2/checkout/orders"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "PayPal-Request-Id": f"starai_{user_id}_{int(datetime.now().timestamp())}"
        }
        
        order_data = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {
                    "currency_code": "USD",
                    "value": str(amount)
                },
                "description": description,
                "custom_id": f"user_{user_id}"
            }]
        }
        
        response = requests.post(order_url, headers=headers, json=order_data, timeout=10)
        
        if response.status_code == 201:
            order_data = response.json()
            order_id = order_data.get('id')
            
            # Find approval link
            for link in order_data.get('links', []):
                if link.get('rel') == 'approve':
                    approval_url = link.get('href')
                    return order_id, approval_url
            
            return None, "No approval URL found"
        else:
            logger.error(f"PayPal order creation failed: {response.status_code} - {response.text}")
            return None, f"Failed to create order: {response.text}"
            
    except Exception as e:
        logger.error(f"PayPal order error: {e}")
        return None, str(e)

def capture_paypal_order(order_id):
    """Capture a PayPal payment"""
    try:
        access_token = get_paypal_access_token()
        if not access_token:
            return False, "PayPal service unavailable"
        
        capture_url = f"{PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
        
        response = requests.post(capture_url, headers=headers, json={}, timeout=10)
        
        if response.status_code == 201:
            capture_data = response.json()
            status = capture_data.get('status')
            
            if status == 'COMPLETED':
                # Extract payment details
                purchase_unit = capture_data.get('purchase_units', [{}])[0]
                payment = purchase_unit.get('payments', {}).get('captures', [{}])[0]
                
                transaction_id = payment.get('id', '')
                amount = float(payment.get('amount', {}).get('value', 0))
                payer = capture_data.get('payer', {})
                
                return True, {
                    'status': 'COMPLETED',
                    'transaction_id': transaction_id,
                    'amount': amount,
                    'payer_email': payer.get('email_address', ''),
                    'payer_name': payer.get('name', {}).get('given_name', '')
                }
            else:
                return False, f"Payment status: {status}"
        else:
            logger.error(f"PayPal capture failed: {response.status_code} - {response.text}")
            return False, f"Capture failed: {response.text}"
            
    except Exception as e:
        logger.error(f"PayPal capture error: {e}")
        return False, str(e)

# ========================
# DONATION DATABASE (UPDATED FOR PAYPAL)
# ========================
class DonationDB:
    def __init__(self):
        if 'DYNO' in os.environ:
            self.db_file = "/tmp/starai_donations.db"
        else:
            self.db_file = "starai_donations.db"
        self.init_db()
    
    def init_db(self):
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
                    transaction_id TEXT UNIQUE,
                    payment_method TEXT DEFAULT 'manual',
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
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS paypal_orders (
                    order_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    amount REAL,
                    status TEXT DEFAULT 'created',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    captured_at TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
            logger.info(f"✅ Database: {self.db_file}")
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
    
    def add_donation(self, user_id, username, first_name, amount, transaction_id="", payment_method="manual"):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Check if transaction already exists
            cursor.execute('SELECT id FROM donations WHERE transaction_id = ?', (transaction_id,))
            if cursor.fetchone():
                conn.close()
                return False, "Transaction already recorded"
            
            cursor.execute('''
                INSERT INTO donations (user_id, username, first_name, amount, transaction_id, payment_method)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, amount, transaction_id, payment_method))
            conn.commit()
            conn.close()
            return True, "Donation recorded"
        except sqlite3.IntegrityError:
            return False, "Transaction ID already exists"
        except Exception as e:
            logger.error(f"❌ Add donation error: {e}")
            return False, str(e)
    
    def verify_donation(self, transaction_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, amount FROM donations WHERE transaction_id = ?', (transaction_id,))
            donation = cursor.fetchone()
            
            if donation:
                user_id, amount = donation
                cursor.execute('UPDATE donations SET status = "verified", verified_at = CURRENT_TIMESTAMP WHERE transaction_id = ?', (transaction_id,))
                
                cursor.execute('SELECT * FROM supporters WHERE user_id = ?', (user_id,))
                supporter = cursor.fetchone()
                
                if supporter:
                    cursor.execute('UPDATE supporters SET total_donated = total_donated + ?, last_donation = CURRENT_TIMESTAMP WHERE user_id = ?', (amount, user_id))
                else:
                    cursor.execute('INSERT INTO supporters (user_id, total_donated, first_donation, last_donation) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)', (user_id, amount))
                
                conn.commit()
                conn.close()
                return True
        except Exception as e:
            logger.error(f"❌ Verify donation error: {e}")
        return False
    
    def add_paypal_order(self, order_id, user_id, amount):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO paypal_orders (order_id, user_id, amount)
                VALUES (?, ?, ?)
            ''', (order_id, user_id, amount))
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            return False  # Order already exists
        except Exception as e:
            logger.error(f"❌ Add PayPal order error: {e}")
            return False
    
    def update_paypal_order(self, order_id, status):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('UPDATE paypal_orders SET status = ? WHERE order_id = ?', (status, order_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ Update PayPal order error: {e}")
            return False
    
    def get_paypal_order(self, order_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM paypal_orders WHERE order_id = ?', (order_id,))
            order = cursor.fetchone()
            conn.close()
            
            if order:
                return {
                    'order_id': order[0],
                    'user_id': order[1],
                    'amount': order[2],
                    'status': order[3],
                    'created_at': order[4],
                    'captured_at': order[5]
                }
        except Exception as e:
            logger.error(f"❌ Get PayPal order error: {e}")
        return None

donation_db = DonationDB()

# ========================
# CONVERSATION MANAGEMENT
# ========================
def get_user_conversation(user_id):
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

Current Date: December 2024"""
            }
        ]
    return user_conversations[user_id]

def update_conversation(user_id, role, content):
    conversation = get_user_conversation(user_id)
    conversation.append({"role": role, "content": content})
    if len(conversation) > 16:
        conversation = [conversation[0]] + conversation[-15:]

def clear_conversation(user_id):
    if user_id in user_conversations:
        del user_conversations[user_id]

# ========================
# IMAGE GENERATION
# ========================
def create_fallback_image(prompt):
    try:
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            img = Image.new('RGB', (512, 512), color=(60, 60, 100))
            draw = ImageDraw.Draw(img)
            font = ImageFont.load_default()
            
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
            
            text = "\n".join(lines[:5])
            if len(lines) > 5:
                text += "\n..."
            
            draw.text((50, 200), f"StarAI:\n{text}", fill=(255, 255, 255), font=font)
            draw.text((10, 480), "✨ Created by StarAI", fill=(200, 200, 255))
            img.save(tmp.name, 'PNG')
            return tmp.name
    except Exception as e:
        logger.error(f"Fallback image error: {e}")
        return None

def generate_image(prompt):
    try:
        logger.info(f"Generating image for: {prompt}")
        
        # Pollinations.ai
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
        
        # Craiyon API (backup)
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
        
        return create_fallback_image(prompt)
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        return create_fallback_image(prompt)

# ========================
# MUSIC SEARCH
# ========================
def search_music(query):
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
    
    if is_supporter:
        supporter_badge = f"\n\n🎖️ *SUPPORTER STATUS:*"
        supporter_badge += f"\n💝 Total Donated: ${total_donated:.2f}"
        supporter_badge += f"\n❤️ Thank you for your support!"
        welcome = welcome.replace("*Just talk to me", supporter_badge + "\n\n*Just talk to me")
    
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

async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Beautiful donation interface with amount buttons"""
    user = update.effective_user
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
• Get supporter perks

*Community Stats:*
👥 Supporters: {stats['supporters']}
💰 Total Raised: ${stats['total_verified']:.2f}

*Your Donations:* ${user_total:.2f}

*Choose amount:*
"""
    
    # Donation amount buttons
    keyboard = [
        [InlineKeyboardButton("☕ Tea - $3", callback_data='donate_3'),
         InlineKeyboardButton("☕ Coffee - $5", callback_data='donate_5')],
        [InlineKeyboardButton("🥤 Smoothie - $10", callback_data='donate_10'),
         InlineKeyboardButton("🍰 Cake - $20", callback_data='donate_20')],
        [InlineKeyboardButton("💰 Custom Amount", callback_data='donate_custom'),
         InlineKeyboardButton("✅ Check Payment", callback_data='i_donated')],
        [InlineKeyboardButton("📊 My Donations", callback_data='my_donations'),
         InlineKeyboardButton("🔙 Back", callback_data='back_to_menu')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(donate_text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(donate_text, parse_mode="Markdown", reply_markup=reply_markup)

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

*Thank you for being part of the community!* 😊
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Donate", callback_data='donate')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(response, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(response, parse_mode="Markdown", reply_markup=reply_markup)

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = ' '.join(context.args)
    if not prompt:
        await update.message.reply_text(
            "🎨 *Usage:* `/image <description>`\n\n*Examples:*\n• `/image sunset over mountains`\n• `/image cute cat in space`",
            parse_mode="Markdown"
        )
        return
    
    msg = await update.message.reply_text(f"✨ *Creating Image:*\n`{prompt}`\n\n⏳ Please wait...", parse_mode="Markdown")
    image_path = generate_image(prompt)
    
    if image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 1000:
        try:
            with open(image_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"🎨 *Generated:* `{prompt}`\n\n✨ Created by StarAI",
                    parse_mode="Markdown"
                )
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
            except:
                pass
        except Exception as e:
            logger.error(f"Send image error: {e}")
            await msg.edit_text("❌ Error sending image. Try again!")
        finally:
            try:
                if os.path.exists(image_path):
                    os.unlink(image_path)
            except:
                pass
    else:
        await msg.edit_text("❌ Image creation failed. Try a simpler description.")

async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args)
    if not query:
        await update.message.reply_text(
            "🎵 *Usage:* `/music <song or artist>`\n\n*Examples:*\n• `/music Bohemian Rhapsody`\n• `/music Taylor Swift`",
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
        response = "❌ *No results found.* Try different search terms."
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    joke = random.choice(JOKES)
    await update.message.reply_text(f"😂 *Joke of the Day:*\n\n{joke}", parse_mode="Markdown")

async def fact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fact = random.choice(FACTS)
    await update.message.reply_text(f"💡 *Did You Know?*\n\n{fact}", parse_mode="Markdown")

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quote = random.choice(QUOTES)
    await update.message.reply_text(f"📜 *Inspirational Quote:*\n\n{quote}", parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    clear_conversation(user.id)
    await update.message.reply_text("🧹 *Conversation cleared!* Let's start fresh! 😊", parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🆘 *STARAI HELP CENTER*

🎨 **MEDIA COMMANDS:**
`/image <description>` - Generate AI image
`/music <song/artist>` - Find music links

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

*Just talk to me naturally!* 😊
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

# ========================
# PAYMENT SELECTION FUNCTION
# ========================
async def show_payment_options(update: Update, context: ContextTypes.DEFAULT_TYPE, amount):
    """Show payment buttons with PayPal automatic option"""
    query = update.callback_query
    user = query.from_user
    
    # Store the selected amount
    context.user_data[f"selected_amount_{user.id}"] = amount
    
    payment_text = f"""
✅ *Selected: ${amount}*

Now choose your payment method:

1. **💳 PayPal** - *Automatic verification*
   • Pay with card or PayPal
   • Instant confirmation
   • Most secure option

2. **☕ Buy Me Coffee** - *Manual verification*
   • Simple one-click donation
   • Send transaction ID after payment

*Note:* PayPal recommended for instant verification!
"""
    
    # Create PayPal order first
    order_id, approval_url = create_paypal_order(amount, user.id)
    
    if order_id and approval_url:
        # Save PayPal order to database
        donation_db.add_paypal_order(order_id, user.id, amount)
        
        # Store order ID in user data
        context.user_data[f"paypal_order_{user.id}"] = order_id
        
        # Payment buttons
        keyboard = [
            [InlineKeyboardButton("💳 PayPal (Auto-verify)", url=approval_url)],
            [InlineKeyboardButton("☕ Buy Me Coffee (Manual)", url='https://www.buymeacoffee.com/StarAI')],
            [InlineKeyboardButton("✅ Check PayPal Payment", callback_data='check_paypal'),
             InlineKeyboardButton("✅ I've Paid BMC", callback_data='i_donated')],
            [InlineKeyboardButton("🔙 Change Amount", callback_data='donate')]
        ]
    else:
        # If PayPal fails, show manual option only
        payment_text += f"\n⚠️ *PayPal temporarily unavailable*\nPlease use Buy Me Coffee or try again later."
        
        keyboard = [
            [InlineKeyboardButton("☕ Buy Me Coffee", url='https://www.buymeacoffee.com/StarAI')],
            [InlineKeyboardButton("✅ I've Paid", callback_data='i_donated'),
             InlineKeyboardButton("🔙 Change Amount", callback_data='donate')]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(payment_text, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=True)

# ========================
# BUTTON HANDLERS (UPDATED WITH PAYMENT BUTTONS)
# ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    logger.info(f"Button pressed: {query.data}")
    
    # Image and Music buttons
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
        joke = random.choice(JOKES)
        await query.edit_message_text(f"😂 *Joke of the Day:*\n\n{joke}", parse_mode="Markdown")
    elif query.data == 'get_fact':
        fact = random.choice(FACTS)
        await query.edit_message_text(f"💡 *Did You Know?*\n\n{fact}", parse_mode="Markdown")
    elif query.data == 'get_quote':
        quote = random.choice(QUOTES)
        await query.edit_message_text(f"📜 *Inspirational Quote:*\n\n{quote}", parse_mode="Markdown")
    
    # Donation amount selection buttons
    elif query.data.startswith('donate_'):
        if query.data == 'donate_custom':
            context.user_data[f"waiting_custom_{query.from_user.id}"] = True
            await query.edit_message_text(
                "💰 *CUSTOM DONATION AMOUNT*\n\n"
                "Please enter the amount you want to donate (in USD):\n\n"
                "*Examples:*\n"
                "• `7.50` (for $7.50)\n"
                "• `15` (for $15)\n"
                "• `25` (for $25)\n\n"
                "Enter amount:",
                parse_mode="Markdown"
            )
        else:
            # Extract amount from button (donate_3, donate_5, etc.)
            amount = int(query.data.split('_')[1])
            await show_payment_options(update, context, amount)
    
    # Donation menu button
    elif query.data == 'donate':
        await donate_command(update, context)
    
    # NEW: Check PayPal payment button
    elif query.data == 'check_paypal':
        user = query.from_user
        order_id = context.user_data.get(f"paypal_order_{user.id}")
        
        if not order_id:
            await query.edit_message_text(
                "❌ *No PayPal order found.*\n\n"
                "Please start a new donation or use Buy Me Coffee.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 New Donation", callback_data='donate')]
                ])
            )
            return
        
        # Check PayPal order status
        await query.edit_message_text(
            "🔍 *Checking PayPal payment...*\n\nPlease wait...",
            parse_mode="Markdown"
        )
        
        success, result = capture_paypal_order(order_id)
        
        if success:
            # Payment captured successfully
            transaction_id = result.get('transaction_id')
            amount = result.get('amount', 0)
            
            # Save to database
            donation_db.update_paypal_order(order_id, 'captured')
            
            # Record donation with automatic verification
            donation_db.add_donation(
                user_id=user.id,
                username=user.username or "No username",
                first_name=user.first_name,
                amount=amount,
                transaction_id=transaction_id,
                payment_method="paypal"
            )
            
            # Auto-verify PayPal payments
            donation_db.verify_donation(transaction_id)
            
            response = f"""
✅ *PAYPAL PAYMENT CONFIRMED!*

*Amount:* ${amount:.2f}
*Transaction ID:* `{transaction_id}`
*Payment Method:* PayPal
*Status:* ✅ **Automatically Verified**

*Thank you for supporting StarAI!* 💝

You now have supporter status! 🎖️
"""
            
            # Clear stored data
            context.user_data.pop(f"selected_amount_{user.id}", None)
            context.user_data.pop(f"paypal_order_{user.id}", None)
            
        else:
            # Payment not completed yet
            response = f"""
⏳ *PAYPAL PAYMENT PENDING*

Your PayPal order is still being processed.

*Order ID:* `{order_id}`

*What to do:*
1. Complete payment on PayPal page
2. Return here and click "Check PayPal Payment" again
3. Or wait a few minutes and try again

*If payment is complete on PayPal but not verifying here, contact admin.*
"""
        
        keyboard = [
            [InlineKeyboardButton("🔄 Check Again", callback_data='check_paypal'),
             InlineKeyboardButton("🏠 Back to Menu", callback_data='back_to_menu')]
        ]
        
        await query.edit_message_text(
            response,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # Payment confirmation button (for BMC)
    elif query.data == 'i_donated':
        user = query.from_user
        
        # Check if amount is selected
        selected_amount = context.user_data.get(f"selected_amount_{user.id}", 0)
        
        if selected_amount == 0:
            # No amount selected, ask to choose first
            await query.edit_message_text(
                "❌ *No Amount Selected*\n\n"
                "Please select a donation amount first!\n\n"
                "Click 🔙 Back to choose an amount.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Donate", callback_data='donate')]
                ])
            )
            return
        
        context.user_data[f"waiting_proof_{user.id}"] = True
        
        await query.edit_message_text(
            f"✅ *PAYMENT CONFIRMATION (Buy Me Coffee)*\n\n"
            f"*Selected Amount:* ${selected_amount:.2f}\n\n"
            "Please send your **Transaction ID** or **Payment Reference**:\n\n"
            "*Format:* `BMC-ABC123` or copy from Buy Me Coffee email\n\n"
            "*How to find:*\n"
            "• Check your Buy Me Coffee supporter list\n"
            "• Look in your confirmation email\n"
            "• Or send a screenshot of your payment\n\n"
            "*Note:* Manual verification may take some time.\n"
            "Thank you! 🙏",
            parse_mode="Markdown"
        )
    
    # My Donations button
    elif query.data == 'my_donations':
        await mydonations_command(update, context)
    
    # Back to menu button
    elif query.data == 'back_to_menu':
        await start(update, context)
    
    # Chat button
    elif query.data == 'chat':
        await query.edit_message_text(
            "💬 *Let's Chat!*\n\n"
            "I'm here to talk about anything! 😊\n\n"
            "*Just type your message and I'll respond naturally!* 🎭",
            parse_mode="Markdown"
        )
    
    # Help button
    elif query.data == 'help':
        await query.edit_message_text(
            "🆘 *STARAI HELP CENTER*\n\n"
            "🎨 **MEDIA COMMANDS:**\n"
            "`/image <description>` - Generate AI image\n"
            "`/music <song/artist>` - Find music links\n\n"
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
            "*Just talk to me naturally!* 😊",
            parse_mode="Markdown"
        )
    
    else:
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
# MESSAGE HANDLER
# ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        user_message = update.message.text
        
        logger.info(f"User {user.id}: {user_message[:50]}")
        
        # Check for custom amount donation
        if context.user_data.get(f"waiting_custom_{user.id}"):
            context.user_data.pop(f"waiting_custom_{user.id}", None)
            
            try:
                amount = float(user_message)
                if amount < 1:
                    await update.message.reply_text("❌ Minimum donation is $1. Please enter a valid amount.")
                    return
                
                # Show payment options for custom amount
                await show_payment_options(update, context, amount)
                return
                
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please enter a number (like 5 or 10.50).")
                return
        
        # Check for Buy Me Coffee payment proof (MANUAL VERIFICATION)
        if context.user_data.get(f"waiting_proof_{user.id}"):
            context.user_data.pop(f"waiting_proof_{user.id}", None)
            
            transaction_id = user_message.strip()
            
            # Get selected amount
            amount = context.user_data.get(f"selected_amount_{user.id}", 0)
            
            if amount == 0:
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
            
            # Save donation (MANUAL - NOT AUTO-VERIFIED)
            success, message = donation_db.add_donation(
                user_id=user.id,
                username=user.username or "No username",
                first_name=user.first_name,
                amount=amount,
                transaction_id=transaction_id,
                payment_method="buymeacoffee"  # Mark as BMC
            )
            
            if success:
                response = f"""
✅ *DONATION RECORDED!*

*Amount:* ${amount:.2f}
*Transaction ID:* `{transaction_id}`
*Payment Method:* Buy Me Coffee
*Date:* {datetime.now().strftime('%Y-%m-%d %H:%M')}

*Status:* ⏳ **Pending Manual Verification**

*What's next:*
1. Your donation is now recorded
2. Admin will verify it manually
3. You'll get supporter status once verified

*Thank you for supporting StarAI!* 💝

Use `/mydonations` to check your status.
"""
                # Clear selected amount
                context.user_data.pop(f"selected_amount_{user.id}", None)
            else:
                response = f"❌ {message}"
            
            await update.message.reply_text(response, parse_mode="Markdown")
            return
        
        # Check for amount input (if transaction ID was sent first)
        if context.user_data.get(f"waiting_amount_{user.id}"):
            transaction_id = context.user_data.pop(f"waiting_amount_{user.id}")
            
            try:
                amount = float(user_message)
                success, message = donation_db.add_donation(
                    user_id=user.id,
                    username=user.username or "No username",
                    first_name=user.first_name,
                    amount=amount,
                    transaction_id=transaction_id,
                    payment_method="buymeacoffee"
                )
                
                if success:
                    response = f"""
✅ *DONATION RECORDED!*

*Amount:* ${amount:.2f}
*Transaction ID:* `{transaction_id}`
*Payment Method:* Buy Me Coffee
*Date:* {datetime.now().strftime('%Y-%m-%d %H:%M')}

*Status:* ⏳ **Pending Manual Verification**

*Thank you for supporting StarAI!* 💝
"""
                else:
                    response = f"❌ {message}"
                
            except ValueError:
                response = "❌ Invalid amount. Please enter a number (like 5 or 10.50)."
            
            await update.message.reply_text(response, parse_mode="Markdown")
            return
        
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # Image requests
        image_keywords = ["create image", "generate image", "draw", "paint", "picture of", "image of"]
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
                        await update.message.reply_photo(photo=photo, caption=f"✨ *Generated:* `{prompt}`\n*By StarAI* 🎨", parse_mode="Markdown")
                    try:
                        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
                    except:
                        pass
                except Exception as e:
                    logger.error(f"Error sending image: {e}")
                    await msg.edit_text("❌ Couldn't send image. Try `/image` command.")
                finally:
                    try:
                        if os.path.exists(image_path):
                            os.unlink(image_path)
                    except:
                        pass
            else:
                await msg.edit_text("❌ Image creation failed. Try: `/image <description>`")
            return
        
        # Music requests
        music_keywords = ["play music", "find song", "music by", "listen to", "song by"]
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
        
        # Fun commands
        if "joke" in user_message.lower() and ("tell" in user_message.lower() or "give" in user_message.lower()):
            await joke_command(update, context)
            return
        
        if "fact" in user_message.lower():
            await fact_command(update, context)
            return
        
        if "quote" in user_message.lower():
            await quote_command(update, context)
            return
        
        # AI response
        ai_response = generate_ai_response(user.id, user_message)
        await update.message.reply_text(ai_response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text(
            "❌ *Error occurred.*\n\nTry:\n• `/help` for commands\n• Rephrase your message",
            parse_mode="Markdown"
        )

# ========================
# AI RESPONSE GENERATOR
# ========================
def generate_ai_response(user_id, user_message):
    try:
        if not client:
            return """🤖 *AI Chat Currently Unavailable*

I can still help you with:
🎨 `/image <description>` - Create images
🎵 `/music <song>` - Find music
😂 `/joke` - Get a laugh
💡 `/fact` - Learn something new
💰 `/donate` - Support this bot

*Get AI Chat:* Add `GROQ_API_KEY` to Heroku Config Vars"""
        
        conversation = get_user_conversation(user_id)
        conversation.append({"role": "user", "content": user_message})
        
        response = client.chat.completions.create(
            messages=conversation,
            model="llama-3.1-8b-instant",
            temperature=0.8,
            max_tokens=600
        )
        
        ai_response = response.choices[0].message.content
        update_conversation(user_id, "assistant", ai_response)
        return ai_response
        
    except Exception as e:
        logger.error(f"AI error: {e}")
        return get_fallback_response(user_message)

def get_fallback_response(user_message):
    user_lower = user_message.lower()
    
    greetings = {
        "hi": "👋 Hello! I'm StarAI! How can I help you today? 😊",
        "hello": "🌟 Hello there! Great to meet you! What would you like to chat about?",
        "hey": "😄 Hey! I'm here and ready to help! Ask me anything!",
        "how are you": "✨ I'm doing great, thanks for asking! Ready to assist you. How about you?",
    }
    
    for key, response in greetings.items():
        if key in user_lower:
            return response
    
    if "your name" in user_lower:
        return "🤖 I'm StarAI! Your friendly AI companion! 😊"
    
    return """✨ I'd love to help! You can:

🎨 *Create images:* `/image sunset over mountains`
🎵 *Find music:* `/music Taylor Swift`
💬 *Chat naturally:* Just talk to me!
🎭 *Have fun:* `/joke`, `/fact`, `/quote`

*Need help?* Try `/help` for all commands! 😊"""

# ========================
# ADMIN COMMANDS
# ========================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin_ids = [admin_id.strip() for admin_id in ADMIN_IDS if admin_id.strip()]
    
    if str(user.id) not in admin_ids and admin_ids:
        await update.message.reply_text("❌ Admin only.", parse_mode="Markdown")
        return
    
    args = context.args
    if not args:
        help_text = """
🔧 *ADMIN COMMANDS*

`/admin stats` - Donation statistics
`/admin pending` - Pending donations
`/admin verify <txid>` - Verify a MANUAL donation
`/admin paypal` - PayPal order status
`/admin users` - List supporters
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return
    
    cmd = args[0].lower()
    
    if cmd == "stats":
        stats = donation_db.get_stats()
        
        # Get PayPal stats
        conn = sqlite3.connect(donation_db.db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), SUM(amount) FROM paypal_orders WHERE status = 'captured'")
        paypal_result = cursor.fetchone()
        paypal_count = paypal_result[0] or 0
        paypal_total = paypal_result[1] or 0
        conn.close()
        
        response = f"""
📊 *ADMIN STATS*

*Total Verified:* ${stats['total_verified']:.2f}
*Total Pending:* ${stats['total_pending']:.2f}
*Total Supporters:* {stats['supporters']}

*PayPal Stats:*
• Successful payments: {paypal_count}
• PayPal total: ${paypal_total:.2f}
"""
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "paypal":
        conn = sqlite3.connect(donation_db.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM paypal_orders ORDER BY created_at DESC LIMIT 10')
        orders = cursor.fetchall()
        conn.close()
        
        if not orders:
            await update.message.reply_text("✅ No PayPal orders.", parse_mode="Markdown")
            return
        
        response = "💳 *RECENT PAYPAL ORDERS*\n\n"
        for order in orders:
            response += f"• Order: `{order[0]}`\n"
            response += f"  User: {order[1]}, Amount: ${order[2]}\n"
            response += f"  Status: {order[3]}, Date: {order[4][:16]}\n\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "pending":
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
            response += f"   Method: {donation[7]}\n"
            response += f"   Date: {donation[8][:16]}\n\n"
        
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

# ========================
# ENVIRONMENT CHECK
# ========================
def check_environment():
    """Check if all required environment variables are set"""
    print("=" * 50)
    print("🌟 STARAI - PAYMENT SYSTEM CHECK")
    print("=" * 50)
    
    required = ['TELEGRAM_TOKEN']
    missing = []
    
    for var in required:
        if not os.environ.get(var):
            missing.append(var)
    
    if missing:
        print(f"❌ MISSING: {', '.join(missing)}")
        print("Set in Heroku: Settings → Config Vars")
        return False
    
    print("✅ Telegram Bot: Ready")
    
    # Check PayPal
    if PAYPAL_CLIENT_ID and PAYPAL_SECRET:
        print("✅ PayPal: Automatic payments ENABLED")
        print(f"   Environment: {PAYPAL_ENVIRONMENT}")
        if PAYPAL_WEBHOOK_ID:
            print("✅ PayPal Webhook: Configured")
        else:
            print("⚠️  PayPal Webhook: Not configured (optional)")
    else:
        print("⚠️  PayPal: Manual mode only")
        print("   Set PAYPAL_CLIENT_ID and PAYPAL_SECRET for auto-verify")
    
    print("☕ Buy Me Coffee: Manual verification")
    print("=" * 50)
    return True

# ========================
# MAIN FUNCTION
# ========================
def main():
    if not check_environment():
        return
    
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        
        commands = [
            ("start", start),
            ("help", help_command),
            ("image", image_command),
            ("music", music_command),
            ("joke", joke_command),
            ("fact", fact_command),
            ("quote", quote_command),
            ("clear", clear_command),
            ("donate", donate_command),
            ("mydonations", mydonations_command),
            ("admin", admin_command),
        ]
        
        for command, handler in commands:
            app.add_handler(CommandHandler(command, handler))
        
        app.add_handler(CallbackQueryHandler(button_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ StarAI is running with PayPal Auto-Verify!")
        print("💰 Users can choose:")
        print("   • PayPal (Automatic verification)")
        print("   • Buy Me Coffee (Manual verification)")
        print("🔧 Send /start to begin")
        print("=" * 50)
        
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Failed to start: {e}")

if __name__ == '__main__':
    main()
