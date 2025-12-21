import os
import time
import asyncio
import sqlite3
import yt_dlp
import uuid
import logging
import threading
import shutil
import math
import requests
import subprocess
import re
from datetime import datetime
from flask import Flask
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters, enums, errors
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- 1. WEB SERVER (For 24/7 Uptime) ---
web_app = Flask('')

@web_app.route('/')
def home():
    return "Universal Bot Active 24/7! 🚀"

def run_web():
    web_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.start()

def ping_self():
    while True:
        try:
            time.sleep(600) # Ping every 10 minutes
            requests.get("http://localhost:8080/")
            logging.info("Ping sent to keep bot alive!")
        except: pass

def start_pinger():
    t = threading.Thread(target=ping_self)
    t.start()

# --- 2. CONFIGURATION (Safe Load) ---
def get_env(name, default=None):
    val = os.environ.get(name)
    if not val or val.strip() == "":
        return default
    return val

API_ID = int(get_env("API_ID", 0))
API_HASH = get_env("API_HASH", "")
BOT_TOKEN = get_env("BOT_TOKEN", "")

# Admin & Owner Config
OWNER_IDS = [int(x) for x in get_env("OWNER_IDS", "").split() if x.strip()]
ADMIN_IDS = [int(x) for x in get_env("ADMIN_IDS", "").split() if x.strip()]
ADMINS = list(set(ADMIN_IDS + OWNER_IDS))

# Channels & Branding
FORCE_SUB = get_env("FORCE_SUB_CHANNEL")
# LOG_CHANNEL fix (handle 0 or None)
LOG_CHANNEL_STR = get_env("LOG_CHANNEL", "0")
LOG_CHANNEL = int(LOG_CHANNEL_STR) if LOG_CHANNEL_STR.lstrip('-').isdigit() else 0

AUTHOR_NAME = "@hdhub4uumss"
AUTHOR_URL = "https://t.me/hdhub4uumss"

# Settings
DOWNLOAD_DIR = "/app/downloads"
COOKIES_FILE = "cookie (1).txt"

# --- 3. LOGGING & CLIENT ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Check Cookie
if not os.path.exists(COOKIES_FILE):
    logger.warning(f"⚠️ ERROR: {COOKIES_FILE} not found! Upload it to GitHub.")
else:
    logger.info(f"✅ Cookies File Found: {COOKIES_FILE}")

app = Client("universal_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global State
user_data = {} 
user_cooldowns = {}
COOLDOWN_SECONDS = 30 # Reduced cooldown

# --- 4. DATABASE (SQLite) ---
DB_NAME = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    try:
        conn.cursor().execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
    except: pass
    conn.close()

def is_banned(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    res = conn.cursor().execute("SELECT user_id FROM banned WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return res is not None

def ban_user_db(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.cursor().execute("INSERT OR REPLACE INTO banned (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def unban_user_db(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.cursor().execute("DELETE FROM banned WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    users = [row[0] for row in conn.cursor().execute("SELECT user_id FROM users").fetchall()]
    conn.close()
    return users

# --- 5. HELPER FUNCTIONS ---

def humanbytes(size):
    if not size: return "0B"
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN.get(n, '') + 'B'

def time_formatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((str(days) + "d, ") if days else "") + \
          ((str(hours) + "h, ") if hours else "") + \
          ((str(minutes) + "m, ") if minutes else "") + \
          ((str(seconds) + "s") if seconds else "")
    return tmp[:-2] if tmp.endswith(", ") else tmp

async def progress_bar(current, total, message, start_time, status_text):
    try:
        now = time.time()
        diff = now - start_time
        if round(diff % 5.00) == 0 or current == total:
            speed = current / diff if diff > 0 else 0
            percentage = current * 100 / total
            eta = (total - current) / speed if speed > 0 else 0
            
            bar_len = 10
            filled = int(percentage / 100 * bar_len)
            bar = '⬢' * filled + '⬡' * (bar_len - filled)
            
            msg = (
                f"**{status_text}**\n\n"
                f"**Progress:** `{bar}` {round(percentage, 1)}%\n"
                f"**Processed:** `{humanbytes(current)}` / `{humanbytes(total)}`\n"
                f"**Speed:** `{humanbytes(speed)}/s`\n"
                f"**ETA:** `{time_formatter(eta * 1000)}`"
            )
            await message.edit(msg)
    except: pass

async def handle_force_sub(client, message):
    if not FORCE_SUB: return True
    user_id = message.from_user.id
    if user_id in ADMINS: return True
    
    try:
        chat_id = int(FORCE_SUB) if str(FORCE_SUB).startswith("-100") else FORCE_SUB
        await client.get_chat_member(chat_id, user_id)
        return True
    except errors.UserNotParticipant:
        try:
            chat_id = int(FORCE_SUB) if str(FORCE_SUB).startswith("-100") else FORCE_SUB
            invite = await client.export_chat_invite_link(chat_id)
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Update Channel", url=invite)]])
            await message.reply(
                f"👋 **Hello {message.from_user.mention}!**\n\n"
                "Please join our update channel to use this bot.\n"
                "This is required to keep the bot free for everyone.",
                reply_markup=btn
            )
            return False
        except: return True
    except: return True

def get_metadata(file_path):
    try:
        metadata = extractMetadata(createParser(file_path))
        width = metadata.get("width") if metadata.has("width") else 0
        height = metadata.get("height") if metadata.has("height") else 0
        duration = metadata.get("duration").seconds if metadata.has("duration") else 0
        return width, height, duration
    except: return 0, 0, 0

def prepare_thumbnail(thumb_path):
    if not thumb_path or not os.path.exists(thumb_path): return None
    try:
        img = Image.open(thumb_path)
        img.thumbnail((320, 320))
        img.save(thumb_path, "JPEG")
        return thumb_path
    except: return None

# --- 6. COMMANDS (User & Admin) ---

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    add_user(message.from_user.id)
    if not await handle_force_sub(client, message): return
    
    txt = (
        f"👋 **Hello {message.from_user.mention}!**\n\n"
        "I am an **Universal File Downloader Bot**.\n"
        "I can download content from **YouTube, Instagram, Facebook, TikTok, Twitter**, and direct links.\n\n"
        "**Features:**\n"
        "✅ High Speed Downloads\n"
        "✅ Custom Metadata Support\n"
        "✅ Ad-Bypass Technology\n"
        "✅ 4K & MKV Support\n\n"
        f"👤 **Author:** [{AUTHOR_NAME}]({AUTHOR_URL})"
    )
    
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Help", callback_data="help_menu"),
         InlineKeyboardButton("ℹ️ About", callback_data="about_menu")]
    ])
    await message.reply(txt, quote=True, reply_markup=btns, disable_web_page_preview=True)

@app.on_callback_query(filters.regex("help_menu"))
async def help_callback(client, callback):
    txt = (
        "📚 **Help Menu**\n\n"
        "**How to use:**\n"
        "Simply send any link (YouTube, Insta, etc.) to the bot.\n\n"
        "**Commands:**\n"
        "• `/start` - Restart Bot\n"
        "• `/dl <link>` - Force Download\n"
        "• `/cancel` - Cancel current task\n\n"
        "**Supported:** 1000+ Websites\n"
        "**Bot Author:** " + AUTHOR_NAME
    )
    await callback.message.edit(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="start_menu")]]))

@app.on_callback_query(filters.regex("start_menu"))
async def back_to_start(client, callback):
    # Same as start command text
    txt = f"👋 **Hello {callback.from_user.mention}!**\n\nReady to download files."
    btns = InlineKeyboardMarkup([[InlineKeyboardButton("📚 Help", callback_data="help_menu")]])
    await callback.message.edit(txt, reply_markup=btns)

# --- ADMIN COMMANDS ---
@app.on_message(filters.command("broadcast") & filters.user(OWNERS))
async def broadcast_handler(client, message):
    if not message.reply_to_message:
        return await message.reply("❌ **Error:** Please reply to a message to broadcast.")
    
    status = await message.reply("🚀 **Processing Broadcast...**")
    users = get_all_users()
    done, blocked = 0, 0
    
    for uid in users:
        try:
            await message.reply_to_message.copy(uid)
            done += 1
            await asyncio.sleep(0.1)
        except errors.FloodWait as e:
            await asyncio.sleep(e.value)
            try: await message.reply_to_message.copy(uid); done+=1
            except: blocked+=1
        except: blocked+=1
            
    await status.edit(f"✅ **Broadcast Completed**\n\n📢 Sent: `{done}`\n🚫 Failed: `{blocked}`")

@app.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_handler(client, message):
    try:
        uid = int(message.command[1])
        if uid in ADMINS: return await message.reply("❌ Cannot ban an Admin.")
        ban_user_db(uid)
        await message.reply(f"🚫 **User {uid} has been Banned.**")
    except: await message.reply("❌ **Usage:** `/ban <user_id>`")

@app.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_handler(client, message):
    try:
        uid = int(message.command[1])
        unban_user_db(uid)
        await message.reply(f"✅ **User {uid} has been Unbanned.**")
    except: await message.reply("❌ **Usage:** `/unban <user_id>`")

@app.on_message(filters.command("log") & filters.user(OWNERS))
async def log_cmd(client, message):
    if os.path.exists(DB_NAME): await message.reply_document(DB_NAME)
    else: await message.reply("No Database Found.")

@app.on_message(filters.command("addadmin") & filters.user(OWNERS))
async def add_admin(client, message):
    await message.reply("ℹ️ To add Admins, please add their IDs to the `ADMIN_IDS` variable in Render settings.", quote=True)

# --- 6. UNIVERSAL DOWNLOADER LOGIC ---

@app.on_message(filters.regex(r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+") | filters.command(["dl", "download"]))
async def link_handler(client, message):
    user_id = message.from_user.id
    add_user(user_id)
    if is_banned(user_id): return
    if not await handle_force_sub(client, message): return
    
    url = message.text
    if message.command and len(message.command) > 1: url = message.command[1]
    
    req_id = str(uuid.uuid4())[:8]
    user_data[req_id] = {"url": url, "uid": user_id}
    
    auth_status = "✅ Cookies" if COOKIES_PATH else "⚠️ No Auth"
    
    # PROFESSIONAL BUTTONS LAYOUT
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Leech (Video)", callback_data=f"dl|leech|{req_id}"),
         InlineKeyboardButton("📂 Mirror (Doc)", callback_data=f"dl|mirror|{req_id}")],
        [InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl|audio|{req_id}"),
         InlineKeyboardButton("❌ Cancel", callback_data=f"dl|cancel|{req_id}")]
    ])
    
    await message.reply(
        f"🔗 **Link Received**\n`{url}`\n\n🛡️ **Status:** {auth_status}\n👇 **Select Action:**",
        reply_markup=btns,
        quote=True,
        disable_web_page_preview=True
    )

@app.on_callback_query(filters.regex(r"^dl\|"))
async def process_dl(client, callback):
    _, action, req_id = callback.data.split("|")
    
    if req_id not in user_data:
        return await callback.answer("❌ Task Expired.", show_alert=True)
    
    if user_data[req_id]['uid'] != callback.from_user.id:
        return await callback.answer("❌ Not your task!", show_alert=True)

    if action == "cancel":
        del user_data[req_id]
        await callback.message.delete()
        return

    await callback.message.delete()
    status = await callback.message.reply("🔄 **Processing Request...**")
    
    url = user_data[req_id]['url']
    user_dir = f"{DOWNLOAD_DIR}/{callback.from_user.id}"
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    
    # Universal YT-DLP Options
    ydl_opts = {
        'outtmpl': f"{user_dir}/{req_id}_%(title)s.%(ext)s",
        'quiet': True,
        'nocheckcertificate': True,
        'writethumbnail': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    }
    
    # Format Selection
    if action == "audio":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}]
    else:
        # Best Video + Best Audio -> MKV
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mkv' # Safest container

    if COOKIES_PATH: ydl_opts['cookiefile'] = COOKIES_PATH

    try:
        await status.edit("⬇️ **Downloading...**\n`Connecting to Source...`")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fpath = ydl.prepare_filename(info)
            base = fpath.rsplit(".", 1)[0]
            
            # Smart Ext Check
            if not os.path.exists(fpath):
                for ext in [".mkv", ".mp4", ".webm", ".mp3", ".m4a"]:
                    if os.path.exists(base + ext):
                        fpath = base + ext
                        break
            
            # Clean Title
            clean_title = info.get('title', 'Video').replace("_", " ")
            
            # Metadata Injection
            if action != "audio":
                await status.edit(f"🏷️ **Injecting Metadata...**\n`{AUTHOR_NAME}`")
                temp_out = f"{base}_meta.mkv"
                cmd = ["ffmpeg", "-y", "-i", fpath, "-c", "copy",
                       "-metadata", f"title={clean_title}",
                       "-metadata", f"artist={AUTHOR_NAME}",
                       "-metadata", f"author={AUTHOR_NAME}",
                       temp_out]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(temp_out):
                    os.remove(fpath)
                    os.rename(temp_out, fpath)

            await status.edit("⬆️ **Uploading...**")
            start = time.time()
            thumb = base + ".jpg"
            if not os.path.exists(thumb): thumb = base + ".webp"
            final_thumb = prepare_thumbnail(thumb)
            
            w, h, d = 0, 0, 0
            if action != "audio":
                w, h, d = get_metadata(fpath)
            if d == 0: d = info.get('duration', 0)
            
            caption = f"🎥 **{clean_title}**\n\n👤 **Uploaded By:** {AUTHOR_NAME}\n⚙️ **Source:** Universal"
            
            if action == "audio":
                await app.send_audio(
                    callback.message.chat.id, audio=fpath, title=clean_title, 
                    thumb=final_thumb, performer=AUTHOR_NAME, caption=caption,
                    progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading Audio...**")
                )
            elif action == "mirror": # Document Mode
                await app.send_document(
                    callback.message.chat.id, document=fpath, thumb=final_thumb, caption=caption,
                    progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading Doc...**")
                )
            else: # Leech Mode (Video)
                await app.send_video(
                    callback.message.chat.id, video=fpath, caption=caption,
                    duration=int(d), width=int(w), height=int(h), thumb=final_thumb,
                    supports_streaming=True,
                    progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading Video...**")
                )
                
            await status.delete()
            await callback.message.reply_text("✅ **Completed Successfully!**")

    except Exception as e:
        await status.edit(f"❌ **Error:** `{str(e)[:200]}`")
    
    try: shutil.rmtree(user_dir)
    except: pass
    if req_id in user_data: del user_data[req_id]

# --- 7. STARTUP ---
if __name__ == "__main__":
    init_db()
    if not os.path.exists("downloads"): os.makedirs("downloads")
    
    keep_alive()   
    start_pinger() 
    
    print("🔥 Universal Bot Started...")
    try:
        app.run()
    except errors.FloodWait as e:
        print(f"❌ FloodWait: {e.value}s. Sleeping...")
        time.sleep(e.value)
        app.run()


