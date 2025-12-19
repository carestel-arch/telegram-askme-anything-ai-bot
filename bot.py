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
            }],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                        "brand_name": "StarAI",
                        "locale": "en-US",
                        "landing_page": "LOGIN",
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "PAY_NOW",
                        "return_url": f"https://t.me/your_bot_username",  # Your bot username
                        "cancel_url": f"https://t.me/your_bot_username"   # Your bot username
                    }
                }
            }
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

def verify_paypal_webhook(headers, body):
    """Verify PayPal webhook signature"""
    try:
        if not PAYPAL_WEBHOOK_ID:
            return True  # Skip verification if webhook ID not set
        
        access_token = get_paypal_access_token()
        if not access_token:
            return False
        
        verification_url = f"{PAYPAL_WEBHOOK_URL}/v1/notifications/verify-webhook-signature"
        headers_verify = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
        
        verification_data = {
            "transmission_id": headers.get('PAYPAL-TRANSMISSION-ID', ''),
            "transmission_time": headers.get('PAYPAL-TRANSMISSION-TIME', ''),
            "cert_url": headers.get('PAYPAL-CERT-URL', ''),
            "auth_algo": headers.get('PAYPAL-AUTH-ALGO', ''),
            "transmission_sig": headers.get('PAYPAL-TRANSMISSION-SIG', ''),
            "webhook_id": PAYPAL_WEBHOOK_ID,
            "webhook_event": body
        }
        
        response = requests.post(verification_url, headers=headers_verify, json=verification_data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            return result.get('verification_status') == 'SUCCESS'
        else:
            logger.error(f"Webhook verification failed: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Webhook verification error: {e}")
        return False

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

# ... [KEEP ALL YOUR EXISTING CODE UNTIL THE PAYMENT SELECTION FUNCTION] ...

# ========================
# UPDATED PAYMENT SELECTION FUNCTION
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
# UPDATED BUTTON HANDLERS
# ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    logger.info(f"Button pressed: {query.data}")
    
    # ... [KEEP ALL EXISTING BUTTON HANDLERS] ...
    
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
    
    # ... [KEEP ALL OTHER BUTTON HANDLERS] ...

# ========================
# WEBHOOK ENDPOINT FOR PAYPAL
# ========================
async def paypal_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle PayPal webhook notifications"""
    try:
        # This would be a separate web server endpoint
        # For now, we'll handle manual capture via button
        pass
    except Exception as e:
        logger.error(f"Webhook error: {e}")

# ========================
# UPDATED MESSAGE HANDLER FOR PAYMENTS
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
        
        # ... [KEEP THE REST OF YOUR EXISTING handle_message CODE] ...

# ========================
# UPDATED ADMIN COMMANDS
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
    
    # ... [KEEP OTHER ADMIN COMMANDS] ...

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
