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
# SECURE API KEY CONFIGURATION
# ========================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set in environment variables")

if not GROQ_API_KEY:
    logger.warning("⚠️ GROQ_API_KEY not found - AI chat features limited")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

ADMIN_IDS = os.environ.get('ADMIN_IDS', '').split(',')
user_conversations = {}
user_sessions = {}
guest_usage_tracker = {}
admin_chat_sessions = {}

# ========================
# FAKE STATISTICS - ENHANCED
# ========================
FAKE_STATS = {
    "total_users": 215000,
    "active_guests": 85000,
    "supporters": 12500,
    "total_verified": 385000.50,
    "images_created": 1250000,
    "music_searches": 890000,
    "ai_chats": 4500000,
    "commands_used": 12000000
}

# ========================
# CONVERSATION STATES
# ========================
NAME, PHONE, EMAIL, PASSWORD, CONFIRM_PASSWORD = range(5)
CONTACT_SUPPORT, ADMIN_REPLY = range(5, 7)
CHANGE_PASSWORD = 8

# ========================
# CHAT ROOM MANAGER
# ========================
class ChatRoomManager:
    def __init__(self):
        self.active_chats = {}  # {chat_id: {users: [], messages: [], admin: id}}
        self.user_chats = {}    # {user_id: chat_id}
    
    def create_chat_room(self, admin_id, chat_name="Support Chat"):
        chat_id = f"chat_{secrets.token_urlsafe(8)}"
        self.active_chats[chat_id] = {
            'name': chat_name,
            'admin': admin_id,
            'users': [admin_id],
            'messages': [],
            'created_at': datetime.now()
        }
        self.user_chats[admin_id] = chat_id
        return chat_id
    
    def add_user_to_chat(self, chat_id, user_id):
        if chat_id in self.active_chats:
            if user_id not in self.active_chats[chat_id]['users']:
                self.active_chats[chat_id]['users'].append(user_id)
            self.user_chats[user_id] = chat_id
            return True
        return False
    
    def send_message(self, chat_id, user_id, message):
        if chat_id in self.active_chats:
            msg_data = {
                'user_id': user_id,
                'message': message,
                'timestamp': datetime.now(),
                'type': 'user' if user_id != self.active_chats[chat_id]['admin'] else 'admin'
            }
            self.active_chats[chat_id]['messages'].append(msg_data)
            return True
        return False
    
    def get_chat_users(self, chat_id):
        return self.active_chats.get(chat_id, {}).get('users', [])
    
    def remove_user(self, chat_id, user_id):
        if chat_id in self.active_chats:
            if user_id in self.active_chats[chat_id]['users']:
                self.active_chats[chat_id]['users'].remove(user_id)
            if user_id in self.user_chats:
                del self.user_chats[user_id]
            return True
        return False
    
    def get_user_chat(self, user_id):
        return self.user_chats.get(user_id)

chat_manager = ChatRoomManager()

# ========================
# COMPLETE USER DATABASE
# ========================
class UserDB:
    def __init__(self):
        if 'DYNO' in os.environ:
            self.db_file = "/tmp/starai_users.db"
        else:
            self.db_file = "starai_users.db"
        self.init_db()
    
    def init_db(self):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    phone TEXT,
                    email TEXT,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    is_verified BOOLEAN DEFAULT 0,
                    verification_code TEXT,
                    account_type TEXT DEFAULT 'free',
                    api_key TEXT UNIQUE,
                    profile_pic TEXT,
                    login_attempts INTEGER DEFAULT 0,
                    last_login_attempt TIMESTAMP,
                    account_status TEXT DEFAULT 'active',
                    reset_token TEXT,
                    reset_token_expiry TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    telegram_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    issue TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP,
                    admin_notes TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admin_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_admin_id INTEGER,
                    to_user_id INTEGER,
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_read BOOLEAN DEFAULT 0,
                    FOREIGN KEY (to_user_id) REFERENCES users (id)
                )
            ''')
            
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
                    verified_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS supporters (
                    user_id INTEGER PRIMARY KEY,
                    total_donated REAL DEFAULT 0,
                    first_donation TIMESTAMP,
                    last_donation TIMESTAMP,
                    supporter_level TEXT DEFAULT 'none',
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id INTEGER PRIMARY KEY,
                    images_created INTEGER DEFAULT 0,
                    music_searches INTEGER DEFAULT 0,
                    ai_chats INTEGER DEFAULT 0,
                    commands_used INTEGER DEFAULT 0,
                    total_messages INTEGER DEFAULT 0,
                    last_active TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    telegram_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS guest_tracking (
                    telegram_id INTEGER PRIMARY KEY,
                    message_count INTEGER DEFAULT 0,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP,
                    reminder_sent BOOLEAN DEFAULT 0,
                    reminder_count INTEGER DEFAULT 0,
                    last_reminder TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"✅ Database initialized: {self.db_file}")
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
    
    def hash_password(self, password, salt=None):
        if salt is None:
            salt = secrets.token_hex(16)
        hash_obj = hashlib.sha256()
        hash_obj.update((password + salt).encode('utf-8'))
        return hash_obj.hexdigest(), salt
    
    def verify_password(self, stored_hash, stored_salt, password):
        if not stored_hash or not stored_salt:
            return False
        hash_obj = hashlib.sha256()
        hash_obj.update((password + stored_salt).encode('utf-8'))
        return hash_obj.hexdigest() == stored_hash
    
    def create_user(self, telegram_id, username, first_name, last_name="", phone="", email="", password=""):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
            if cursor.fetchone():
                conn.close()
                return None, "User already exists"
            
            if not password or len(password) < 6:
                return None, "Password must be at least 6 characters"
            
            password_hash, salt = self.hash_password(password)
            api_key = secrets.token_urlsafe(32)
            verification_code = secrets.token_urlsafe(8)
            
            cursor.execute('''
                INSERT INTO users (telegram_id, username, first_name, last_name, phone, email, 
                                  password_hash, salt, verification_code, api_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (telegram_id, username, first_name, last_name, phone, email, 
                  password_hash, salt, verification_code, api_key))
            
            user_id = cursor.lastrowid
            cursor.execute('INSERT INTO user_stats (user_id) VALUES (?)', (user_id,))
            
            conn.commit()
            conn.close()
            return user_id, "Account created successfully"
        except Exception as e:
            logger.error(f"Create user error: {e}")
            return None, str(e)
    
    def login_user(self, telegram_id, password):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, telegram_id, username, first_name, password_hash, salt, 
                       account_type, is_active, is_verified, login_attempts, last_login_attempt
                FROM users 
                WHERE telegram_id = ?
            ''', (telegram_id,))
            
            user = cursor.fetchone()
            
            if not user:
                conn.close()
                return None, "User not found. Please register first."
            
            user_id, telegram_id, username, first_name, password_hash, salt, account_type, is_active, is_verified, login_attempts, last_login_attempt = user
            
            if login_attempts >= 5:
                if last_login_attempt:
                    last_attempt_time = datetime.strptime(last_login_attempt, '%Y-%m-%d %H:%M:%S')
                    if datetime.now() < last_attempt_time + timedelta(minutes=30):
                        conn.close()
                        return None, "Account locked. Too many failed attempts. Try again in 30 minutes."
                    else:
                        cursor.execute('UPDATE users SET login_attempts = 0 WHERE id = ?', (user_id,))
                        conn.commit()
            
            if not is_active:
                conn.close()
                return None, "Account is suspended"
            
            if not self.verify_password(password_hash, salt, password):
                cursor.execute('''
                    UPDATE users 
                    SET login_attempts = login_attempts + 1, 
                        last_login_attempt = CURRENT_TIMESTAMP 
                    WHERE id = ?
                ''', (user_id,))
                conn.commit()
                conn.close()
                return None, "Incorrect password. Please try again."
            
            cursor.execute('UPDATE users SET login_attempts = 0 WHERE id = ?', (user_id,))
            
            session_id = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(days=30)
            
            cursor.execute('''
                INSERT INTO sessions (session_id, user_id, telegram_id, expires_at)
                VALUES (?, ?, ?, ?)
            ''', (session_id, user_id, telegram_id, expires_at))
            
            cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user_id,))
            
            conn.commit()
            conn.close()
            
            user_data = {
                'user_id': user_id,
                'telegram_id': telegram_id,
                'username': username,
                'first_name': first_name,
                'account_type': account_type,
                'session_id': session_id,
                'is_verified': bool(is_verified)
            }
            
            return user_data, "Login successful"
        except Exception as e:
            logger.error(f"Login error: {e}")
            return None, str(e)
    
    def verify_session(self, session_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT u.id, u.telegram_id, u.username, u.first_name, u.account_type,
                       s.expires_at, s.is_active
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.session_id = ? AND u.is_active = 1 AND s.is_active = 1
            ''', (session_id,))
            
            session = cursor.fetchone()
            
            if not session:
                conn.close()
                return None, "Invalid or expired session"
            
            user_id, telegram_id, username, first_name, account_type, expires_at, is_active = session
            
            if datetime.now() > datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S'):
                cursor.execute('UPDATE sessions SET is_active = 0 WHERE session_id = ?', (session_id,))
                conn.commit()
                conn.close()
                return None, "Session expired"
            
            cursor.execute('UPDATE user_stats SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            
            user_data = {
                'user_id': user_id,
                'telegram_id': telegram_id,
                'username': username,
                'first_name': first_name,
                'account_type': account_type,
                'session_id': session_id
            }
            
            return user_data, "Session valid"
        except Exception as e:
            logger.error(f"Session verify error: {e}")
            return None, str(e)
    
    def logout_user(self, session_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('UPDATE sessions SET is_active = 0 WHERE session_id = ?', (session_id,))
            conn.commit()
            conn.close()
            return True, "Logged out successfully"
        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False, str(e)
    
    def get_user_profile(self, user_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT u.id, u.telegram_id, u.username, u.first_name, u.last_name, 
                       u.phone, u.email, u.created_at, u.account_type, u.is_verified,
                       s.total_donated, s.supporter_level,
                       st.images_created, st.music_searches, st.ai_chats, st.commands_used, st.total_messages
                FROM users u
                LEFT JOIN supporters s ON u.id = s.user_id
                LEFT JOIN user_stats st ON u.id = st.user_id
                WHERE u.id = ?
            ''', (user_id,))
            
            user = cursor.fetchone()
            conn.close()
            
            if not user:
                return None
            
            profile = {
                'id': user[0],
                'telegram_id': user[1],
                'username': user[2],
                'first_name': user[3],
                'last_name': user[4],
                'phone': user[5],
                'email': user[6],
                'created_at': user[7],
                'account_type': user[8],
                'is_verified': bool(user[9]),
                'total_donated': user[10] or 0,
                'supporter_level': user[11] or 'none',
                'images_created': user[12] or 0,
                'music_searches': user[13] or 0,
                'ai_chats': user[14] or 0,
                'commands_used': user[15] or 0,
                'total_messages': user[16] or 0
            }
            
            return profile
        except Exception as e:
            logger.error(f"Get profile error: {e}")
            return None
    
    def update_user_stats(self, user_id, stat_type):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            stat_fields = {
                'images_created': 'images_created',
                'music_searches': 'music_searches',
                'ai_chats': 'ai_chats',
                'commands_used': 'commands_used',
                'total_messages': 'total_messages'
            }
            
            if stat_type in stat_fields:
                field = stat_fields[stat_type]
                cursor.execute(f'UPDATE user_stats SET {field} = {field} + 1 WHERE user_id = ?', (user_id,))
                conn.commit()
            
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Update stats error: {e}")
            return False
    
    def track_guest_activity(self, telegram_id):
        """Track guest activity and send reminders to register"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT message_count, reminder_sent, reminder_count, last_reminder FROM guest_tracking WHERE telegram_id = ?', (telegram_id,))
            guest = cursor.fetchone()
            
            if not guest:
                cursor.execute('''
                    INSERT INTO guest_tracking (telegram_id, message_count, last_seen, reminder_sent, reminder_count)
                    VALUES (?, 1, CURRENT_TIMESTAMP, 0, 0)
                ''', (telegram_id,))
                conn.commit()
                conn.close()
                return False, "first_message"
            else:
                message_count, reminder_sent, reminder_count, last_reminder = guest
                message_count += 1
                
                # Calculate if enough time has passed since last reminder
                can_remind_again = True
                if last_reminder:
                    last_reminder_time = datetime.strptime(last_reminder, '%Y-%m-%d %H:%M:%S')
                    if datetime.now() < last_reminder_time + timedelta(hours=2):
                        can_remind_again = False
                
                should_remind = False
                reminder_type = None
                
                if not reminder_sent and message_count >= 3:
                    should_remind = True
                    reminder_type = "first"
                elif reminder_sent and reminder_count < 5 and message_count >= 8 and can_remind_again:
                    should_remind = True
                    reminder_type = "followup"
                
                if should_remind:
                    cursor.execute('''
                        UPDATE guest_tracking 
                        SET message_count = ?, last_seen = CURRENT_TIMESTAMP, 
                            reminder_sent = 1, reminder_count = reminder_count + 1,
                            last_reminder = CURRENT_TIMESTAMP
                        WHERE telegram_id = ?
                    ''', (message_count, telegram_id))
                    conn.commit()
                    conn.close()
                    return True, reminder_type
                else:
                    cursor.execute('''
                        UPDATE guest_tracking 
                        SET message_count = ?, last_seen = CURRENT_TIMESTAMP 
                        WHERE telegram_id = ?
                    ''', (message_count, telegram_id))
                    conn.commit()
                    conn.close()
                    return False, "no_reminder"
        except Exception as e:
            logger.error(f"Track guest activity error: {e}")
            return False, "error"
    
    def reset_guest_tracking(self, telegram_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM guest_tracking WHERE telegram_id = ?', (telegram_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Reset guest tracking error: {e}")
            return False
    
    def generate_reset_token(self, telegram_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
            user = cursor.fetchone()
            
            if not user:
                conn.close()
                return None, "User not found"
            
            reset_token = secrets.token_urlsafe(32)
            expiry = datetime.now() + timedelta(hours=24)
            
            cursor.execute('''
                UPDATE users 
                SET reset_token = ?, reset_token_expiry = ?
                WHERE telegram_id = ?
            ''', (reset_token, expiry, telegram_id))
            
            conn.commit()
            conn.close()
            return reset_token, "Reset token generated"
        except Exception as e:
            logger.error(f"Reset token error: {e}")
            return None, str(e)
    
    def verify_reset_token(self, reset_token):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT telegram_id, reset_token_expiry FROM users WHERE reset_token = ?', (reset_token,))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return None, "Invalid reset token"
            
            telegram_id, expiry = result
            
            if datetime.now() > datetime.strptime(expiry, '%Y-%m-%d %H:%M:%S'):
                conn.close()
                return None, "Reset token expired"
            
            conn.close()
            return telegram_id, "Token valid"
        except Exception as e:
            logger.error(f"Verify reset token error: {e}")
            return None, str(e)
    
    def reset_password(self, telegram_id, new_password):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            password_hash, salt = self.hash_password(new_password)
            
            cursor.execute('''
                UPDATE users 
                SET password_hash = ?, salt = ?, reset_token = NULL, reset_token_expiry = NULL, login_attempts = 0
                WHERE telegram_id = ?
            ''', (password_hash, salt, telegram_id))
            
            conn.commit()
            conn.close()
            return True, "Password reset successful"
        except Exception as e:
            logger.error(f"Reset password error: {e}")
            return False, str(e)
    
    def create_support_ticket(self, telegram_id, username, first_name, issue):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
            user = cursor.fetchone()
            user_id = user[0] if user else None
            
            cursor.execute('''
                INSERT INTO support_tickets (user_id, telegram_id, username, first_name, issue)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, telegram_id, username, first_name, issue))
            
            ticket_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return ticket_id, "Support ticket created"
        except Exception as e:
            logger.error(f"Create support ticket error: {e}")
            return None, str(e)
    
    def get_open_tickets(self):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, user_id, telegram_id, username, first_name, issue, created_at
                FROM support_tickets 
                WHERE status = 'open'
                ORDER BY created_at DESC
            ''')
            
            tickets = cursor.fetchall()
            conn.close()
            return tickets
        except Exception as e:
            logger.error(f"Get open tickets error: {e}")
            return []
    
    def update_ticket_status(self, ticket_id, status, admin_notes=""):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE support_tickets 
                SET status = ?, resolved_at = CURRENT_TIMESTAMP, admin_notes = ?
                WHERE id = ?
            ''', (status, admin_notes, ticket_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Update ticket status error: {e}")
            return False
    
    def send_admin_message(self, from_admin_id, to_user_id, message):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO admin_messages (from_admin_id, to_user_id, message)
                VALUES (?, ?, ?)
            ''', (from_admin_id, to_user_id, message))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Send admin message error: {e}")
            return False
    
    def get_user_messages(self, user_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, from_admin_id, message, created_at, is_read
                FROM admin_messages 
                WHERE to_user_id = ?
                ORDER BY created_at DESC
                LIMIT 10
            ''', (user_id,))
            
            messages = cursor.fetchall()
            conn.close()
            return messages
        except Exception as e:
            logger.error(f"Get user messages error: {e}")
            return []
    
    def add_donation(self, user_id, username, first_name, amount, transaction_id=""):
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
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT user_id, amount FROM donations WHERE transaction_id = ?', (transaction_id,))
            donation = cursor.fetchone()
            
            if donation:
                user_id, amount = donation
                
                cursor.execute('UPDATE donations SET status = "verified", verified_at = CURRENT_TIMESTAMP WHERE transaction_id = ?', (transaction_id,))
                
                cursor.execute('SELECT COALESCE(SUM(amount), 0) FROM donations WHERE user_id = ? AND status = "verified"', (user_id,))
                total_donated = cursor.fetchone()[0]
                
                supporter_level = "none"
                if total_donated >= 50:
                    supporter_level = "platinum"
                elif total_donated >= 20:
                    supporter_level = "gold"
                elif total_donated >= 10:
                    supporter_level = "silver"
                elif total_donated >= 5:
                    supporter_level = "bronze"
                elif total_donated > 0:
                    supporter_level = "supporter"
                
                cursor.execute('SELECT * FROM supporters WHERE user_id = ?', (user_id,))
                supporter = cursor.fetchone()
                
                if supporter:
                    cursor.execute('''
                        UPDATE supporters 
                        SET total_donated = ?, last_donation = CURRENT_TIMESTAMP, supporter_level = ?
                        WHERE user_id = ?
                    ''', (total_donated, supporter_level, user_id))
                else:
                    cursor.execute('''
                        INSERT INTO supporters (user_id, total_donated, first_donation, last_donation, supporter_level)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                    ''', (user_id, total_donated, supporter_level))
                
                if total_donated >= 10:
                    cursor.execute('UPDATE users SET account_type = "premium" WHERE id = ?', (user_id,))
                
                conn.commit()
                conn.close()
                return True
        except Exception as e:
            logger.error(f"❌ Verify donation error: {e}")
        return False
    
    def get_user_donations(self, user_id):
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM donations WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
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
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "verified"')
            total_verified = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT SUM(amount) FROM donations WHERE status = "pending"')
            total_pending = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT COUNT(*) FROM supporters WHERE total_donated > 0')
            supporters = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT COUNT(*) FROM guest_tracking')
            active_guests = cursor.fetchone()[0] or 0
            
            conn.close()
            return {
                "total_verified": total_verified,
                "total_pending": total_pending,
                "supporters": supporters,
                "total_users": total_users,
                "active_guests": active_guests
            }
        except Exception as e:
            logger.error(f"❌ Get stats error: {e}")
            return {"total_verified": 0, "total_pending": 0, "supporters": 0, "total_users": 0, "active_guests": 0}
    
    # NEW METHODS FOR USER MANAGEMENT
    def delete_user(self, user_id):
        """Admin: Delete user account"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Get telegram_id first
            cursor.execute('SELECT telegram_id FROM users WHERE id = ?', (user_id,))
            result = cursor.fetchone()
            if not result:
                conn.close()
                return False, "User not found"
            
            telegram_id = result[0]
            
            # Delete from all tables
            cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM user_stats WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM supporters WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM donations WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM support_tickets WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM admin_messages WHERE to_user_id = ?', (user_id,))
            cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
            
            # Also clear guest tracking
            cursor.execute('DELETE FROM guest_tracking WHERE telegram_id = ?', (telegram_id,))
            
            conn.commit()
            conn.close()
            return True, f"User account (ID: {user_id}) deleted successfully"
        except Exception as e:
            logger.error(f"Delete user error: {e}")
            return False, str(e)
    
    def admin_reset_password(self, user_id):
        """Admin: Reset user password"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Generate new password
            new_password = secrets.token_urlsafe(8)
            password_hash, salt = self.hash_password(new_password)
            
            cursor.execute('''
                UPDATE users 
                SET password_hash = ?, salt = ?, login_attempts = 0, reset_token = NULL
                WHERE id = ?
            ''', (password_hash, salt, user_id))
            
            conn.commit()
            conn.close()
            return True, f"Password reset to: {new_password}"
        except Exception as e:
            logger.error(f"Admin reset password error: {e}")
            return False, str(e)
    
    def ban_user(self, user_id, action="ban"):
        """Admin: Ban or unban user"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            is_active = 0 if action == "ban" else 1
            cursor.execute('UPDATE users SET is_active = ? WHERE id = ?', (is_active, user_id))
            
            conn.commit()
            conn.close()
            action_text = "banned" if action == "ban" else "unbanned"
            return True, f"User {action_text} successfully"
        except Exception as e:
            logger.error(f"Ban user error: {e}")
            return False, str(e)
    
    def update_user_profile(self, user_id, field, value):
        """User: Update profile field"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute(f'UPDATE users SET {field} = ? WHERE id = ?', (value, user_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Update profile error: {e}")
            return False
    
    def change_user_password(self, user_id, old_password, new_password):
        """User: Change their own password"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT password_hash, salt FROM users WHERE id = ?', (user_id,))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return False, "User not found"
            
            stored_hash, salt = result
            
            if not self.verify_password(stored_hash, salt, old_password):
                conn.close()
                return False, "Current password is incorrect"
            
            if len(new_password) < 6:
                conn.close()
                return False, "New password must be at least 6 characters"
            
            new_hash, new_salt = self.hash_password(new_password)
            
            cursor.execute('''
                UPDATE users 
                SET password_hash = ?, salt = ?, login_attempts = 0
                WHERE id = ?
            ''', (new_hash, new_salt, user_id))
            
            conn.commit()
            conn.close()
            return True, "Password changed successfully"
        except Exception as e:
            logger.error(f"Change password error: {e}")
            return False, str(e)

# Initialize database
user_db = UserDB()

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
# GUEST REGISTRATION REMINDERS
# ========================
GUEST_REMINDERS = {
    "first": [
        "👋 *Welcome to StarAI!* ✨\n\nYou're currently in *Guest Mode*. Did you know registered users get:\n✅ Priority AI responses\n✅ Unlimited image generation\n✅ Full chat history\n✅ Supporter perks\n\nRegister now: `/register`",
        "🎯 *Quick Tip!* 🎯\n\nAs a guest, you're missing out on:\n• 🏆 Supporter badges\n• 📊 Usage statistics\n• 🔒 Secure account\n• 💰 Donation rewards\n\nUpgrade your experience: `/register`",
        "🌟 *Unlock Full Potential!* 🌟\n\nJoin {total_users:,}+ registered users who enjoy:\n• ⚡ Faster responses\n• 🎨 More image styles\n• 💬 Better chat memory\n• 🏅 Achievement badges\n\nStart here: `/register`"
    ],
    "followup": [
        "🔓 *Still in Guest Mode?* 🔓\n\nYou've used {count} messages! Imagine what you could do with a full account:\n✅ Save all conversations\n✅ Track your progress\n✅ Get premium features\n✅ Join supporter community\n\nRegister free: `/register`",
        "💡 *Pro User Alert!* 💡\n\nGuest mode is limited! Registered users get:\n• 🚀 3x faster image generation\n• 🎯 Priority support\n• 📈 Detailed analytics\n• 🏆 Exclusive badges\n\nUpgrade now: `/register`",
        "🎁 *Special Offer!* 🎁\n\nRegister now and get:\n• 🆓 Free account (always free!)\n• ⭐ Enhanced AI capabilities\n• 📱 Sync across devices\n• 👑 Early access to new features\n\nDon't miss out: `/register`"
    ]
}

# ========================
# ENHANCED STATISTICS
# ========================
def get_enhanced_stats():
    real_stats = user_db.get_stats()
    
    # Start with fake stats as base
    stats = FAKE_STATS.copy()
    
    # Add real data on top
    if real_stats.get("total_users", 0) > 0:
        stats["total_users"] += real_stats["total_users"]
    
    if real_stats.get("supporters", 0) > 0:
        stats["supporters"] += real_stats["supporters"]
    
    if real_stats.get("total_verified", 0) > 0:
        stats["total_verified"] += real_stats["total_verified"]
    
    # Add some random variation to make it look dynamic
    variation = random.randint(1, 1000)
    stats["active_guests"] += variation
    
    return stats

# ========================
# NOTIFICATION SYSTEM
# ========================
async def notify_user(user_id, message, context):
    """Send notification to user"""
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🔔 *NOTIFICATION*\n\n{message}",
            parse_mode="Markdown"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")
        return False

# ========================
# REGISTRATION CONVERSATION
# ========================
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    conn = sqlite3.connect(user_db.db_file)
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (user.id,))
    existing_user = cursor.fetchone()
    conn.close()
    
    if existing_user:
        await update.message.reply_text(
            "❌ *You already have an account!*\n\n"
            "Use `/login` to access your account.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🌟 *CREATE YOUR STARAI ACCOUNT*\n\n"
        "Let's create your account step by step!\n\n"
        "First, what's your full name?\n"
        "*Format:* First Name Last Name\n\n"
        "*Example:* John Doe",
        parse_mode="Markdown"
    )
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name_parts = update.message.text.strip().split()
    if len(name_parts) < 2:
        await update.message.reply_text(
            "❌ Please enter both your first and last name.\n"
            "*Example:* John Doe\n\n"
            "What's your full name?",
            parse_mode="Markdown"
        )
        return NAME
    
    context.user_data['first_name'] = name_parts[0]
    context.user_data['last_name'] = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ""
    
    await update.message.reply_text(
        "📱 *Step 2: Phone Number*\n\n"
        "Please provide your phone number:\n"
        "*Format:* +1234567890\n\n"
        "*Example:* +1234567890\n\n"
        "This helps us secure your account and provide better support.",
        parse_mode="Markdown"
    )
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    
    if not re.match(r'^\+?[1-9]\d{1,14}$', phone):
        await update.message.reply_text(
            "❌ Invalid phone number format.\n"
            "Please enter a valid phone number:\n"
            "*Format:* +1234567890\n\n"
            "*Example:* +1234567890",
            parse_mode="Markdown"
        )
        return PHONE
    
    context.user_data['phone'] = phone
    
    await update.message.reply_text(
        "📧 *Step 3: Email Address*\n\n"
        "Please provide your email address:\n"
        "*Format:* your.email@example.com\n\n"
        "*Example:* john.doe@example.com\n\n"
        "We'll use this for account verification and important updates.",
        parse_mode="Markdown"
    )
    return EMAIL

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        await update.message.reply_text(
            "❌ Invalid email format.\n"
            "Please enter a valid email address:\n"
            "*Format:* your.email@example.com\n\n"
            "*Example:* john.doe@example.com",
            parse_mode="Markdown"
        )
        return EMAIL
    
    context.user_data['email'] = email
    
    await update.message.reply_text(
        "🔐 *Step 4: Create Password*\n\n"
        "Create a strong password for your account:\n"
        "• At least 6 characters\n"
        "• Use letters and numbers\n"
        "• Don't use common passwords\n\n"
        "*Example:* MySecurePass123",
        parse_mode="Markdown"
    )
    return PASSWORD

async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    
    if len(password) < 6:
        await update.message.reply_text(
            "❌ Password must be at least 6 characters.\n"
            "Please create a stronger password:\n"
            "*Example:* MySecurePass123",
            parse_mode="Markdown"
        )
        return PASSWORD
    
    context.user_data['password'] = password
    
    await update.message.reply_text(
        "🔐 *Step 5: Confirm Password*\n\n"
        "Please re-enter your password to confirm:",
        parse_mode="Markdown"
    )
    return CONFIRM_PASSWORD

async def confirm_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    confirm_password_text = update.message.text.strip()
    
    if confirm_password_text != context.user_data.get('password', ''):
        await update.message.reply_text(
            "❌ Passwords don't match!\n\n"
            "Please start over with `/register`",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    user = update.effective_user
    
    user_id, message = user_db.create_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=context.user_data['first_name'],
        last_name=context.user_data.get('last_name', ''),
        phone=context.user_data['phone'],
        email=context.user_data['email'],
        password=context.user_data['password']
    )
    
    if user_id:
        user_data, login_msg = user_db.login_user(user.id, context.user_data['password'])
        
        if user_data:
            context.user_data.update(user_data)
            await update.message.reply_text(
                f"🎉 *ACCOUNT CREATED SUCCESSFULLY!*\n\n"
                f"Welcome to StarAI, {context.user_data['first_name']}!\n\n"
                f"*Your Account Details:*\n"
                f"• Name: {context.user_data['first_name']} {context.user_data.get('last_name', '')}\n"
                f"• Phone: {context.user_data['phone']}\n"
                f"• Email: {context.user_data['email']}\n"
                f"• Account Type: Free\n"
                f"• Status: Active ✅\n\n"
                f"*What you can do now:*\n"
                "• `/profile` - View your complete profile\n"
                "• `/donate` - Support StarAI & get perks\n"
                "• Try all features without limits!\n\n"
                f"*{login_msg}*",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"✅ *Account Created!*\n\n"
                f"Please login with:\n"
                "`/login`",
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(
            f"❌ *Registration Failed*\n\n{message}\n\n"
            "Please try again with `/register`",
            parse_mode="Markdown"
        )
    
    context.user_data.pop('first_name', None)
    context.user_data.pop('last_name', None)
    context.user_data.pop('phone', None)
    context.user_data.pop('email', None)
    context.user_data.pop('password', None)
    
    return ConversationHandler.END

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Registration cancelled.\n\n"
        "You can register anytime with `/register`",
        parse_mode="Markdown"
    )
    
    context.user_data.pop('first_name', None)
    context.user_data.pop('last_name', None)
    context.user_data.pop('phone', None)
    context.user_data.pop('email', None)
    context.user_data.pop('password', None)
    
    return ConversationHandler.END

# ========================
# LOGIN COMMAND
# ========================
async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if 'session_id' in context.user_data:
        await update.message.reply_text(
            "✅ *Already Logged In*\n\n"
            "You are already logged in to your account.\n"
            "• `/profile` - View your profile\n"
            "• `/logout` - Logout from account",
            parse_mode="Markdown"
        )
        return
    
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "🔐 *LOGIN TO YOUR ACCOUNT*\n\n"
            "Please enter your password:\n"
            "`/login yourpassword`\n\n"
            "*Example:* `/login MySecurePass123`\n\n"
            "Forgot password? Use `/forgotpassword`",
            parse_mode="Markdown"
        )
        return
    
    password = ' '.join(args)
    user_data, message = user_db.login_user(user.id, password)
    
    if user_data:
        context.user_data.update(user_data)
        await update.message.reply_text(
            f"✅ *LOGIN SUCCESSFUL!*\n\n"
            f"Welcome back, {user_data['first_name']}!\n\n"
            f"*Account Type:* {user_data['account_type'].title()}\n"
            f"*Status:* ✅ Logged in\n\n"
            "• `/profile` - View your profile\n"
            "• `/donate` - Support StarAI\n"
            "• `/logout` - Logout",
            parse_mode="Markdown"
        )
    else:
        if "Incorrect password" in message:
            await update.message.reply_text(
                "❌ *INCORRECT PASSWORD*\n\n"
                "The password you entered is incorrect.\n\n"
                "Try again: `/login yourpassword`\n\n"
                "Forgot password? Use `/forgotpassword`",
                parse_mode="Markdown"
            )
        elif "Account locked" in message:
            await update.message.reply_text(
                "🔒 *ACCOUNT LOCKED*\n\n"
                "Too many failed login attempts.\n"
                "Please try again in 30 minutes.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ *Login Failed*\n\n{message}\n\n"
                "Try registering first:\n"
                "`/register`",
                parse_mode="Markdown"
            )

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'session_id' in context.user_data:
        session_id = context.user_data['session_id']
        success, message = user_db.logout_user(session_id)
        
        context.user_data.clear()
        
        if success:
            await update.message.reply_text(
                "✅ *Logged Out Successfully*\n\n"
                "You have been logged out of your account.\n\n"
                "• `/login` - Login again\n"
                "• `/register` - Create new account\n"
                "• Continue as guest",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ *Logout Failed*\n\n{message}",
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(
            "ℹ️ *Not Logged In*\n\n"
            "You are not currently logged in.\n"
            "• `/login` - Login to account\n"
            "• `/register` - Create account",
            parse_mode="Markdown"
        )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if 'user_id' not in context.user_data:
        conn = sqlite3.connect(user_db.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (user.id,))
        db_user = cursor.fetchone()
        conn.close()
        
        if db_user:
            await update.message.reply_text(
                "🔒 *Authentication Required*\n\n"
                "Please login to view your profile:\n"
                "`/login`\n\n"
                "Or register if you haven't:\n"
                "`/register`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ *No Account Found*\n\n"
                "You don't have an account yet.\n"
                "Create one with:\n"
                "`/register`\n\n"
                "Benefits:\n"
                "• Secure account with password\n"
                "• Track donations & statistics\n"
                "• Save conversation history\n"
                "• Get supporter perks",
                parse_mode="Markdown"
            )
        return
    
    user_id = context.user_data['user_id']
    profile = user_db.get_user_profile(user_id)
    
    if profile:
        join_date = profile['created_at'][:10] if profile['created_at'] else "Unknown"
        
        supporter_levels = {
            'none': 'No Supporter',
            'supporter': '🌱 Supporter',
            'bronze': '🥉 Bronze',
            'silver': '🥈 Silver', 
            'gold': '🥇 Gold',
            'platinum': '🏆 Platinum'
        }
        
        supporter_level = supporter_levels.get(profile['supporter_level'], 'No Supporter')
        
        account_types = {
            'free': 'Free 🆓',
            'premium': 'Premium ⭐',
            'admin': 'Admin 👑'
        }
        
        account_type = account_types.get(profile['account_type'], 'Free')
        
        profile_text = f"""
👤 *YOUR PROFILE*

*Basic Info:*
• Name: {profile['first_name']} {profile['last_name'] or ''}
• Username: @{profile['username'] or 'Not set'}
• Phone: {profile['phone'] or 'Not set'}
• Email: {profile['email'] or 'Not set'}
• Member Since: {join_date}
• Account Type: {account_type}

*Statistics:*
📊 Images Created: {profile['images_created']}
🎵 Music Searches: {profile['music_searches']}
💬 AI Chats: {profile['ai_chats']}
⚡ Commands Used: {profile['commands_used']}
📝 Total Messages: {profile['total_messages']}

*Donations:*
💰 Total Donated: ${profile['total_donated']:.2f}
🏅 Supporter Level: {supporter_level}
✅ Verified: {'Yes ✅' if profile['is_verified'] else 'No ⏳'}

*Actions:*
• `/logout` - Logout
• `/donate` - Become supporter
• `/editprofile` - Edit your profile
"""
        
        await update.message.reply_text(profile_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "❌ *Profile Not Found*\n\n"
            "Unable to load your profile.\n"
            "Try logging in again: `/login`",
            parse_mode="Markdown"
        )

# ========================
# START COMMAND
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    conn = sqlite3.connect(user_db.db_file)
    cursor = conn.cursor()
    cursor.execute('SELECT id, first_name FROM users WHERE telegram_id = ?', (user.id,))
    user_data = cursor.fetchone()
    conn.close()
    
    stats = get_enhanced_stats()
    
    # Format stats with commas
    total_users = f"{stats['total_users']:,}"
    active_guests = f"{stats['active_guests']:,}"
    supporters = f"{stats['supporters']:,}"
    total_verified = f"${stats['total_verified']:,.2f}"
    images_created = f"{stats['images_created']:,}"
    music_searches = f"{stats['music_searches']:,}"
    
    welcome = f"""
🌟 *WELCOME TO STARAI, {user.first_name}!* 🌟

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

👥 **COMMUNITY STATS:**
• 🎯 Total Users: {total_users}
• 👤 Active Today: {active_guests}
• ⭐ Supporters: {supporters}
• 💰 Total Raised: {total_verified}
• 🎨 Images Created: {images_created}
• 🎵 Music Searches: {music_searches}
"""
    
    if 'user_id' in context.user_data:
        welcome += f"\n✅ *Logged in as:* {context.user_data.get('first_name', user.first_name)}"
    elif user_data:
        welcome += f"\n🔓 *Account detected:* Login with `/login`"
    else:
        welcome += f"\n👤 *Guest Mode:* Register with `/register` for full features!"
    
    welcome += f"""

🔧 **QUICK ACTIONS:**
• `/image` - Create images
• `/music` - Find music  
• `/joke` - Get a laugh
• `/help` - All commands
• `/donate` - Support us
• `/support` - Get help

*Click buttons below or type commands!* 😊
"""
    
    buttons = []
    
    if 'user_id' in context.user_data:
        buttons.append([
            InlineKeyboardButton("👤 Profile", callback_data='profile'),
            InlineKeyboardButton("💰 Donate", callback_data='donate')
        ])
        buttons.append([
            InlineKeyboardButton("📨 Messages", callback_data='messages'),
            InlineKeyboardButton("🆘 Support", callback_data='support')
        ])
    else:
        buttons.append([
            InlineKeyboardButton("📝 Register", callback_data='register'),
            InlineKeyboardButton("🔐 Login", callback_data='login')
        ])
        buttons.append([
            InlineKeyboardButton("🔓 Forgot Password", callback_data='forgot_password'),
            InlineKeyboardButton("🆘 Help", callback_data='help')
        ])
    
    buttons.extend([
        [InlineKeyboardButton("🎨 Create Image", callback_data='create_image'),
         InlineKeyboardButton("🎵 Find Music", callback_data='find_music')],
        [InlineKeyboardButton("😂 Get Joke", callback_data='get_joke'),
         InlineKeyboardButton("💡 Get Fact", callback_data='get_fact')],
        [InlineKeyboardButton("📜 Get Quote", callback_data='get_quote'),
         InlineKeyboardButton("💬 Chat", callback_data='chat')],
        [InlineKeyboardButton("💰 Donate Now", callback_data='donate'),
         InlineKeyboardButton("ℹ️ About", callback_data='about')]
    ])
    
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=reply_markup)

# ========================
# DONATION COMMANDS
# ========================
async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    stats = get_enhanced_stats()
    user_total = 0
    
    if 'user_id' in context.user_data:
        user_total = user_db.get_user_total(context.user_data['user_id'])
    
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
👥 Supporters: {stats['supporters']:,}
💰 Total Raised: ${stats['total_verified']:,.2f}

*Your Donations:* ${user_total:.2f}

*Choose amount:*
"""
    
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
    user = update.effective_user
    
    if 'user_id' not in context.user_data:
        await update.message.reply_text(
            "🔒 *Login Required*\n\n"
            "Please login to view your donations:\n"
            "`/login`\n\n"
            "Or register:\n"
            "`/register`",
            parse_mode="Markdown"
        )
        return
    
    user_id = context.user_data['user_id']
    donations = user_db.get_user_donations(user_id)
    total = user_db.get_user_total(user_id)
    
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

# ========================
# OTHER BOT COMMANDS
# ========================
async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = ' '.join(context.args)
    
    if not prompt:
        await update.message.reply_text(
            "🎨 *Usage:* `/image <description>`\n\n*Examples:*\n• `/image sunset over mountains`\n• `/image cute cat in space`",
            parse_mode="Markdown"
        )
        return
    
    if 'user_id' in context.user_data:
        user_db.update_user_stats(context.user_data['user_id'], 'images_created')
    
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
    
    if 'user_id' in context.user_data:
        user_db.update_user_stats(context.user_data['user_id'], 'music_searches')
    
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

👤 **ACCOUNT COMMANDS:**
`/register` - Create account (5-step process)
`/login <password>` - Login to account  
`/profile` - View profile
`/logout` - Logout
`/forgotpassword` - Reset password
`/editprofile` - Edit your profile
`/support <message>` - Contact support
`/mytickets` - View support tickets
`/messages` - View admin messages

🎨 **MEDIA COMMANDS:**
`/image <description>` - Generate AI image
`/music <song/artist>` - Find music links

💰 **SUPPORT COMMANDS:**
`/donate` - Support StarAI development
`/mydonations` - Check donations

🎭 **FUN COMMANDS:**
`/joke` - Get a joke
`/fact` - Learn a fact  
`/quote` - Inspiring quote
`/clear` - Clear chat memory

💬 **CHAT ROOM:**
`/chatroom` - Create or join chat rooms

👑 **ADMIN (Admins only):**
`/admin` - Admin panel
`/adminusers` - User management
`/reply <user_id> <message>` - Reply to user

*Just talk to me naturally!* 😊
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_enhanced_stats()
    
    about_text = f"""
🌟 *ABOUT STARAI*

StarAI is your complete AI companion powered by cutting-edge technology.

✨ **FEATURES:**
• Advanced AI Chat (Groq AI)
• Image Generation (Pollinations.ai + Craiyon)
• Music Search (YouTube Integration)
• Account System with Security
• Support & Community Features

📊 **COMMUNITY GROWTH:**
• 🎯 Total Users: {stats['total_users']:,}
• ⭐ Supporters: {stats['supporters']:,}
• 💰 Funds Raised: ${stats['total_verified']:,.2f}
• 🎨 Images Created: {stats['images_created']:,}

👥 **OUR TEAM:**
• Dedicated developers
• Active support staff
• Community moderators

🔧 **TECHNOLOGY:**
• Python + Telegram Bot API
• SQLite Database
• Multiple AI APIs
• Secure Authentication

💝 **SUPPORT US:**
Help keep StarAI free and growing!
Use `/donate` to contribute.

*Thank you for being part of our community!* ❤️
"""
    
    await update.message.reply_text(about_text, parse_mode="Markdown")

# ========================
# PAYMENT SELECTION
# ========================
async def show_payment_options(update: Update, context: ContextTypes.DEFAULT_TYPE, amount):
    query = update.callback_query
    context.user_data[f"selected_amount_{query.from_user.id}"] = amount
    
    payment_text = f"""
✅ *Selected: ${amount}*

Now choose your payment method:

1. **PayPal** - Secure payment with card or PayPal balance
2. **Buy Me Coffee** - Simple one-click donation

*After payment, click "✅ I've Paid" below and send your Transaction ID.*
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 PayPal Payment", url='https://www.paypal.com/ncp/payment/HCPVDSSXRL4K8'),
         InlineKeyboardButton("☕ Buy Me Coffee", url='https://www.buymeacoffee.com/StarAI')],
        [InlineKeyboardButton("✅ I've Paid", callback_data='i_donated'),
         InlineKeyboardButton("🔙 Change Amount", callback_data='donate')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(payment_text, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=True)

# ========================
# PASSWORD RESET
# ========================
async def forgot_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    conn = sqlite3.connect(user_db.db_file)
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (user.id,))
    existing_user = cursor.fetchone()
    conn.close()
    
    if not existing_user:
        await update.message.reply_text(
            "❌ *No Account Found*\n\n"
            "You don't have an account yet.\n"
            "Create one with:\n"
            "`/register`",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🔐 *PASSWORD RESET*\n\n"
        "We'll help you reset your password.\n\n"
        "Please choose an option:\n\n"
        "1. **Generate reset link** (requires email)\n"
        "2. **Contact support** (for manual reset)\n\n"
        "Which option would you like? (Reply with 1 or 2)",
        parse_mode="Markdown"
    )
    
    return CONTACT_SUPPORT

async def handle_contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice = update.message.text.strip()
    
    if choice == "1":
        reset_token, message = user_db.generate_reset_token(user.id)
        
        if reset_token:
            conn = sqlite3.connect(user_db.db_file)
            cursor = conn.cursor()
            cursor.execute('SELECT email FROM users WHERE telegram_id = ?', (user.id,))
            user_email = cursor.fetchone()
            conn.close()
            
            if user_email and user_email[0]:
                await update.message.reply_text(
                    f"✅ *Reset Link Generated*\n\n"
                    f"A password reset link has been sent to:\n"
                    f"📧 {user_email[0]}\n\n"
                    f"*Note:* Check your email for reset instructions.\n"
                    f"The link expires in 24 hours.",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    "❌ *No Email Found*\n\n"
                    "We don't have your email on file.\n"
                    "Please contact support instead.",
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(
                f"❌ *Error*\n\n{message}",
                parse_mode="Markdown"
            )
    
    elif choice == "2":
        await update.message.reply_text(
            "👤 *CONTACT SUPPORT*\n\n"
            "Please describe your issue:\n\n"
            "*Examples:*\n"
            "• 'I forgot my password and need it reset'\n"
            "• 'My account is locked'\n"
            "• 'I need help with my account'\n\n"
            "Type your message below:",
            parse_mode="Markdown"
        )
        context.user_data['waiting_support_msg'] = True
    
    else:
        await update.message.reply_text(
            "❌ Please choose 1 or 2:\n\n"
            "1. Generate reset link\n"
            "2. Contact support",
            parse_mode="Markdown"
        )
        return CONTACT_SUPPORT
    
    return ConversationHandler.END

async def reset_password_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle password reset with token"""
    args = context.args
    if not args or len(args) < 1:
        await update.message.reply_text(
            "🔐 *PASSWORD RESET*\n\n"
            "Usage: `/reset <reset_token>`\n\n"
            "Or use: `/forgotpassword` to get a new token",
            parse_mode="Markdown"
        )
        return
    
    reset_token = args[0]
    telegram_id, message = user_db.verify_reset_token(reset_token)
    
    if telegram_id:
        context.user_data[f"reset_in_progress_{update.effective_user.id}"] = True
        context.user_data[f"reset_token_{update.effective_user.id}"] = reset_token
        await update.message.reply_text(
            "✅ *Token Verified!*\n\n"
            "Please enter your new password (minimum 6 characters):",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ {message}", parse_mode="Markdown")

# ========================
# SUPPORT COMMANDS
# ========================
async def create_support_ticket_with_notification(update, context, user, issue):
    """Create support ticket and notify admins"""
    ticket_id, message = user_db.create_support_ticket(
        user.id,
        user.username or "No username",
        user.first_name,
        issue
    )
    
    if ticket_id:
        # Notify user
        await update.message.reply_text(
            f"✅ *SUPPORT TICKET #{ticket_id} CREATED*\n\n"
            f"*Issue:* {issue}\n\n"
            f"📋 *What happens next:*\n"
            f"1. ✅ Ticket received\n"
            f"2. 👀 Admin will review soon\n"
            f"3. 🔔 You'll get notified when admin replies\n"
            f"4. 📊 Track with `/mytickets`\n\n"
            f"⏰ *Response time:* Usually within 24 hours\n\n"
            f"Thank you for contacting support! 🙏",
            parse_mode="Markdown"
        )
        
        # Notify all admins
        admin_ids = [admin_id.strip() for admin_id in ADMIN_IDS if admin_id.strip()]
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🆘 *NEW SUPPORT TICKET #{ticket_id}*\n\n"
                         f"👤 *User:* {user.first_name} (@{user.username or 'No username'})\n"
                         f"🆔 *Telegram ID:* {user.id}\n"
                         f"📝 *Issue:* {issue}\n\n"
                         f"💬 *Quick Actions:*\n"
                         f"• `/reply {user.id} <message>` - Reply directly\n"
                         f"• `/admin support` - View all tickets\n"
                         f"• `/ticket {ticket_id}` - View this ticket\n\n"
                         f"⏰ *Created:* {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    else:
        await update.message.reply_text(
            f"❌ *Failed to create ticket*\n\n{message}",
            parse_mode="Markdown"
        )

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    args = context.args
    if not args:
        # Show interactive support menu
        keyboard = [
            [InlineKeyboardButton("🔐 Password Reset", callback_data='support_password')],
            [InlineKeyboardButton("👤 Account Issues", callback_data='support_account')],
            [InlineKeyboardButton("💰 Donation Help", callback_data='support_donation')],
            [InlineKeyboardButton("🐛 Bug Report", callback_data='support_bug')],
            [InlineKeyboardButton("💬 Other Issue", callback_data='support_other')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🆘 *STARAI SUPPORT CENTER*\n\n"
            "Need help? Choose your issue type below or type:\n"
            "`/support <your message>`\n\n"
            "*Example:* `/support I can't login to my account`\n\n"
            "📞 *We respond within 24 hours!*",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    
    issue = ' '.join(args)
    await create_support_ticket_with_notification(update, context, user, issue)

async def mytickets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    conn = sqlite3.connect(user_db.db_file)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, issue, status, created_at, admin_notes
        FROM support_tickets 
        WHERE telegram_id = ?
        ORDER BY created_at DESC
        LIMIT 5
    ''', (user.id,))
    
    tickets = cursor.fetchall()
    conn.close()
    
    if not tickets:
        await update.message.reply_text(
            "📭 *NO SUPPORT TICKETS*\n\n"
            "You haven't created any support tickets yet.\n\n"
            "Need help? Use:\n"
            "`/support <your message>`",
            parse_mode="Markdown"
        )
        return
    
    response = "📋 *YOUR SUPPORT TICKETS*\n\n"
    for ticket in tickets:
        ticket_id, issue, status, created_at, admin_notes = ticket
        status_icon = "✅" if status == "resolved" else "⏳" if status == "in_progress" else "🆕"
        
        response += f"{status_icon} *Ticket #{ticket_id}*\n"
        response += f"📝 *Issue:* {issue[:50]}...\n" if len(issue) > 50 else f"📝 *Issue:* {issue}\n"
        response += f"📅 *Created:* {created_at[:16]}\n"
        response += f"🔄 *Status:* {status.title()}\n"
        
        if admin_notes:
            response += f"💬 *Admin Note:* {admin_notes[:50]}...\n" if len(admin_notes) > 50 else f"💬 *Admin Note:* {admin_notes}\n"
        
        response += "\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if 'user_id' not in context.user_data:
        await update.message.reply_text(
            "🔒 *Login Required*\n\n"
            "Please login to view your messages:\n"
            "`/login`",
            parse_mode="Markdown"
        )
        return
    
    user_id = context.user_data['user_id']
    messages = user_db.get_user_messages(user_id)
    
    if not messages:
        await update.message.reply_text(
            "📭 *NO MESSAGES*\n\n"
            "You don't have any messages from support yet.\n\n"
            "Need help? Use:\n"
            "`/support <your message>`",
            parse_mode="Markdown"
        )
        return
    
    response = "📨 *MESSAGES FROM SUPPORT*\n\n"
    for msg in messages:
        msg_id, from_admin_id, message, created_at, is_read = msg
        read_icon = "📖" if is_read else "📬"
        
        response += f"{read_icon} *Message #{msg_id}*\n"
        response += f"📅 *Date:* {created_at[:16]}\n"
        response += f"💬 *Message:* {message[:100]}...\n" if len(message) > 100 else f"💬 *Message:* {message}\n"
        response += "\n"
    
    response += "\n*Need to reply?* Use `/support <your message>`"
    await update.message.reply_text(response, parse_mode="Markdown")

async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View specific ticket details"""
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Usage: `/ticket <ticket_id>`\n\n"
            "*Example:* `/ticket 123`",
            parse_mode="Markdown"
        )
        return
    
    try:
        ticket_id = int(args[0])
        
        conn = sqlite3.connect(user_db.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, issue, status, created_at, resolved_at, admin_notes
            FROM support_tickets 
            WHERE id = ?
        ''', (ticket_id,))
        
        ticket = cursor.fetchone()
        conn.close()
        
        if not ticket:
            await update.message.reply_text(f"❌ Ticket #{ticket_id} not found.", parse_mode="Markdown")
            return
        
        ticket_id, issue, status, created_at, resolved_at, admin_notes = ticket
        
        response = f"📋 *TICKET #{ticket_id}*\n\n"
        response += f"📝 *Issue:* {issue}\n\n"
        response += f"📅 *Created:* {created_at[:16]}\n"
        response += f"🔄 *Status:* {status.title()}\n"
        
        if resolved_at:
            response += f"✅ *Resolved:* {resolved_at[:16]}\n"
        
        if admin_notes:
            response += f"\n💬 *Admin Notes:*\n{admin_notes}\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid ticket ID.", parse_mode="Markdown")

# ========================
# ADMIN MESSAGING
# ========================
async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin_ids = [admin_id.strip() for admin_id in ADMIN_IDS if admin_id.strip()]
    
    if str(user.id) not in admin_ids and admin_ids:
        await update.message.reply_text("❌ Admin only.", parse_mode="Markdown")
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
        success = await notify_user(
            target_user_id,
            f"📨 *MESSAGE FROM SUPPORT*\n\n"
            f"{message}\n\n"
            f"💬 *This is an official message from StarAI Support*\n"
            f"📅 *Date:* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"*Need more help? Reply with `/support <message>`*",
            context
        )
        
        if success:
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
        else:
            await update.message.reply_text(
                "❌ *User cannot receive messages*\n\n"
                "The user may have blocked the bot or not started a chat.",
                parse_mode="Markdown"
            )
    
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")

async def admin_support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin_ids = [admin_id.strip() for admin_id in ADMIN_IDS if admin_id.strip()]
    
    if str(user.id) not in admin_ids and admin_ids:
        await update.message.reply_text("❌ Admin only.", parse_mode="Markdown")
        return
    
    tickets = user_db.get_open_tickets()
    
    if not tickets:
        await update.message.reply_text("✅ No open support tickets.", parse_mode="Markdown")
        return
    
    response = "🆘 *OPEN SUPPORT TICKETS*\n\n"
    for i, ticket in enumerate(tickets, 1):
        ticket_id, user_id, telegram_id, username, first_name, issue, created_at = ticket
        
        response += f"{i}. *Ticket #{ticket_id}*\n"
        response += f"   👤 *User:* {first_name} (@{username or 'No username'})\n"
        response += f"   📝 *Issue:* {issue[:50]}...\n" if len(issue) > 50 else f"   📝 *Issue:* {issue}\n"
        response += f"   📅 *Created:* {created_at[:16]}\n"
        response += f"   💬 *Reply:* `/reply {user_id} <message>`\n\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

# ========================
# ADMIN USER MANAGEMENT
# ========================
async def admin_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list and manage users"""
    user = update.effective_user
    admin_ids = [admin_id.strip() for admin_id in ADMIN_IDS if admin_id.strip()]
    
    if str(user.id) not in admin_ids and admin_ids:
        await update.message.reply_text("❌ Admin only.", parse_mode="Markdown")
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
                LIMIT 20
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
                    response += f"*{i}. {first_name}*"
                    if username:
                        response += f" (@{username})"
                    
                    response += f"\n   ├─ ID: `{user_id}`"
                    response += f"\n   ├─ Status: {status}"
                    response += f"\n   ├─ Type: {account_type.title()}"
                    response += f"\n   └─ Joined: {created_at[:10]}\n\n"
            
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Admin users list error: {e}")
            await update.message.reply_text("❌ Error fetching users.", parse_mode="Markdown")
    
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
        search_query = ' '.join(args[1:])
        try:
            conn = sqlite3.connect(user_db.db_file)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, telegram_id, username, first_name, email, created_at
                FROM users 
                WHERE username LIKE ? OR first_name LIKE ? OR email LIKE ? OR telegram_id = ?
                ORDER BY created_at DESC 
                LIMIT 10
            ''', (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", search_query))
            
            users = cursor.fetchall()
            conn.close()
            
            if not users:
                await update.message.reply_text(f"❌ No users found for '{search_query}'", parse_mode="Markdown")
            else:
                response = f"🔍 *SEARCH RESULTS: '{search_query}'*\n\n"
                for i, user_data in enumerate(users, 1):
                    user_id, telegram_id, username, first_name, email, created_at = user_data
                    
                    response += f"*{i}. {first_name}*"
                    if username:
                        response += f" (@{username})"
                    
                    response += f"\n   ├─ ID: `{user_id}`"
                    response += f"\n   ├─ Telegram: `{telegram_id}`"
                    if email:
                        response += f"\n   ├─ Email: {email}"
                    response += f"\n   └─ Joined: {created_at[:10]}\n\n"
                
                await update.message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Admin search error: {e}")
            await update.message.reply_text("❌ Error searching users.", parse_mode="Markdown")
    
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

# ========================
# USER PROFILE EDITING
# ========================
async def editprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow users to edit their profile"""
    user = update.effective_user
    
    if 'user_id' not in context.user_data:
        await update.message.reply_text(
            "🔒 *Login Required*\n\n"
            "Please login to edit your profile:\n"
            "`/login`",
            parse_mode="Markdown"
        )
        return
    
    args = context.args
    
    if not args:
        # Show edit menu
        keyboard = [
            [InlineKeyboardButton("📝 Change Name", callback_data='edit_name'),
             InlineKeyboardButton("📱 Change Phone", callback_data='edit_phone')],
            [InlineKeyboardButton("📧 Change Email", callback_data='edit_email'),
             InlineKeyboardButton("🔐 Change Password", callback_data='edit_password')],
            [InlineKeyboardButton("👤 View Profile", callback_data='profile'),
             InlineKeyboardButton("❌ Cancel", callback_data='cancel_edit')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⚙️ *EDIT YOUR PROFILE*\n\n"
            "What would you like to change?\n\n"
            "• `/editprofile name <new_name>`\n"
            "• `/editprofile phone <new_phone>`\n"
            "• `/editprofile email <new_email>`\n"
            "• `/editprofile password` (will ask for new password)\n\n"
            "Or click buttons below:",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    
    # Handle profile editing
    user_id = context.user_data['user_id']
    field = args[0].lower()
    
    if field == "password":
        context.user_data[f"change_password_{user.id}"] = True
        await update.message.reply_text(
            "🔐 *CHANGE PASSWORD*\n\n"
            "Please enter your current password:",
            parse_mode="Markdown"
        )
    
    elif field == "name" and len(args) > 1:
        new_name = ' '.join(args[1:])
        name_parts = new_name.split()
        if len(name_parts) < 2:
            await update.message.reply_text(
                "❌ Please enter both first and last name.\n"
                "*Example:* `/editprofile name John Doe`",
                parse_mode="Markdown"
            )
            return
        
        success = user_db.update_user_profile(user_id, 'first_name', name_parts[0])
        if len(name_parts) > 1:
            user_db.update_user_profile(user_id, 'last_name', ' '.join(name_parts[1:]))
        
        if success:
            context.user_data['first_name'] = name_parts[0]
            await update.message.reply_text(f"✅ Name updated to: {new_name}", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Failed to update name", parse_mode="Markdown")
    
    elif field == "phone" and len(args) > 1:
        new_phone = args[1]
        if re.match(r'^\+?[1-9]\d{1,14}$', new_phone):
            success = user_db.update_user_profile(user_id, 'phone', new_phone)
            if success:
                await update.message.reply_text(f"✅ Phone updated to: {new_phone}", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Failed to update phone", parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "❌ Invalid phone format.\n"
                "*Format:* +1234567890\n"
                "*Example:* `/editprofile phone +1234567890`",
                parse_mode="Markdown"
            )
    
    elif field == "email" and len(args) > 1:
        new_email = args[1]
        if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', new_email):
            success = user_db.update_user_profile(user_id, 'email', new_email)
            if success:
                await update.message.reply_text(f"✅ Email updated to: {new_email}", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Failed to update email", parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "❌ Invalid email format.\n"
                "*Format:* your.email@example.com\n"
                "*Example:* `/editprofile email john.doe@example.com`",
                parse_mode="Markdown"
            )
    
    else:
        await update.message.reply_text(
            "❌ Invalid command. Use:\n"
            "• `/editprofile name <new_name>`\n"
            "• `/editprofile phone <new_phone>`\n"
            "• `/editprofile email <new_email>`\n"
            "• `/editprofile password`",
            parse_mode="Markdown"
        )

# ========================
# CHAT ROOM COMMANDS
# ========================
async def chatroom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create or join a chat room"""
    user = update.effective_user
    
    args = context.args
    
    if not args:
        keyboard = [
            [InlineKeyboardButton("➕ Create Chat Room", callback_data='create_chat'),
             InlineKeyboardButton("🔗 Join Chat Room", callback_data='join_chat')],
            [InlineKeyboardButton("👥 My Chats", callback_data='my_chats'),
             InlineKeyboardButton("❌ Leave Chat", callback_data='leave_chat')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "💬 *CHAT ROOMS*\n\n"
            "Connect with other users or support in real-time!\n\n"
            "*Commands:*\n"
            "• `/chatroom create <name>` - Create new chat room\n"
            "• `/chatroom join <code>` - Join existing room\n"
            "• `/chatroom leave` - Leave current chat\n"
            "• `/chatroom users` - See room participants\n"
            "• `/chatroom list` - List your active chats\n\n"
            "Or click buttons below:",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    
    cmd = args[0].lower()
    
    if cmd == "create" and len(args) > 1:
        chat_name = ' '.join(args[1:])
        chat_id = chat_manager.create_chat_room(user.id, chat_name)
        
        await update.message.reply_text(
            f"✅ *CHAT ROOM CREATED!*\n\n"
            f"*Name:* {chat_name}\n"
            f"*Room Code:* `{chat_id}`\n\n"
            f"*Share this code with others:*\n"
            f"`/chatroom join {chat_id}`\n\n"
            f"*Participants:*\n"
            f"👑 {user.first_name} (Admin)\n\n"
            f"*Start chatting by sending messages!* 💬",
            parse_mode="Markdown"
        )
    
    elif cmd == "join" and len(args) > 1:
        chat_id = args[1]
        if chat_manager.add_user_to_chat(chat_id, user.id):
            chat_info = chat_manager.active_chats.get(chat_id, {})
            users = chat_info.get('users', [])
            
            # Notify all users in chat
            for u_id in users:
                if u_id != user.id:
                    try:
                        await context.bot.send_message(
                            chat_id=u_id,
                            text=f"👋 *{user.first_name} has joined the chat!*"
                        )
                    except:
                        pass
            
            await update.message.reply_text(
                f"✅ *JOINED CHAT ROOM!*\n\n"
                f"*Room:* {chat_info.get('name', 'Unknown')}\n"
                f"*Participants:* {len(users)} users\n\n"
                f"*You can now chat with everyone in this room!*\n\n"
                f"Send messages normally to chat. Type `/chatroom leave` to exit.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ Invalid chat room code", parse_mode="Markdown")
    
    elif cmd == "leave":
        if user.id in chat_manager.user_chats:
            chat_id = chat_manager.user_chats[user.id]
            chat_manager.remove_user(chat_id, user.id)
            
            # Notify remaining users
            users = chat_manager.get_chat_users(chat_id)
            for u_id in users:
                try:
                    await context.bot.send_message(
                        chat_id=u_id,
                        text=f"👋 *{user.first_name} has left the chat.*"
                    )
                except:
                    pass
            
            await update.message.reply_text("✅ Left the chat room", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ You're not in any chat room", parse_mode="Markdown")
    
    elif cmd == "users":
        if user.id in chat_manager.user_chats:
            chat_id = chat_manager.user_chats[user.id]
            users = chat_manager.get_chat_users(chat_id)
            chat_info = chat_manager.active_chats.get(chat_id, {})
            
            response = f"👥 *CHAT ROOM PARTICIPANTS*\n\n"
            response += f"*Room:* {chat_info.get('name', 'Unknown')}\n"
            response += f"*Total Users:* {len(users)}\n\n"
            
            for u_id in users:
                try:
                    user_info = await context.bot.get_chat(u_id)
                    prefix = "👑 " if u_id == chat_info.get('admin') else "👤 "
                    response += f"{prefix}{user_info.first_name}\n"
                except:
                    response += f"👤 User {u_id}\n"
            
            await update.message.reply_text(response, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ You're not in any chat room", parse_mode="Markdown")
    
    elif cmd == "list":
        user_chats = []
        for chat_id, chat_info in chat_manager.active_chats.items():
            if user.id in chat_info['users']:
                user_chats.append((chat_id, chat_info))
        
        if not user_chats:
            await update.message.reply_text("❌ You're not in any chat rooms", parse_mode="Markdown")
            return
        
        response = "📋 *YOUR CHAT ROOMS*\n\n"
        for chat_id, chat_info in user_chats:
            response += f"*{chat_info['name']}*\n"
            response += f"Code: `{chat_id}`\n"
            response += f"Users: {len(chat_info['users'])}\n"
            response += f"Created: {chat_info['created_at'].strftime('%Y-%m-%d')}\n\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")

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
        try:
            conn = sqlite3.connect(user_db.db_file)
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT id, telegram_id, username, first_name, email, 
                       created_at, account_type
                FROM users 
                ORDER BY created_at DESC 
                LIMIT 10
            ''')
            
            users = cursor.fetchall()
            conn.close()
            
            if not users:
                response = "📭 *No registered users yet.*"
            else:
                response = f"👥 *REGISTERED USERS*\n"
                response += f"*Total Users:* {total_users}\n\n"
                
                for i, user_data in enumerate(users, 1):
                    user_id, telegram_id, username, first_name, email, created_at, account_type = user_data
                    
                    response += f"*{i}. {first_name}*"
                    if username:
                        response += f" (@{username})"
                    
                    response += f"\n   ├─ ID: `{user_id}`"
                    response += f"\n   ├─ Telegram: `{telegram_id}`"
                    if email:
                        response += f"\n   ├─ Email: {email}"
                    response += f"\n   ├─ Type: {account_type.title()}"
                    response += f"\n   └─ Joined: {created_at[:10]}\n\n"
            
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Admin users error: {e}")
            await update.message.reply_text("❌ Error fetching users.", parse_mode="Markdown")
    
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
                LIMIT 10
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
                    response += f"{i}. {status_icon} *${amount:.2f}*\n"
                    response += f"   ├─ By: {first_name or 'Guest'}"
                    if username:
                        response += f" (@{username})"
                    response += f"\n   ├─ User ID: {user_id}"
                    response += f"\n   ├─ TXID: {txid[:15]}..." if txid else "\n   ├─ TXID: Not provided"
                    response += f"\n   └─ Date: {created_at[:16]}\n\n"
            
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Admin donations error: {e}")
            await update.message.reply_text("❌ Error fetching donations.", parse_mode="Markdown")
    
    elif cmd == "pending":
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
    
    elif cmd == "support":
        await admin_support_command(update, context)
    
    elif cmd == "restart":
        await update.message.reply_text("🔄 *Bot restart initiated...*\n\nBot will restart in 5 seconds.", parse_mode="Markdown")
        await asyncio.sleep(2)
        await update.message.reply_text("✅ *Bot restarted successfully!*", parse_mode="Markdown")
    
    else:
        await update.message.reply_text("❌ Unknown admin command. Use `/admin` for help.", parse_mode="Markdown")

# ========================
# BUTTON HANDLERS
# ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    logger.info(f"Button pressed: {query.data}")
    
    # Registration and Login
    if query.data == 'register':
        await query.edit_message_text(
            "📝 *START REGISTRATION*\n\n"
            "Start creating your account with:\n"
            "`/register`\n\n"
            "Follow the 5-step process:\n"
            "1. Your name\n"
            "2. Phone number\n"
            "3. Email address\n"
            "4. Create password\n"
            "5. Confirm password\n\n"
            "*Start now:* `/register`",
            parse_mode="Markdown"
        )
    
    elif query.data == 'login':
        await query.edit_message_text(
            "🔐 *LOGIN TO ACCOUNT*\n\n"
            "Login to your account with:\n"
            "`/login yourpassword`\n\n"
            "*Example:* `/login MySecurePass123`\n\n"
            "Forgot password? Use `/forgotpassword`",
            parse_mode="Markdown"
        )
    
    elif query.data == 'forgot_password':
        await query.edit_message_text(
            "🔓 *FORGOT PASSWORD*\n\n"
            "Need help with your password?\n\n"
            "Use the command:\n"
            "`/forgotpassword`\n\n"
            "This will start the password reset process.",
            parse_mode="Markdown"
        )
    
    elif query.data == 'profile':
        await profile_command(update, context)
    
    elif query.data == 'messages':
        await messages_command(update, context)
    
    elif query.data == 'support':
        await query.edit_message_text(
            "🆘 *SUPPORT CENTER*\n\n"
            "Need help? We're here for you!\n\n"
            "*Quick Options:*\n"
            "• `/support <message>` - Contact support\n"
            "• `/mytickets` - View your tickets\n"
            "• `/forgotpassword` - Password help\n"
            "• `/help` - All commands\n\n"
            "We respond within 24 hours! ⏰",
            parse_mode="Markdown"
        )
    
    # Support callbacks
    elif query.data.startswith('support_'):
        issue_type = query.data.replace('support_', '')
        issue_types = {
            'password': "I need help with my password",
            'account': "I'm having account issues",
            'donation': "I need help with donations",
            'bug': "I found a bug or problem",
            'other': "I have another issue"
        }
        
        context.user_data[f"support_type_{query.from_user.id}"] = issue_type
        await query.edit_message_text(
            f"📝 *{issue_type.upper()} SUPPORT*\n\n"
            f"Please describe your issue in detail:\n\n"
            f"*Example:* '{issue_types[issue_type]} because...'\n\n"
            f"Type your message now:",
            parse_mode="Markdown"
        )
    
    # Donation callbacks
    elif query.data == 'donate':
        await donate_command(update, context)
    
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
            amount = int(query.data.split('_')[1])
            await show_payment_options(update, context, amount)
    
    elif query.data == 'i_donated':
        await query.edit_message_text(
            "✅ *PAYMENT CONFIRMATION*\n\n"
            "Please send your transaction ID as a message.\n\n"
            "*Format:* `TXID123456789`\n\n"
            "We'll verify your payment and update your supporter status!",
            parse_mode="Markdown"
        )
    
    elif query.data == 'my_donations':
        await mydonations_command(update, context)
    
    elif query.data == 'back_to_menu':
        await start(update, context)
    
    # Feature callbacks
    elif query.data == 'create_image':
        await query.edit_message_text(
            "🎨 *IMAGE CREATION*\n\n"
            "Create amazing images with AI!\n\n"
            "*Usage:* `/image <description>`\n\n"
            "*Examples:*\n"
            "• `/image sunset over mountains`\n"
            "• `/image cyberpunk city at night`\n"
            "• `/image cute cat wearing glasses`\n\n"
            "Try it now!",
            parse_mode="Markdown"
        )
    
    elif query.data == 'find_music':
        await query.edit_message_text(
            "🎵 *MUSIC SEARCH*\n\n"
            "Find songs and artists on YouTube!\n\n"
            "*Usage:* `/music <song or artist>`\n\n"
            "*Examples:*\n"
            "• `/music Bohemian Rhapsody`\n"
            "• `/music Taylor Swift`\n"
            "• `/music chill lofi beats`\n\n"
            "Get direct YouTube links to listen!",
            parse_mode="Markdown"
        )
    
    elif query.data == 'get_joke':
        joke = random.choice(JOKES)
        await query.edit_message_text(f"😂 *JOKE OF THE DAY*\n\n{joke}", parse_mode="Markdown")
    
    elif query.data == 'get_fact':
        fact = random.choice(FACTS)
        await query.edit_message_text(f"💡 *DID YOU KNOW?*\n\n{fact}", parse_mode="Markdown")
    
    elif query.data == 'get_quote':
        quote = random.choice(QUOTES)
        await query.edit_message_text(f"📜 *INSPIRATIONAL QUOTE*\n\n{quote}", parse_mode="Markdown")
    
    elif query.data == 'chat':
        await query.edit_message_text(
            "💬 *LET'S CHAT!*\n\n"
            "I'm here to talk about anything! 😊\n\n"
            "*Just type your message and I'll respond naturally!*\n\n"
            "What's on your mind? 🎭",
            parse_mode="Markdown"
        )
    
    # Chat room callbacks
    elif query.data == 'create_chat':
        context.user_data[f"waiting_chat_name_{query.from_user.id}"] = True
        await query.edit_message_text(
            "💬 *CREATE CHAT ROOM*\n\n"
            "Please enter a name for your chat room:\n\n"
            "*Examples:*\n"
            "• StarAI Support\n"
            "• Tech Discussion\n"
            "• Casual Chat\n\n"
            "Enter chat room name:",
            parse_mode="Markdown"
        )
    
    elif query.data == 'join_chat':
        context.user_data[f"waiting_chat_code_{query.from_user.id}"] = True
        await query.edit_message_text(
            "🔗 *JOIN CHAT ROOM*\n\n"
            "Please enter the chat room code:\n\n"
            "*Format:* `chat_xxxxxxxx`\n\n"
            "Enter chat room code:",
            parse_mode="Markdown"
        )
    
    elif query.data == 'my_chats':
        await chatroom_command(update, context)
    
    elif query.data == 'leave_chat':
        if query.from_user.id in chat_manager.user_chats:
            chat_id = chat_manager.user_chats[query.from_user.id]
            chat_manager.remove_user(chat_id, query.from_user.id)
            await query.edit_message_text("✅ Left the chat room", parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ You're not in any chat room", parse_mode="Markdown")
    
    # Profile editing callbacks
    elif query.data.startswith('edit_'):
        field = query.data.replace('edit_', '')
        
        if field == 'name':
            context.user_data[f"waiting_new_name_{query.from_user.id}"] = True
            await query.edit_message_text(
                "📝 *CHANGE NAME*\n\n"
                "Please enter your new full name:\n\n"
                "*Format:* First Name Last Name\n"
                "*Example:* John Doe\n\n"
                "Enter new name:",
                parse_mode="Markdown"
            )
        
        elif field == 'phone':
            context.user_data[f"waiting_new_phone_{query.from_user.id}"] = True
            await query.edit_message_text(
                "📱 *CHANGE PHONE*\n\n"
                "Please enter your new phone number:\n\n"
                "*Format:* +1234567890\n"
                "*Example:* +1234567890\n\n"
                "Enter new phone:",
                parse_mode="Markdown"
            )
        
        elif field == 'email':
            context.user_data[f"waiting_new_email_{query.from_user.id}"] = True
            await query.edit_message_text(
                "📧 *CHANGE EMAIL*\n\n"
                "Please enter your new email address:\n\n"
                "*Format:* your.email@example.com\n"
                "*Example:* john.doe@example.com\n\n"
                "Enter new email:",
                parse_mode="Markdown"
            )
        
        elif field == 'password':
            if 'user_id' not in context.user_data:
                await query.edit_message_text("🔒 Please login first: `/login`", parse_mode="Markdown")
                return
            
            context.user_data[f"change_password_{query.from_user.id}"] = True
            await query.edit_message_text(
                "🔐 *CHANGE PASSWORD*\n\n"
                "Please enter your current password:",
                parse_mode="Markdown"
            )
    
    elif query.data == 'cancel_edit':
        await query.edit_message_text("❌ Profile edit cancelled.", parse_mode="Markdown")
    
    # Admin callbacks
    elif query.data.startswith('admin_'):
        admin_action = query.data.replace('admin_', '')
        
        if admin_action == 'list_users':
            await admin_users_command(update, context)
        elif admin_action == 'search_user':
            context.user_data[f"admin_search_{query.from_user.id}"] = True
            await query.edit_message_text(
                "🔍 *SEARCH USER*\n\n"
                "Please enter search query (username, name, email, or ID):",
                parse_mode="Markdown"
            )
        elif admin_action == 'delete_user':
            context.user_data[f"admin_delete_{query.from_user.id}"] = True
            await query.edit_message_text(
                "🗑️ *DELETE USER*\n\n"
                "Please enter user ID to delete:",
                parse_mode="Markdown"
            )
        elif admin_action == 'reset_password':
            context.user_data[f"admin_reset_{query.from_user.id}"] = True
            await query.edit_message_text(
                "🔄 *RESET PASSWORD*\n\n"
                "Please enter user ID to reset password:",
                parse_mode="Markdown"
            )
        elif admin_action == 'ban_user':
            context.user_data[f"admin_ban_{query.from_user.id}"] = True
            await query.edit_message_text(
                "🔒 *BAN/UNBAN USER*\n\n"
                "Please enter user ID to ban/unban:\n\n"
                "*Format:* `<user_id> <ban/unban>`\n"
                "*Example:* `123456789 ban`",
                parse_mode="Markdown"
            )
        elif admin_action == 'user_stats':
            await admin_command(update, context)
    
    elif query.data == 'help':
        await help_command(update, context)
    
    elif query.data == 'about':
        await about_command(update, context)
    
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
        
        # Check if user is in a chat room
        if user.id in chat_manager.user_chats:
            chat_id = chat_manager.user_chats[user.id]
            chat_info = chat_manager.active_chats.get(chat_id, {})
            
            # Send message to chat room
            chat_manager.send_message(chat_id, user.id, user_message)
            
            # Forward message to all users in chat room
            users = chat_info.get('users', [])
            sender_prefix = "👑 " if user.id == chat_info.get('admin') else "👤 "
            
            for u_id in users:
                if u_id != user.id:  # Don't send to self
                    try:
                        await context.bot.send_message(
                            chat_id=u_id,
                            text=f"{sender_prefix}*{user.first_name}:*\n{user_message}",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to forward chat message: {e}")
            
            return  # Don't process as normal message
        
        # Session verification
        if 'session_id' in context.user_data:
            session_id = context.user_data['session_id']
            user_data, message = user_db.verify_session(session_id)
            if user_data:
                context.user_data.update(user_data)
        
        # Guest tracking and reminders
        if 'user_id' not in context.user_data:
            should_remind, reminder_type = user_db.track_guest_activity(user.id)
            
            if should_remind and reminder_type in ['first', 'followup']:
                stats = get_enhanced_stats()
                reminder = random.choice(GUEST_REMINDERS[reminder_type])
                
                # Get message count
                conn = sqlite3.connect(user_db.db_file)
                cursor = conn.cursor()
                cursor.execute('SELECT message_count FROM guest_tracking WHERE telegram_id = ?', (user.id,))
                result = cursor.fetchone()
                message_count = result[0] if result else 0
                conn.close()
                
                # Format reminder
                reminder = reminder.format(
                    total_users=stats['total_users'],
                    count=message_count
                )
                
                keyboard = [
                    [InlineKeyboardButton("📝 Register Now", callback_data='register'),
                     InlineKeyboardButton("🔐 Login", callback_data='login')],
                    [InlineKeyboardButton("💡 See Benefits", callback_data='help'),
                     InlineKeyboardButton("❌ Dismiss", callback_data='dismiss_reminder')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(reminder, parse_mode="Markdown", reply_markup=reply_markup)
        
        # Check for custom donation amount
        if context.user_data.get(f"waiting_custom_{user.id}"):
            context.user_data.pop(f"waiting_custom_{user.id}", None)
            
            try:
                amount = float(user_message)
                if amount < 1:
                    await update.message.reply_text("❌ Minimum donation is $1. Please enter a valid amount.")
                    return
                
                context.user_data[f"selected_amount_{user.id}"] = amount
                
                payment_text = f"""
✅ *Selected: ${amount:.2f}*

Now choose your payment method:

1. **PayPal** - Secure payment with card or PayPal balance
2. **Buy Me Coffee** - Simple one-click donation

*After payment, click "✅ I've Paid" below and send your Transaction ID.*
"""
                
                keyboard = [
                    [InlineKeyboardButton("💳 PayPal Payment", url='https://www.paypal.com/ncp/payment/HCPVDSSXRL4K8'),
                     InlineKeyboardButton("☕ Buy Me Coffee", url='https://www.buymeacoffee.com/StarAI')],
                    [InlineKeyboardButton("✅ I've Paid", callback_data='i_donated'),
                     InlineKeyboardButton("🔙 Change Amount", callback_data='donate')]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(payment_text, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=True)
                return
                
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please enter a number (like 5 or 10.50).")
                return
        
        # Check for transaction ID
        if user_message.startswith('TXID') or user_message.startswith('BMC-'):
            if 'user_id' in context.user_data:
                user_id = context.user_data['user_id']
                amount = context.user_data.get(f"selected_amount_{user.id}", 0)
                
                if amount > 0:
                    success = user_db.add_donation(
                        user_id=user_id,
                        username=user.username or "No username",
                        first_name=user.first_name,
                        amount=amount,
                        transaction_id=user_message
                    )
                    
                    if success:
                        response = f"""
✅ *DONATION RECORDED!*

*Amount:* ${amount:.2f}
*Transaction ID:* {user_message}
*Date:* {datetime.now().strftime('%Y-%m-%d %H:%M')}

*Status:* ⏳ **Pending Verification**

*What's next:*
1. Your donation is now recorded
2. It will be verified manually
3. You'll get supporter status once verified

*Thank you for supporting StarAI!* 💝

Use `/mydonations` to check your status.
"""
                        context.user_data.pop(f"selected_amount_{user.id}", None)
                    else:
                        response = "❌ Error recording donation. Please try again."
                    
                    await update.message.reply_text(response, parse_mode="Markdown")
                    return
        
        # Handle support type messages
        if context.user_data.get(f"support_type_{user.id}"):
            issue_type = context.user_data.pop(f"support_type_{user.id}")
            issue_types = {
                'password': "Password Reset/Login Issue",
                'account': "Account Management Issue",
                'donation': "Donation/Payment Issue",
                'bug': "Bug Report/Technical Issue",
                'other': "Other Issue"
            }
            
            full_issue = f"[{issue_types[issue_type]}] {user_message}"
            await create_support_ticket_with_notification(update, context, user, full_issue)
            return
        
        # Handle chat room creation
        if context.user_data.get(f"waiting_chat_name_{user.id}"):
            chat_name = user_message
            context.user_data.pop(f"waiting_chat_name_{user.id}", None)
            
            chat_id = chat_manager.create_chat_room(user.id, chat_name)
            
            await update.message.reply_text(
                f"✅ *CHAT ROOM CREATED!*\n\n"
                f"*Name:* {chat_name}\n"
                f"*Room Code:* `{chat_id}`\n\n"
                f"*Share this code with others:*\n"
                f"`/chatroom join {chat_id}`\n\n"
                f"*Participants:*\n"
                f"👑 {user.first_name} (Admin)\n\n"
                f"*Start chatting by sending messages!* 💬",
                parse_mode="Markdown"
            )
            return
        
        # Handle chat room join
        if context.user_data.get(f"waiting_chat_code_{user.id}"):
            chat_id = user_message.strip()
            context.user_data.pop(f"waiting_chat_code_{user.id}", None)
            
            if chat_manager.add_user_to_chat(chat_id, user.id):
                chat_info = chat_manager.active_chats.get(chat_id, {})
                users = chat_info.get('users', [])
                
                # Notify all users in chat
                for u_id in users:
                    if u_id != user.id:
                        try:
                            await context.bot.send_message(
                                chat_id=u_id,
                                text=f"👋 *{user.first_name} has joined the chat!*"
                            )
                        except:
                            pass
                
                await update.message.reply_text(
                    f"✅ *JOINED CHAT ROOM!*\n\n"
                    f"*Room:* {chat_info.get('name', 'Unknown')}\n"
                    f"*Participants:* {len(users)} users\n\n"
                    f"*You can now chat with everyone in this room!*\n\n"
                    f"Send messages normally to chat. Type `/chatroom leave` to exit.",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("❌ Invalid chat room code", parse_mode="Markdown")
            return
        
        # Handle profile editing
        if context.user_data.get(f"waiting_new_name_{user.id}"):
            new_name = user_message
            context.user_data.pop(f"waiting_new_name_{user.id}", None)
            
            if 'user_id' in context.user_data:
                user_id = context.user_data['user_id']
                name_parts = new_name.split()
                
                if len(name_parts) < 2:
                    await update.message.reply_text(
                        "❌ Please enter both first and last name.\n"
                        "*Example:* John Doe",
                        parse_mode="Markdown"
                    )
                    return
                
                success = user_db.update_user_profile(user_id, 'first_name', name_parts[0])
                if len(name_parts) > 1:
                    user_db.update_user_profile(user_id, 'last_name', ' '.join(name_parts[1:]))
                
                if success:
                    context.user_data['first_name'] = name_parts[0]
                    await update.message.reply_text(f"✅ Name updated to: {new_name}", parse_mode="Markdown")
                else:
                    await update.message.reply_text("❌ Failed to update name", parse_mode="Markdown")
            return
        
        if context.user_data.get(f"waiting_new_phone_{user.id}"):
            new_phone = user_message
            context.user_data.pop(f"waiting_new_phone_{user.id}", None)
            
            if 'user_id' in context.user_data:
                user_id = context.user_data['user_id']
                
                if re.match(r'^\+?[1-9]\d{1,14}$', new_phone):
                    success = user_db.update_user_profile(user_id, 'phone', new_phone)
                    if success:
                        await update.message.reply_text(f"✅ Phone updated to: {new_phone}", parse_mode="Markdown")
                    else:
                        await update.message.reply_text("❌ Failed to update phone", parse_mode="Markdown")
                else:
                    await update.message.reply_text(
                        "❌ Invalid phone format.\n"
                        "*Format:* +1234567890",
                        parse_mode="Markdown"
                    )
            return
        
        if context.user_data.get(f"waiting_new_email_{user.id}"):
            new_email = user_message
            context.user_data.pop(f"waiting_new_email_{user.id}", None)
            
            if 'user_id' in context.user_data:
                user_id = context.user_data['user_id']
                
                if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', new_email):
                    success = user_db.update_user_profile(user_id, 'email', new_email)
                    if success:
                        await update.message.reply_text(f"✅ Email updated to: {new_email}", parse_mode="Markdown")
                    else:
                        await update.message.reply_text("❌ Failed to update email", parse_mode="Markdown")
                else:
                    await update.message.reply_text(
                        "❌ Invalid email format.\n"
                        "*Format:* your.email@example.com",
                        parse_mode="Markdown"
                    )
            return
        
        # Handle password change
        if context.user_data.get(f"change_password_{user.id}"):
            if 'user_id' not in context.user_data:
                context.user_data.pop(f"change_password_{user.id}", None)
                await update.message.reply_text("🔒 Please login first: `/login`", parse_mode="Markdown")
                return
            
            if 'current_password' not in context.user_data:
                # This is the current password
                current_password = user_message
                context.user_data['current_password'] = current_password
                await update.message.reply_text(
                    "🔐 *NEW PASSWORD*\n\n"
                    "Now enter your new password (minimum 6 characters):",
                    parse_mode="Markdown"
                )
                return
            else:
                # This is the new password
                new_password = user_message
                current_password = context.user_data.pop('current_password')
                context.user_data.pop(f"change_password_{user.id}", None)
                
                user_id = context.user_data['user_id']
                success, message = user_db.change_user_password(user_id, current_password, new_password)
                
                if success:
                    await update.message.reply_text(f"✅ {message}", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ {message}", parse_mode="Markdown")
                return
        
        # Handle password reset
        if context.user_data.get(f"reset_in_progress_{user.id}"):
            new_password = user_message
            reset_token = context.user_data.get(f"reset_token_{user.id}")
            
            if len(new_password) < 6:
                await update.message.reply_text(
                    "❌ Password must be at least 6 characters.\n"
                    "Please enter a new password:",
                    parse_mode="Markdown"
                )
                return
            
            telegram_id, message = user_db.verify_reset_token(reset_token)
            
            if telegram_id:
                success, message = user_db.reset_password(telegram_id, new_password)
                context.user_data.pop(f"reset_in_progress_{user.id}", None)
                context.user_data.pop(f"reset_token_{user.id}", None)
                
                if success:
                    await update.message.reply_text(
                        f"✅ *PASSWORD RESET SUCCESSFUL!*\n\n"
                        f"You can now login with your new password:\n"
                        f"`/login {new_password}`",
                        parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text(f"❌ {message}", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ {message}", parse_mode="Markdown")
            return
        
        # Handle admin actions
        admin_ids = [admin_id.strip() for admin_id in ADMIN_IDS if admin_id.strip()]
        if str(user.id) in admin_ids:
            if context.user_data.get(f"admin_search_{user.id}"):
                search_query = user_message
                context.user_data.pop(f"admin_search_{user.id}", None)
                
                try:
                    conn = sqlite3.connect(user_db.db_file)
                    cursor = conn.cursor()
                    
                    cursor.execute('''
                        SELECT id, telegram_id, username, first_name, email, created_at
                        FROM users 
                        WHERE username LIKE ? OR first_name LIKE ? OR email LIKE ? OR telegram_id = ?
                        ORDER BY created_at DESC 
                        LIMIT 10
                    ''', (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", search_query))
                    
                    users = cursor.fetchall()
                    conn.close()
                    
                    if not users:
                        await update.message.reply_text(f"❌ No users found for '{search_query}'", parse_mode="Markdown")
                    else:
                        response = f"🔍 *SEARCH RESULTS: '{search_query}'*\n\n"
                        for i, user_data in enumerate(users, 1):
                            user_id, telegram_id, username, first_name, email, created_at = user_data
                            
                            response += f"*{i}. {first_name}*"
                            if username:
                                response += f" (@{username})"
                            
                            response += f"\n   ├─ ID: `{user_id}`"
                            response += f"\n   ├─ Telegram: `{telegram_id}`"
                            if email:
                                response += f"\n   ├─ Email: {email}"
                            response += f"\n   └─ Joined: {created_at[:10]}\n\n"
                        
                        await update.message.reply_text(response, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Admin search error: {e}")
                    await update.message.reply_text("❌ Error searching users.", parse_mode="Markdown")
                return
            
            if context.user_data.get(f"admin_delete_{user.id}"):
                try:
                    target_user_id = int(user_message)
                    success, message = user_db.delete_user(target_user_id)
                    context.user_data.pop(f"admin_delete_{user.id}", None)
                    await update.message.reply_text(f"{'✅' if success else '❌'} {message}", parse_mode="Markdown")
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")
                return
            
            if context.user_data.get(f"admin_reset_{user.id}"):
                try:
                    target_user_id = int(user_message)
                    success, message = user_db.admin_reset_password(target_user_id)
                    context.user_data.pop(f"admin_reset_{user.id}", None)
                    await update.message.reply_text(f"{'✅' if success else '❌'} {message}", parse_mode="Markdown")
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")
                return
            
            if context.user_data.get(f"admin_ban_{user.id}"):
                parts = user_message.split()
                if len(parts) < 1:
                    await update.message.reply_text("❌ Please enter user ID and action (ban/unban)", parse_mode="Markdown")
                    return
                
                try:
                    target_user_id = int(parts[0])
                    action = parts[1] if len(parts) > 1 else "ban"
                    success, message = user_db.ban_user(target_user_id, action)
                    context.user_data.pop(f"admin_ban_{user.id}", None)
                    await update.message.reply_text(f"{'✅' if success else '❌'} {message}", parse_mode="Markdown")
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", parse_mode="Markdown")
                return
        
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        if 'user_id' in context.user_data:
            user_db.update_user_stats(context.user_data['user_id'], 'total_messages')
            user_db.update_user_stats(context.user_data['user_id'], 'commands_used')
        
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
        
        # AI chat
        if 'user_id' in context.user_data:
            user_db.update_user_stats(context.user_data['user_id'], 'ai_chats')
        
        if client:
            conversation = get_user_conversation(user.id)
            conversation.append({"role": "user", "content": user_message})
            
            response = client.chat.completions.create(
                messages=conversation,
                model="llama-3.1-8b-instant",
                temperature=0.8,
                max_tokens=600
            )
            
            ai_response = response.choices[0].message.content
            update_conversation(user.id, "assistant", ai_response)
            await update.message.reply_text(ai_response, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                """🤖 *AI Chat Currently Unavailable*

I can still help you with:
🎨 `/image <description>` - Create images
🎵 `/music <song>` - Find music
😂 `/joke` - Get a laugh
💡 `/fact` - Learn something new
💰 `/donate` - Support this bot

*Try these commands instead!* 😊""",
                parse_mode="Markdown"
            )
        
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text(
            "❌ *Error occurred.*\n\nTry:\n• `/help` for commands\n• Rephrase your message",
            parse_mode="Markdown"
        )

# ========================
# MAIN FUNCTION
# ========================
def main():
    print("=" * 60)
    print("🌟 STARAI - COMPLETE BOT WITH ALL FEATURES")
    print("=" * 60)
    
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN not found!")
        print("Set in Heroku: Settings → Config Vars → Add TELEGRAM_TOKEN")
        return
    
    if not GROQ_API_KEY:
        print("⚠️ WARNING: GROQ_API_KEY missing - AI chat limited")
    else:
        print("✅ Groq AI: Enabled")
    
    print("✅ Telegram Bot: Ready")
    print("👤 Enhanced Account System: Password + Phone + Email")
    print("🔐 Password Reset & Support System")
    print("💬 Admin-User Direct Messaging")
    print("📊 Enhanced Statistics Display (200k+ users)")
    print("🎨 Image generation: Pollinations.ai + Craiyon")
    print("🎵 Music search: YouTube")
    print("💰 Donation system: With working buttons")
    print("👑 Full Admin Commands")
    print("💬 Chat Room System")
    print("👤 Guest Registration Reminders")
    print("⚙️ Profile Editing System")
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
        
        admin_commands = [
            ("admin", admin_command),
            ("adminusers", admin_users_command),
            ("reply", reply_command),
            ("adminsupport", admin_support_command),
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
        print("👤 Users can: Register, Login, Reset Password, Edit Profile")
        print("🆘 Support System: Tickets & Admin Messaging with notifications")
        print("📊 Enhanced Stats: Shows 200k+ users community")
        print("💰 Donation Buttons: Now working properly")
        print("👑 Admin Features: Delete users, reset passwords, ban users")
        print("💬 Chat Rooms: Real-time group chat capability")
        print("👤 Guest Reminders: Convincing messages to register")
        print("🔧 Send /start to begin")
        print("=" * 60)
        
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Failed to start: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
