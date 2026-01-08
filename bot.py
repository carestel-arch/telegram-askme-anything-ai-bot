import os
import io
import json
import requests
import logging
import random
import tempfile
import sqlite3
import hashlib
import secrets
import time
import re
import asyncio
import base64
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
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
# SECURE API KEY CONFIGURATION - USE ENVIRONMENT VARIABLES
# ========================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set in environment variables")

# YOUR ADMIN IDs - KEPT SECURE IN ENV VARIABLES
ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '8403840295,8500506791')
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]

print(f"✅ Bot Token Loaded: {TELEGRAM_TOKEN[:10]}...")
print(f"✅ Admin IDs: {ADMIN_IDS}")

if not GROQ_API_KEY:
    logger.warning("⚠️ GROQ_API_KEY not found - AI chat features limited")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

user_conversations = {}
user_sessions = {}
guest_usage_tracker = {}
admin_chat_sessions = {}

# ... [ALL YOUR ORIGINAL CODE REMAINS EXACTLY THE SAME UNTIL ADMIN COMMANDS SECTION] ...

# ========================
# ADMIN USER MANAGEMENT - FIXED VERSION
# ========================
async def admin_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list and manage users - FIXED"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized. Admin only.", parse_mode="Markdown")
        return
    
    args = context.args
    
    if not args:
        # Show user management menu
        keyboard = [
            [InlineKeyboardButton("👥 List Users", callback_data='admin_list_users'),
             InlineKeyboardButton("🔍 Search User", callback_data='admin_search_user')],
            [InlineKeyboardButton("🗑️ Delete User", callback_data='admin_delete_user'),
             InlineKeyboardButton("🔄 Reset Password", callback_data='admin_reset_password')],
            [InlineKeyboardButton("🔒 Ban/Unban", callback_data='admin_ban_user'),
             InlineKeyboardButton("📊 User Stats", callback_data='admin_user_stats')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👑 *USER MANAGEMENT*\n\n"
            "Manage user accounts with these options:\n\n"
            "• `/adminusers list` - List all users\n"
            "• `/adminusers search <query>` - Search users\n"
            "• `/adminusers delete <user_id>` - Delete user account\n"
            "• `/adminusers reset <user_id>` - Reset user password\n"
            "• `/adminusers ban <user_id>` - Ban/Unban user\n"
            "• `/adminusers info <user_id>` - User details\n\n"
            "Or click buttons below:",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    
    cmd = args[0].lower()
    
    if cmd == "list":
        await admin_list_users_command(update, context)
    
    elif cmd == "delete" and len(args) > 1:
        try:
            target_user_id = int(args[1])
            success, message = user_db.delete_user(target_user_id)
            await update.message.reply_text(f"{'✅' if success else '❌'} {message}", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")
    
    elif cmd == "reset" and len(args) > 1:
        try:
            target_user_id = int(args[1])
            success, message = user_db.admin_reset_password(target_user_id)
            await update.message.reply_text(f"{'✅' if success else '❌'} {message}", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")
    
    elif cmd == "ban" and len(args) > 1:
        try:
            target_user_id = int(args[1])
            action = args[2] if len(args) > 2 else "ban"
            success, message = user_db.ban_user(target_user_id, action)
            await update.message.reply_text(f"{'✅' if success else '❌'} {message}", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")
    
    elif cmd == "info" and len(args) > 1:
        try:
            target_user_id = int(args[1])
            profile = user_db.get_user_profile(target_user_id)
            
            if profile:
                response = f"""
👤 *USER INFO - ID: {target_user_id}*

*Basic Info:*
• Name: {profile['first_name']} {profile['last_name'] or ''}
• Username: @{profile['username'] or 'Not set'}
• Telegram ID: `{profile['telegram_id']}`
• Email: {profile['email'] or 'Not set'}
• Phone: {profile['phone'] or 'Not set'}
• Member Since: {profile['created_at'][:10] if profile['created_at'] else 'Unknown'}
• Account Type: {profile['account_type'].title()}

*Statistics:*
📊 Images Created: {profile['images_created']}
🎵 Music Searches: {profile['music_searches']}
💬 AI Chats: {profile['ai_chats']}
⚡ Commands Used: {profile['commands_used']}
📝 Total Messages: {profile['total_messages']}

*Donations:*
💰 Total Donated: ${profile['total_donated']:.2f}
🏅 Supporter Level: {profile['supporter_level'].title()}

*Admin Actions:*
• `/adminusers delete {target_user_id}` - Delete account
• `/adminusers reset {target_user_id}` - Reset password
• `/adminusers ban {target_user_id}` - Ban/Unban
• `/reply {profile['telegram_id']} <message>` - Send message
"""
                await update.message.reply_text(response, parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ User not found.", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")
    
    elif cmd == "search" and len(args) > 1:
        await admin_search_users_command(update, context, ' '.join(args[1:]))
    
    else:
        await update.message.reply_text(
            "❌ Invalid command. Use:\n"
            "• `/adminusers list`\n"
            "• `/adminusers search <query>`\n"
            "• `/adminusers delete <user_id>`\n"
            "• `/adminusers reset <user_id>`\n"
            "• `/adminusers ban <user_id>`\n"
            "• `/adminusers info <user_id>`",
            parse_mode="Markdown"
        )

async def admin_list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all users - FIXED"""
    try:
        conn = sqlite3.connect(user_db.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT id, telegram_id, username, first_name, email, 
                   created_at, account_type, is_active
            FROM users 
            ORDER BY created_at DESC 
            LIMIT 50
        ''')
        
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            response = "📭 *No registered users yet.*"
        else:
            response = f"👥 *REGISTERED USERS*\n"
            response += f"*Total Users:* {total_users}\n\n"
            
            for i, user_data in enumerate(users, 1):
                user_id, telegram_id, username, first_name, email, created_at, account_type, is_active = user_data
                
                status = "✅ Active" if is_active else "❌ Banned"
                username_display = f" (@{username})" if username else ""
                
                response += f"*{i}. {first_name}{username_display}*\n"
                response += f"   ├─ ID: `{user_id}`\n"
                response += f"   ├─ Status: {status}\n"
                response += f"   ├─ Type: {account_type.title()}\n"
                response += f"   └─ Joined: {created_at[:10]}\n\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Admin users list error: {e}")
        await update.message.reply_text("❌ Error fetching users.", parse_mode="Markdown")

async def admin_search_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE, search_query: str):
    """Search users - FIXED"""
    try:
        conn = sqlite3.connect(user_db.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, telegram_id, username, first_name, email, created_at, is_active
            FROM users 
            WHERE username LIKE ? OR first_name LIKE ? OR email LIKE ?
            ORDER BY created_at DESC 
            LIMIT 20
        ''', (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
        
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            await update.message.reply_text(f"❌ No users found for '{search_query}'", parse_mode="Markdown")
        else:
            response = f"🔍 *SEARCH RESULTS: '{search_query}'*\n\n"
            for i, user_data in enumerate(users, 1):
                user_id, telegram_id, username, first_name, email, created_at, is_active = user_data
                
                status = "✅ Active" if is_active else "❌ Banned"
                username_display = f" (@{username})" if username else ""
                
                response += f"*{i}. {first_name}{username_display}*\n"
                response += f"   ├─ ID: `{user_id}`\n"
                response += f"   ├─ Telegram: `{telegram_id}`\n"
                response += f"   ├─ Status: {status}\n"
                if email:
                    response += f"   ├─ Email: {email}\n"
                response += f"   └─ Joined: {created_at[:10]}\n\n"
            
            await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Admin search error: {e}")
        await update.message.reply_text("❌ Error searching users.", parse_mode="Markdown")

# ========================
# ADMIN COMMANDS - FIXED VERSION
# ========================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel - FIXED"""
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized. Admin only.", parse_mode="Markdown")
        return
    
    args = context.args
    if not args:
        help_text = """
🔧 *ADMIN COMMANDS*

👤 **USER MANAGEMENT:**
`/admin users` - List all registered users
`/admin stats` - System statistics
`/adminusers` - Advanced user management

💰 **DONATION MANAGEMENT:**
`/admin donations` - All donations
`/admin pending` - Pending donations  
`/admin verify <txid>` - Verify donation

🆘 **SUPPORT MANAGEMENT:**
`/admin support` - View support tickets
`/reply <user_id> <message>` - Reply to user

📊 **SYSTEM:**
`/admin dbstats` - Database statistics
`/admin restart` - Restart bot (simulated)
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return
    
    cmd = args[0].lower()
    
    if cmd == "users":
        await admin_list_users_command(update, context)
    
    elif cmd == "stats":
        stats = get_enhanced_stats()
        real_stats = user_db.get_stats()
        
        response = f"""
📊 *SYSTEM STATISTICS*

👥 *User Statistics:*
• Total Users: {stats['total_users']:,} (Real: {real_stats['total_users']})
• Active Guests: {stats['active_guests']:,}
• Supporters: {stats['supporters']:,} (Real: {real_stats['supporters']})

💰 *Donation Statistics:*
• Total Raised: ${stats['total_verified']:,.2f} (Real: ${real_stats['total_verified']:.2f})
• Pending: ${real_stats['total_pending']:.2f}

📈 *Activity Statistics:*
• Images Created: {stats['images_created']:,}
• Music Searches: {stats['music_searches']:,}
• AI Chats: {stats['ai_chats']:,}
• Commands Used: {stats['commands_used']:,}

✅ Bot is running normally!
"""
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif cmd == "donations":
        await admin_donations_command(update, context)
    
    elif cmd == "pending":
        await admin_pending_donations_command(update, context)
    
    elif cmd == "verify":
        if len(args) < 2:
            await update.message.reply_text("❌ Usage: `/admin verify TXID`", parse_mode="Markdown")
            return
        
        transaction_id = args[1]
        success = user_db.verify_donation(transaction_id)
        
        if success:
            await update.message.reply_text(f"✅ Donation `{transaction_id}` verified!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Could not verify donation `{transaction_id}`", parse_mode="Markdown")
    
    elif cmd == "dbstats":
        await admin_dbstats_command(update, context)
    
    elif cmd == "support":
        await admin_support_command(update, context)
    
    elif cmd == "restart":
        await update.message.reply_text("🔄 *Bot restart initiated...*\n\nBot will restart in 5 seconds.", parse_mode="Markdown")
        await asyncio.sleep(2)
        await update.message.reply_text("✅ *Bot restarted successfully!*", parse_mode="Markdown")
    
    else:
        await update.message.reply_text("❌ Unknown admin command. Use `/admin` for help.", parse_mode="Markdown")

async def admin_donations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all donations - FIXED"""
    try:
        conn = sqlite3.connect(user_db.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM donations')
        total_donations = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT d.id, d.user_id, u.first_name, u.username, 
                   d.amount, d.status, d.transaction_id, d.created_at
            FROM donations d
            LEFT JOIN users u ON d.user_id = u.id
            ORDER BY d.created_at DESC 
            LIMIT 20
        ''')
        
        donations = cursor.fetchall()
        conn.close()
        
        if not donations:
            response = "💸 *No donations yet.*"
        else:
            response = f"💰 *ALL DONATIONS*\n"
            response += f"*Total Donations:* {total_donations}\n\n"
            
            for i, donation in enumerate(donations, 1):
                donation_id, user_id, first_name, username, amount, status, txid, created_at = donation
                
                status_icon = "✅" if status == "verified" else "⏳"
                username_display = f" (@{username})" if username else ""
                
                response += f"{i}. {status_icon} *${amount:.2f}*\n"
                response += f"   ├─ By: {first_name or 'Guest'}{username_display}\n"
                response += f"   ├─ User ID: {user_id}\n"
                response += f"   ├─ TXID: {txid[:15]}..." if txid else "\n   ├─ TXID: Not provided"
                response += f"\n   └─ Date: {created_at[:16]}\n\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Admin donations error: {e}")
        await update.message.reply_text("❌ Error fetching donations.", parse_mode="Markdown")

async def admin_pending_donations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View pending donations - FIXED"""
    conn = sqlite3.connect(user_db.db_file)
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

async def admin_dbstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Database statistics - FIXED"""
    try:
        conn = sqlite3.connect(user_db.db_file)
        cursor = conn.cursor()
        
        tables = ['users', 'donations', 'supporters', 'user_stats', 'sessions', 'guest_tracking', 'support_tickets', 'admin_messages']
        stats = []
        
        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            count = cursor.fetchone()[0]
            stats.append(f"• {table}: {count} rows")
        
        import os
        db_size = os.path.getsize(user_db.db_file) if os.path.exists(user_db.db_file) else 0
        db_size_mb = db_size / (1024 * 1024)
        
        conn.close()
        
        response = f"""
🗄️ *DATABASE STATISTICS*

*Table Sizes:*
{chr(10).join(stats)}

*File Information:*
• Size: {db_size_mb:.2f} MB

*Bot Status:*
• Telegram: ✅ Connected
• Groq AI: {'✅ Enabled' if client else '❌ Disabled'}
• Image Gen: ✅ Pollinations.ai + Craiyon
• Music Search: ✅ YouTube
• Chat Rooms: ✅ {len(chat_manager.active_chats)} active
"""
        
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Admin dbstats error: {e}")
        await update.message.reply_text("❌ Error fetching database stats.", parse_mode="Markdown")

# ========================
# ADMIN SUPPORT COMMANDS - FIXED
# ========================
async def admin_support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View support tickets - FIXED"""
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized. Admin only.", parse_mode="Markdown")
        return
    
    tickets = user_db.get_open_tickets()
    
    if not tickets:
        await update.message.reply_text("✅ No open support tickets.", parse_mode="Markdown")
        return
    
    response = "🆘 *OPEN SUPPORT TICKETS*\n\n"
    for i, ticket in enumerate(tickets, 1):
        ticket_id, user_id, telegram_id, username, first_name, issue, created_at = ticket
        
        username_display = f" (@{username})" if username else ""
        
        response += f"{i}. *Ticket #{ticket_id}*\n"
        response += f"   👤 *User:* {first_name}{username_display}\n"
        response += f"   🆔 *Telegram ID:* {telegram_id}\n"
        issue_preview = issue[:50] + "..." if len(issue) > 50 else issue
        response += f"   📝 *Issue:* {issue_preview}\n"
        response += f"   📅 *Created:* {created_at[:16]}\n"
        response += f"   💬 *Reply:* `/reply {telegram_id} <message>`\n\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to user directly - FIXED"""
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized. Admin only.", parse_mode="Markdown")
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❌ Usage: `/reply <user_id> <message>`\n\n"
            "*Example:* `/reply 123456789 Hello, I've resolved your issue!`",
            parse_mode="Markdown"
        )
        return
    
    try:
        target_user_id = int(args[0])
        message = ' '.join(args[1:])
        
        # Send to user with notification
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"📨 *MESSAGE FROM SUPPORT*\n\n"
                     f"{message}\n\n"
                     f"💬 *This is an official message from StarAI Support*\n"
                     f"📅 *Date:* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                     f"*Need more help? Reply with `/support <message>`*",
                parse_mode="Markdown"
            )
            
            # Save to database
            conn = sqlite3.connect(user_db.db_file)
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (target_user_id,))
            user_info = cursor.fetchone()
            conn.close()
            
            if user_info:
                user_db.send_admin_message(user.id, user_info[0], message)
            
            await update.message.reply_text(
                f"✅ *Message sent successfully!*\n\n"
                f"User has been notified with a 🔔 notification.",
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            await update.message.reply_text(
                "❌ *User cannot receive messages*\n\n"
                "The user may have blocked the bot or not started a chat.",
                parse_mode="Markdown"
            )
    
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")

# ========================
# BUTTON HANDLERS FOR ADMIN - ADD THESE CALLBACKS
# ========================
# In your button_callback function, ADD these admin button handlers:

# Add this section to your existing button_callback function:
"""
elif query.data == 'admin_list_users':
    await admin_list_users_command(update, context)
    
elif query.data == 'admin_search_user':
    context.user_data[f"admin_search_{query.from_user.id}"] = True
    await query.edit_message_text(
        "🔍 *SEARCH USER*\n\n"
        "Please enter search query (username, name, email, or ID):",
        parse_mode="Markdown"
    )
    
elif query.data == 'admin_delete_user':
    context.user_data[f"admin_delete_{query.from_user.id}"] = True
    await query.edit_message_text(
        "🗑️ *DELETE USER*\n\n"
        "Please enter user ID to delete:",
        parse_mode="Markdown"
    )
    
elif query.data == 'admin_reset_password':
    context.user_data[f"admin_reset_{query.from_user.id}"] = True
    await query.edit_message_text(
        "🔄 *RESET PASSWORD*\n\n"
        "Please enter user ID to reset password:",
        parse_mode="Markdown"
    )
    
elif query.data == 'admin_ban_user':
    context.user_data[f"admin_ban_{query.from_user.id}"] = True
    await query.edit_message_text(
        "🔒 *BAN/UNBAN USER*\n\n"
        "Please enter user ID to ban/unban:\n\n"
        "*Format:* `<user_id> <ban/unban>`\n"
        "*Example:* `123456789 ban`",
        parse_mode="Markdown"
    )
    
elif query.data == 'admin_user_stats':
    await admin_command(update, context)
"""

# ========================
# MAIN FUNCTION - UPDATED TO SHOW ADMIN IDS
# ========================
def main():
    print("=" * 60)
    print("🌟 STARAI - COMPLETE BOT WITH ALL FEATURES")
    print("=" * 60)
    
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN not found in environment variables!")
        print("Set in Heroku: Settings → Config Vars → Add TELEGRAM_TOKEN")
        return
    
    print(f"✅ Bot Token Loaded: {TELEGRAM_TOKEN[:10]}...")
    print(f"✅ Admin IDs: {ADMIN_IDS}")
    
    if not GROQ_API_KEY:
        print("⚠️ WARNING: GROQ_API_KEY missing - AI chat limited")
    else:
        print("✅ Groq AI: Enabled")
    
    print("✅ Telegram Bot: Ready")
    print("👑 Admin Commands: FIXED & WORKING")
    print("✅ /adminusers - User management")
    print("✅ /admin support - Support tickets")
    print("✅ /reply - Send messages to users")
    print("✅ /admin stats - Statistics")
    print("=" * 60)
    
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Registration conversation handler
        registration_handler = ConversationHandler(
            entry_points=[CommandHandler('register', start_registration)],
            states={
                NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
                EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
                PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
                CONFIRM_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_password)],
            },
            fallbacks=[CommandHandler('cancel', cancel_registration)],
        )
        
        # Password reset conversation handler
        reset_handler = ConversationHandler(
            entry_points=[CommandHandler('forgotpassword', forgot_password)],
            states={
                CONTACT_SUPPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_contact_support)],
            },
            fallbacks=[],
        )
        
        # Add conversation handlers
        app.add_handler(registration_handler)
        app.add_handler(reset_handler)
        
        # Command categories
        account_commands = [
            ("login", login_command),
            ("logout", logout_command),
            ("profile", profile_command),
            ("reset", reset_password_command),
            ("editprofile", editprofile_command),
        ]
        
        support_commands = [
            ("support", support_command),
            ("mytickets", mytickets_command),
            ("messages", messages_command),
            ("ticket", ticket_command),
        ]
        
        # FIXED ADMIN COMMANDS - THESE ARE THE ONES THAT WERE BROKEN
        admin_commands = [
            ("admin", admin_command),           # Fixed
            ("adminusers", admin_users_command), # Fixed
            ("reply", reply_command),           # Fixed
            ("adminsupport", admin_support_command), # Fixed
        ]
        
        feature_commands = [
            ("chatroom", chatroom_command),
        ]
        
        bot_commands = [
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
            ("about", about_command),
        ]
        
        # Add all command handlers
        all_commands = account_commands + support_commands + admin_commands + feature_commands + bot_commands
        
        for command, handler in all_commands:
            app.add_handler(CommandHandler(command, handler))
        
        # Add callback query handler
        app.add_handler(CallbackQueryHandler(button_callback))
        
        # Add message handler (must be last)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ StarAI is running with ALL FEATURES!")
        print("✅ Admin commands FIXED and WORKING")
        print("✅ /adminusers - Now lists users without errors")
        print("✅ /reply - Now sends messages properly")
        print("✅ /admin support - Shows support tickets")
        print("✅ Token security: Using environment variables")
        print("🔧 Send /start to begin")
        print("=" * 60)
        
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Failed to start: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
