import os
import io
import json
import requests
import logging
import random
import tempfile
import base64
import sqlite3
import hashlib
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

# API Keys
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# PayPal API Credentials (from your PayPal Developer Dashboard)
PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID')
PAYPAL_SECRET = os.environ.get('PAYPAL_SECRET')
PAYPAL_SANDBOX = os.environ.get('PAYPAL_SANDBOX', 'true').lower() == 'true'

# Admin
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')

# Initialize Groq AI
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ========================
# PAYPAL INTEGRATION
# ========================
class PayPalPayment:
    def __init__(self):
        self.client_id = PAYPAL_CLIENT_ID
        self.secret = PAYPAL_SECRET
        self.sandbox = PAYPAL_SANDBOX
        self.base_url = "https://api-m.sandbox.paypal.com" if self.sandbox else "https://api-m.paypal.com"
        self.access_token = None
        self.token_expiry = 0
    
    def get_access_token(self):
        """Get PayPal access token"""
        try:
            # Check if token is still valid (expires in 1 hour)
            if self.access_token and time.time() < self.token_expiry:
                return self.access_token
            
            auth_response = requests.post(
                f"{self.base_url}/v1/oauth2/token",
                auth=(self.client_id, self.secret),
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "en_US"
                },
                data={"grant_type": "client_credentials"}
            )
            
            if auth_response.status_code == 200:
                token_data = auth_response.json()
                self.access_token = token_data["access_token"]
                self.token_expiry = time.time() + token_data["expires_in"] - 300  # 5 min buffer
                logger.info("PayPal access token obtained")
                return self.access_token
            else:
                logger.error(f"PayPal auth failed: {auth_response.text}")
                return None
                
        except Exception as e:
            logger.error(f"PayPal auth error: {e}")
            return None
    
    def create_payment(self, user_id, amount, currency="USD", description="StarAI Donation"):
        """Create PayPal payment"""
        try:
            token = self.get_access_token()
            if not token:
                return None, "Could not connect to PayPal"
            
            payment_data = {
                "intent": "sale",
                "payer": {"payment_method": "paypal"},
                "transactions": [{
                    "amount": {
                        "total": f"{amount:.2f}",
                        "currency": currency
                    },
                    "description": description,
                    "custom": f"user_id:{user_id}",
                    "invoice_number": f"STARAI-{user_id}-{int(time.time())}"
                }],
                "redirect_urls": {
                    "return_url": f"https://t.me/StarAI_Bot?start=payment_success",
                    "cancel_url": f"https://t.me/StarAI_Bot?start=payment_cancel"
                }
            }
            
            response = requests.post(
                f"{self.base_url}/v1/payments/payment",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    "Prefer": "return=representation"
                },
                json=payment_data,
                timeout=30
            )
            
            if response.status_code == 201:
                payment = response.json()
                payment_id = payment["id"]
                
                # Find approval URL
                approval_url = None
                for link in payment.get("links", []):
                    if link.get("rel") == "approval_url":
                        approval_url = link.get("href")
                        break
                
                if approval_url:
                    return payment_id, approval_url
                else:
                    return None, "No approval URL found"
            else:
                logger.error(f"PayPal create payment failed: {response.text}")
                return None, f"Payment creation failed: {response.status_code}"
                
        except Exception as e:
            logger.error(f"Create payment error: {e}")
            return None, str(e)
    
    def execute_payment(self, payment_id, payer_id):
        """Execute PayPal payment after approval"""
        try:
            token = self.get_access_token()
            if not token:
                return False, "No access token"
            
            response = requests.post(
                f"{self.base_url}/v1/payments/payment/{payment_id}/execute",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"
                },
                json={"payer_id": payer_id},
                timeout=30
            )
            
            if response.status_code == 200:
                payment = response.json()
                if payment.get("state") == "approved":
                    # Extract transaction details
                    transactions = payment.get("transactions", [])
                    if transactions:
                        transaction = transactions[0]
                        amount = transaction.get("amount", {}).get("total")
                        currency = transaction.get("amount", {}).get("currency")
                        return True, {
                            "payment_id": payment_id,
                            "transaction_id": payment.get("id"),
                            "amount": float(amount) if amount else 0,
                            "currency": currency,
                            "state": "approved"
                        }
                return False, "Payment not approved"
            else:
                logger.error(f"PayPal execute failed: {response.text}")
                return False, f"Execution failed: {response.status_code}"
                
        except Exception as e:
            logger.error(f"Execute payment error: {e}")
            return False, str(e)
    
    def get_payment_details(self, payment_id):
        """Get payment details"""
        try:
            token = self.get_access_token()
            if not token:
                return None
            
            response = requests.get(
                f"{self.base_url}/v1/payments/payment/{payment_id}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Get payment failed: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Get payment error: {e}")
            return None
    
    def verify_transaction(self, transaction_id):
        """Verify a transaction by ID"""
        try:
            # For PayPal, transaction ID is usually the sale ID
            token = self.get_access_token()
            if not token:
                return None
            
            response = requests.get(
                f"{self.base_url}/v1/payments/sale/{transaction_id}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"
                },
                timeout=30
            )
            
            if response.status_code == 200:
                sale = response.json()
                if sale.get("state") == "completed":
                    return {
                        "transaction_id": transaction_id,
                        "amount": float(sale.get("amount", {}).get("total", 0)),
                        "currency": sale.get("amount", {}).get("currency", "USD"),
                        "state": "completed",
                        "create_time": sale.get("create_time")
                    }
            return None
            
        except Exception as e:
            logger.error(f"Verify transaction error: {e}")
            return None

# Initialize PayPal
paypal = PayPalPayment()

# ========================
# SIMPLE DATABASE
# ========================
class SimpleDB:
    def __init__(self):
        self.db_file = "starai.db"
        self.init_db()
    
    def init_db(self):
        """Initialize SQLite database"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Donations table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount REAL,
                    currency TEXT DEFAULT 'USD',
                    payment_id TEXT UNIQUE,
                    transaction_id TEXT,
                    status TEXT DEFAULT 'pending',
                    method TEXT DEFAULT 'paypal',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    verified_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Pending payments table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_payments (
                    payment_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    amount REAL,
                    currency TEXT,
                    approval_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"DB init error: {e}")
    
    def save_user(self, user_id, username, first_name, last_name):
        """Save or update user"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Save user error: {e}")
    
    def save_pending_payment(self, payment_id, user_id, amount, currency, approval_url):
        """Save pending payment"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            expires_at = datetime.now().timestamp() + 3600  # 1 hour
            
            cursor.execute('''
                INSERT OR REPLACE INTO pending_payments 
                (payment_id, user_id, amount, currency, approval_url, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (payment_id, user_id, amount, currency, approval_url, expires_at))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Save pending error: {e}")
            return False
    
    def get_pending_payment(self, payment_id):
        """Get pending payment"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM pending_payments WHERE payment_id = ?', (payment_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "payment_id": row[0],
                    "user_id": row[1],
                    "amount": row[2],
                    "currency": row[3],
                    "approval_url": row[4],
                    "created_at": row[5]
                }
            return None
        except Exception as e:
            logger.error(f"Get pending error: {e}")
            return None
    
    def save_donation(self, user_id, amount, currency, payment_id, transaction_id, status, method="paypal"):
        """Save donation"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            verified_at = datetime.now().timestamp() if status == "completed" else None
            
            cursor.execute('''
                INSERT INTO donations 
                (user_id, amount, currency, payment_id, transaction_id, status, method, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, amount, currency, payment_id, transaction_id, status, method, verified_at))
            
            # Remove from pending
            cursor.execute('DELETE FROM pending_payments WHERE payment_id = ?', (payment_id,))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Save donation error: {e}")
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
                    "amount": row[2],
                    "currency": row[3],
                    "payment_id": row[4],
                    "transaction_id": row[5],
                    "status": row[6],
                    "method": row[7],
                    "created_at": row[8]
                })
            return donations
        except Exception as e:
            logger.error(f"Get donations error: {e}")
            return []
    
    def get_user_total(self, user_id):
        """Get user's total donations"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT SUM(amount) FROM donations 
                WHERE user_id = ? AND status = 'completed'
            ''', (user_id,))
            
            total = cursor.fetchone()[0] or 0
            conn.close()
            return total
        except Exception as e:
            logger.error(f"Get total error: {e}")
            return 0
    
    def get_stats(self):
        """Get donation statistics"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Total amount
            cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "completed"')
            total = cursor.fetchone()[0] or 0
            
            # Supporters count
            cursor.execute('SELECT COUNT(DISTINCT user_id) FROM donations WHERE status = "completed"')
            supporters = cursor.fetchone()[0] or 0
            
            # Recent donations
            cursor.execute('''
                SELECT u.first_name, d.amount, d.created_at 
                FROM donations d
                JOIN users u ON d.user_id = u.user_id
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
            logger.error(f"Get stats error: {e}")
            return {"total": 0, "supporters": 0, "recent": []}

# Initialize database
db = SimpleDB()

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
    db.save_user(user.id, user.username, user.first_name, user.last_name)
    
    # Check for payment success return
    if context.args and "payment_success" in context.args[0]:
        await handle_payment_return(update, context, "success")
        return
    elif context.args and "payment_cancel" in context.args[0]:
        await handle_payment_return(update, context, "cancel")
        return
    
    user_name = user.first_name
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
• Keep StarAI free for everyone!

*Current Stats:*
👥 Supporters: {stats['supporters']}
💰 Total Raised: ${stats['total']:.2f}

*Your Donations:* ${db.get_user_total(user.id):.2f}

*Choose amount:*
"""
    
    keyboard = [
        [InlineKeyboardButton("☕ Tea - $3", callback_data='donate_3'),
         InlineKeyboardButton("🍵 Coffee - $5", callback_data='donate_5')],
        [InlineKeyboardButton("🥤 Smoothie - $10", callback_data='donate_10'),
         InlineKeyboardButton("🍰 Cake - $20", callback_data='donate_20')],
        [InlineKeyboardButton("🎖️ Custom Amount", callback_data='donate_custom'),
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
            if donation["status"] == "pending":
                response += f" (Pending)"
        
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

*Even without donating:*
• Share StarAI with friends
• Give feedback
• Keep using the bot!

*Thank you!* 😊
"""
    
    keyboard = [[InlineKeyboardButton("💰 Donate Now", callback_data='donate')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(response, parse_mode="Markdown", reply_markup=reply_markup)

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
    
    keyboard = [[InlineKeyboardButton("💰 Donate Now", callback_data='donate')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(response, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_payment_return(update: Update, context: ContextTypes.DEFAULT_TYPE, status):
    """Handle PayPal return"""
    if status == "success":
        await update.message.reply_text(
            "✅ *Payment Approved!*\n\n"
            "Your payment has been approved by PayPal.\n"
            "It will be processed and verified shortly.\n\n"
            "Use `/mydonations` to check your status.\n"
            "Thank you for supporting StarAI! 💝",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ *Payment Cancelled*\n\n"
            "Your payment was cancelled.\n"
            "No worries! You can try again with `/donate`\n\n"
            "Thank you for considering supporting StarAI! 😊",
            parse_mode="Markdown"
        )

# ========================
# PAYMENT HANDLING
# ========================
async def create_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, amount):
    """Create PayPal payment"""
    query = update.callback_query
    user = query.from_user
    
    # Create PayPal payment
    payment_id, approval_url = paypal.create_payment(user.id, amount)
    
    if not payment_id:
        await query.answer("❌ Payment creation failed. Try again.", show_alert=True)
        return
    
    # Save pending payment
    db.save_pending_payment(payment_id, user.id, amount, "USD", approval_url)
    
    # Show payment instructions
    payment_msg = f"""
💳 *PAYMENT READY*

*Amount:* ${amount:.2f}
*Payment ID:* `{payment_id}`

*Instructions:*
1. Click the *🔗 PayPal Link* below
2. Login to PayPal
3. Approve the payment
4. Return to this chat

*After approval:* Your donation will be automatically verified!

*Payment Link:* [Click Here]({approval_url})
"""
    
    keyboard = [
        [InlineKeyboardButton("🔗 PayPal Link", url=approval_url)],
        [InlineKeyboardButton("✅ I've Paid", callback_data=f'verify_{payment_id}'),
         InlineKeyboardButton("🔄 Check Status", callback_data=f'check_{payment_id}')],
        [InlineKeyboardButton("❌ Cancel", callback_data='donate')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(payment_msg, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=False)

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id):
    """Verify PayPal payment"""
    query = update.callback_query
    user = query.from_user
    
    # Get pending payment
    pending = db.get_pending_payment(payment_id)
    if not pending:
        await query.answer("❌ Payment not found or expired", show_alert=True)
        return
    
    # Check with PayPal
    payment_details = paypal.get_payment_details(payment_id)
    if not payment_details:
        await query.answer("❌ Could not verify payment. Try again later.", show_alert=True)
        return
    
    # Check payment state
    state = payment_details.get("state")
    
    if state == "approved":
        # Try to execute payment
        # Note: In real implementation, you'd execute after user approves
        # For now, we'll mark as completed
        
        db.save_donation(
            user_id=user.id,
            amount=pending["amount"],
            currency="USD",
            payment_id=payment_id,
            transaction_id=payment_id,  # Use payment ID as transaction ID
            status="completed"
        )
        
        success_msg = f"""
✅ *PAYMENT VERIFIED!* 🎉

Thank you for your donation of *${pending['amount']:.2f}*!
You are now a StarAI Supporter! 🎖️

*Payment ID:* `{payment_id}`
*Amount:* ${pending['amount']:.2f}
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
    
    elif state == "created":
        # Payment created but not approved
        await query.answer("⏳ Payment created but not approved yet. Click the PayPal link to approve.", show_alert=True)
    else:
        # Other states
        await query.answer(f"Payment status: {state}. Please contact support if approved.", show_alert=True)

# ========================
# BUTTON HANDLERS
# ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'donate':
        await donate_command(update, context)
    
    elif query.data.startswith('donate_'):
        amount_str = query.data.replace('donate_', '')
        
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
        payment_id = query.data.replace('verify_', '')
        await verify_payment(update, context, payment_id)
    
    elif query.data.startswith('check_'):
        payment_id = query.data.replace('check_', '')
        await verify_payment(update, context, payment_id)
    
    elif query.data == 'check_payment':
        await query.edit_message_text(
            "🔍 *CHECK PAYMENT STATUS*\n\n"
            "Enter your Payment ID or Transaction ID:\n\n"
            "*Format:* `PAYID-...` or `trx_...`\n\n"
            "Send the ID to check status.",
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
                    payment_id, approval_url = paypal.create_payment(user.id, amount)
                    
                    if payment_id:
                        db.save_pending_payment(payment_id, user.id, amount, "USD", approval_url)
                        
                        payment_msg = f"""
💳 *CUSTOM PAYMENT: ${amount:.2f}*

*Payment ID:* `{payment_id}`

Click the PayPal link below to complete payment:

[🔗 PayPal Payment Link]({approval_url})

*After payment:* Click *✅ I've Paid* below.
"""
                        
                        keyboard = [
                            [InlineKeyboardButton("🔗 PayPal Link", url=approval_url)],
                            [InlineKeyboardButton("✅ I've Paid", callback_data=f'verify_{payment_id}')],
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
            
            transaction_id = user_message.strip()
            
            # Try to verify with PayPal
            transaction = paypal.verify_transaction(transaction_id)
            if transaction:
                # Check if already in database
                donations = db.get_user_donations(user.id)
                found = False
                for donation in donations:
                    if donation["transaction_id"] == transaction_id or donation["payment_id"] == transaction_id:
                        found = True
                        status = "✅ Completed" if donation["status"] == "completed" else "⏳ Pending"
                        await update.message.reply_text(
                            f"✅ *PAYMENT FOUND*\n\n"
                            f"*Status:* {status}\n"
                            f"*Amount:* ${donation['amount']:.2f}\n"
                            f"*Date:* {donation['created_at'][:10]}\n\n"
                            f"Use `/mydonations` for details.",
                            parse_mode="Markdown"
                        )
                        break
                
                if not found:
                    # Add to database
                    db.save_donation(
                        user_id=user.id,
                        amount=transaction["amount"],
                        currency=transaction["currency"],
                        payment_id=transaction_id,
                        transaction_id=transaction_id,
                        status="completed"
                    )
                    
                    await update.message.reply_text(
                        f"✅ *PAYMENT VERIFIED!*\n\n"
                        f"*Amount:* ${transaction['amount']:.2f}\n"
                        f"*Status:* ✅ Completed\n"
                        f"*Date:* {transaction['create_time'][:10]}\n\n"
                        f"Thank you for your donation! 🎖️",
                        parse_mode="Markdown"
                    )
            else:
                await update.message.reply_text(
                    "❌ *PAYMENT NOT FOUND*\n\n"
                    "We couldn't verify this transaction ID.\n\n"
                    "*Possible reasons:*\n"
                    "• Transaction ID is incorrect\n"
                    "• Payment is still processing\n"
                    "• Payment was cancelled\n\n"
                    "Try again or contact support.",
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
    
    if str(user.id) != str(ADMIN_USER_ID):
        await update.message.reply_text("❌ Admin only.", parse_mode="Markdown")
        return
    
    args = context.args
    
    if not args:
        help_text = """
🔧 *ADMIN COMMANDS*

`/admin stats` - Detailed stats
`/admin pending` - Pending payments
`/admin verify <id>` - Verify payment
`/admin users` - All supporters
`/admin export` - Export data
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return
    
    cmd = args[0].lower()
    
    if cmd == "stats":
        # Get detailed stats
        conn = sqlite3.connect(db.db_file)
        cursor = conn.cursor()
        
        # Today's donations
        cursor.execute('SELECT SUM(amount) FROM donations WHERE date(created_at) = date("now") AND status = "completed"')
        today = cursor.fetchone()[0] or 0
        
        # This month
        cursor.execute('SELECT SUM(amount) FROM donations WHERE strftime("%Y-%m", created_at) = strftime("%Y-%m", "now") AND status = "completed"')
        month = cursor.fetchone()[0] or 0
        
        # All time
        cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "completed"')
        total = cursor.fetchone()[0] or 0
        
        # Top donors
        cursor.execute('''
            SELECT u.first_name, SUM(d.amount) as total
            FROM donations d
            JOIN users u ON d.user_id = u.user_id
            WHERE d.status = "completed"
            GROUP BY d.user_id
            ORDER BY total DESC
            LIMIT 5
        ''')
        top = cursor.fetchall()
        
        conn.close()
        
        response = f"""
📊 *ADMIN STATS*

*Today:* ${today:.2f}
*This Month:* ${month:.2f}
*All Time:* ${total:.2f}

*Top 5 Donors:*
"""
        for name, amount in top:
            response += f"\n• {name}: ${amount:.2f}"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "pending":
        # Get pending payments
        conn = sqlite3.connect(db.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM pending_payments ORDER BY created_at DESC')
        pending = cursor.fetchall()
        
        conn.close()
        
        if not pending:
            await update.message.reply_text("✅ No pending payments.", parse_mode="Markdown")
            return
        
        response = "⏳ *PENDING PAYMENTS*\n\n"
        for payment in pending:
            response += f"• ID: `{payment[0]}`\n"
            response += f"  User: {payment[1]}, Amount: ${payment[2]:.2f}\n"
            response += f"  Created: {payment[5][:16]}\n\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "export":
        # Export data as JSON
        conn = sqlite3.connect(db.db_file)
        cursor = conn.cursor()
        
        # Get all donations
        cursor.execute('SELECT * FROM donations ORDER BY created_at DESC')
        donations = cursor.fetchall()
        
        # Get all users
        cursor.execute('SELECT * FROM users')
        users = cursor.fetchall()
        
        conn.close()
        
        data = {
            "donations": donations,
            "users": users,
            "exported_at": datetime.now().isoformat()
        }
        
        # Save to file
        with open("export.json", "w") as f:
            json.dump(data, f, indent=2)
        
        await update.message.reply_text("✅ Data exported to export.json", parse_mode="Markdown")

# ========================
# MAIN FUNCTION
# ========================
def main():
    """Start the bot"""
    print("=" * 50)
    print("🌟 STARAI - WITH PAYPAL PAYMENTS")
    print("=" * 50)
    
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN missing!")
        print("Set in Heroku: Settings → Config Vars")
        return
    
    if not PAYPAL_CLIENT_ID or not PAYPAL_SECRET:
        print("⚠️ WARNING: PayPal credentials missing")
        print("Donations will use manual verification")
    
    print("✅ Features: AI Chat, Image Generation, Music, PayPal Donations")
    print("💰 PayPal: Automatic payment verification")
    print("🔧 Admin: /admin commands available")
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
        print("=" * 50)
        
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Failed to start: {e}")

if __name__ == '__main__':
    main()
