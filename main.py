import os
import time
import math
import shutil
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

# --- SECURE CONFIGURATION (Load form Cloud Environment) ---
# Ye values GitHub par nahi hongi, seedha Render se aayengi
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Owner & Admins
OWNER_IDS = [int(x) for x in os.environ.get("OWNER_IDS", "").split()]
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split()]
ADMINS = list(set(ADMIN_IDS + OWNER_IDS))

# Channels
FORCE_SUB = os.environ.get("FORCE_SUB_CHANNEL", None)
LOG_CHANNEL = os.environ.get("LOG_CHANNEL", None)

# Metadata
META_TITLE = os.environ.get("METADATA_TITLE", "Downloaded via Bot")
META_AUTHOR = os.environ.get("METADATA_AUTHOR", "Winning Wonders Hub")

# Settings
DOWNLOAD_DIR = "/app/downloads"
COOKIES_FILE = "cookies.txt"

# --- COOKIE SECURITY (Optional) ---
# Agar cookies content environment variable me hai to file bana lo
if "COOKIES_CONTENT" in os.environ:
    with open(COOKIES_FILE, "w") as f:
        f.write(os.environ.get("COOKIES_CONTENT"))

# Setup
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

app = Client("secure_bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Memory
user_cooldowns = {}
COOLDOWN_SECONDS = 60
DOWNLOAD_QUEUE = {}
DB_NAME = "bot_data.db"

# --- DB & HELPER FUNCTIONS ---
def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, join_date TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY, reason TEXT)")
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    try:
        conn.cursor().execute("INSERT OR IGNORE INTO users (user_id, join_date) VALUES (?, ?)", (user_id, str(time.time())))
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
    conn.cursor().execute("INSERT OR REPLACE INTO banned (user_id, reason) VALUES (?, ?)", (user_id, "Banned"))
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

async def handle_force_sub(client, message):
    if not FORCE_SUB: return True
    user_id = message.from_user.id
    if user_id in ADMINS: return True
    try:
        # Check integer or username format
        chat_id = int(FORCE_SUB) if FORCE_SUB.lstrip('-').isdigit() else FORCE_SUB
        await client.get_chat_member(chat_id, user_id)
        return True
    except errors.UserNotParticipant:
        try:
            chat_id = int(FORCE_SUB) if FORCE_SUB.lstrip('-').isdigit() else FORCE_SUB
            invite = await client.export_chat_invite_link(chat_id)
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

def TimeFormatter(milliseconds: int) -> str:
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
            bar = '▰' * int(percentage / 100 * 12) + '▱' * (12 - int(percentage / 100 * 12))
            msg = f"{status_text}\n\n**{bar}** {round(percentage, 1)}%\n💾 `{humanbytes(current)}` / `{humanbytes(total)}`\n🚀 `{humanbytes(speed)}/s` | ⏳ `{TimeFormatter(eta * 1000)}`"
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
async def start_cmd(client, message):
    add_user(message.from_user.id)
    if not await handle_force_sub(client, message): return
    await message.reply(f"👋 **Hi {message.from_user.first_name}!**\nI am Secure & Live 24/7! 🔒\nSend a link to download.", quote=True)

@app.on_message(filters.command("broadcast") & filters.user(OWNER_IDS))
async def broadcast_cmd(client, message):
    if not message.reply_to_message: return await message.reply("❌ Reply to a message.")
    msg = await message.reply("🚀 Sending...")
    users = get_all_users()
    done = 0
    for uid in users:
        try:
            await message.reply_to_message.copy(uid)
            done += 1
            await asyncio.sleep(0.1)
        except: pass
    await msg.edit(f"✅ Sent to {done} users.")

@app.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_cmd(client, message):
    try:
        uid = int(message.command[1])
        if uid in ADMINS: return await message.reply("❌ Cannot ban Admin.")
        ban_user_db(uid)
        await message.reply(f"🚫 Banned `{uid}`.")
    except: await message.reply("❌ Usage: `/ban ID`")

@app.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_cmd(client, message):
    try:
        uid = int(message.command[1])
        unban_user_db(uid)
        await message.reply(f"✅ Unbanned `{uid}`.")
    except: await message.reply("❌ Usage: `/unban ID`")

@app.on_message(filters.command(["dl", "download"]))
async def init_dl(client, message):
    user_id = message.from_user.id
    add_user(user_id)
    if is_banned(user_id): return
    if not await handle_force_sub(client, message): return
    
    if user_id not in ADMINS:
        if user_id in user_cooldowns:
            rem = COOLDOWN_SECONDS - (time.time() - user_cooldowns[user_id])
            if rem > 0: return await message.reply(f"⏳ Wait {int(rem)}s.")

    url = message.command[1] if len(message.command) > 1 else (message.reply_to_message.text if message.reply_to_message else None)
    if not url: return await message.reply("❌ Send Link.")

    req_id = str(uuid.uuid4())[:8]
    DOWNLOAD_QUEUE[req_id] = {"url": url, "uid": user_id}
    
    auth_status = "✅ Auth" if os.path.exists(COOKIES_FILE) else "⚠️ No Auth"
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌟 High Quality (MKV)", callback_data=f"q|best|{req_id}"),
         InlineKeyboardButton("🎵 Audio Only", callback_data=f"q|audio|{req_id}")]
    ])
    await message.reply_text(f"🎬 **Download Manager**\nLink: `{url}`\nAuth: {auth_status}", reply_markup=btns)

@app.on_callback_query(filters.regex(r"^q\|"))
async def process_dl(client, callback: CallbackQuery):
    _, quality, req_id = callback.data.split("|")
    if req_id not in DOWNLOAD_QUEUE: return await callback.answer("❌ Expired.")
    req = DOWNLOAD_QUEUE[req_id]
    
    await callback.message.delete()
    status = await callback.message.reply("🔄 **Starting...**")
    
    if callback.from_user.id not in ADMINS: user_cooldowns[callback.from_user.id] = time.time()
    
    user_dir = os.path.join(DOWNLOAD_DIR, str(callback.from_user.id))
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    out_tmpl = f"{user_dir}/{req_id}_%(title)s.%(ext)s"
    
    ydl_opts = {
        'outtmpl': out_tmpl, 'quiet': True, 'nocheckcertificate': True,
        'writethumbnail': True, 'addmetadata': True, 'max_filesize': 1900*1024*1024,
        'postprocessors': [{'key': 'FFmpegEmbedSubtitle'}], 'writesubtitles': True
    }
    if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE

    if quality == "audio": ydl_opts['format'] = "bestaudio/best"
    else: 
        ydl_opts['format'] = "bestvideo+bestaudio/best"
        ydl_opts['merge_output_format'] = "mkv"

    try:
        await status.edit("⬇️ **Downloading...**")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req['url'], download=True)
            fpath = ydl.prepare_filename(info)
            tpath = fpath.rsplit(".", 1)[0] + ".jpg"
            if not os.path.exists(tpath): tpath = fpath.rsplit(".", 1)[0] + ".webp"
        
        base = fpath.rsplit(".", 1)[0]
        if not os.path.exists(fpath):
            if os.path.exists(base+".mkv"): fpath = base+".mkv"
            elif os.path.exists(base+".mp4"): fpath = base+".mp4"
            else: raise Exception("Download Failed")
            
        await status.edit("🏷️ **Injecting Metadata...**")
        await edit_video_metadata(fpath)
        
        await status.edit("⬆️ **Uploading...**")
        start = time.time()
        thumb = prepare_thumbnail(tpath)
        w, h, d = get_metadata(fpath)
        if d == 0: d = info.get('duration', 0)
        
        if quality == "audio":
            await app.send_audio(callback.message.chat.id, audio=fpath, title=info.get('title'), thumb=thumb, performer=META_AUTHOR)
        else:
            await app.send_video(
                callback.message.chat.id, video=fpath, caption=f"🎥 **{info.get('title')}**\n👤 {META_AUTHOR}",
                duration=int(d), width=int(w), height=int(h), thumb=thumb,
                supports_streaming=True, progress=progress_bar, progress_args=(status, start, "⬆️ **Uploading...**")
            )
        await status.delete()
    except Exception as e:
        await status.edit(f"❌ Error: {str(e)[:100]}")
    
    try: shutil.rmtree(user_dir)
    except: pass
    if req_id in DOWNLOAD_QUEUE: del DOWNLOAD_QUEUE[req_id]

if __name__ == "__main__":
    init_db()
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    keep_alive()
    app.run()


