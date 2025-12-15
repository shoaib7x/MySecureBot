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
import subprocess
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
    return "Bot is Running 24/7! 🚀"

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

# --- COOKIE LOGIC ---
COOKIES_PATH = None
possible_cookies = [
    "cookie (1).txt", "cookies.txt", "/etc/secrets/cookies.txt", "/app/cookie (1).txt"
]
for c in possible_cookies:
    if os.path.exists(c):
        COOKIES_PATH = c
        print(f"✅ Cookies Found: {c}")
        break

if not COOKIES_PATH:
    print("⚠️ WARNING: No cookies found! Restricted sites may fail.")

# Logging
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

app = Client("pro_bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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
            await message.reply("⚠️ **Please Join Update Channel!**", reply_markup=btn)
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
            bar = '▰' * int(percentage / 100 * 12) + '▱' * (12 - int(percentage / 100 * 12))
            msg = f"{status_text}\n\n**{bar}** {round(percentage, 1)}%\n💾 `{round(current/1048576, 2)}MB` / `{round(total/1048576, 2)}MB`\n🚀 `{round(speed/1048576, 2)} MB/s` | ⏳ `{int(eta)}s`"
            await message.edit(msg)
    except: pass

def get_metadata(file_path):
    try:
        metadata = extractMetadata(createParser(file_path))
        return (metadata.get("width") or 0, metadata.get("height") or 0, metadata.get("duration").seconds or 0)
    except: return 0, 0, 0

def prepare_thumbnail(thumb_path):
    if not thumb_path or not os.path.exists(thumb_path): return None
    try:
        img = Image.open(thumb_path)
        img.thumbnail((320, 320))
        img.save(thumb_path, "JPEG")
        return thumb_path
    except: return None

# --- 5. COMMANDS ---
@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    add_user(message.from_user.id)
    if not await handle_force_sub(client, message): return
    auth_status = "✅ Active" if COOKIES_PATH else "⚠️ Inactive"
    
    txt = (
        f"👋 **Hello {message.from_user.first_name}!**\n\n"
        "I am an **Universal Video Downloader**.\n"
        "I support 1000+ sites (YT, Insta, X, etc).\n"
        f"🍪 **Cookies:** {auth_status}\n\n"
        "🔹 **Use:** Send any link to download."
    )
    await message.reply(txt, quote=True)

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    txt = "**User:**\n/dl <link> - Download\n/start - Check\n\n**Admin:**\n/ban <id>\n/unban <id>\n/broadcast <reply>\n/log - DB File"
    await message.reply(txt, quote=True)

@app.on_message(filters.command("broadcast") & filters.user(OWNERS))
async def broadcast_cmd(client, message):
    if not message.reply_to_message: return await message.reply("Reply to a message!")
    msg = await message.reply("🚀 Sending...")
    users = get_all_users()
    done = 0
    blocked = 0
    for uid in users:
        try:
            await message.reply_to_message.copy(uid)
            done += 1
            await asyncio.sleep(0.1)
        except errors.FloodWait as e:
            await asyncio.sleep(e.value)
            try: await message.reply_to_message.copy(uid); done+=1
            except: blocked+=1
        except: blocked += 1
    await msg.edit(f"✅ **Broadcast Done!**\nSent: {done}\nFailed: {blocked}")

@app.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_cmd(client, message):
    try:
        uid = int(message.command[1])
        ban_user_db(uid)
        await message.reply(f"🚫 Banned {uid}")
    except: await message.reply("Usage: /ban UserID")

@app.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_cmd(client, message):
    try:
        uid = int(message.command[1])
        unban_user_db(uid)
        await message.reply(f"✅ Unbanned {uid}")
    except: await message.reply("Usage: /unban UserID")

@app.on_message(filters.command("log") & filters.user(OWNERS))
async def log_cmd(client, message):
    if os.path.exists(DB_NAME): await message.reply_document(DB_NAME)
    else: await message.reply("No Data.")

# --- 6. DOWNLOAD HANDLER (ALL BUTTONS WORKING) ---
@app.on_message(filters.command(["dl", "download"]))
async def dl_init(client, message):
    user_id = message.from_user.id
    add_user(user_id)
    if is_banned(user_id): return
    if not await handle_force_sub(client, message): return
    
    url = None
    if len(message.command) > 1: url = message.command[1]
    elif message.reply_to_message: url = message.reply_to_message.text or message.reply_to_message.caption
    
    if not url: return await message.reply("❌ Send Link.")

    req_id = str(uuid.uuid4())[:8]
    DOWNLOAD_QUEUE[req_id] = {"url": url, "uid": user_id}
    
    # 4 WORKING BUTTONS
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌟 Best (MKV)", callback_data=f"q|best|{req_id}"),
         InlineKeyboardButton("📺 720p", callback_data=f"q|720|{req_id}")],
        [InlineKeyboardButton("📱 480p", callback_data=f"q|480|{req_id}"),
         InlineKeyboardButton("🎵 Audio", callback_data=f"q|audio|{req_id}")]
    ])
    await message.reply(f"🎬 **Download Manager**\nLink: `{url}`", reply_markup=btns)

@app.on_callback_query(filters.regex(r"^q\|"))
async def process_dl(client, callback):
    _, quality, req_id = callback.data.split("|")
    if req_id not in DOWNLOAD_QUEUE: return await callback.answer("❌ Expired.")
    req = DOWNLOAD_QUEUE[req_id]
    
    await callback.message.delete()
    status = await callback.message.reply("🔄 **Starting...**")
    
    user_dir = f"downloads/{callback.from_user.id}"
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    
    # Common Options
    ydl_opts = {
        'outtmpl': f"{user_dir}/{req_id}_%(title)s.%(ext)s",
        'quiet': True, 'nocheckcertificate': True,
        'writethumbnail': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    }
    
    # --- Format Logic ---
    if quality == "audio":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}]
    elif quality == "720":
        ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
        ydl_opts['merge_output_format'] = 'mkv'
    elif quality == "480":
        ydl_opts['format'] = 'bestvideo[height<=480]+bestaudio/best[height<=480]'
        ydl_opts['merge_output_format'] = 'mkv'
    else: # Best
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mkv'

    if COOKIES_PATH: ydl_opts['cookiefile'] = COOKIES_PATH

    try:
        await status.edit("⬇️ **Downloading...**")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req['url'], download=True)
            fpath = ydl.prepare_filename(info)
            base = fpath.rsplit(".", 1)[0]
            
            # Extension Check
            if not os.path.exists(fpath):
                for ext in [".mkv", ".mp4", ".webm", ".mp3"]:
                    if os.path.exists(base + ext):
                        fpath = base + ext
                        break
            
            if quality != "audio":
                await status.edit("🏷️ **Metadata...**")
                # Metadata Injection
                temp_out = f"{base}_meta.mkv"
                cmd = ["ffmpeg", "-y", "-i", fpath, "-c", "copy",
                       "-metadata", f"title={META_TITLE}",
                       "-metadata", f"artist={META_AUTHOR}",
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
            w, h, d = get_metadata(fpath)
            if d == 0: d = info.get('duration', 0)
            
            if quality == "audio":
                await app.send_audio(callback.message.chat.id, audio=fpath, title=info.get('title'), thumb=final_thumb, performer=META_AUTHOR, progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading...**"))
            else:
                await app.send_video(callback.message.chat.id, video=fpath, caption=f"🎥 **{info.get('title')}**\n👤 {META_AUTHOR}", duration=int(d), width=int(w), height=int(h), thumb=final_thumb, supports_streaming=True, progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading...**"))
            await status.delete()
    except Exception as e:
        await status.edit(f"❌ **Error:** {str(e)[:200]}")
    
    try: shutil.rmtree(user_dir)
    except: pass
    if req_id in DOWNLOAD_QUEUE: del DOWNLOAD_QUEUE[req_id]

# --- 7. STARTUP & FLOODWAIT ---
if __name__ == "__main__":
    init_db()
    if not os.path.exists("downloads"): os.makedirs("downloads")
    
    keep_alive()   
    start_pinger() 
    
    print("🔥 Bot Started...")
    try:
        app.run()
    except errors.FloodWait as e:
        print(f"❌ FLOOD WAIT: {e.value}s. Sleeping...")
        time.sleep(e.value)
        app.run()


