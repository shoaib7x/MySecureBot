import os
import time
import asyncio
import sqlite3
import yt_dlp
import uuid
import logging
import subprocess
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters, enums, errors
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from web_server import keep_alive

# --- LOAD CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Logging
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# Check Credentials
if API_ID == 0 or not BOT_TOKEN:
    logger.error("❌ CRITICAL ERROR: Token missing! Render Environment Check karo.")

OWNERS = [int(x) for x in os.environ.get("OWNER_IDS", "").split() if x.strip()]
ADMINS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split() if x.strip()]
ADMINS.extend(OWNERS)
ADMINS = list(set(ADMINS)) 

FORCE_SUB = int(os.environ.get("FORCE_SUB_CHANNEL")) if os.environ.get("FORCE_SUB_CHANNEL") else None
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL")) if os.environ.get("LOG_CHANNEL") else None
META_TITLE = os.environ.get("METADATA_TITLE", "Downloaded via Bot")
META_AUTHOR = os.environ.get("METADATA_AUTHOR", "Winning Wonders Hub")
DOWNLOAD_DIR = "/app/downloads"
COOKIES_FILE = "cookies.txt"

# Cookie Security (Write cookies if available)
if "COOKIES_CONTENT" in os.environ:
    with open(COOKIES_FILE, "w") as f:
        f.write(os.environ.get("COOKIES_CONTENT"))

app = Client("pro_bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_cooldowns = {}
DOWNLOAD_QUEUE = {}
DB_NAME = "bot_data.db"

# --- DB & HELPERS ---
def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

async def handle_force_sub(client, message):
    if not FORCE_SUB: return True
    user_id = message.from_user.id
    if user_id in ADMINS: return True
    try:
        await client.get_chat_member(FORCE_SUB, user_id)
        return True
    except errors.UserNotParticipant:
        try:
            invite = await client.export_chat_invite_link(FORCE_SUB)
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=invite)]])
            await message.reply("⚠️ **Please Join Update Channel!**", reply_markup=btn)
            return False
        except: return True
    except: return True

def humanbytes(size):
    if not size: return "0B"
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN.get(n, '') + 'B'

async def progress_bar(current, total, message, start_time, status_text):
    try:
        now = time.time()
        diff = now - start_time
        if round(diff % 5.00) == 0 or current == total:
            speed = current / diff if diff > 0 else 0
            percentage = current * 100 / total
            bar = '▰' * int(percentage / 100 * 12) + '▱' * (12 - int(percentage / 100 * 12))
            msg = f"{status_text}\n\n**{bar}** {round(percentage, 1)}%\n💾 `{humanbytes(current)}` / `{humanbytes(total)}`"
            await message.edit(msg)
    except: pass

async def edit_video_metadata(input_file):
    output_file = f"{input_file}_meta.mkv"
    cmd = ["ffmpeg", "-y", "-i", input_file, "-c", "copy",
           "-metadata", f"title={META_TITLE}", "-metadata", f"artist={META_AUTHOR}", output_file]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await process.communicate()
        if os.path.exists(output_file):
            os.remove(input_file)
            os.rename(output_file, input_file)
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

# --- HANDLERS ---
@app.on_message(filters.command("start"))
async def start(client, message):
    add_user(message.from_user.id)
    if not await handle_force_sub(client, message): return
    await message.reply(f"👋 **Hi {message.from_user.first_name}!**\nBot is Live! 🟢\nSend a link to download.", quote=True)

@app.on_message(filters.command(["dl", "download"]))
async def dl_cmd(client, message):
    user_id = message.from_user.id
    add_user(user_id)
    if not await handle_force_sub(client, message): return
    
    url = message.command[1] if len(message.command) > 1 else (message.reply_to_message.text if message.reply_to_message else None)
    if not url: return await message.reply("❌ Send Link.")

    req_id = str(uuid.uuid4())[:8]
    DOWNLOAD_QUEUE[req_id] = {"url": url, "uid": user_id}
    
    # Check cookies status
    cookies_status = "✅ Cookies Active" if os.path.exists(COOKIES_FILE) else "⚠️ No Cookies (Restricted Videos may fail)"
    
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌟 High Quality (MKV)", callback_data=f"q|best|{req_id}")]
    ])
    await message.reply(f"🎬 **Found Link:**\n`{url}`\nAuth: {cookies_status}", reply_markup=btns)

@app.on_callback_query(filters.regex(r"^q\|"))
async def process_dl(client, callback):
    req_id = callback.data.split("|")[2]
    if req_id not in DOWNLOAD_QUEUE: return await callback.answer("❌ Expired.")
    
    req = DOWNLOAD_QUEUE[req_id]
    await callback.message.delete()
    status = await callback.message.reply("🔄 **Starting...**")
    
    user_dir = f"downloads/{callback.from_user.id}"
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    
    # --- ANTI-BOT SETTINGS ---
    ydl_opts = {
        'outtmpl': f"{user_dir}/{req_id}_%(title)s.%(ext)s",
        'quiet': True, 
        'nocheckcertificate': True, 
        'writethumbnail': True,
        'format': 'bestvideo+bestaudio/best', 
        'merge_output_format': 'mkv',
        
        # Spoofing User Agent (To look like a real PC)
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'socket_timeout': 10,
        'retries': 5
    }
    
    # Use cookies if available
    if os.path.exists(COOKIES_FILE): 
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        await status.edit("⬇️ **Downloading...**")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req['url'], download=True)
            fpath = ydl.prepare_filename(info)
            base = fpath.rsplit(".", 1)[0]
            if not os.path.exists(fpath): fpath = base + ".mkv"
            
            await status.edit("🏷️ **Metadata...**")
            await edit_video_metadata(fpath)
            
            await status.edit("⬆️ **Uploading...**")
            start = time.time()
            thumb = base + ".jpg"
            if not os.path.exists(thumb): thumb = base + ".webp"
            final_thumb = prepare_thumbnail(thumb)
            w, h, d = get_metadata(fpath)
            if d == 0: d = info.get('duration', 0)
            
            await app.send_video(
                callback.message.chat.id, video=fpath, caption=f"🎥 **{info.get('title')}**\n👤 {META_AUTHOR}",
                width=w, height=h, duration=d, thumb=final_thumb,
                supports_streaming=True, progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading...**")
            )
            await status.delete()
    except Exception as e:
        await status.edit(f"❌ **Failed:** {str(e)[:200]}\n\n💡 _Tip: Update cookies if this persists._")
    
    try: shutil.rmtree(user_dir)
    except: pass

if __name__ == "__main__":
    init_db()
    if not os.path.exists("downloads"): os.makedirs("downloads")
    keep_alive()
    app.run()


