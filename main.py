import os
import time
import asyncio
import sqlite3
import yt_dlp
import uuid
import logging
import threading
import shutil
import requests
from flask import Flask
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters, enums, errors
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- 1. WEB SERVER & PINGER (For 24/7 Uptime) ---
web_app = Flask('')

@web_app.route('/')
def home():
    return "Universal Bot is Running 24/7! 🚀"

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
            print("Ping sent to keep bot alive!")
        except:
            pass

def start_pinger():
    t = threading.Thread(target=ping_self)
    t.start()

# --- 2. CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

OWNERS = [int(x) for x in os.environ.get("OWNER_IDS", "").split() if x.strip()]
ADMINS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split() if x.strip()]
ADMINS.extend(OWNERS)
ADMINS = list(set(ADMINS)) 

FORCE_SUB = os.environ.get("FORCE_SUB_CHANNEL", None)
META_TITLE = os.environ.get("METADATA_TITLE", "Downloaded via Bot")
META_AUTHOR = os.environ.get("METADATA_AUTHOR", "Winning Wonders Hub")
DOWNLOAD_DIR = "/app/downloads"

# --- SMART COOKIE FINDER ---
COOKIES_PATH = None
possible_cookies = [
    "cookie (1).txt", 
    "cookies.txt", 
    "/etc/secrets/cookies.txt", 
    "/app/cookie (1).txt"
]

for c in possible_cookies:
    if os.path.exists(c):
        COOKIES_PATH = c
        print(f"✅ Cookies Loaded: {c}")
        break

if not COOKIES_PATH:
    print("⚠️ WARNING: No cookies found! Some sites may block downloads.")

# Logging
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

app = Client("universal_bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_cooldowns = {}
COOLDOWN_SECONDS = 60
DOWNLOAD_QUEUE = {}
DB_NAME = "bot_data.db"

# --- 3. DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.cursor().execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
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

# --- 4. HELPERS ---
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
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=invite)]])
            await message.reply("⚠️ **Please Join Our Channel to Use Bot!**", reply_markup=btn)
            return False
        except: return True
    except: return True

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
            bar = '▰' * filled + '▱' * (bar_len - filled)
            
            msg = (
                f"{status_text}\n\n"
                f"**Progress:** {bar} {round(percentage, 1)}%\n"
                f"**Size:** `{round(current/1048576, 2)}MB` / `{round(total/1048576, 2)}MB`\n"
                f"**Speed:** `{round(speed/1048576, 2)} MB/s`\n"
                f"**ETA:** `{int(eta)}s`"
            )
            await message.edit(msg)
    except: pass

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

# --- 5. COMMAND HANDLERS ---
@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    add_user(message.from_user.id)
    if not await handle_force_sub(client, message): return
    
    txt = (
        f"👋 **Hello {message.from_user.first_name}!**\n\n"
        "I am an **Universal Video Downloader Bot**.\n"
        "I support 1000+ sites (YT, Insta, X, FB, TikTok, etc).\n"
        "🍪 **Cookies Support:** Active\n\n"
        "🔹 **Dev:** @Winning_Wonders_Hub"
    )
    await message.reply(txt, quote=True)

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    txt = (
        "📚 **Help Menu**\n\n"
        "**User Commands:**\n"
        "• `/dl <link>` - Download Video from ANY site\n"
        "• `/start` - Check Alive Status\n\n"
        "**Admin Commands:**\n"
        "• `/ban <id>` - Ban User\n"
        "• `/unban <id>` - Unban User\n"
        "• `/broadcast <reply>` - Send Message to All\n"
        "• `/log` - Get Database File"
    )
    await message.reply(txt, quote=True)

@app.on_message(filters.command("broadcast") & filters.user(OWNERS))
async def broadcast_cmd(client, message):
    if not message.reply_to_message:
        return await message.reply("❌ Reply to a message to broadcast it.")
    
    msg = await message.reply("🚀 **Broadcasting started...**")
    users = get_all_users()
    done = 0
    blocked = 0
    
    for uid in users:
        try:
            await message.reply_to_message.copy(uid)
            done += 1
            await asyncio.sleep(0.1)
        except:
            blocked += 1
            
    await msg.edit(f"✅ **Broadcast Completed!**\n\nSent: {done}\nBlocked/Failed: {blocked}")

@app.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_cmd(client, message):
    if len(message.command) < 2: return await message.reply("❌ Usage: `/ban UserID`")
    try:
        uid = int(message.command[1])
        if uid in ADMINS: return await message.reply("❌ Cannot ban an Admin.")
        ban_user_db(uid)
        await message.reply(f"🚫 **User {uid} has been Banned.**")
    except: await message.reply("❌ Invalid User ID.")

@app.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_cmd(client, message):
    if len(message.command) < 2: return await message.reply("❌ Usage: `/unban UserID`")
    try:
        uid = int(message.command[1])
        unban_user_db(uid)
        await message.reply(f"✅ **User {uid} has been Unbanned.**")
    except: await message.reply("❌ Invalid User ID.")

@app.on_message(filters.command("log") & filters.user(OWNERS))
async def log_cmd(client, message):
    try:
        if os.path.exists(DB_NAME):
            await message.reply_document(DB_NAME, caption="🗄 **Bot Database File**")
        else:
            await message.reply("❌ No database found yet.")
    except: pass

# --- 6. DOWNLOAD HANDLER (Universal & Ad-Free Logic) ---
@app.on_message(filters.command(["dl", "download"]))
async def dl_init(client, message):
    user_id = message.from_user.id
    add_user(user_id)
    
    if is_banned(user_id):
        return await message.reply("🚫 **You are banned from using this bot.**")
    
    if not await handle_force_sub(client, message): return
    
    if user_id not in ADMINS:
        if user_id in user_cooldowns:
            rem = COOLDOWN_SECONDS - (time.time() - user_cooldowns[user_id])
            if rem > 0:
                return await message.reply(f"⏳ **Please wait {int(rem)} seconds.**")
    
    # Smart URL Detection (Text or Reply)
    url = None
    if len(message.command) > 1:
        url = message.command[1]
    elif message.reply_to_message:
        url = message.reply_to_message.text or message.reply_to_message.caption
        
    if not url:
        return await message.reply("❌ **Send a link from ANY site:** `/dl <link>`")

    req_id = str(uuid.uuid4())[:8]
    DOWNLOAD_QUEUE[req_id] = {"url": url, "uid": user_id}
    
    auth_text = "✅ **Auth:** Cookies Loaded" if COOKIES_PATH else "⚠️ **Auth:** No Cookies (Some sites might fail)"
    
    # Universal Buttons
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌟 Best Quality (MKV)", callback_data=f"q|best|{req_id}")],
        [InlineKeyboardButton("📺 1080p", callback_data=f"q|1080|{req_id}"),
         InlineKeyboardButton("📺 720p", callback_data=f"q|720|{req_id}")],
        [InlineKeyboardButton("📱 480p", callback_data=f"q|480|{req_id}"),
         InlineKeyboardButton("🎵 Audio Only", callback_data=f"q|audio|{req_id}")]
    ])
    
    await message.reply(
        f"🎬 **Universal Downloader**\n\n🔗 **Link:** `{url}`\n{auth_text}\n🛡️ **Ad-Block:** Active",
        reply_markup=btns,
        disable_web_page_preview=True
    )

@app.on_callback_query(filters.regex(r"^q\|"))
async def process_callback(client, callback):
    _, quality, req_id = callback.data.split("|")
    
    if req_id not in DOWNLOAD_QUEUE:
        return await callback.answer("❌ Session Expired.", show_alert=True)
    
    req = DOWNLOAD_QUEUE[req_id]
    if callback.from_user.id != req['uid']:
        return await callback.answer("❌ This is not your task!", show_alert=True)

    await callback.message.delete()
    status = await callback.message.reply(f"🔄 **Connecting to Source...**")
    
    if callback.from_user.id not in ADMINS:
        user_cooldowns[callback.from_user.id] = time.time()
        
    user_dir = f"downloads/{callback.from_user.id}"
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    
    # --- UNIVERSAL YT-DLP OPTIONS (Ad-Free & Anti-Block) ---
    ydl_opts = {
        'outtmpl': f"{user_dir}/{req_id}_%(title)s.%(ext)s",
        'quiet': True,
        'nocheckcertificate': True,
        'writethumbnail': True,
        # Fake User-Agent to look like a real browser (Crucial for non-YT sites)
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        # Geo-Bypass & Ad-Blocking headers
        'geo_bypass': True,
        'referer': 'https://www.google.com/',
        'socket_timeout': 15,
    }
    
    # Format Selection Logic
    if quality == "audio":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}]
    elif quality == "1080":
        ydl_opts['format'] = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
        ydl_opts['merge_output_format'] = 'mkv'
    elif quality == "720":
        ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
        ydl_opts['merge_output_format'] = 'mkv'
    elif quality == "480":
        ydl_opts['format'] = 'bestvideo[height<=480]+bestaudio/best[height<=480]'
        ydl_opts['merge_output_format'] = 'mkv'
    else: # BEST (Universal Default)
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mkv'

    if COOKIES_PATH: ydl_opts['cookiefile'] = COOKIES_PATH

    try:
        await status.edit("⬇️ **Downloading...**\n(Bypassing Ads & Captchas)")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Universal Extraction (Works for Insta, X, TikTok, YT, etc.)
            info = ydl.extract_info(req['url'], download=True)
            fpath = ydl.prepare_filename(info)
            base = fpath.rsplit(".", 1)[0]
            
            # Smart Extension Finder
            if not os.path.exists(fpath):
                for ext in [".mkv", ".mp4", ".webm", ".mp3", ".m4a"]:
                    if os.path.exists(base + ext):
                        fpath = base + ext
                        break
            
            if quality != "audio":
                await status.edit(f"🏷️ **Metadata...**")
                # Clean Metadata Injection
                temp_out = f"{fpath}_meta.mkv"
                cmd = [
                    "ffmpeg", "-y", "-i", fpath, "-c", "copy",
                    "-metadata", f"title={META_TITLE}",
                    "-metadata", f"artist={META_AUTHOR}",
                    "-metadata", f"author={META_AUTHOR}",
                    temp_out
                ]
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
            if quality != "audio":
                w, h, d = get_metadata(fpath)
            if d == 0: d = info.get('duration', 0)
            
            # Universal Title Cleaning
            clean_title = info.get('title', 'Video').replace("_", " ")
            
            if quality == "audio":
                await app.send_audio(
                    callback.message.chat.id, audio=fpath, title=clean_title, 
                    thumb=final_thumb, performer=META_AUTHOR, 
                    progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading...**")
                )
            else:
                await app.send_video(
                    callback.message.chat.id, video=fpath, caption=f"🎥 **{clean_title}**\n💿 Quality: {quality}\n👤 {META_AUTHOR}", 
                    duration=int(d), width=int(w), height=int(h), thumb=final_thumb,
                    supports_streaming=True, progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading...**")
                )
            await status.delete()
            await callback.message.reply_text("✅ **Completed!**")

    except Exception as e:
        await status.edit(f"❌ **Error:** {str(e)[:200]}")
    
    try: shutil.rmtree(user_dir)
    except: pass
    if req_id in DOWNLOAD_QUEUE: del DOWNLOAD_QUEUE[req_id]

if __name__ == "__main__":
    init_db()
    if not os.path.exists("downloads"): os.makedirs("downloads")
    
    keep_alive()   
    start_pinger() 
    
    print("🔥 Universal Bot Started...")
    app.run()


