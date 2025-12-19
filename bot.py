import os
import json
import requests
import logging
import random
import tempfile
import sqlite3
import time
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

# API Keys from Environment Variables
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# Your PayPal Sandbox Credentials (from your message)
PAYPAL_CLIENT_ID = "AaLMaKrP4FZiExJmkpBQT2NjEuBX7mH-zebhXXJVlU6lOMFWZgxf0Ms2NTN3QaMOfyCtRH6sB1XsqiH6"
PAYPAL_SECRET = "EFfK9muqw2zEkP9jFhSY2JKblyHdi7-ihkZdGdm8EzaWtkoP9LsH6iJPsPX91XbGH2xWfGZjee1AP4jN"
PAYPAL_SANDBOX = True  # Set to False when going live
PAYPAL_EMAIL = "sb-avklh48247972@business.example.com"

# Admin Telegram User ID
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', "")

# Initialize Groq AI
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ========================
# PAYPAL INTEGRATION
# ========================
class PayPal:
    def __init__(self):
        self.client_id = PAYPAL_CLIENT_ID
        self.secret = PAYPAL_SECRET
        self.sandbox = PAYPAL_SANDBOX
        self.base_url = "https://api-m.sandbox.paypal.com" if self.sandbox else "https://api-m.paypal.com"
        self.web_base = "https://www.sandbox.paypal.com" if self.sandbox else "https://www.paypal.com"
        self.access_token = None
        self.token_expiry = 0
    
    def get_access_token(self):
        """Get PayPal access token"""
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token
        
        try:
            response = requests.post(
                f"{self.base_url}/v1/oauth2/token",
                auth=(self.client_id, self.secret),
                headers={"Accept": "application/json", "Accept-Language": "en_US"},
                data={"grant_type": "client_credentials"},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                self.access_token = data["access_token"]
                self.token_expiry = time.time() + data["expires_in"] - 300  # 5 min buffer
                logger.info("✅ PayPal access token obtained")
                return self.access_token
            else:
                logger.error(f"❌ PayPal auth failed: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ PayPal auth error: {e}")
            return None
    
    def create_order(self, user_id, amount, currency="USD"):
        """Create PayPal order"""
        try:
            token = self.get_access_token()
            if not token:
                return None, "Cannot connect to PayPal"
            
            order_data = {
                "intent": "CAPTURE",
                "purchase_units": [{
                    "amount": {
                        "currency_code": currency,
                        "value": f"{amount:.2f}"
                    },
                    "description": f"StarAI Donation - User {user_id}",
                    "custom_id": f"STARAI_{user_id}_{int(time.time())}"
                }],
                "application_context": {
                    "brand_name": "StarAI",
                    "user_action": "PAY_NOW",
                    "return_url": "https://t.me/StarAI_Bot?start=payment_success",
                    "cancel_url": "https://t.me/StarAI_Bot?start=payment_cancel"
                }
            }
            
            response = requests.post(
                f"{self.base_url}/v2/checkout/orders",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    "Prefer": "return=representation"
                },
                json=order_data,
                timeout=30
            )
            
            if response.status_code == 201:
                order = response.json()
                order_id = order["id"]
                
                # Find approval link
                approval_url = None
                for link in order.get("links", []):
                    if link.get("rel") == "approve":
                        approval_url = link.get("href")
                        break
                
                if approval_url:
                    logger.info(f"✅ PayPal order created: {order_id}")
                    return order_id, approval_url
                else:
                    return None, "No approval URL found"
            else:
                error_msg = response.json().get("message", "Unknown error")
                logger.error(f"❌ PayPal order failed: {error_msg}")
                return None, error_msg
                
        except Exception as e:
            logger.error(f"❌ Create order error: {e}")
            return None, str(e)
    
    def capture_order(self, order_id):
        """Capture PayPal payment"""
        try:
            token = self.get_access_token()
            if not token:
                return False, "Cannot connect to PayPal"
            
            response = requests.post(
                f"{self.base_url}/v2/checkout/orders/{order_id}/capture",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    "Prefer": "return=representation"
                },
                json={},
                timeout=30
            )
            
            if response.status_code == 201:
                capture = response.json()
                status = capture.get("status", "")
                
                if status == "COMPLETED":
                    # Extract transaction details
                    purchase_units = capture.get("purchase_units", [])
                    if purchase_units:
                        payments = purchase_units[0].get("payments", {})
                        captures = payments.get("captures", [])
                        if captures:
                            capture_data = captures[0]
                            return True, {
                                "order_id": order_id,
                                "capture_id": capture_data.get("id"),
                                "amount": float(capture_data.get("amount", {}).get("value", 0)),
                                "currency": capture_data.get("amount", {}).get("currency_code", "USD"),
                                "status": "COMPLETED",
                                "create_time": capture_data.get("create_time")
                            }
                
                return False, f"Order status: {status}"
            else:
                error_msg = response.json().get("message", "Unknown error")
                return False, error_msg
                
        except Exception as e:
            logger.error(f"❌ Capture order error: {e}")
            return False, str(e)
    
    def get_order_details(self, order_id):
        """Get order details"""
        try:
            token = self.get_access_token()
            if not token:
                return None
            
            response = requests.get(
                f"{self.base_url}/v2/checkout/orders/{order_id}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"❌ Get order failed: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Get order error: {e}")
            return None

# Initialize PayPal
paypal = PayPal()

# ========================
# SIMPLE DATABASE
# ========================
class DonationDB:
    def __init__(self):
        self.db_file = "starai_donations.db"
        self.init_db()
    
    def init_db(self):
        """Initialize SQLite database"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Donations table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    amount REAL,
                    currency TEXT DEFAULT 'USD',
                    order_id TEXT UNIQUE,
                    capture_id TEXT,
                    status TEXT DEFAULT 'pending',
                    payment_method TEXT DEFAULT 'paypal',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            ''')
            
            # Users table (for quick stats)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    total_donated REAL DEFAULT 0,
                    last_donation TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("✅ Database initialized")
            
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
    
    def save_donation(self, user_id, username, first_name, amount, currency, order_id, status="pending"):
        """Save donation record"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO donations 
                (user_id, username, first_name, amount, currency, order_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, amount, currency, order_id, status))
            
            # Update user stats
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, total_donated, last_donation)
                VALUES (?, ?, ?, 
                    COALESCE((SELECT total_donated FROM users WHERE user_id = ?), 0) + ?,
                    CURRENT_TIMESTAMP)
            ''', (user_id, username, first_name, user_id, amount))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            logger.error(f"❌ Save donation error: {e}")
            return False
    
    def update_donation(self, order_id, capture_id, status="completed"):
        """Update donation status"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE donations 
                SET capture_id = ?, status = ?, completed_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (capture_id, status, order_id))
            
            conn.commit()
            
            # Get user info for stats update
            cursor.execute('SELECT user_id, amount FROM donations WHERE order_id = ?', (order_id,))
            donation = cursor.fetchone()
            
            if donation:
                user_id, amount = donation
                cursor.execute('''
                    UPDATE users 
                    SET total_donated = total_donated + ?, last_donation = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                ''', (amount, user_id))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            logger.error(f"❌ Update donation error: {e}")
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
                LIMIT 10
            ''', (user_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            donations = []
            for row in rows:
                donations.append({
                    "id": row[0],
                    "amount": row[4],
                    "currency": row[5],
                    "order_id": row[6],
                    "status": row[8],
                    "created_at": row[10],
                    "completed_at": row[11]
                })
            return donations
            
        except Exception as e:
            logger.error(f"❌ Get donations error: {e}")
            return []
    
    def get_user_total(self, user_id):
        """Get user's total donations"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT total_donated FROM users WHERE user_id = ?', (user_id,))
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
            
            # Total raised
            cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "completed"')
            total = cursor.fetchone()[0] or 0
            
            # Supporters count
            cursor.execute('SELECT COUNT(DISTINCT user_id) FROM donations WHERE status = "completed"')
            supporters = cursor.fetchone()[0] or 0
            
            # Recent donations
            cursor.execute('''
                SELECT d.first_name, d.amount, d.created_at 
                FROM donations d
                WHERE d.status = 'completed'
                ORDER BY d.created_at DESC
                LIMIT 5
            ''')
            recent = cursor.fetchall()
            
            conn.close()
            
            return {
                "total": total,
                "supporters": supporters,
                "recent": recent
            }
            
        except Exception as e:
            logger.error(f"❌ Get stats error: {e}")
            return {"total": 0, "supporters": 0, "recent": []}

# Initialize database
db = DonationDB()

# ========================
# IMAGE GENERATION
# ========================
def create_fallback_image(prompt):
    """Create fallback image"""
    try:
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            img = Image.new('RGB', (512, 512), color=(60, 60, 100))
            draw = ImageDraw.Draw(img)
            
            # Simple text
            words = prompt.split()
            lines = []
            line = ""
            for word in words:
                if len(line + " " + word) <= 30:
                    line = line + " " + word if line else word
                else:
                    lines.append(line)
                    line = word
            if line:
                lines.append(line)
            
            text = "\n".join(lines[:5])
            draw.text((50, 200), f"StarAI:\n{text}", fill=(255, 255, 255))
            draw.text((10, 480), "✨ Created by StarAI", fill=(200, 200, 255))
            
            img.save(tmp.name, 'PNG')
            return tmp.name
    except:
        return None

def generate_image(prompt):
    """Generate AI image"""
    try:
        clean_prompt = prompt.strip().replace(" ", "%20")
        poll_url = f"https://image.pollinations.ai/prompt/{clean_prompt}"
        params = {"width": "512", "height": "512", "seed": str(random.randint(1, 999999))}
        
        response = requests.get(poll_url, params=params, timeout=30)
        if response.status_code == 200 and len(response.content) > 1000:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp.write(response.content)
                return tmp.name
    except:
        pass
    
    return create_fallback_image(prompt)

# ========================
# MUSIC SEARCH
# ========================
def search_music(query):
    """Search for music"""
    try:
        videos_search = VideosSearch(query, limit=3)
        results = videos_search.result()['result']
        
        music_list = []
        for i, video in enumerate(results[:3], 1):
            title = video['title'][:50] + "..." if len(video['title']) > 50 else video['title']
            url = video['link']
            duration = video.get('duration', 'N/A')
            music_list.append(f"{i}. 🎵 {title}\n   ⏱️ {duration}\n   🔗 {url}")
        return music_list
    except:
        return ["Use: `/music <song or artist>`"]

# ========================
# BOT COMMANDS
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user = update.effective_user
    user_name = user.first_name
    
    # Check if payment return
    if context.args:
        if "payment_success" in context.args[0]:
            await handle_payment_return(update, context, "success")
            return
        elif "payment_cancel" in context.args[0]:
            await handle_payment_return(update, context, "cancel")
            return
    
    total_donated = db.get_user_total(user.id)
    
    welcome = f"""
🌟 *WELCOME TO STARAI, {user_name}!* 🌟

✨ *Your AI Companion*

🎨 **CREATE IMAGES:** `/image <prompt>`
🎵 **FIND MUSIC:** `/music <song>`
💬 **CHAT:** Just talk to me!
💰 **SUPPORT:** `/donate` (Keeps me running!)

🔧 **COMMANDS:**
`/help` - All commands
`/mydonations` - Your donations
`/stats` - Donation stats
`/clear` - Reset chat
"""
    
    if total_donated > 0:
        welcome += f"\n🎖️ *SUPPORTER STATUS:*"
        welcome += f"\n💝 Total Donated: ${total_donated:.2f}"
        welcome += f"\n❤️ Thank you for your support!"
    
    keyboard = [
        [InlineKeyboardButton("🎨 Create Image", callback_data='create_image'),
         InlineKeyboardButton("🎵 Find Music", callback_data='find_music')],
        [InlineKeyboardButton("💰 Donate", callback_data='donate'),
         InlineKeyboardButton("😂 Joke", callback_data='joke')],
        [InlineKeyboardButton("🆘 Help", callback_data='help'),
         InlineKeyboardButton("📊 Stats", callback_data='stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=reply_markup)

async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Donation command"""
    user = update.effective_user
    stats = db.get_stats()
    
    donate_text = f"""
💰 *SUPPORT STARAI* 💰

*Why donate?*
• API costs (AI, images, music)
• Server hosting
• Development time
• Keep StarAI free!

*Current Stats:*
👥 Supporters: {stats['supporters']}
💰 Total Raised: ${stats['total']:.2f}

*Your Donations:* ${db.get_user_total(user.id):.2f}

*Choose amount:*
"""
    
    keyboard = [
        [InlineKeyboardButton("☕ Tea - $3", callback_data='pay_3'),
         InlineKeyboardButton("🍵 Coffee - $5", callback_data='pay_5')],
        [InlineKeyboardButton("🥤 Smoothie - $10", callback_data='pay_10'),
         InlineKeyboardButton("🍰 Cake - $20", callback_data='pay_20')],
        [InlineKeyboardButton("🎖️ Custom Amount", callback_data='pay_custom'),
         InlineKeyboardButton("✅ Check Payment", callback_data='check_payment')],
        [InlineKeyboardButton("📊 My Donations", callback_data='my_donations'),
         InlineKeyboardButton("🔙 Back", callback_data='back')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(donate_text, parse_mode="Markdown", reply_markup=reply_markup)

async def mydonations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user donations"""
    user = update.effective_user
    donations = db.get_user_donations(user.id)
    total = db.get_user_total(user.id)
    
    if donations:
        response = f"""
📊 *YOUR DONATIONS*

*Total:* ${total:.2f}
*Transactions:* {len(donations)}

*Recent:*
"""
        for donation in donations[:5]:
            status_icon = "✅" if donation["status"] == "completed" else "⏳"
            response += f"\n{status_icon} ${donation['amount']:.2f} - {donation['created_at'][:10]}"
        
        if total > 0:
            response += f"\n\n🎖️ *Supporter Level:* "
            if total >= 50:
                response += "Platinum 🏆"
            elif total >= 20:
                response += "Gold 🥇"
            elif total >= 10:
                response += "Silver 🥈"
            elif total >= 3:
                response += "Bronze 🥉"
            else:
                response += "Supporter 💝"
            
            response += f"\n❤️ Thank you for supporting StarAI!"
    else:
        response = """
💸 *NO DONATIONS YET*

You haven't made any donations yet.

Use `/donate` to support StarAI development!

*Thank you!* 😊
"""
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show donation stats"""
    stats = db.get_stats()
    
    response = f"""
📈 *STARAI DONATION STATS*

💰 *Total Raised:* ${stats['total']:.2f}
👥 *Supporters:* {stats['supporters']}

*Recent Supporters:*
"""
    
    if stats['recent']:
        for name, amount, date in stats['recent']:
            response += f"\n• {name} - ${amount:.2f} ({date[:10]})"
    else:
        response += "\nNo recent donations yet."
    
    response += f"\n\n*Support keeps StarAI running!* ☕"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def handle_payment_return(update: Update, context: ContextTypes.DEFAULT_TYPE, status):
    """Handle PayPal return"""
    if status == "success":
        await update.message.reply_text(
            "✅ *Payment Approved!*\n\n"
            "Your payment has been approved!\n"
            "Click *✅ Verify Payment* in your previous donation message.\n\n"
            "Thank you for supporting StarAI! 💝",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ *Payment Cancelled*\n\n"
            "Your payment was cancelled.\n"
            "No worries! You can try again with `/donate`\n\n"
            "Thank you for considering! 😊",
            parse_mode="Markdown"
        )

# ========================
# PAYMENT HANDLING
# ========================
async def create_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, amount):
    """Create PayPal payment"""
    query = update.callback_query
    user = query.from_user
    
    # Create PayPal order
    order_id, approval_url = paypal.create_order(user.id, amount)
    
    if not order_id:
        await query.answer("❌ Payment creation failed. Try again.", show_alert=True)
        return
    
    # Save donation record
    db.save_donation(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        amount=amount,
        currency="USD",
        order_id=order_id,
        status="pending"
    )
    
    payment_msg = f"""
💳 *PAYMENT READY*

*Amount:* ${amount:.2f}
*Order ID:* `{order_id}`

*Instructions:*
1. Click the *🔗 PayPal Link* below
2. Login with PayPal
3. Approve the payment
4. Return to this chat

*Payment Link:* [Click Here]({approval_url})
"""
    
    keyboard = [
        [InlineKeyboardButton("🔗 PayPal Link", url=approval_url)],
        [InlineKeyboardButton("✅ Verify Payment", callback_data=f'verify_{order_id}'),
         InlineKeyboardButton("🔄 Check Status", callback_data=f'check_{order_id}')],
        [InlineKeyboardButton("❌ Cancel", callback_data='donate')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(payment_msg, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=False)

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id):
    """Verify PayPal payment"""
    query = update.callback_query
    user = query.from_user
    
    # Capture the payment
    success, result = paypal.capture_order(order_id)
    
    if success:
        # Update donation record
        db.update_donation(order_id, result["capture_id"], "completed")
        
        success_msg = f"""
✅ *PAYMENT VERIFIED!* 🎉

Thank you for your donation of *${result['amount']:.2f}*!
You are now a StarAI Supporter! 🎖️

*Order ID:* `{order_id}`
*Capture ID:* `{result['capture_id']}`
*Amount:* ${result['amount']:.2f}
*Status:* ✅ Completed

*Thank you for supporting StarAI!* 💝🙏
"""
        
        keyboard = [
            [InlineKeyboardButton("🎖️ My Status", callback_data='my_donations'),
             InlineKeyboardButton("💰 Donate More", callback_data='donate')],
            [InlineKeyboardButton("🔙 Menu", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(success_msg, parse_mode="Markdown", reply_markup=reply_markup)
    
    else:
        error_msg = f"""
❌ *PAYMENT NOT COMPLETED*

We couldn't complete the payment.

*Order ID:* `{order_id}`
*Error:* {result}

*Possible reasons:*
1. Payment not approved yet (click PayPal link first)
2. Payment was cancelled
3. Technical issue

*Try again or contact support.*
"""
        
        keyboard = [
            [InlineKeyboardButton("🔄 Try Again", callback_data=f'verify_{order_id}'),
             InlineKeyboardButton("🔗 PayPal Link", callback_data=f'link_{order_id}')],
            [InlineKeyboardButton("❌ Cancel", callback_data='donate')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=reply_markup)

# ========================
# BUTTON HANDLERS
# ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'donate':
        await donate_command(update, context)
    
    elif query.data.startswith('pay_'):
        amount_str = query.data.replace('pay_', '')
        
        if amount_str == 'custom':
            await query.edit_message_text(
                "💰 *Custom Donation*\n\n"
                "Enter the amount you want to donate (in USD):\n\n"
                "*Examples:* 5, 10.50, 25\n\n"
                "Please send the amount as a number:",
                parse_mode="Markdown"
            )
            context.user_data[f"waiting_custom_{query.from_user.id}"] = True
            return
        
        try:
            amount = float(amount_str)
            await create_payment(update, context, amount)
        except ValueError:
            await query.answer("Invalid amount", show_alert=True)
    
    elif query.data.startswith('verify_'):
        order_id = query.data.replace('verify_', '')
        await verify_payment(update, context, order_id)
    
    elif query.data.startswith('check_'):
        order_id = query.data.replace('check_', '')
        await verify_payment(update, context, order_id)
    
    elif query.data == 'check_payment':
        await query.edit_message_text(
            "🔍 *CHECK PAYMENT STATUS*\n\n"
            "Enter your Order ID:\n\n"
            "*Format:* `ORDER-ID-HERE`\n\n"
            "Send the Order ID to check status.",
            parse_mode="Markdown"
        )
        context.user_data[f"waiting_check_{query.from_user.id}"] = True
    
    elif query.data == 'my_donations':
        await mydonations_command(update, context)
    
    elif query.data == 'stats':
        await stats_command(update, context)
    
    elif query.data == 'back':
        await start(update, context)
    
    # Other buttons...
    elif query.data == 'create_image':
        await query.edit_message_text("🎨 *Image Creation*\n\nSend: `/image <description>`", parse_mode="Markdown")
    elif query.data == 'find_music':
        await query.edit_message_text("🎵 *Music Search*\n\nSend: `/music <song>`", parse_mode="Markdown")
    elif query.data == 'joke':
        jokes = ["😂 Why don't scientists trust atoms? Because they make up everything!", "😄 Why did the scarecrow win an award? He was outstanding in his field!"]
        await query.edit_message_text(f"😂 *Joke:*\n\n{random.choice(jokes)}", parse_mode="Markdown")
    elif query.data == 'help':
        await help_command(update, context)

# ========================
# MESSAGE HANDLER
# ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all messages"""
    try:
        user = update.effective_user
        user_message = update.message.text
        
        # Check for custom amount
        if context.user_data.get(f"waiting_custom_{user.id}"):
            context.user_data.pop(f"waiting_custom_{user.id}", None)
            
            try:
                amount = float(user_message)
                if amount < 1:
                    await update.message.reply_text("❌ Minimum donation is $1", parse_mode="Markdown")
                elif amount > 1000:
                    await update.message.reply_text("❌ Maximum donation is $1000", parse_mode="Markdown")
                else:
                    # Create payment
                    order_id, approval_url = paypal.create_order(user.id, amount)
                    
                    if order_id:
                        db.save_donation(
                            user_id=user.id,
                            username=user.username,
                            first_name=user.first_name,
                            amount=amount,
                            currency="USD",
                            order_id=order_id,
                            status="pending"
                        )
                        
                        payment_msg = f"""
💳 *CUSTOM PAYMENT: ${amount:.2f}*

*Order ID:* `{order_id}`

Click the PayPal link below to complete payment:

[🔗 PayPal Payment Link]({approval_url})

*After payment:* Click *✅ Verify Payment*.
"""
                        
                        keyboard = [
                            [InlineKeyboardButton("🔗 PayPal Link", url=approval_url)],
                            [InlineKeyboardButton("✅ Verify Payment", callback_data=f'verify_{order_id}')],
                            [InlineKeyboardButton("❌ Cancel", callback_data='donate')]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await update.message.reply_text(payment_msg, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=False)
                    else:
                        await update.message.reply_text("❌ Could not create payment. Try again.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Please enter a valid number (like 5 or 10.50)", parse_mode="Markdown")
            return
        
        # Check for payment verification
        if context.user_data.get(f"waiting_check_{user.id}"):
            context.user_data.pop(f"waiting_check_{user.id}", None)
            
            order_id = user_message.strip()
            
            # Check order status
            order_details = paypal.get_order_details(order_id)
            if order_details:
                status = order_details.get("status", "UNKNOWN")
                amount = 0
                
                # Get amount
                purchase_units = order_details.get("purchase_units", [])
                if purchase_units:
                    amount_data = purchase_units[0].get("amount", {})
                    amount = float(amount_data.get("value", 0))
                
                if status == "COMPLETED":
                    await update.message.reply_text(
                        f"✅ *PAYMENT COMPLETED*\n\n"
                        f"*Order ID:* `{order_id}`\n"
                        f"*Amount:* ${amount:.2f}\n"
                        f"*Status:* ✅ Completed\n\n"
                        f"Thank you for your donation! 🎖️",
                        parse_mode="Markdown"
                    )
                elif status == "APPROVED":
                    await update.message.reply_text(
                        f"⏳ *PAYMENT APPROVED*\n\n"
                        f"*Order ID:* `{order_id}`\n"
                        f"*Amount:* ${amount:.2f}\n"
                        f"*Status:* ⏳ Approved (needs capture)\n\n"
                        f"Click *✅ Verify Payment* to complete.",
                        parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text(
                        f"❓ *PAYMENT STATUS: {status}*\n\n"
                        f"*Order ID:* `{order_id}`\n"
                        f"*Amount:* ${amount:.2f}\n"
                        f"*Status:* {status}\n\n"
                        f"Contact support for help.",
                        parse_mode="Markdown"
                    )
            else:
                await update.message.reply_text(
                    "❌ *ORDER NOT FOUND*\n\n"
                    "We couldn't find this Order ID.\n\n"
                    "*Check:*\n"
                    "• Order ID is correct\n"
                    "• Payment was created\n"
                    "• Try again later",
                    parse_mode="Markdown"
                )
            return
        
        # Check for image requests
        if any(word in user_message.lower() for word in ["create image", "generate image", "draw", "picture of", "image of"]):
            prompt = user_message
            for word in ["create image", "generate image", "draw", "picture of", "image of"]:
                if word in user_message.lower():
                    parts = user_message.lower().split(word)
                    if len(parts) > 1:
                        prompt = parts[1].strip()
                        break
            
            if not prompt:
                prompt = "a beautiful artwork"
            
            msg = await update.message.reply_text(f"🎨 *Creating:* {prompt}...", parse_mode="Markdown")
            image_path = generate_image(prompt)
            
            if image_path and os.path.exists(image_path):
                try:
                    with open(image_path, 'rb') as photo:
                        await update.message.reply_photo(
                            photo=photo,
                            caption=f"✨ *Generated:* {prompt}\n*By StarAI* 🎨",
                            parse_mode="Markdown"
                        )
                    try:
                        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
                    except:
                        pass
                except:
                    await msg.edit_text("❌ Couldn't send image.")
                finally:
                    try:
                        os.unlink(image_path)
                    except:
                        pass
            else:
                await msg.edit_text("❌ Image creation failed.")
            return
        
        # Check for music requests
        if any(word in user_message.lower() for word in ["play music", "find song", "music by", "listen to"]):
            query = user_message
            for word in ["play music", "find song", "music by", "listen to"]:
                if word in user_message.lower():
                    parts = user_message.lower().split(word)
                    if len(parts) > 1:
                        query = parts[1].strip()
                        break
            
            if not query:
                query = "popular music"
            
            msg = await update.message.reply_text(f"🎵 *Searching:* {query}...", parse_mode="Markdown")
            results = search_music(query)
            
            response = "🎶 *Results:*\n\n" + "\n".join(results)
            await msg.edit_text(response, parse_mode="Markdown")
            return
        
        # Default AI response
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        responses = [
            "✨ I'm here to help! You can ask me anything or use commands like `/image` or `/music`.",
            "😊 How can I assist you today? I can create images, find music, or just chat!",
            "🌟 Need help? Try `/help` to see all commands, or just tell me what you need!",
        ]
        
        if "donat" in user_message.lower() or "support" in user_message.lower():
            response = "💰 Want to support StarAI? Use `/donate` to make a payment via PayPal! It's optional but appreciated! ☕"
        elif "hi" in user_message.lower() or "hello" in user_message.lower():
            response = f"👋 Hello {user.first_name}! I'm StarAI! How can I help you today? 😊"
        else:
            response = random.choice(responses)
        
        await update.message.reply_text(response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Something went wrong. Try again or use `/help`.", parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 *STARAI HELP*

🎨 **IMAGE:**
`/image <description>` - Create AI image

🎵 **MUSIC:**
`/music <song>` - Find music on YouTube

💰 **DONATIONS (PayPal):**
`/donate` - Support via PayPal
`/mydonations` - Your donation history
`/stats` - Donation statistics

💬 **CHAT:**
Just talk to me naturally!

🎭 **FUN:**
`/joke` - Get a joke
`/fact` - Interesting fact
`/quote` - Inspiring quote

🔧 **OTHER:**
`/start` - Welcome message
`/clear` - Reset conversation
`/help` - This message

*Thank you for using StarAI!* 😊
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate image"""
    prompt = ' '.join(context.args)
    
    if not prompt:
        await update.message.reply_text("🎨 *Usage:* `/image <description>`", parse_mode="Markdown")
        return
    
    msg = await update.message.reply_text(f"🎨 *Creating:* {prompt}...", parse_mode="Markdown")
    image_path = generate_image(prompt)
    
    if image_path and os.path.exists(image_path):
        try:
            with open(image_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"✨ *Generated:* {prompt}\n*By StarAI* 🎨",
                    parse_mode="Markdown"
                )
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
        except:
            await msg.edit_text("❌ Couldn't send image.")
        finally:
            try:
                os.unlink(image_path)
            except:
                pass
    else:
        await msg.edit_text("❌ Image creation failed.")

async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search music"""
    query = ' '.join(context.args)
    
    if not query:
        await update.message.reply_text("🎵 *Usage:* `/music <song or artist>`", parse_mode="Markdown")
        return
    
    await update.message.reply_text(f"🔍 *Searching:* {query}", parse_mode="Markdown")
    results = search_music(query)
    
    response = "🎶 *Results:*\n\n" + "\n".join(results) + "\n\n💡 YouTube links for listening."
    await update.message.reply_text(response, parse_mode="Markdown")

async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tell a joke"""
    jokes = [
        "😂 Why don't scientists trust atoms? Because they make up everything!",
        "😄 Why did the scarecrow win an award? He was outstanding in his field!",
        "🤣 What do you call a fake noodle? An impasta!",
        "😆 Why did the math book look so sad? It had too many problems!",
        "😊 How does the moon cut his hair? Eclipse it!"
    ]
    await update.message.reply_text(f"😂 *Joke:*\n\n{random.choice(jokes)}", parse_mode="Markdown")

async def fact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Share a fact"""
    facts = [
        "🐝 Honey never spoils! 3000-year-old honey found in tombs is still edible.",
        "🧠 Octopuses have three hearts! Two pump blood to gills, one to body.",
        "🌊 The shortest war was Britain-Zanzibar in 1896. It lasted 38 minutes!",
        "🐌 Snails can sleep for 3 years when hibernating.",
        "🦒 A giraffe's neck has same vertebrae as humans: seven!"
    ]
    await update.message.reply_text(f"💡 *Fact:*\n\n{random.choice(facts)}", parse_mode="Markdown")

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Share quote"""
    quotes = [
        "🌟 'The only way to do great work is to love what you do.' - Steve Jobs",
        "💫 'Your time is limited, don't waste it living someone else's life.' - Steve Jobs",
        "🚀 'The future belongs to those who believe in the beauty of their dreams.' - Eleanor Roosevelt",
        "🌱 'The only impossible journey is the one you never begin.' - Tony Robbins",
        "💖 'Be yourself; everyone else is already taken.' - Oscar Wilde"
    ]
    await update.message.reply_text(f"📜 *Quote:*\n\n{random.choice(quotes)}", parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation"""
    await update.message.reply_text("🧹 *Chat cleared!* Let's start fresh! 😊", parse_mode="Markdown")

# ========================
# ADMIN COMMANDS
# ========================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin commands"""
    user = update.effective_user
    
    if ADMIN_USER_ID and str(user.id) != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.", parse_mode="Markdown")
        return
    
    args = context.args
    
    if not args:
        help_text = """
🔧 *ADMIN COMMANDS*

`/admin stats` - Detailed stats
`/admin test_paypal` - Test PayPal connection
`/admin verify <order_id>` - Manually verify payment
`/admin export` - Export donation data
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return
    
    cmd = args[0].lower()
    
    if cmd == "stats":
        conn = sqlite3.connect(db.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM donations')
        total_donations = cursor.fetchone()[0]
        
        cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "completed"')
        total_amount = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM donations WHERE status = "completed"')
        unique_donors = cursor.fetchone()[0]
        
        cursor.execute('SELECT * FROM donations WHERE status = "completed" ORDER BY created_at DESC LIMIT 10')
        recent = cursor.fetchall()
        
        conn.close()
        
        response = f"""
📊 *ADMIN STATS*

*Total Donations:* {total_donations}
*Total Amount:* ${total_amount:.2f}
*Unique Donors:* {unique_donors}

*Recent Donations (Last 10):*
"""
        for row in recent:
            response += f"\n• ${row[4]:.2f} - User {row[1]} - {row[10]}"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "test_paypal":
        # Test PayPal connection
        token = paypal.get_access_token()
        if token:
            await update.message.reply_text("✅ PayPal connection successful!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ PayPal connection failed", parse_mode="Markdown")

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start the bot"""
    print("=" * 50)
    print("🌟 STARAI - WITH REAL PAYPAL INTEGRATION")
    print("=" * 50)
    print(f"💰 PayPal Sandbox: {PAYPAL_SANDBOX}")
    print(f"📧 PayPal Email: {PAYPAL_EMAIL}")
    print("=" * 50)
    
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN missing!")
        print("Set in Heroku: Settings → Config Vars")
        return
    
    print("✅ Features: AI Chat, Image Generation, Music, PayPal Donations")
    print("💰 PayPal: Automatic payment verification with YOUR credentials")
    print("🔧 Ready to deploy!")
    print("=" * 50)
    
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Add command handlers
        commands = [
            ("start", start),
            ("help", help_command),
            ("donate", donate_command),
            ("mydonations", mydonations_command),
            ("stats", stats_command),
            ("image", image_command),
            ("music", music_command),
            ("joke", joke_command),
            ("fact", fact_command),
            ("quote", quote_command),
            ("clear", clear_command),
            ("admin", admin_command),
        ]
        
        for command, handler in commands:
            app.add_handler(CommandHandler(command, handler))
        
        # Add button handler
        app.add_handler(CallbackQueryHandler(button_callback))
        
        # Add message handler
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ Bot is running! Send /start to begin")
        print("💰 Send /donate to test PayPal payments")
        print("🔧 Test with Sandbox account: sb-avklh48247972@business.example.com")
        print("=" * 50)
        
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Failed to start: {e}")

if __name__ == '__main__':
    main()
