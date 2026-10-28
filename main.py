import asyncio
import json
import logging
import os
import re
import hashlib
import time
import zipfile
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton as TelegramInlineKeyboardButton, ReplyKeyboardRemove, BufferedInputFile, BotCommand
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# Tugma yozuvlari uchun foydalanuvchi tanlagan small-caps uslubi.
_SMALL_CAPS = str.maketrans({
    "a":"ᴀ", "b":"ʙ", "c":"ᴄ", "d":"ᴅ", "e":"ᴇ", "f":"ғ", "g":"ɢ",
    "h":"ʜ", "i":"ɪ", "j":"ᴊ", "k":"ᴋ", "l":"ʟ", "m":"ᴍ", "n":"ɴ",
    "o":"ᴏ", "p":"ᴘ", "q":"ǫ", "r":"ʀ", "s":"s", "t":"ᴛ", "u":"ᴜ",
    "v":"ᴠ", "w":"ᴡ", "x":"x", "y":"ʏ", "z":"ᴢ"
})
def small_caps(value):
    return str(value).translate(_SMALL_CAPS)
def InlineKeyboardButton(*, text, **kwargs):
    """Tugma sozlamalarini har tugmada diskdan o'qimasdan, xotiradagi ma'lumotdan oladi."""
    try:
        _cfg = load_data().get("settings", {})
        _cb = str(kwargs.get("callback_data", ""))
        _custom_text = _cfg.get("button_texts", {}).get(_cb)
        if _custom_text:
            text = _custom_text
        _colors = _cfg.get("button_colors", {})
        _group = _button_color_group(_cb, str(text))
        _color = _colors.get(_group) or _colors.get("all")
        if _color in ("primary", "success", "danger"):
            kwargs.setdefault("style", _color)
    except Exception:
        pass
    return TelegramInlineKeyboardButton(text=small_caps(text), **kwargs)

def _button_color_group(callback_data, text=""):
    cb = str(callback_data or "")
    # Katalog sahifalash tugmalari alohida sozlanadi; episode raqamlari bundan mustasno.
    if cb == "page_info":
        return "page_indicator"
    if cb.startswith(("season_page:", "media_page:")):
        if "⬅" in str(text): return "page_left"
        if "➡" in str(text): return "page_right"
    if cb in ("home", "admin_home") or "Orqaga" in text or "Asosiy menyu" in text:
        return "back"
    if cb.startswith(("episode:", "show:", "season_show:", "catalog_show:")) or cb in ("list", "movie_list", "continue_watching"):
        return "content"
    if cb in ("search", "search_name", "search_menu") or "qidirish" in text.casefold():
        return "search"
    return "other"

# ================== SOZLAMALAR ==================
# Render Environment Variables: BOT_TOKEN, OWNER_ID, ADMIN_ID.
# Qiymatlarda bosh/oxir bo'sh joy bo'lsa tozalanadi.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")

# ADMIN_ID bitta ID yoki vergul bilan ajratilgan bir nechta ID bo'lishi mumkin.
ADMIN_IDS_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_IDS = []
for _admin_value in ADMIN_IDS_RAW.split(","):
    _admin_value = _admin_value.strip()
    if _admin_value.isascii() and _admin_value.isdecimal():
        _parsed_admin = int(_admin_value)
        if _parsed_admin > 0 and _parsed_admin not in ADMIN_IDS:
            ADMIN_IDS.append(_parsed_admin)

OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
if OWNER_ID_RAW.isascii() and OWNER_ID_RAW.isdecimal() and int(OWNER_ID_RAW) > 0:
    OWNER_ID = int(OWNER_ID_RAW)
else:
    # OWNER_ID bo'sh qolgan bo'lsa, ADMIN_ID dagi birinchi ID egasi sifatida olinadi.
    OWNER_ID = ADMIN_IDS[0] if ADMIN_IDS else 0
DATA_FILE = Path(os.getenv("DATA_FILE", "data.json"))
BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID", "").strip()
ADMIN_CONTACT = "@AnimBot_Admin"
# media postlari saqlanadigan kanal ID sini STORAGE_CHANNEL_ID environment variable orqali berish mumkin.
CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID", "").strip()

def get_backup_channel_id():
    """Owner panelidagi kanal sozlamasi ENV qiymatidan ustun turadi."""
    return str(load_data().get("settings", {}).get("backup_channel_id") or BACKUP_CHANNEL_ID or "").strip()
# =================================================

logging.basicConfig(level=logging.INFO)
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi. Render > Service > Environment bo'limida "
        "BOT_TOKEN nomli variable yarating va BotFather tokenini kiriting."
    )
def small_caps_html(value):
    """HTML teglarini, entitylarni va code/pre ichidagi texnik matnni buzmasdan bezaydi."""
    value = str(value)
    tokens = re.split(r"(<[^>]+>|&(?:amp|lt|gt|quot|#39|#\d+|#x[0-9a-fA-F]+);)", value)
    output = []
    in_code = False
    for token in tokens:
        if not token:
            continue
        if token.startswith("<") and token.endswith(">"):
            tag = re.match(r"</?([a-zA-Z0-9]+)", token)
            if tag and tag.group(1).lower() in {"code", "pre"}:
                if token.startswith("</"):
                    in_code = False
                elif not token.endswith("/>"):
                    in_code = True
            output.append(token)
        elif token.startswith("&") and token.endswith(";"):
            output.append(token)
        elif in_code:
            output.append(token)
        else:
            # Slash commands (/start, /done) va @username oddiy shrift bo'lib qoladi.
            pieces = re.split(r"(@[A-Za-z0-9_]{3,}|/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?)", token)
            for piece in pieces:
                if not piece:
                    continue
                if re.fullmatch(r"(?:@[A-Za-z0-9_]{3,}|/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?)", piece):
                    output.append(piece)
                else:
                    output.append(small_caps(piece))
    return "".join(output)

class StyledBot(Bot):
    async def send_message(self, chat_id, text, *args, **kwargs):
        return await super().send_message(chat_id, small_caps_html(text), *args, **kwargs)

    async def send_photo(self, chat_id, photo, *args, **kwargs):
        if kwargs.get("caption") is not None:
            kwargs["caption"] = small_caps_html(kwargs["caption"])
        return await super().send_photo(chat_id, photo, *args, **kwargs)

    async def edit_message_text(self, text, *args, **kwargs):
        return await super().edit_message_text(small_caps_html(text), *args, **kwargs)

    async def edit_message_caption(self, *args, **kwargs):
        if kwargs.get("caption") is not None:
            kwargs["caption"] = small_caps_html(kwargs["caption"])
        return await super().edit_message_caption(*args, **kwargs)

bot = StyledBot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

class MaintenanceMiddleware:
    """Texnik tanaffusda ownerdan boshqalarning so'rovlarini to'xtatadi."""
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and not is_owner(user.id):
            cfg = load_data().get("settings", {})
            if cfg.get("maintenance", False):
                message = getattr(event, "message", None) or event
                try:
                    if isinstance(event, CallbackQuery):
                        await event.answer("🛠 Botda texnik ishlar ketmoqda. Keyinroq urinib ko'ring.", show_alert=True)
                    elif isinstance(event, Message):
                        await event.answer("🛠 <b>Texnik tanaffus</b>\nBot yangilanmoqda. Iltimos, keyinroq urinib ko'ring.")
                except Exception:
                    pass
                return None
        return await handler(event, data)

dp.message.outer_middleware(MaintenanceMiddleware())
dp.callback_query.outer_middleware(MaintenanceMiddleware())


def default_data():
    return {
        "admins": [OWNER_ID],
        "start_text": "🎬 <b>Assalomu alaykum, {first}!</b>\n\n🍿 <b>Bugun nima ko'ramiz?</b>",
        "channels": [],
        "medias": {},
        "channel_catalog": {},
        "users": {},
        "banned_users": [],
        "settings": {"catalog_channel_id": "", "button_colors": {}, "button_texts": {}, "maintenance": False, "vip_enabled": False, "vip_month_price": 15000, "vip_max_months": 6, "vip_duration_days": 30, "early_view_days": 1, "referral_reward": 5000, "notify_new_content": True, "admin_contact": "obidov_xz", "ordinary_base_status": "sozlanmagan", "vip_base_status": "sozlanmagan"}
    }


# Bitta Render jarayonida JSON faylni har bir tugma/handlerda qayta parse qilmaslik uchun kesh.
_DATA_CACHE = None
_DATA_CACHE_MTIME = None

def load_data():
    global _DATA_CACHE, _DATA_CACHE_MTIME
    try:
        current_mtime = DATA_FILE.stat().st_mtime_ns if DATA_FILE.exists() else None
    except OSError:
        current_mtime = None
    if _DATA_CACHE is not None and current_mtime == _DATA_CACHE_MTIME:
        return _DATA_CACHE
    if not DATA_FILE.exists():
        save_data(default_data())
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = default_data()
    for key, value in default_data().items():
        data.setdefault(key, value)
    data.setdefault("channel_catalog", {})
    data.setdefault("settings", {})
    for setting_key, setting_value in default_data()["settings"].items():
        data["settings"].setdefault(setting_key, setting_value)
    if "medias" not in data:
        data["medias"] = data.pop("ani" + "mes", {})
    for media in data.get("medias", {}).values():
        media.setdefault("seasons", {"1": {"episodes": media.get("episodes", {})}})
        media.setdefault("genres", "")
        media.setdefault("status", "")
        media.setdefault("quality", "")
        media.setdefault("voice", "")
    # Avvaldan saqlangan medialarni yangi qoida bo'yicha darhol ochiq qoldiramiz.
    for media in data.get("medias", {}).values():
        media.setdefault("created_at", 0)
    for user_id, user in data.get("users", {}).items():
        user.setdefault("favorites", [])
        user.setdefault("last_watched", None)
        user.setdefault("last_seen", user.get("joined_at", 0))
    _DATA_CACHE = data
    try:
        _DATA_CACHE_MTIME = DATA_FILE.stat().st_mtime_ns
    except OSError:
        _DATA_CACHE_MTIME = None
    return data


def get_setting(name: str):
    return load_data().get("settings", {}).get(name, default_data()["settings"].get(name))


def save_data(data):
    global _DATA_CACHE, _DATA_CACHE_MTIME
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    _DATA_CACHE = data
    try:
        _DATA_CACHE_MTIME = DATA_FILE.stat().st_mtime_ns
    except OSError:
        _DATA_CACHE_MTIME = None


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID) and user_id == OWNER_ID


def get_admin_permissions(user_id: int):
    if is_owner(user_id):
        return {"add_content", "add_episode", "edit_content", "delete_content"}
    data = load_data()
    if user_id in ADMIN_IDS and str(user_id) not in data.get("admin_permissions", {}):
        return {"add_content", "add_episode"}
    return set(data.get("admin_permissions", {}).get(str(user_id), ["add_content", "add_episode"]))


def has_permission(user_id: int, permission: str) -> bool:
    return is_owner(user_id) or (is_admin(user_id) and permission in get_admin_permissions(user_id))


def is_admin(user_id: int) -> bool:
    # Owner va ruxsati bor adminlar.
    if is_owner(user_id):
        return True
    data = load_data()
    return user_id in ADMIN_IDS or (user_id in data.get("admins", []) and bool(get_admin_permissions(user_id)))


def now_ts() -> int:
    return int(time.time())




def ensure_user(data, tg_user):
    uid = str(tg_user.id)
    is_new = uid not in data.setdefault("users", {})
    if is_new:
        data["users"][uid] = {
            "id": tg_user.id, "username": getattr(tg_user, "username", None),
            "first_name": getattr(tg_user, "first_name", "") or "",
            "favorites": [], "last_watched": None, "joined_at": now_ts(), "last_seen": now_ts()
        }
    else:
        user = data["users"][uid]
        user["username"] = getattr(tg_user, "username", None)
        user["first_name"] = getattr(tg_user, "first_name", "") or user.get("first_name", "")
        user["last_seen"] = now_ts()
    return data["users"][uid], is_new


def fmt_date(timestamp: int) -> str:
    if not timestamp:
        return "Faol emas"
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")












async def notify_new_user(tg_user):
    data = load_data()
    user, is_new = ensure_user(data, tg_user)
    if not is_new:
        save_data(data)
        return
    save_data(data)
    username = f"@{tg_user.username}" if getattr(tg_user, "username", None) else "username yo'q"
    total = len(data.get("users", {}))
    text = (f"🆕 <b>Yangi obunachi</b>\n👤 Ism: {user.get('first_name') or '-'}\n"
            f"🔗 Username: {username}\n🆔 ID: <code>{tg_user.id}</code>\n"
            f"👥 Jami obunachilar: {total}")
    recipients = set([OWNER_ID] + ADMIN_IDS + [x for x in data.get("admins", []) if isinstance(x, int)])
    for admin_id in recipients:
        if not admin_id:
            continue
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            logging.info("Yangi obunachi xabari %s ga yuborilmadi: %s", admin_id, exc)


async def send_start(message: Message, user_id=None):
    data = load_data()
    menu_user_id = int(user_id or message.from_user.id)
    # Eski pastki ReplyKeyboard'ni olib tashlab, faqat inline menyudan foydalanamiz.
    remove_msg = await message.answer("⁣", reply_markup=ReplyKeyboardRemove())
    try:
        await remove_msg.delete()
    except Exception:
        pass
    tg_user = message.from_user
    first = (f"@{tg_user.username}" if tg_user.username else (tg_user.first_name or "do'stim"))
    start_text = data.get("start_text", "🎬 Assalomu alaykum, {first}!\n\n🍿 Bugun nima ko'ramiz?").replace("{first}", first)
    await message.answer(start_text, reply_markup=main_menu(menu_user_id))


def main_menu(user_id=None):
    # Foydalanuvchi yuborgan namunadagi joylashuv; mavjud funksiyalar olib tashlanmaydi.
    rows = [
        [InlineKeyboardButton(text="🔎 Anime va Kino izlash", callback_data="search_menu")],
        [InlineKeyboardButton(text="👤 Hisobim", callback_data="account"), InlineKeyboardButton(text="👥 Referal", callback_data="referral")],
        [InlineKeyboardButton(text="💰 Balans", callback_data="account"), InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")],
        [InlineKeyboardButton(text="❓ VIP o‘zi nima?", callback_data="vip_info")],
        [InlineKeyboardButton(text="📖 Qo‘llanma", callback_data="guide")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_reply_keyboard():
    """Eski pastki klaviatura endi ishlatilmaydi."""
    return ReplyKeyboardRemove()


def search_menu(user_id=None):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Kod bo'yicha izlash", callback_data="search"), InlineKeyboardButton(text="🔠 Nom bo'yicha izlash", callback_data="search_name")],
        [InlineKeyboardButton(text="🎞 Kinolar ro'yxati", callback_data="category_list:movie"), InlineKeyboardButton(text="📺 Seriallar ro'yxati", callback_data="category_list:series")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")],
    ])


def admin_menu(user_id=None):
    uid = user_id
    if uid is not None and not is_owner(uid):
        perms = get_admin_permissions(uid)
    else:
        perms = {"add_content", "add_episode", "edit_content", "delete_content"}
    rows = []
    if "add_content" in perms:
        rows.append([InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="newtype:movie"), InlineKeyboardButton(text="➕ Serial qo'shish", callback_data="newtype:series")])
        rows.append([InlineKeyboardButton(text="🍥 Anime qo'shish", callback_data="newtype:anime"), InlineKeyboardButton(text="🎨 Multfilm qo'shish", callback_data="newtype:cartoon")])
    # Admin panelida qism/fasl qo'shish tugmasi alohida ko'rsatiladi.
    if "add_episode" in perms:
        rows.append([InlineKeyboardButton(text="➕ Qism / yangi fasl qo'shish", callback_data="add_episode")])
    if "edit_content" in perms:
        rows.append([InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data="rename_media"), InlineKeyboardButton(text="🖼 Posterni o'zgartirish", callback_data="edit_poster")])
        rows.append([InlineKeyboardButton(text="🔢 Kodini o'zgartirish", callback_data="rename_code")])
    if "delete_content" in perms:
        rows.append([InlineKeyboardButton(text="🗑 Kontent o'chirish", callback_data="del_media")])
        rows.append([InlineKeyboardButton(text="🗑 Qism o'chirish", callback_data="del_episode"), InlineKeyboardButton(text="🗑 Fasl o'chirish", callback_data="del_season")])
    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text="⛔ Sizga huquq berilmagan", callback_data="admin_home")]])


def owner_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="add_movie"), InlineKeyboardButton(text="➕ Serial qo'shish", callback_data="add_series")],
        [InlineKeyboardButton(text="🍥 Anime qo'shish", callback_data="newtype:anime"), InlineKeyboardButton(text="🎨 Multfilm qo'shish", callback_data="newtype:cartoon")],
        [InlineKeyboardButton(text="➕ Qism yoki yangi sezon", callback_data="add_episode"), InlineKeyboardButton(text="🗑 Kontent o'chirish", callback_data="del_media")],
        [InlineKeyboardButton(text="🗑 Fasl o'chirish", callback_data="del_season")],
        [InlineKeyboardButton(text="✏️ Nom / kodni tahrirlash", callback_data="rename_media"), InlineKeyboardButton(text="🖼 Posterni tahrirlash", callback_data="edit_poster")],
        [InlineKeyboardButton(text="👤 Admin qo'shish", callback_data="add_admin"), InlineKeyboardButton(text="➖ Admin o'chirish", callback_data="del_admin")],
        [InlineKeyboardButton(text="🔐 Admin huquqlarini sozlash", callback_data="admin_permissions")],
        [InlineKeyboardButton(text="📢 Kanal qo'shish", callback_data="add_channel"), InlineKeyboardButton(text="➖ Kanal o'chirish", callback_data="del_channel")],
        [InlineKeyboardButton(text="🎨 Tugma ranglari", callback_data="appearance")],
        [InlineKeyboardButton(text="💾 Zaxira olish", callback_data="owner_backup"), InlineKeyboardButton(text="♻️ Zaxirani tiklash", callback_data="owner_restore_help")],
        [InlineKeyboardButton(text="🗄 Zaxira kanalini o'zgartirish", callback_data="backup_channel_change")],
        [InlineKeyboardButton(text="🛠 Texnik tanaffus: holat", callback_data="owner_maintenance")],
        [InlineKeyboardButton(text=("💎 VIP: O'CHIRISH" if load_data().get("settings", {}).get("vip_enabled", False) else "💎 VIP: YOQISH"), callback_data="owner_vip")],
        [InlineKeyboardButton(text="📣 Hammaga xabar yuborish", callback_data="broadcast")],
        [InlineKeyboardButton(text="⚙️ Bot sozlamalari", callback_data="owner_settings")],
        [InlineKeyboardButton(text="👥 Obunachilarni boshqarish", callback_data="manage_users")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text=("📣 Yangi kontent xabari: YOQILGAN" if load_data().get("settings", {}).get("notify_new_content", True) else "📣 Yangi kontent xabari: O‘CHIRILGAN"), callback_data="owner_notify_toggle")],
    ])


def normalize_media_title(value: str) -> str:
    """Sarlavhani kanal captioni va botdagi media nomi bilan solishtirish uchun normallashtiradi."""
    value = re.sub(r"#\w+", " ", value or "", flags=re.UNICODE)
    value = re.sub(r"^(nom|title|kino|media)\s*:\s*", "", value.strip(), flags=re.I)
    return " ".join(value.casefold().split()).strip(" .-–—:")


def catalog_item_for_media(data: dict, media: dict):
    target = normalize_media_title(media.get("name", ""))
    if not target:
        return None, None
    for key, item in data.get("channel_catalog", {}).items():
        if normalize_media_title(item.get("name", "")) == target:
            return key, item
    return None, None


def build_pager_row(left_button, middle_button, right_button):
    """Standart sahifalash: ⬅️ 1/3 ➡️. Episode raqamlari bundan mustasno."""
    buttons = [b for b in (left_button, middle_button, right_button) if b is not None]
    return [buttons]


def episode_keyboard(code: str, episodes: dict, catalog_key=None, catalog_item=None, user_id=None, season="1", media=None, page=0):
    """Qismlar faqat 1, 2, 3 ko‘rinishida; ko‘p faslda avval fasl tanlanadi."""
    rows, row = [], []
    media = media or {}
    seasons = media.get("seasons") or {"1": {"episodes": episodes}}
    def sort_key(value):
        return (0, int(value)) if str(value).isdigit() else (1, str(value))
    season_keys = sorted(seasons, key=sort_key)
    tier = "regular"
    if len(season_keys) > 1 and season == "choose":
        visible_seasons = []
        for sn in season_keys:
            eps = seasons.get(sn, {}).get("episodes", {})
            if any((isinstance(rec, dict) and (tier in rec or "chat_id" in rec or "file_id" in rec)) for rec in eps.values()):
                visible_seasons.append(sn)
        # Fasllar oldindan kiritilgan, ammo postlar hali kelmagan bo‘lsa, fasllarni ham ko‘rsatamiz.
        if not visible_seasons:
            visible_seasons = season_keys
        for sn in visible_seasons:
            row.append(InlineKeyboardButton(text=f"{sn}-fasl", callback_data=f"season_show:{code}:{sn}"))
            if len(row) == 3:
                rows.append(row); row=[]
        if row: rows.append(row)
    else:
        season_data = seasons.get(str(season), {"episodes": episodes})
        season_episodes = season_data.get("episodes", episodes)
        available_numbers = []
        for number, record in season_episodes.items():
            if isinstance(record, dict) and "regular" in record and isinstance(record["regular"], dict):
                available_numbers.append(number)
            elif isinstance(record, dict) and "file_id" in record:
                # Ruchnoy qo'shilgan fayllar kanal sozlamalaridan mustaqil.
                available_numbers.append(number)
            elif isinstance(record, dict) and "chat_id" in record:
                # Eski bitta-bazali format faqat ikki baza sozlanmagan holatda ishlaydi.
                available_numbers.append(number)
        sorted_numbers = sorted(available_numbers, key=sort_key)
        # Har sahifada 15 ta qism; keyingi sahifalar uchun oldinga/orqaga tugmalari.
        page_size = 15
        total_pages = max(1, (len(sorted_numbers) + page_size - 1) // page_size)
        page = max(0, min(int(page), total_pages - 1))
        page_numbers = sorted_numbers[page * page_size:(page + 1) * page_size]
        # Kino va serial qismlari faqat raqam bilan ko'rsatiladi: 1, 2, 3...
        for number in page_numbers:
            row.append(InlineKeyboardButton(text=str(number), callback_data=f"episode:{code}:{season}:{number}"))
            if len(row) == 5:
                rows.append(row); row=[]
        if row: rows.append(row)
        if total_pages > 1:
            left_btn = InlineKeyboardButton(text="⬅️", callback_data=f"season_page:{code}:{season}:{page-1}") if page > 0 else None
            middle_btn = InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="page_info")
            right_btn = InlineKeyboardButton(text="➡️", callback_data=f"season_page:{code}:{season}:{page+1}") if page < total_pages - 1 else None
            rows.extend(build_pager_row(left_btn, middle_btn, right_btn))
        if len(season_keys) > 1:
            rows.append([InlineKeyboardButton(text="⬅️ Fasllar", callback_data=f"season_show:{code}:choose")])
    rows.append([InlineKeyboardButton(text="♥️ Sevimlilarga qo‘shish", callback_data=f"favorite_add:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def media_caption(code: str, media: dict) -> str:
    description = (media.get("description") or "").strip()
    kind = {"movie":"🎞 Kino", "series":"📺 Serial", "drama":"🎭 Drama", "anime":"🍥 Anime", "cartoon":"🎨 Multfilm"}.get(media.get("media_type"), "🎬 Kontent")
    seasons = media.get("seasons") or {"1": {"episodes": media.get("episodes", {})}}
    total_episodes = sum(len(value.get("episodes", {})) for value in seasons.values())
    base = f"{kind}: <b>{media['name']}</b>\n\n🔢 Kod: <code>{code}</code>\n📺 Fasllar: {len(seasons)}\n🎞 Qismlar: {total_episodes}"
    if media.get("status"):
        base += f"\n📊 Holati: {media['status']}"
    if media.get("quality"):
        base += f"\n🎥 Sifati: {media['quality']}"
    if media.get("genres"):
        base += f"\n🎭 Janri: {media['genres']}"
    if media.get("voice"):
        base += f"\n🎙 Ovoz: {media['voice']}"
    if description:
        base += "\n\n" + description
    return base[:1024]


def media_available_for_user(user_id: int, media: dict, data=None) -> bool:
    """Barcha qo‘shilgan kino va seriallar darhol hamma foydalanuvchiga ochiq."""
    return True


async def send_media_card(chat_id: int, code: str, media: dict):
    data = load_data()
    caption = media_caption(code, media)
    seasons = media.get("seasons") or {"1": {"episodes": media.get("episodes", {})}}
    season_keys = sorted(seasons, key=lambda v: int(v) if str(v).isdigit() else 999999)
    first_season = season_keys[0] if season_keys else "1"
    season_data = seasons.get(first_season, {"episodes": media.get("episodes", {})})
    markup = episode_keyboard(code, season_data.get("episodes", {}), user_id=chat_id, season=("choose" if len(season_keys) > 1 else first_season), media=media)
    poster = media.get("poster_file_id")
    if poster:
        await bot.send_photo(chat_id, poster, caption=caption, reply_markup=markup)
    else:
        await bot.send_message(chat_id, caption, reply_markup=markup)


class Form(StatesGroup):
    owner_restore_file = State()
    search_code = State()
    search_name = State()
    media_code = State()
    media_name = State()
    media_info = State()
    media_details = State()
    media_season_count = State()
    episode_code = State()
    episode_season = State()
    episode_batch = State()
    delete_code = State()
    delete_episode_code = State()
    delete_episode_number = State()
    delete_season_code = State()
    delete_season_number = State()
    rename_code = State()
    rename_name = State()
    recode_old = State()
    recode_new = State()
    admin_id_add = State()
    admin_id_del = State()
    channel_add = State()
    channel_del = State()
    start_text = State()
    manage_user_id = State()
    owner_setting_value = State()
    owner_backup_channel = State()
    broadcast_message = State()
    button_text_value = State()
    edit_poster_code = State()
    edit_poster_file = State()
    admin_permissions_id = State()
    manage_vip_days = State()
    manage_balance_amount = State()
    manage_mute_duration = State()


def parse_channel_entry(entry):
    """Eski format (@username yoki -100... ID) va yangi ID|invite formatini o'qiydi."""
    raw = str(entry).strip()
    if "|" in raw:
        chat_ref, invite_url = (part.strip() for part in raw.split("|", 1))
    else:
        chat_ref, invite_url = raw, ""
    if chat_ref.lstrip("-").isdigit():
        chat_ref = int(chat_ref)
    elif chat_ref.startswith("https://t.me/"):
        # Kanal username URL ko'rinishida berilgan bo'lsa, username'ga aylantiramiz.
        tail = chat_ref.removeprefix("https://t.me/").strip("/")
        chat_ref = "@" + tail if tail and not tail.startswith("+") else chat_ref
    if not invite_url and isinstance(chat_ref, str) and chat_ref.startswith("@"):
        invite_url = "https://t.me/" + chat_ref[1:]
    return chat_ref, invite_url, raw


async def check_subscription(user_id: int) -> bool:
    channels = load_data().get("channels", [])
    for entry in channels:
        chat_ref, _invite_url, raw = parse_channel_entry(entry)
        try:
            member = await bot.get_chat_member(chat_id=chat_ref, user_id=user_id)
            # Restricted a'zolar ham is_member=True bo'lsa obunachi hisoblanadi.
            status = str(member.status)
            status = status.split(".")[-1].lower()
            is_member = status in ("creator", "administrator", "member")
            if status == "restricted":
                is_member = bool(getattr(member, "is_member", False))
            if not is_member:
                logging.info("Subscription missing: user=%s channel=%s status=%s", user_id, raw, status)
                return False
        except Exception as exc:
            logging.exception("get_chat_member failed for channel=%s: %s", raw, exc)
            return False
    return True


def subscription_keyboard():
    rows = []
    for entry in load_data().get("channels", []):
        chat_ref, invite_url, raw = parse_channel_entry(entry)
        # Private kanal uchun taklif havolasi shart; uni admin ID|https://t.me/+... ko'rinishida beradi.
        if invite_url:
            label = str(chat_ref) if str(chat_ref).startswith("@") else "Maxfiy kanal"
            rows.append([InlineKeyboardButton(text=f"📢 {label}", url=invite_url)])
    rows.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def require_subscription_message(message: Message) -> bool:
    data = load_data()
    if int(message.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        await message.answer("⛔ Siz botdan foydalanishdan chetlatilgansiz.")
        return False
    user_record = data.get("users", {}).get(str(message.from_user.id), {})
    mute_until = int(user_record.get("mute_until", 0) or 0)
    if mute_until > now_ts():
        await message.answer(f"🔇 Botdan foydalanish vaqtincha cheklangan.\nQayta foydalanish: {fmt_date(mute_until)}")
        return False
    if await check_subscription(message.from_user.id):
        return True
    await message.answer(
        "🔒 <b>Botdan foydalanishdan oldin majburiy kanallarga obuna bo'ling.</b>",
        reply_markup=subscription_keyboard()
    )
    return False


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    # Avval ro'yxatdan o'tkazish va yangi obunachi xabarini yuborishni kutamiz;
    # create_task bilan parallel ishlatish yangi userni oldindan saqlab, xabarni o'tkazib yuborardi.
    await notify_new_user(message.from_user)
    data = load_data()
    ensure_user(data, message.from_user)
    save_data(data)
    if int(message.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await message.answer("⛔ Siz botdan foydalanishdan chetlatilgansiz.")
    _u = data.get("users", {}).get(str(message.from_user.id), {})
    if int(_u.get("mute_until", 0) or 0) > now_ts():
        return await message.answer(f"🔇 Botdan foydalanish vaqtincha cheklangan.\nQayta foydalanish: {fmt_date(int(_u['mute_until']))}")
    if not await check_subscription(message.from_user.id):
        return await message.answer("🔒 <b>Botdan foydalanishdan oldin majburiy kanallarga obuna bo‘ling.</b>", reply_markup=subscription_keyboard())
    await send_start(message)


@dp.callback_query(F.data == "owner_settings")
async def owner_settings_callback(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    cfg = load_data().get("settings", {})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👥 Referal mukofoti: {cfg.get('referral_reward', 5000)} so‘m", callback_data="setcfg:referral_reward")],
        [InlineKeyboardButton(text=f"👑 VIP 1 oy narxi: {cfg.get('vip_month_price', 15000)} so‘m", callback_data="setcfg:vip_month_price")],
        [InlineKeyboardButton(text=f"📅 VIP maksimal muddati: {cfg.get('vip_max_months', 6)} oy", callback_data="setcfg:vip_max_months")],
        [InlineKeyboardButton(text=f"⏳ VIP oy davomiyligi: {cfg.get('vip_duration_days', 30)} kun", callback_data="setcfg:vip_duration_days")],
        [InlineKeyboardButton(text=f"🎬 Erta ko‘rish: {cfg.get('early_view_days', 1)} kun", callback_data="setcfg:early_view_days")],
        [InlineKeyboardButton(text=f"💬 To‘lov admini: @{str(cfg.get('admin_contact', 'obidov_xz')).lstrip('@')}", callback_data="setcfg:admin_contact")],
        [InlineKeyboardButton(text="⬅️ Owner paneli", callback_data="admin_home")]
    ])
    try:
        await call.message.edit_text("⚙️ <b>Bot sozlamalari</b>", reply_markup=kb)
    except Exception:
        await call.message.answer("⚙️ <b>Bot sozlamalari</b>", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("setcfg:"))
async def owner_setting_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    key = call.data.split(":", 1)[1]
    prompts = {
        "admin_contact": "To‘lov adminining username sini yuboring, masalan @obidov_xz:",
        "vip_month_price": "VIP obunasining 1 oylik narxini so‘mda yuboring:",
        "referral_reward": "Har bir referal uchun beriladigan mukofotni so‘mda yuboring:",
        "vip_max_months": "VIP uchun ruxsat etilgan maksimal oy sonini yuboring:",
        "vip_duration_days": "VIP obunasining bir oyi necha kun bo‘lishini yuboring:",
        "early_view_days": "VIP erta ko‘rish necha kun oldin bo‘lishini yuboring:",
    }
    if key not in prompts:
        return await call.answer("Noma'lum sozlama", show_alert=True)
    await state.set_state(Form.owner_setting_value)
    await state.update_data(setting_key=key)
    await call.message.answer("⚙️ " + prompts[key] + "\nBekor qilish: /cancel")
    await call.answer()


@dp.message(Form.owner_setting_value)
async def owner_setting_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Faqat owner uchun.")
    if (message.text or "").strip() == "/cancel":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=owner_menu())
    saved = await state.get_data()
    key = saved.get("setting_key")
    raw = (message.text or "").strip()
    limits = {}
    if key in limits:
        try:
            value = int(raw)
        except ValueError:
            return await message.answer("Faqat raqam yuboring:")
        low, high = limits[key]
        if not low <= value <= high:
            return await message.answer(f"Qiymat {low} dan {high} gacha bo'lishi kerak. Qayta yuboring:")
    elif key == "admin_contact":
        value = raw
        if not value.startswith("@") or len(value) < 2 or " " in value:
            return await message.answer("Username @ bilan boshlansin, masalan @AnimBot_Admin. Qayta yuboring:")
    elif key in {"vip_max_months", "vip_duration_days", "early_view_days", "vip_month_price", "referral_reward"}:
        try: value = int(raw)
        except ValueError: return await message.answer("Faqat raqam yuboring:")
        if value < 0: return await message.answer("Qiymat manfiy bo‘lishi mumkin emas:")
        if key in {"vip_max_months", "vip_duration_days"} and value < 1:
            return await message.answer("Bu qiymat kamida 1 bo‘lishi kerak:")
    else:
        await state.clear()
        return await message.answer("Sozlama topilmadi.")
    data = load_data()
    data.setdefault("settings", {})[key] = value
    save_data(data)
    await state.clear()
    labels = {"admin_contact": "To‘lov admini", "vip_month_price": "VIP oylik narxi", "referral_reward": "Referal mukofoti", "vip_max_months": "VIP maksimal muddati", "vip_duration_days": "VIP oy davomiyligi", "early_view_days": "Erta ko‘rish"}
    await message.answer(f"✅ {labels.get(key, key)} yangilandi: <b>{value}</b>\nO‘zgarish darhol kuchga kirdi.", reply_markup=owner_menu())


@dp.callback_query(F.data == "manage_users")
async def manage_users_callback(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    await state.set_state(Form.manage_user_id)
    await call.message.answer("👥 <b>Obunachilarni boshqarish</b>\nBoshqariladigan foydalanuvchi ID sini yuboring:")
    await call.answer()


@dp.callback_query(F.data == "backup_channel_change")
async def backup_channel_change_callback(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    current = get_backup_channel_id() or "sozlanmagan"
    await state.set_state(Form.owner_backup_channel)
    await call.message.answer(
        "🗄 <b>Zaxira kanalini sozlash</b>\n"
        f"Hozirgi kanal ID: <code>{current}</code>\n\n"
        "Yangi kanal ID sini yuboring (masalan, <code>-1001234567890</code>).\n"
        "O‘chirish uchun <code>off</code> yuboring. Bot o‘sha kanalga admin bo‘lishi kerak.\n"
        "Bekor qilish: /cancel"
    )
    await call.answer()


@dp.message(Form.owner_backup_channel)
async def backup_channel_change_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Faqat owner uchun.")
    raw = (message.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=owner_menu())
    data = load_data()
    if raw.casefold() in {"off", "o‘chirish", "ochirish"}:
        data.setdefault("settings", {}).pop("backup_channel_id", None)
        save_data(data)
        await state.clear()
        return await message.answer("✅ Zaxira kanal sozlamasi olib tashlandi. ENV dagi BACKUP_CHANNEL_ID ishlatiladi; u ham bo‘sh bo‘lsa, zaxira shaxsiy xabarga yuboriladi.", reply_markup=owner_menu())
    try:
        channel_id = int(raw)
        if channel_id >= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Kanal ID manfiy raqam bo‘lishi kerak, masalan -1001234567890. Qayta yuboring:")
    data.setdefault("settings", {})["backup_channel_id"] = str(channel_id)
    save_data(data)
    await state.clear()
    await message.answer(f"✅ Zaxira kanali saqlandi: <code>{channel_id}</code>\nBot kanalga admin qilinganini tekshiring.", reply_markup=owner_menu())


@dp.callback_query(F.data == "owner_backup")
async def owner_backup_callback(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    data = load_data()
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    doc = BufferedInputFile(payload, filename=f"bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    try:
        backup_channel_id = get_backup_channel_id()
        if backup_channel_id:
            await bot.send_document(backup_channel_id, doc, caption="🤖 Bot avtomatik zaxirasi")
            await call.message.answer("✅ Zaxira JSON fayli BACKUP_CHANNEL_ID kanaliga yuborildi.", reply_markup=owner_menu())
        else:
            await bot.send_document(call.from_user.id, doc, caption="💾 Bot zaxirasi. Xavfsiz joyda saqlang.")
            await call.message.answer("✅ Zaxira sizga shaxsiy xabarda yuborildi. Renderda BACKUP_CHANNEL_ID kiritsangiz, kanalga ham yuboriladi.", reply_markup=owner_menu())
    except Exception as exc:
        await call.message.answer(f"❌ Zaxira yuborilmadi. Kanal IDsi va bot adminligini tekshiring. ({type(exc).__name__})")
    await call.answer()

@dp.callback_query(F.data == "owner_restore_help")
async def owner_restore_help(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    await state.set_state(Form.owner_restore_file)
    await state.update_data(restore_files=0)
    await call.message.answer(
        "♻️ <b>Zaxirani tiklash</b>\n"
        "Bitta JSON fayl yoki ichida bir nechta .json bo'lgan ZIP arxiv yuboring. "
        "Bir nechta JSONni alohida yubormoqchi bo'lsangiz, ketma-ket yuboring va oxirida /done yozing.\n"
        "Yuborilgan JSONlar birlashtiriladi: medias kodlari qo'shiladi/yangilanadi, qolgan sozlamalar oxirgi fayldan olinadi.\n"
        "Bekor qilish: /cancel"
    )
    await call.answer()


def _merge_backup_objects(base, incoming):
    if not isinstance(incoming, dict):
        raise ValueError("JSON ildizi obyekt bo'lishi kerak")
    if not isinstance(incoming.get("medias", {}), dict):
        raise ValueError("medias bo'limi noto'g'ri")
    # Har bir zaxiradagi media kodlarini saqlab qolamiz; bir xil kod bo'lsa yangi qiymat ustun.
    base.setdefault("medias", {}).update(incoming.get("medias", {}))
    for key, value in incoming.items():
        if key == "medias":
            continue
        if key == "users" and isinstance(value, dict):
            base.setdefault("users", {}).update(value)
        elif key == "settings" and isinstance(value, dict):
            base.setdefault("settings", {}).update(value)
        elif key in ("admins", "channels", "banned_users") and isinstance(value, list):
            current = base.setdefault(key, [])
            base[key] = list(dict.fromkeys(current + value))
        else:
            base[key] = value
    return base


@dp.message(Form.owner_restore_file)
async def owner_restore_file(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    if message.text and message.text.strip().lower() in ("/done", "done", "tayyor"):
        saved = await state.get_data()
        files_count = int(saved.get("restore_files", 0))
        if not files_count:
            return await message.answer("❌ Hali JSON yuborilmadi. JSON yoki ZIP yuboring:")
        restored = saved.get("restore_data")
        if not isinstance(restored, dict):
            return await message.answer("❌ Tiklash ma'lumotlari topilmadi. Qaytadan boshlang: /cancel")
        base = default_data()
        for key, value in base.items():
            restored.setdefault(key, value)
        restored.setdefault("settings", {})
        for key, value in base["settings"].items():
            restored["settings"].setdefault(key, value)
        save_data(restored)
        await state.clear()
        return await message.answer(f"✅ Tiklandi. {files_count} ta JSON fayl birlashtirildi; jami {len(restored.get('medias', {}))} ta kontent kodi mavjud.", reply_markup=owner_menu())
    if not message.document:
        return await message.answer("JSON yoki ichida JSON fayllari bo'lgan ZIP arxiv yuboring. Bir nechta fayl uchun oxirida /done yozing.")
    fname = (message.document.file_name or "").lower()
    if not (fname.endswith(".json") or fname.endswith(".zip")):
        return await message.answer("❌ Faqat .json yoki .zip fayl qabul qilinadi.")
    try:
        file = await bot.get_file(message.document.file_id)
        stream = await bot.download_file(file.file_path)
        raw = stream.read() if hasattr(stream, "read") else bytes(stream)
        json_blobs = []
        if fname.endswith(".json"):
            json_blobs = [raw]
        else:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                json_blobs = [archive.read(n) for n in archive.namelist() if n.lower().endswith(".json") and not n.endswith("/")]
            if not json_blobs:
                return await message.answer("❌ ZIP ichidan .json fayl topilmadi.")
        saved = await state.get_data()
        base = saved.get("restore_data") or default_data()
        for blob in json_blobs:
            incoming = json.loads(blob.decode("utf-8-sig"))
            base = _merge_backup_objects(base, incoming)
        count = int(saved.get("restore_files", 0)) + len(json_blobs)
        await state.update_data(restore_data=base, restore_files=count)
        await message.answer(f"✅ {len(json_blobs)} ta JSON o'qildi. Jami: {count} ta. Yana fayl yuboring yoki tiklashni yakunlash uchun /done yozing.")
    except Exception as exc:
        logging.exception("Backup restore failed")
        await message.answer(f"❌ Zaxirani o'qib bo'lmadi: {type(exc).__name__}: {str(exc)[:180]}. JSON fayl bot yaratgan zaxira ekanini tekshiring.")

@dp.callback_query(F.data == "owner_maintenance")
async def owner_maintenance_callback(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    data = load_data(); cfg = data.setdefault("settings", {})
    cfg["maintenance"] = not bool(cfg.get("maintenance", False)); save_data(data)
    status = "YOQILDI" if cfg["maintenance"] else "O'CHIRILDI"
    await call.message.answer(f"🛠 Texnik tanaffus {status}.", reply_markup=owner_menu())
    await call.answer()

@dp.callback_query(F.data == "owner_vip")
async def owner_vip_callback(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    data = load_data(); cfg = data.setdefault("settings", {})
    cfg["vip_enabled"] = not bool(cfg.get("vip_enabled", False)); save_data(data)
    status = "YOQILDI" if cfg["vip_enabled"] else "O'CHIRILDI"
    detail = "Foydalanuvchilarga VIP olish va VIP nima tugmalari ko'rsatiladi." if cfg["vip_enabled"] else "Foydalanuvchilardan VIP tugmalari yashiriladi."
    await call.message.answer(f"💎 VIP rejimi {status}. {detail}", reply_markup=owner_menu())
    await call.answer()

@dp.callback_query(F.data == "vip_info")
async def vip_info_callback(call: CallbackQuery):
    cfg = load_data().get("settings", {})
    if not cfg.get("vip_enabled", False) and not is_owner(call.from_user.id):
        return await call.answer("VIP hozircha o‘chirilgan.", show_alert=True)
    price = int(cfg.get("vip_month_price", 15000) or 15000)
    text = (
        "👑 <b>VIP — tomoshadan ko‘proq zavq oling!</b>\n\n"
        "VIP obunasi orqali sevimli anime, serial va filmlaringizni yanada qulayroq tomosha qiling! 💎\n\n"
        "✨ <b>VIP imkoniyatlari:</b>\n\n"
        "🚀 3 kun oldin tomosha qilish\n"
        "🎬 720p–1080p sifatdagi videolar (manbada mavjud bo‘lsa)\n"
        "🔓 Majburiy kanallarga obunasiz kirish\n"
        "📤 Kontentni ulashish imkoniyati\n"
        "♥️ Sevimlilar va ▶️ Davom ettirish\n\n"
        f"💎 VIP narxi: oyiga {price:,} so‘m".replace(",", " ")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")],
    ])
    try: await call.message.edit_text(text, reply_markup=kb)
    except Exception: await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "vip_buy")
async def vip_buy_callback(call: CallbackQuery):
    data = load_data(); cfg = data.get("settings", {})
    if not cfg.get("vip_enabled", False) and not is_owner(call.from_user.id):
        return await call.answer("VIP hozircha o'chirilgan.", show_alert=True)
    price = int(cfg.get("vip_month_price", 15000) or 15000)
    max_months = max(1, min(24, int(cfg.get("vip_max_months", 6) or 6)))
    rows = [[InlineKeyboardButton(text=f"{m} oy — {price*m:,} so'm".replace(',', ' '), callback_data=f"vip_plan:{m}")] for m in range(1, max_months+1)]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="vip_info")])
    text = f"💎 <b>VIP sotib olish</b>\n\n1 oy: {price:,} so'm\nKerakli muddatni tanlang. Xarid ikki marta tasdiqlanadi va pul balansingizdan yechiladi.".replace(',', ' ')
    try: await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception: await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data.startswith("vip_plan:"))
async def vip_plan_callback(call: CallbackQuery):
    data=load_data(); cfg=data.get("settings", {})
    if not cfg.get("vip_enabled", False) and not is_owner(call.from_user.id):
        return await call.answer("VIP hozircha o'chirilgan.", show_alert=True)
    try: months=int(call.data.split(':',1)[1])
    except ValueError: return await call.answer("Muddat noto'g'ri", show_alert=True)
    max_months=max(1,min(24,int(cfg.get('vip_max_months',6) or 6)))
    if not 1 <= months <= max_months: return await call.answer("Bu muddat mavjud emas", show_alert=True)
    price=int(cfg.get('vip_month_price',15000) or 15000)*months
    user=data.setdefault('users',{}).setdefault(str(call.from_user.id),{})
    balance=float(user.get('balance',0) or 0)
    if balance < price:
        return await call.answer(f"Balansingiz yetarli emas. Kerak: {price:,} so'm; balans: {balance:g} so'm".replace(',', ' '), show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, sotib olaman", callback_data=f"vip_confirm:{months}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="vip_buy")]
    ])
    text=f"⚠️ <b>Xaridni tasdiqlash</b>\n\nVIP muddati: {months} oy\nNarxi: {price:,} so'm\nBalansingiz: {balance:g} so'm\nXaridni davom ettirish uchun yana bir marta tasdiqlang.".replace(',', ' ')
    try: await call.message.edit_text(text,reply_markup=kb)
    except Exception: await call.message.answer(text,reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("vip_confirm:"))
async def vip_confirm_callback(call: CallbackQuery):
    try: months=int(call.data.split(':',1)[1])
    except ValueError: return await call.answer("Muddat noto'g'ri",show_alert=True)
    data=load_data(); cfg=data.get('settings',{})
    if not cfg.get('vip_enabled',False) and not is_owner(call.from_user.id): return await call.answer("VIP hozircha o'chirilgan.",show_alert=True)
    max_months=max(1,min(24,int(cfg.get('vip_max_months',6) or 6)))
    if not 1 <= months <= max_months: return await call.answer("Muddat noto'g'ri",show_alert=True)
    user=data.setdefault('users',{}).setdefault(str(call.from_user.id),{})
    price=int(cfg.get('vip_month_price',15000) or 15000)*months
    balance=float(user.get('balance',0) or 0)
    if balance < price: return await call.answer("Balansingiz yetarli emas.",show_alert=True)
    # Takroriy callbackdan ikki marta pul yechilmasligi uchun balans tekshiruvi va yozish bir amalda.
    user['balance']=balance-price
    base=max(now_ts(),int(user.get('vip_until',0) or 0))
    user['vip_until']=base+months*int(cfg.get('vip_duration_days',30) or 30)*86400
    user.pop('vip_warned_until',None)
    save_data(data)
    text=f"✅ <b>VIP faollashtirildi!</b>\nMuddat: {months} oy\nTo'landi: {price:,} so'm\nQolgan balans: {user['balance']:g} so'm\nTugash vaqti: {fmt_date(user['vip_until'])}".replace(',', ' ')
    try: await call.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Asosiy menyu",callback_data="home")]]))
    except Exception: await call.message.answer(text)
    await call.answer("VIP sotib olindi")


@dp.callback_query(F.data == "admin_home")
async def admin_home_callback(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    if is_owner(call.from_user.id):
        await call.message.answer("👑 <b>Owner paneli</b>", reply_markup=owner_menu())
    else:
        await call.message.answer("🛠 <b>Admin paneli</b>", reply_markup=admin_menu(call.from_user.id))
    await call.answer()


@dp.message(F.text.regexp(r"^/manage(?:\s+\d+)?$"))
async def manage_user_command(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ Adminlar uchun.")
    parts = (message.text or "").split()
    if len(parts) == 1:
        await state.set_state(Form.manage_user_id)
        return await message.answer("Boshqariladigan foydalanuvchi Telegram ID sini yuboring:")
    await show_user_management(message, int(parts[1]))


async def show_user_management(message: Message, user_id: int):
    data = load_data()
    user = data.get("users", {}).get(str(user_id))
    if not user:
        return await message.answer("❌ Bu foydalanuvchi bazada topilmadi. U botda /start bosgan bo‘lishi kerak.")
    banned = user_id in [int(x) for x in data.get("banned_users", [])]
    vip_until = int(user.get("vip_until", 0) or 0)
    mute_until = int(user.get("mute_until", 0) or 0)
    balance = float(user.get("balance", 0) or 0)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 VIP berish / muddatini uzaytirish", callback_data=f"uservip:{user_id}"), InlineKeyboardButton(text="❌ VIPni olish", callback_data=f"uservipremove:{user_id}")],
        [InlineKeyboardButton(text="💰 Balansga pul qo‘shish", callback_data=f"userbalance:{user_id}"), InlineKeyboardButton(text="💸 Balansdan ayirish", callback_data=f"userbalanceout:{user_id}")],
        [InlineKeyboardButton(text="🔇 Mute qilish", callback_data=f"usermute:{user_id}"), InlineKeyboardButton(text="🔊 Mute bekor qilish", callback_data=f"usermuteoff:{user_id}")],
        [InlineKeyboardButton(text="🚫 Ban qilish" if not banned else "✅ Unban qilish", callback_data=f"userban:{user_id}")],
        [InlineKeyboardButton(text="⬅️ Owner paneli", callback_data="admin_home")]
    ])
    await message.answer(
        f"👤 <b>Foydalanuvchi</b>\nID: <code>{user_id}</code>\nUsername: @{user.get('username') or 'yo‘q'}"
        f"\nVIP: {'Faol (' + fmt_date(vip_until) + ')' if vip_until > now_ts() else 'Faol emas'}"
        f"\nBalans: {balance:g} so‘m\nMute: {fmt_date(mute_until) if mute_until > now_ts() else 'Yo‘q'}"
        f"\nBan: {'Ha' if banned else 'Yo‘q'}", reply_markup=kb)


@dp.message(Form.manage_user_id)
async def manage_user_id_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Adminlar uchun.")
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        return await message.answer("Faqat Telegram ID raqamini yuboring:")
    await state.clear()
    await show_user_management(message, user_id)


@dp.callback_query(F.data.startswith("userban:"))
async def user_ban_callback(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    user_id = int(call.data.split(":", 1)[1])
    data = load_data()
    banned = [int(x) for x in data.get("banned_users", [])]
    if user_id in banned:
        data["banned_users"] = [x for x in banned if x != user_id]
        result = "unban qilindi"
    else:
        data.setdefault("banned_users", []).append(user_id)
        result = "ban qilindi"
    save_data(data)
    await call.answer(f"Foydalanuvchi {result}", show_alert=True)
    await show_user_management(call.message, user_id)












@dp.callback_query(F.data.startswith("uservip:"))
async def user_vip_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id): return await call.answer("Faqat owner uchun", show_alert=True)
    uid=int(call.data.split(":",1)[1]); await state.update_data(manage_uid=uid); await state.set_state(Form.manage_vip_days)
    await call.message.answer("VIP muddatini kunlarda yuboring (masalan, 30). 0 yuborsangiz cheksiz VIP beriladi:")
    await call.answer()

@dp.message(Form.manage_vip_days)
async def user_vip_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return await state.clear()
    try: days=int((message.text or "").strip()); assert days >= 0
    except Exception: return await message.answer("0 yoki musbat kun sonini yuboring:")
    ctx=await state.get_data(); uid=int(ctx["manage_uid"]); data=load_data(); user=data.get("users",{}).get(str(uid))
    if not user: await state.clear(); return await message.answer("Foydalanuvchi topilmadi.")
    user["vip_until"] = 0 if days == 0 else now_ts()+days*86400
    save_data(data); await state.clear(); await message.answer("✅ VIP yangilandi."); await show_user_management(message,uid)

@dp.callback_query(F.data.startswith("uservipremove:"))
async def user_vip_remove(call: CallbackQuery):
    if not is_owner(call.from_user.id): return await call.answer("Faqat owner uchun", show_alert=True)
    uid=int(call.data.split(":",1)[1]); data=load_data(); user=data.get("users",{}).get(str(uid),{})
    user["vip_until"]=0; save_data(data); await call.answer("VIP bekor qilindi",show_alert=True); await show_user_management(call.message,uid)

@dp.callback_query(F.data.startswith(("userbalance:","userbalanceout:")))
async def user_balance_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id): return await call.answer("Faqat owner uchun", show_alert=True)
    mode,uid=call.data.split(":",1); await state.update_data(manage_uid=int(uid), balance_mode=mode); await state.set_state(Form.manage_balance_amount)
    await call.message.answer("Summani so‘mda yuboring (faqat musbat son):"); await call.answer()

@dp.message(Form.manage_balance_amount)
async def user_balance_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return await state.clear()
    try: amount=float((message.text or "").strip().replace(",",".")); assert amount > 0
    except Exception: return await message.answer("Musbat summa kiriting:")
    ctx=await state.get_data(); uid=int(ctx["manage_uid"]); data=load_data(); user=data.get("users",{}).get(str(uid))
    if not user: await state.clear(); return await message.answer("Foydalanuvchi topilmadi.")
    old=float(user.get("balance",0) or 0); user["balance"] = old + amount if ctx["balance_mode"]=="userbalance" else max(0,old-amount)
    save_data(data); await state.clear(); await message.answer("✅ Balans yangilandi."); await show_user_management(message,uid)

@dp.callback_query(F.data.startswith("usermute:"))
async def user_mute_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id): return await call.answer("Faqat owner uchun", show_alert=True)
    uid=int(call.data.split(":",1)[1]); await state.update_data(manage_uid=uid); await state.set_state(Form.manage_mute_duration)
    await call.message.answer("Mute muddatini daqiqalarda yuboring (masalan, 60 = 1 soat, 1440 = 1 kun):"); await call.answer()

@dp.message(Form.manage_mute_duration)
async def user_mute_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return await state.clear()
    try: minutes=int((message.text or "").strip()); assert minutes > 0
    except Exception: return await message.answer("Musbat daqiqa sonini yuboring:")
    ctx=await state.get_data(); uid=int(ctx["manage_uid"]); data=load_data(); user=data.get("users",{}).get(str(uid))
    if not user: await state.clear(); return await message.answer("Foydalanuvchi topilmadi.")
    user["mute_until"] = now_ts()+minutes*60; save_data(data); await state.clear(); await message.answer("✅ Mute muddati saqlandi."); await show_user_management(message,uid)

@dp.callback_query(F.data.startswith("usermuteoff:"))
async def user_mute_off(call: CallbackQuery):
    if not is_owner(call.from_user.id): return await call.answer("Faqat owner uchun", show_alert=True)
    uid=int(call.data.split(":",1)[1]); data=load_data(); data.get("users",{}).setdefault(str(uid),{})["mute_until"]=0
    save_data(data); await call.answer("Mute bekor qilindi",show_alert=True); await show_user_management(call.message,uid)

@dp.message(Command("backup"))
async def backup_command(message: Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ Bu buyruq faqat owner uchun.")
    payload = json.dumps(load_data(), ensure_ascii=False, indent=2).encode("utf-8")
    doc = BufferedInputFile(payload, filename=f"bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    backup_channel_id = get_backup_channel_id()
    if backup_channel_id:
        await bot.send_document(backup_channel_id, doc, caption="🤖 Bot zaxirasi")
        await message.answer("✅ Zaxira kanalga yuborildi.")
    else:
        await message.answer_document(doc, caption="💾 Bot zaxirasi")

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Sizda admin huquqi yo'q.")
    try: await message.delete()
    except Exception: pass
    await message.answer("🎬 <b>Kino va serial boshqaruvi</b>", reply_markup=admin_menu(message.from_user.id))


@dp.message(Command("owner"))
async def owner_command(message: Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ Bu buyruq faqat bot egasi uchun.")
    try: await message.delete()
    except Exception: pass
    await message.answer("⚙️ <b>Owner paneli</b>", reply_markup=owner_menu())


@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    data = load_data()
    try:
        await call.message.edit_text(data["start_text"], reply_markup=main_menu(call.from_user.id))
    except Exception:
        await call.message.answer(data["start_text"], reply_markup=main_menu(call.from_user.id))
    await call.answer()


@dp.callback_query(F.data == "search_menu")
async def search_menu_open(call: CallbackQuery):
    try:
        await call.message.edit_text("🔎 <b>Kino va video izlash</b>", reply_markup=search_menu(call.from_user.id))
    except Exception:
        await call.message.answer("🔎 <b>Kino va video izlash</b>", reply_markup=search_menu(call.from_user.id))
    await call.answer()


@dp.callback_query(F.data == "guide")
async def guide(call: CallbackQuery):
    text = "📖 <b>Qo'llanma</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛑 Orqaga", callback_data="home")]])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


def _catalog_key(text: str, tags: list[str]) -> str:
    # #1, #2 kabi raqamli teglar qism raqami; qolgan teglar guruh identifikatori.
    identity = next((tag.casefold() for tag in tags if not tag.isdigit()), "")
    if not identity:
        identity = re.sub(r"#\w+", " ", text.splitlines()[0] if text else "")
        identity = re.sub(r"^(nom|title|kino|media)\s*:\s*", "", identity, flags=re.I)
        identity = " ".join(identity.casefold().split())
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12] if identity else ""


@dp.channel_post()
async def index_storage_channel_post(message: Message):
    """Bitta kontent bazasi kanalidagi postlarni kod, fasl va qism bo‘yicha indekslaydi.

    Caption misoli: #kino_25 #season_2 #3 yoki #serial_25 #season_2 #ep_3.
    Bot ushbu bazaviy kanalda administrator bo‘lishi kerak.
    """
    data = load_data()
    settings = data.get("settings", {})
    channel_id = str(message.chat.id)
    catalog_ref = str(settings.get("catalog_channel_id") or CHANNEL_ID or "").strip()
    def matches(ref):
        if not ref:
            return False
        if ref.startswith("@"):
            return (message.chat.username or "").casefold() == ref[1:].casefold()
        return channel_id == ref
    tier = "regular" if matches(catalog_ref) else ""
    shared_base = bool(matches(catalog_ref))
    if not tier:
        return
    text = (message.caption or message.text or "").strip()
    tags = re.findall(r"#([\w]+)", text, flags=re.UNICODE)
    code_tag = next((tag.split("_", 1)[1] for tag in tags if tag.casefold().startswith(("kino_", "serial_", "movie_")) and tag.split("_", 1)[1].isdigit()), None)
    season_tag = next((tag.split("_", 1)[1] for tag in tags if tag.casefold().startswith(("season_", "fasl_")) and tag.split("_", 1)[1].isdigit()), "1")
    part_tag = next((tag.split("_", 1)[1] for tag in tags if tag.casefold().startswith(("ep_", "qism_")) and tag.split("_", 1)[1].isdigit()), None)
    if not part_tag:
        part_tag = next((tag for tag in tags if tag.isdigit()), None)
    if code_tag and code_tag in data.get("medias", {}) and part_tag:
        media = data["medias"][code_tag]
        season = media.setdefault("seasons", {}).setdefault(str(int(season_tag)), {"episodes": {}})
        season.setdefault("episodes", {}).setdefault(str(int(part_tag)), {})[tier] = {"chat_id": message.chat.id, "message_id": message.message_id}
        # Legacy `episodes` mapping retained for old content and old callbacks.
        if str(int(season_tag)) == "1":
            media.setdefault("episodes", {}).setdefault(str(int(part_tag)), {})[tier] = {"chat_id": message.chat.id, "message_id": message.message_id}
        save_data(data)
        logging.info("Indexed %s: code=%s season=%s episode=%s", tier, code_tag, season_tag, part_tag)
        return
    # Backward compatibility for the old title #1 format; do not mix tiers.
    if not text:
        return
    old_tags = [tag for tag in tags if "_" not in tag]
    old_part = next((tag for tag in old_tags if tag.isdigit()), None)
    title_lines = [re.sub(r"#([\w]+)", "", line).strip(" \t-|:") for line in text.splitlines()]
    title_lines = [line for line in title_lines if line]
    title = title_lines[0] if title_lines else ""
    matched_code = next((code for code, item in data.get("medias", {}).items() if normalize_media_title(item.get("name", "")) == normalize_media_title(title)), None)
    if matched_code and old_part:
        media = data["medias"][matched_code]
        season = media.setdefault("seasons", {}).setdefault("1", {"episodes": {}})
        season.setdefault("episodes", {}).setdefault(str(int(old_part)), {})[tier] = {"chat_id": message.chat.id, "message_id": message.message_id}
        save_data(data)
        return
    # Eski umumiy katalog indekslashni saqlab qolamiz.
    key = _catalog_key(text, tags)
    if not key:
        return
    item = data.setdefault("channel_catalog", {}).setdefault(key, {"name": "", "description": "", "search_text": "", "parts": {}, "updated_at": now_ts()})
    if old_part:
        item["name"] = item.get("name") or title or "Nomsiz"
        item.setdefault("parts", {})[str(int(old_part))] = {"chat_id": message.chat.id, "message_id": message.message_id, "tier": tier}
    elif title:
        item["name"] = title
        item["description"] = "\n".join(title_lines[1:]).strip()
        item["info_chat_id"] = message.chat.id
        item["info_message_id"] = message.message_id
    item["search_text"] = (item.get("search_text", "") + " " + text).casefold()
    item["updated_at"] = now_ts()
    save_data(data)


@dp.callback_query(F.data.startswith("catalog_show:"))
async def catalog_show(call: CallbackQuery):
    data = load_data()
    key = call.data.split(":", 1)[1]
    item = data.get("channel_catalog", {}).get(key)
    if not item:
        return await call.answer("Bu kino bazada topilmadi.", show_alert=True)
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    parts = item.get("parts", {})
    rows, row = [], []
    tier = "regular"
    visible_parts = {number: part for number, part in parts.items() if part.get("tier") in (None, "", tier)}
    for number in sorted(visible_parts, key=lambda value: int(value) if str(value).isdigit() else 999999):
        row.append(InlineKeyboardButton(text=str(number), callback_data=f"catalog_part:{key}:{number}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Qidiruvga qaytish", callback_data="search_menu")])
    description = item.get("description", "")
    caption = f"🎬 <b>{item.get('name', 'Nomsiz kino')}</b>\n\n{description}\n\n📼 Qismlar: {len(parts)} ta"[:3900]
    await call.message.answer(caption, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data.startswith("catalog_part:"))
async def catalog_send_part(call: CallbackQuery):
    data = load_data()
    try:
        _, key, number = call.data.split(":", 2)
        item = data["channel_catalog"][key]
        part = item["parts"][number]
        tier = "regular"
        if part.get("tier") not in (None, "", tier):
            return await call.answer("Bu qism noto'g'ri bazaga biriktirilgan.", show_alert=True)
    except (KeyError, ValueError):
        return await call.answer("Qism topilmadi.", show_alert=True)
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    user = data.setdefault("users", {}).setdefault(str(call.from_user.id), {})
    matched_code = next((code for code, media in data.get("medias", {}).items() if normalize_media_title(media.get("name", "")) == normalize_media_title(item.get("name", ""))), None)
    user["last_watched"] = {"catalog_key": key, "code": matched_code, "number": number, "media_type": "movie", "updated_at": now_ts()}
    save_data(data)
    try:
        await bot.copy_message(chat_id=call.from_user.id, from_chat_id=part["chat_id"], message_id=part["message_id"], protect_content=False)
        await call.answer(f"{number}-qism yuborildi")
    except Exception:
        logging.exception("Catalog part copy failed")
        await call.answer("Qism yuborilmadi. Botning kanal adminligi va postni tekshiring.", show_alert=True)


@dp.callback_query(F.data == "search_name")
async def search_name_start(call: CallbackQuery, state: FSMContext):
    await state.update_data(search_filter="all")
    await state.set_state(Form.search_name)
    await call.message.answer("🔤 Qidirilayotgan kino yoki serial nomini yozing. Har safar yuborganingizda mos nomlar tanlash uchun chiqadi:")
    await call.answer()


@dp.callback_query(F.data.startswith("category:"))
async def category_search_start(call: CallbackQuery, state: FSMContext):
    media_type = call.data.split(":", 1)[1]
    labels = {"movie":"kino", "series":"serial", "drama":"drama", "anime":"anime", "cartoon":"multfilm"}
    await state.update_data(search_filter=media_type)
    await state.set_state(Form.search_name)
    await call.message.answer(f"🔎 {labels.get(media_type, 'kontent').capitalize()} bo'limi. Nomini yozing — mos kelgan nomlar tanlash uchun chiqadi:")
    await call.answer()


@dp.message(Form.search_name)
async def search_name_result(message: Message, state: FSMContext):
    if not await require_subscription_message(message):
        return
    query = (message.text or "").strip().casefold()
    if not query:
        return await message.answer("Nomdan kamida bir necha harf yozing:")
    saved = await state.get_data()
    media_filter = saved.get("search_filter", "all")
    data = load_data()
    matches = [(str(code), item) for code, item in data.get("medias", {}).items()
               if item.get("media_type", "series") in {"movie", "series", "drama", "anime", "cartoon"}
               and query in item.get("name", "").casefold()
               and (media_filter == "all" or item.get("media_type", "series") == media_filter)
               and media_available_for_user(message.from_user.id, item, data)]
    # Prefix matches first, then substring matches, so results are easier to select.
    matches.sort(key=lambda pair: (not pair[1].get("name", "").casefold().startswith(query), pair[1].get("name", "").casefold()))
    if not matches:
        return await message.answer("❌ Mos nom topilmadi. Boshqa nom yoki qismini yozib ko'ring:", reply_markup=search_menu(message.from_user.id))
    rows = []
    for code, item in matches[:40]:
        icon = {"movie":"🎞", "series":"📺", "drama":"🎭", "anime":"🍥", "cartoon":"🎨"}.get(item.get("media_type"), "🎬")
        rows.append([InlineKeyboardButton(text=f"{icon} {item.get('name','Nomsiz')} · {code}", callback_data=f"show:{code}")])
    rows.append([InlineKeyboardButton(text="🔎 Yana qidirish", callback_data="search_name")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    await message.answer(f"🔎 <b>Topilgan nomlar: {len(matches)}</b>\nKeraklisini tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.clear()


@dp.callback_query(F.data == "search")
async def search_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Form.search_code)
    await call.message.answer("🔢 Kino yoki serial kodini yuboring:")
    await call.answer()


@dp.message(Form.search_code)
async def search_code(message: Message, state: FSMContext):
    await state.clear()
    if not await require_subscription_message(message):
        return
    data = load_data()
    code = message.text.strip() if message.text else ""
    media = data["medias"].get(code)
    if not media:
        return await message.answer("❌ Bu kod bo‘yicha kino yoki serial topilmadi.")
    await send_media_card(message.chat.id, code, media)


@dp.callback_query(F.data == "page_info")
async def page_info(call: CallbackQuery):
    await call.answer()

@dp.callback_query(F.data.startswith("season_page:"))
async def season_page(call: CallbackQuery):
    try:
        _, code, season_no, page = call.data.split(":")
        data = load_data(); media = data["medias"][code]
        seasons = media.get("seasons") or {"1": {"episodes": media.get("episodes", {})}}
        episodes = seasons.get(season_no, {}).get("episodes", {})
        markup = episode_keyboard(code, episodes, user_id=call.from_user.id, season=season_no, media=media, page=int(page))
        await call.message.edit_reply_markup(reply_markup=markup)
    except Exception:
        await call.answer("Sahifani ochib bo‘lmadi.", show_alert=True)
    else:
        await call.answer()

@dp.callback_query(F.data.startswith("season_show:"))
async def season_show(call: CallbackQuery):
    try:
        _, code, season = call.data.split(":", 2)
        data = load_data()
        media = data["medias"][code]
        season_data = (media.get("seasons") or {}).get(season, {"episodes": media.get("episodes", {})})
        kb = episode_keyboard(code, season_data.get("episodes", {}), user_id=call.from_user.id, season=season, media=media)
        title = f"🎬 <b>{media['name']}</b>\n\n📺 {season}-fasl — qismni tanlang:" if season != "choose" else f"🎬 <b>{media['name']}</b>\n\n📺 Faslni tanlang:"
        await call.message.answer(title, reply_markup=kb)
        await call.answer()
    except (KeyError, ValueError):
        await call.answer("Fasl topilmadi.", show_alert=True)


def episode_navigation_keyboard(code, season_no, current_no, available_numbers):
    def sk(v): return (0, int(v)) if str(v).isdigit() else (1, str(v))
    nums = sorted([str(n) for n in available_numbers], key=sk)
    try: idx = nums.index(str(current_no))
    except ValueError: idx = -1
    row = []
    if idx > 0: row.append(InlineKeyboardButton(text="⬅️ Oldingi qism", callback_data=f"episode:{code}:{season_no}:{nums[idx-1]}"))
    if idx >= 0 and idx < len(nums)-1: row.append(InlineKeyboardButton(text="Keyingi qism ➡️", callback_data=f"episode:{code}:{season_no}:{nums[idx+1]}"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton(text="📋 Qismlar ro'yxati", callback_data=f"season_show:{code}:{season_no}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data.startswith("episode:"))
async def send_episode(call: CallbackQuery):
    data = load_data()
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not await check_subscription(call.from_user.id):
        await call.message.answer(
            "🔒 Avval kanallarga obuna bo'ling.", reply_markup=subscription_keyboard()
        )
        return await call.answer()
    try:
        parts = call.data.split(":")
        if len(parts) == 3:
            _, code, number = parts
            season_number = "1"
        else:
            _, code, season_number, number = parts
        current_data = load_data()
        media = current_data["medias"][code]
        season_data = (media.get("seasons") or {}).get(season_number, {"episodes": media.get("episodes", {})})
        episode_record = season_data.get("episodes", {}).get(number) or media.get("episodes", {}).get(number)
        if not episode_record:
            await call.answer("Bu qism hali joylanmagan.", show_alert=True)
            return
        tier = "regular"
        if isinstance(episode_record, dict) and "regular" in episode_record:
            episode = episode_record["regular"]
        elif isinstance(episode_record, dict) and "file_id" in episode_record:
            # Yangi, ruchnoy qo'shilgan qism.
            episode = episode_record
        elif isinstance(episode_record, dict) and "chat_id" in episode_record:
            # Eski kanalga bog'langan qismlar bilan moslik.
            episode = episode_record
        else:
            episode = None
        if not episode or not isinstance(episode, dict):
            await call.answer("Bu qism hali qo'shilmagan.", show_alert=True)
            return
        user = current_data.setdefault("users", {}).setdefault(str(call.from_user.id), {})
        user["last_watched"] = {"code": code, "season": season_number, "number": number, "media_type": media.get("media_type", "series"), "updated_at": now_ts()}
        save_data(current_data)
        protect = False
        available_numbers = []
        for ep_no, ep_rec in season_data.get("episodes", {}).items():
            if isinstance(ep_rec, dict) and ("file_id" in ep_rec or "chat_id" in ep_rec or "regular" in ep_rec):
                available_numbers.append(str(ep_no))
        nav_markup = episode_navigation_keyboard(code, season_number, number, available_numbers)
        if episode.get("file_id"):
            media_type = episode.get("media_type", "video")
            file_id = episode["file_id"]
            caption = episode.get("caption") or f"🎬 <b>{media.get('name', 'Kontent')}</b> — {number}-qism"
            send_method = {
                "video": bot.send_video,
                "document": bot.send_document,
                "audio": bot.send_audio,
                "animation": bot.send_animation,
                "photo": bot.send_photo,
                "voice": bot.send_voice,
            }.get(media_type, bot.send_video)
            await send_method(chat_id=call.from_user.id, **{media_type: file_id}, caption=caption, protect_content=protect, reply_markup=nav_markup)
        elif "chat_id" in episode and "message_id" in episode:
            await bot.copy_message(chat_id=call.from_user.id, from_chat_id=episode["chat_id"], message_id=episode["message_id"], protect_content=protect, reply_markup=nav_markup)
        else:
            await call.answer("Qism ma'lumoti noto'g'ri saqlangan.", show_alert=True)
            return
        await call.answer("Qism yuborildi")
    except Exception:
        await call.answer("❌ Qism yuborilmadi. Kanal va xabarni tekshiring.", show_alert=True)


@dp.callback_query(F.data.startswith("category_list:"))
async def category_list_callback(call: CallbackQuery):
    media_type = call.data.split(":", 1)[1]
    labels = {"movie":("🎞 Kinolar", "📭 Kino hali qo'shilmagan"), "series":("📺 Seriallar", "📭 Serial hali qo'shilmagan"), "drama":("🎭 Dramalar", "📭 Drama hali qo'shilmagan"), "anime":("🍥 Anime", "📭 Anime hali qo'shilmagan"), "cartoon":("🎨 Multfilmlar", "📭 Multfilm hali qo'shilmagan")}
    title, empty = labels.get(media_type, ("🎬 Kontent", "📭 Hozircha bo'sh"))
    await media_category_list(call, media_type, title, empty)


@dp.callback_query(F.data == "movie_list")
async def movie_list(call: CallbackQuery):
    data = load_data()
    if not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo‘ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    items = [(code, item) for code, item in data.get("medias", {}).items() if item.get("media_type") == "movie" and media_available_for_user(call.from_user.id, item, data)]
    rows = [[InlineKeyboardButton(text=f"🎞 {item['name']} · {code}", callback_data=f"show:{code}")] for code, item in items[:80]]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    await call.message.answer("🎞 <b>Kinolar ro‘yxati</b>" if items else "📭 Hozircha kino qo‘shilmagan.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data == "series_list")
async def series_list(call: CallbackQuery):
    await media_category_list(call, "series", "📺 Seriallar ro‘yxati", "📭 Hozircha serial qo‘shilmagan.")


async def media_category_list(call: CallbackQuery, media_type: str, title: str, empty_text: str, page: int = 0):
    data = load_data()
    if not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo‘ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    items = [(str(code), item) for code, item in data.get("medias", {}).items()
             if item.get("media_type", "series") == media_type
             and media_available_for_user(call.from_user.id, item, data)]
    items.sort(key=lambda pair: pair[1].get("name", "").casefold())
    page_size = 10
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(int(page), total_pages - 1))
    visible = items[page * page_size:(page + 1) * page_size]
    rows = [[InlineKeyboardButton(text=f"🎬 {item.get('name', 'Nomsiz')} · {code}", callback_data=f"show:{code}")]
            for code, item in visible]
    if total_pages > 1:
        left_btn = InlineKeyboardButton(text="⬅️", callback_data=f"media_page:{media_type}:{page-1}") if page > 0 else None
        middle_btn = InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="page_info")
        right_btn = InlineKeyboardButton(text="➡️", callback_data=f"media_page:{media_type}:{page+1}") if page < total_pages - 1 else None
        rows.extend(build_pager_row(left_btn, middle_btn, right_btn))
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    text = f"<b>{title}</b>\n\nJami: {len(items)} ta" if items else empty_text
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data.startswith("media_page:"))
async def media_page_callback(call: CallbackQuery):
    try:
        _, media_type, page = call.data.split(":")
    except ValueError:
        return await call.answer()
    labels = {"movie":("🎞 Kinolar ro'yxati", "📭 Hozircha kino qo'shilmagan."),
              "anime":("🍥 Anime ro'yxati", "📭 Hozircha anime qo'shilmagan."),
              "cartoon":("🎨 Multiklar ro'yxati", "📭 Hozircha multik qo'shilmagan."),
              "series":("📺 Seriallar ro'yxati", "📭 Hozircha serial qo'shilmagan.")}
    title, empty = labels.get(media_type, ("🎬 Kontent ro'yxati", "📭 Hozircha bo'sh."))
    await media_category_list(call, media_type, title, empty, int(page))


@dp.callback_query(F.data.startswith("favorite_add:"))
async def favorite_add(call: CallbackQuery):
    data = load_data()
    code = call.data.split(":", 1)[1]
    if code not in data.get("medias", {}):
        return await call.answer("Topilmadi", show_alert=True)
    user = data.setdefault("users", {}).setdefault(str(call.from_user.id), {"id": call.from_user.id, "favorites": []})
    favs = user.setdefault("favorites", [])
    if code in favs:
        favs.remove(code)
        msg = "Sevimlilardan olib tashlandi"
    else:
        favs.append(code)
        msg = "Yoqtirganlaringizga saqlandi ⭐"
    save_data(data)
    await call.answer(msg, show_alert=True)


@dp.callback_query(F.data == "favorites")
async def favorites_list(call: CallbackQuery):
    data = load_data()
    user = data.get("users", {}).get(str(call.from_user.id), {})
    codes = user.get("favorites", [])
    rows = []
    for code in codes:
        item = data.get("medias", {}).get(str(code))
        if item:
            icon = "🎞" if item.get("media_type") == "movie" else "🍥"
            rows.append([InlineKeyboardButton(text=f"{icon} {item['name']} · {code}", callback_data=f"show:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="home")])
    await call.message.answer("⭐ <b>Yoqtirganlarim</b>" if len(rows) > 1 else "⭐ Hozircha saqlangan narsa yo‘q.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data == "continue_watching")
async def continue_watching(call: CallbackQuery):
    data = load_data()
    user = data.get("users", {}).get(str(call.from_user.id), {})
    last = user.get("last_watched") or {}
    code = str(last.get("code", ""))
    item = data.get("medias", {}).get(code)
    if item:
        await send_media_card(call.from_user.id, code, item)
        await call.answer("Davom ettirish uchun kerakli qismni bosing")
    elif last.get("catalog_key") in data.get("channel_catalog", {}):
        key = last["catalog_key"]
        catalog_item = data["channel_catalog"][key]
        parts = catalog_item.get("parts", {})
        rows = [[InlineKeyboardButton(text=f"▶️ {number}-qism", callback_data=f"catalog_part:{key}:{number}")] for number in sorted(parts, key=lambda value: int(value) if str(value).isdigit() else 999999)]
        await call.message.answer(f"🎬 <b>{catalog_item.get('name', 'Nomsiz')}</b>\n\n{catalog_item.get('description', '')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await call.answer("Oxirgi ko‘rilgan kino topildi")
    else:
        await call.answer("Hali tomosha qilgan narsangiz yo‘q.", show_alert=True)


@dp.callback_query(F.data.startswith("show:"))
async def show_media(call: CallbackQuery):
    code = call.data.split(":", 1)[1]
    data = load_data()
    if code not in data["medias"]:
        return await call.answer("Kontent topilmadi", show_alert=True)
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    try:
        await call.message.edit_text("🎬 <b>Kontent ochildi.</b>")
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    item = data["medias"][code]
    await send_media_card(call.from_user.id, code, item)
    # Faqat poster va mavjud qismlar tugmalari ko'rsatiladi; qism avtomatik yuborilmaydi.
    await call.answer()


async def ask(call: CallbackQuery, state: FSMContext, new_state: State, prompt: str):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Adminlar uchun.", show_alert=True)
    await state.set_state(new_state)
    await call.message.answer(prompt)
    await call.answer()


@dp.callback_query(F.data == "admin_add_content")
async def admin_add_content_callback(call: CallbackQuery, state: FSMContext):
    if not has_permission(call.from_user.id, "add_content"):
        return await call.answer("⛔ Sizda kontent qo'shish huquqi yo'q.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎞 Kino", callback_data="newtype:movie"), InlineKeyboardButton(text="🍥 Anime", callback_data="newtype:anime")],
        [InlineKeyboardButton(text="🎨 Multfilm", callback_data="newtype:cartoon")],
    ])
    await call.message.answer("Qaysi turdagi kontent qo'shamiz?", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "add_movie")
async def add_movie(call: CallbackQuery, state: FSMContext):
    if not has_permission(call.from_user.id, "add_content"):
        return await call.answer("⛔ Sizda kontent qo'shish huquqi yo'q.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎞 Kino", callback_data="newtype:movie"), InlineKeyboardButton(text="🍥 Anime", callback_data="newtype:anime")],
        [InlineKeyboardButton(text="🎨 Multfilm", callback_data="newtype:cartoon")],
    ])
    await call.message.answer("Qaysi turdagi kontent qo'shamiz?", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "add_series")
async def add_series(call: CallbackQuery, state: FSMContext):
    if not has_permission(call.from_user.id, "add_content"):
        return await call.answer("⛔ Sizda kontent qo'shish huquqi yo'q.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Serial", callback_data="newtype:series")],
        [InlineKeyboardButton(text="🍥 Anime", callback_data="newtype:anime"), InlineKeyboardButton(text="🎨 Multfilm", callback_data="newtype:cartoon")],
    ])
    await call.message.answer("Qaysi turdagi kontent qo'shamiz?", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("newtype:"))
async def choose_new_media_type(call: CallbackQuery, state: FSMContext):
    if not has_permission(call.from_user.id, "add_content"):
        return await call.answer("⛔ Sizda kontent qo'shish huquqi yo'q.", show_alert=True)
    media_type = call.data.split(":", 1)[1]
    await state.update_data(media_type=media_type)
    await state.set_state(Form.media_code)
    await call.message.answer("🔢 Yangi kontent uchun takrorlanmaydigan kod yuboring:")
    await call.answer()


@dp.message(Form.media_code)
async def media_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if not code or code in data["medias"]:
        return await message.answer("❌ Kod bo'sh yoki band. Boshqa kod yuboring:")
    await state.update_data(new_code=code)
    await state.set_state(Form.media_name)
    saved = await state.get_data()
    await message.answer({"movie":"🎞 Kino nomini", "series":"📺 Serial nomini", "drama":"🎭 Drama nomini", "anime":"🍥 Anime nomini", "cartoon":"🎨 Multfilm nomini"}.get(saved.get("media_type"), "🎬 Kontent nomini") + " yuboring:")


@dp.message(Form.media_name)
async def media_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    name = (message.text or "").strip()
    if not name:
        return await message.answer("Kontent nomini matn ko'rinishida yuboring:")
    await state.update_data(new_name=name)
    await state.set_state(Form.media_info)
    await message.answer(
        "🖼 Endi poster rasmi va ma'lumotini yuboring.\n"
        "• Rasmni caption bilan yuboring; yoki\n"
        "• Faqat tavsif matnini yuboring.\n"
        "Rasm/tavsif kerak bo'lmasa /skip yuboring."
    )


@dp.message(Form.media_info)
async def media_info(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    poster = None
    description = ""
    forwarded_record = None
    origin = getattr(message, "forward_origin", None)
    if origin and getattr(origin, "type", "") == "channel":
        source_chat = getattr(origin, "chat", None)
        source_message_id = getattr(origin, "message_id", None)
        if not source_chat or not source_message_id:
            return await message.answer("❌ Forward manbasini aniqlab bo'lmadi. Kanal postini to'g'ridan-to'g'ri forward qiling.")
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(source_chat.id, me.id)
            status = str(member.status).split(".")[-1].lower()
            if status not in ("administrator", "creator"):
                return await message.answer("❌ Bot bu manba kanal/guruhda admin emas. Botni har bir kontent kanaliga administrator qiling.")
        except Exception as exc:
            logging.warning("Forward source permission check failed: %s", exc)
            return await message.answer("❌ Manba kanalni tekshirib bo'lmadi. Bot shu kanal/guruhda administrator ekanini tekshiring.")
        forwarded_record = {"chat_id": source_chat.id, "message_id": source_message_id}
        description = (message.caption or "").strip()
        if message.photo:
            poster = message.photo[-1].file_id
    elif message.text and message.text.strip() == "/skip":
        pass
    elif message.photo:
        poster = message.photo[-1].file_id
        description = (message.caption or "").strip()
    elif message.text:
        description = message.text.strip()
    else:
        return await message.answer("Rasm yoki matn yuboring, yoki /skip bosing.")
    await state.update_data(pending_poster=poster, pending_description=description, pending_forward_record=forwarded_record)
    await state.set_state(Form.media_details)
    await message.answer(
        "📝 Qo‘shimcha ma’lumotlarni quyidagi ko‘rinishda yuboring:\n"
        "Janr: Ekshn, Fantastika\nHolati: Tugallangan\nSifati: 720p, 1080p\nOvoz: Anibla\n\n"
        "Kerak bo‘lmagan qatorlarni tashlab ketsangiz bo‘ladi. Hammasini o‘tkazish uchun /skip yuboring."
    )


@dp.message(Form.media_details)
async def media_details_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    saved = await state.get_data()
    raw = (message.text or "").strip()
    fields = {"genres": "", "status": "", "quality": "", "voice": ""}
    if raw and raw != "/skip":
        aliases = {"janr": "genres", "genre": "genres", "holati": "status", "status": "status", "sifati": "quality", "sifat": "quality", "quality": "quality", "ovoz": "voice", "voice": "voice", "studio": "voice"}
        for line in raw.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                field = aliases.get(key.strip().casefold())
                if field:
                    fields[field] = value.strip()
    code, name = str(saved["new_code"]), str(saved["new_name"])
    data = load_data()
    media_type = saved.get("media_type", "series")
    first_episode = saved.get("pending_forward_record")
    seasons = {"1": {"episodes": ({"1": first_episode} if first_episode else {})}}
    data["medias"][code] = {
        "name": name, "description": saved.get("pending_description", ""),
        "poster_file_id": saved.get("pending_poster"), "episodes": ({"1": first_episode} if first_episode else {}), "seasons": seasons,
        "created_at": now_ts(), "media_type": media_type, **fields
    }
    save_data(data)
    await state.set_state(Form.media_season_count)
    await message.answer("📺 Bu kontent nechta fasldan iborat? Faqat son yuboring. Kino uchun 1 deb yozing:")


async def notify_content_added(code: str, item: dict):
    """Yangi media/kino qo‘shilganda barcha ro‘yxatdan o‘tgan foydalanuvchilarga xabar."""
    data = load_data()
    is_movie = item.get("media_type") == "movie"
    label = "KINO" if is_movie else "SERIAL"
    years = item.get("year", "") or item.get("years", "") or "Yili ko'rsatilmagan"
    genre = item.get("genre", "") or item.get("genres", "") or "Janr ko'rsatilmagan"
    seasons = item.get("seasons") or {"1": {"episodes": item.get("episodes", {})}}
    episode_count = sum(len(v.get("episodes", {})) for v in seasons.values() if isinstance(v, dict))
    text = (f"🔥 <b>Premyera!</b>\n\n"
            f"🎬 {item.get('name', 'Nomsiz')}\n"
            f"{years} · {genre}\n"
            f"📺 {episode_count} qism\n\n"
            f"📥 Barcha qismlar tayyor — hoziroq <code>{code}</code> kodi orqali yuklab oling!👇")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎬 Tomosha qilish", callback_data=f"show:{code}")]])
    if not data.get("settings", {}).get("notify_new_content", True):
        return
    for uid in list(data.get("users", {})):
        try:
            await bot.send_message(int(uid), text, reply_markup=kb)
        except Exception as exc:
            logging.info("New content notification failed for %s: %s", uid, exc)


@dp.message(Form.media_season_count)
async def media_season_count_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    try:
        count = int((message.text or "").strip())
    except ValueError:
        return await message.answer("Fasllar sonini raqam bilan yuboring, masalan 1 yoki 3:")
    if count < 1 or count > 30:
        return await message.answer("Fasllar soni 1 dan 30 gacha bo‘lishi kerak:")
    saved = await state.get_data()
    code = str(saved.get("new_code"))
    data = load_data()
    item = data.get("medias", {}).get(code)
    if not item:
        await state.clear()
        return await message.answer("Kontent topilmadi. Qaytadan qo‘shing.", reply_markup=admin_menu(message.from_user.id))
    existing_eps = item.get("seasons", {}).get("1", {}).get("episodes", {})
    item["seasons"] = {str(n): {"episodes": {}} for n in range(1, count + 1)}
    if existing_eps:
        item["seasons"].setdefault("1", {"episodes": {}})["episodes"].update(existing_eps)
    save_data(data)
    await state.clear()
    await notify_content_added(code, item)
    await message.answer(
        f"✅ <b>{item['name']}</b> (kod <code>{code}</code>) botga qo‘shildi.\n\n"
        f"📺 Fasllar: {count} ta\n\n"
        "Endi serial/anime qismlarini <b>Qism yoki yangi sezon</b> bo'limidan qo'shing.\n"
        "Kino qo'shayotganda esa bot admin bo'lgan kanal/guruh postini forward qilsangiz, u 1-qism sifatida saqlanadi.",
        reply_markup=admin_menu()
    )


@dp.callback_query(F.data == "add_episode")
async def add_episode(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.episode_code, "Qismlar qo‘shiladigan kino yoki serial kodini yuboring:")


@dp.message(Form.episode_code)
async def episode_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if code not in data["medias"]:
        return await message.answer("❌ Bunday kod topilmadi. Qayta yuboring:")
    item = data["medias"][code]
    seasons = item.get("seasons") or {"1": {"episodes": item.get("episodes", {})}}
    await state.update_data(batch_code=code)
    await state.set_state(Form.episode_season)
    await message.answer(
        f"🎬 <b>{item['name']}</b> tanlandi.\nMavjud fasllar: {', '.join(sorted(seasons, key=lambda x: int(x) if str(x).isdigit() else 999))}\n\n"
        "Fasl raqamini yuboring (masalan: 2). Yangi fasl bo‘lsa ham raqamini yozing."
    )


@dp.message(Form.episode_season)
async def episode_season_select(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 100:
        return await message.answer("Fasl raqamini 1 dan 100 gacha raqam bilan yuboring, masalan 2:")
    season_no = str(int(raw))
    saved = await state.get_data()
    code = str(saved.get("batch_code", ""))
    data = load_data()
    item = data.get("medias", {}).get(code)
    if not item:
        await state.clear()
        return await message.answer("Kontent topilmadi. Qaytadan boshlang.", reply_markup=admin_menu(message.from_user.id))
    item.setdefault("seasons", {}).setdefault(season_no, {"episodes": {}})
    save_data(data)
    await state.update_data(batch_season=season_no, batch_count=0)
    await state.set_state(Form.episode_batch)
    prompt = await message.answer(
        f"🎬 <b>{item['name']}</b> · <b>{season_no}-fasl</b>\nQismlarni bittadan yuboring. Birinchi media 1-qism, ikkinchisi 2-qism bo‘ladi.\n/done — tugatish · /cancel — bekor qilish"
    )
    await state.update_data(progress_message_id=prompt.message_id)


@dp.message(Form.episode_batch)
async def episode_batch_message(message: Message, state: FSMContext):
    """Qism faqat bot administrator bo'lgan kanaldan forward qilinganda qabul qilinadi."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    text = (message.text or "").strip().lower()
    if text in ("/done", "done", "tayyor"):
        saved = await state.get_data()
        count = int(saved.get("batch_count", 0))
        await state.clear()
        return await message.answer(f"✅ Yakunlandi. {count} ta qism saqlandi.", reply_markup=admin_menu(message.from_user.id))
    if text == "/cancel":
        saved = await state.get_data()
        code = str(saved.get("batch_code", "")); season_no = str(saved.get("batch_season", "1"))
        added = saved.get("batch_added", [])
        data = load_data(); item = data.get("medias", {}).get(code)
        if item:
            eps = item.get("seasons", {}).get(season_no, {}).get("episodes", {})
            for n in added:
                eps.pop(str(n), None)
                if season_no == "1": item.get("episodes", {}).pop(str(n), None)
            save_data(data)
        await state.clear()
        return await message.answer("❎ Bekor qilindi. Shu jarayonda qo'shilgan qismlar olib tashlandi; avvalgi qismlar saqlandi.", reply_markup=admin_menu(message.from_user.id))

    # Media istalgan manbadan qabul qilinadi: kanal, mavzu, guruh yoki shaxsiy chat.
    # Faylni serverga yuklamaymiz; Telegram file_id yoki kerak bo'lsa source message saqlanadi.
    media_type = None
    file_id = None
    for attr in ("video", "document", "audio", "animation", "photo", "voice"):
        obj = getattr(message, attr, None)
        if obj:
            media_type = attr
            file_id = (obj[-1].file_id if attr == "photo" else obj.file_id)
            break
    origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    origin_message_id = getattr(origin, "message_id", None) if origin else None
    if not file_id and not (origin_chat and origin_message_id):
        return await message.answer("❌ Video yoki media yuboring/forward qiling. Tugatish: /done")

    saved = await state.get_data()
    code = str(saved.get("batch_code", ""))
    data = load_data()
    item = data.get("medias", {}).get(code)
    if not item:
        await state.clear()
        return await message.answer("❌ Kino/serial topilmadi. Qaytadan boshlang.", reply_markup=admin_menu(message.from_user.id))
    season_no = str(saved.get("batch_season", "1"))
    season = item.setdefault("seasons", {}).setdefault(season_no, {"episodes": {}})
    episodes = season.setdefault("episodes", {})
    numeric = [int(n) for n in episodes if str(n).isdigit()]
    number = max(numeric, default=0) + 1
    if origin_chat and origin_message_id:
        # Original postni copy_message orqali yuborish cover/banner va captionni saqlashga yordam beradi.
        record = {"chat_id": origin_chat.id, "message_id": origin_message_id, "media_type": media_type or "video"}
    elif file_id:
        record = {"file_id": file_id, "media_type": media_type or "video", "caption": (message.caption or "")[:900]}
    episodes[str(number)] = record
    if season_no == "1":
        item.setdefault("episodes", {})[str(number)] = record
    save_data(data)
    count = int(saved.get("batch_count", 0)) + 1
    added = list(saved.get("batch_added", [])); added.append(str(number))
    await state.update_data(batch_count=count, batch_added=added)
    progress_id = saved.get("progress_message_id")
    if progress_id:
        try:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=progress_id,
                text=f"🎬 <b>{item['name']}</b> · {season_no}-fasl\n✅ {count} ta qism saqlandi. Oxirgisi: <b>{number}-qism</b>.\n\nKanal postlarini forward qilishda davom eting.\nTugatish: /done · Bekor qilish: /cancel")
        except Exception:
            await message.answer(f"✅ {number}-qism saqlandi. Keyingi postni forward qiling yoki /done yozing.")
    else:
        await message.answer(f"✅ {number}-qism saqlandi. Keyingi postni forward qiling yoki /done yozing.")

@dp.callback_query(F.data == "edit_poster")
async def edit_poster_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id) and not has_permission(call.from_user.id, "edit_content"):
        return await call.answer("⛔ Sizda tahrirlash huquqi yo'q.", show_alert=True)
    await state.set_state(Form.edit_poster_code)
    await call.message.answer("Posterini almashtirmoqchi bo'lgan kontent kodini yuboring:")
    await call.answer()


@dp.message(Form.edit_poster_code)
async def edit_poster_code_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id) and not has_permission(message.from_user.id, "edit_content"):
        await state.clear(); return await message.answer("⛔ Tahrirlash huquqi yo'q.")
    code = (message.text or "").strip()
    if code not in load_data().get("medias", {}):
        return await message.answer("Kod topilmadi. Qayta yuboring:")
    await state.update_data(edit_poster_code=code)
    await state.set_state(Form.edit_poster_file)
    await message.answer("Yangi poster rasmini yuboring. Posterni olib tashlash uchun /remove yozing:")


@dp.message(Form.edit_poster_file)
async def edit_poster_file_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id) and not has_permission(message.from_user.id, "edit_content"):
        await state.clear(); return await message.answer("⛔ Tahrirlash huquqi yo'q.")
    saved = await state.get_data(); code = str(saved.get("edit_poster_code", ""))
    data = load_data(); item = data.get("medias", {}).get(code)
    if not item:
        await state.clear(); return await message.answer("Kontent topilmadi.")
    if message.photo:
        item["poster_file_id"] = message.photo[-1].file_id
    elif (message.text or "").strip() == "/remove":
        item["poster_file_id"] = None
    else:
        return await message.answer("Rasm yuboring yoki /remove yozing:")
    save_data(data); await state.clear()
    await message.answer(f"✅ <b>{item.get('name')}</b> posteri yangilandi.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "admin_permissions")
async def admin_permissions_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun.", show_alert=True)
    await state.set_state(Form.admin_permissions_id)
    await call.message.answer("Huquqlarini o'zgartirmoqchi bo'lgan admin Telegram ID sini yuboring:")
    await call.answer()


@dp.message(Form.admin_permissions_id)
async def admin_permissions_choose(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear(); return await message.answer("Faqat owner uchun.")
    try: uid = int((message.text or "").strip())
    except ValueError: return await message.answer("Telegram ID raqamini yuboring:")
    data = load_data()
    if uid not in data.get("admins", []) and uid not in ADMIN_IDS:
        return await message.answer("Bu ID adminlar ro'yxatida yo'q. Avval admin qo'shing:")
    perms = set(get_admin_permissions(uid))
    labels = [("add_content", "Kontent qo'shish"), ("add_episode", "Qism/sezon qo'shish"), ("edit_content", "Nom/poster tahrirlash"), ("delete_content", "Kontent o'chirish")]
    rows = [[InlineKeyboardButton(text=("✅ " if key in perms else "⬜ ")+label, callback_data=f"perm_toggle:{uid}:{key}")] for key,label in labels]
    rows.append([InlineKeyboardButton(text="✅ Tayyor", callback_data="admin_home")])
    await state.clear()
    await message.answer(f"🔐 <b>{uid}</b> admin huquqlari. Bosib yoqing/o'chiring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("perm_toggle:"))
async def admin_permission_toggle(call: CallbackQuery):
    if not is_owner(call.from_user.id): return await call.answer("Faqat owner uchun.", show_alert=True)
    _, uid_raw, perm = call.data.split(":", 2); uid = int(uid_raw)
    valid = {"add_content", "add_episode", "edit_content", "delete_content"}
    if perm not in valid: return await call.answer("Noto'g'ri huquq", show_alert=True)
    data = load_data(); permissions = set(data.setdefault("admin_permissions", {}).get(str(uid), ["add_content", "add_episode"]))
    if perm in permissions: permissions.remove(perm)
    else: permissions.add(perm)
    data["admin_permissions"][str(uid)] = sorted(permissions); save_data(data)
    labels = [("add_content", "Kontent qo'shish"), ("add_episode", "Qism/sezon qo'shish"), ("edit_content", "Nom/poster tahrirlash"), ("delete_content", "Kontent o'chirish")]
    rows = [[InlineKeyboardButton(text=("✅ " if key in permissions else "⬜ ")+label, callback_data=f"perm_toggle:{uid}:{key}")] for key,label in labels]
    rows.append([InlineKeyboardButton(text="✅ Tayyor", callback_data="admin_home")])
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer("Huquq yangilandi")


@dp.callback_query(F.data == "del_media")
async def del_media(call: CallbackQuery, state: FSMContext):
    if not has_permission(call.from_user.id, "delete_content"):
        return await call.answer("⛔ Sizda o'chirish huquqi yo'q.", show_alert=True)
    await ask(call, state, Form.delete_code, "O'chiriladigan kino yoki serial kodini yuboring:")


@dp.message(Form.delete_code)
async def delete_media(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if code not in data["medias"]:
        return await message.answer("❌ media kodi topilmadi:")
    name = data["medias"][code]["name"]
    del data["medias"][code]
    save_data(data)
    await state.clear()
    await message.answer(f"🗑 <b>{name}</b> o'chirildi.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "del_episode")
async def del_episode(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.delete_episode_code, "Qismi o'chiriladigan media kodini yuboring:")


@dp.message(Form.delete_episode_code)
async def delete_episode_ask_number(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if code not in data["medias"]:
        return await message.answer("❌ media kodi topilmadi. Qayta yuboring:")
    await state.update_data(delete_episode_code=code)
    await state.set_state(Form.delete_episode_number)
    await message.answer("O'chiriladigan qism raqamini yuboring:")


@dp.message(Form.delete_episode_number)
async def delete_episode_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    number = (message.text or "").strip()
    saved = await state.get_data()
    data = load_data()
    code = saved["delete_episode_code"]
    if code not in data["medias"] or number not in data["medias"][code]["episodes"]:
        return await message.answer("❌ Bunday qism topilmadi. Qism raqamini qayta yuboring:")
    media = data["medias"][code]
    removed = False
    seasons = media.get("seasons", {})
    for season_no, season_data in seasons.items():
        eps = season_data.get("episodes", {})
        if number in eps:
            del eps[number]; removed = True
            if str(season_no) == "1": media.get("episodes", {}).pop(number, None)
            break
    if not removed and number in media.get("episodes", {}):
        del media["episodes"][number]; removed = True
    if not removed:
        return await message.answer("❌ Bunday qism topilmadi. Qism raqamini qayta yuboring:")
    save_data(data)
    await state.clear()
    await message.answer(f"🗑 {number}-qism o'chirildi.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "del_season")
async def del_season_start(call: CallbackQuery, state: FSMContext):
    if not has_permission(call.from_user.id, "delete_content"):
        return await call.answer("⛔ Sizda o‘chirish huquqi yo‘q.", show_alert=True)
    await ask(call, state, Form.delete_season_code, "Faslini o‘chirmoqchi bo‘lgan kino/serial kodini yuboring:")

@dp.message(Form.delete_season_code)
async def del_season_code(message: Message, state: FSMContext):
    if not has_permission(message.from_user.id, "delete_content"):
        await state.clear(); return
    code = (message.text or "").strip(); data = load_data()
    if code not in data.get("medias", {}):
        return await message.answer("Kod topilmadi. Qayta yuboring:")
    await state.update_data(delete_season_code=code)
    await state.set_state(Form.delete_season_number)
    await message.answer("O‘chiriladigan fasl raqamini yuboring:")

@dp.message(Form.delete_season_number)
async def del_season_number(message: Message, state: FSMContext):
    if not has_permission(message.from_user.id, "delete_content"):
        await state.clear(); return
    raw = (message.text or "").strip()
    if not raw.isdigit(): return await message.answer("Fasl raqamini raqam bilan yuboring:")
    saved = await state.get_data(); code = str(saved.get("delete_season_code", "")); sn = str(int(raw))
    data = load_data(); media = data.get("medias", {}).get(code)
    if not media or sn not in media.get("seasons", {}):
        return await message.answer("Bunday fasl topilmadi. Qayta yuboring:")
    count = len(media["seasons"][sn].get("episodes", {}))
    del media["seasons"][sn]
    if sn == "1": media["episodes"] = {}
    save_data(data); await state.clear()
    await message.answer(f"🗑 {sn}-fasl va undagi {count} ta qism o‘chirildi.", reply_markup=admin_menu(message.from_user.id))

@dp.callback_query(F.data == "rename_media")
async def rename_start(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.rename_code, "Nomi o'zgaradigan media kodini yuboring:")


@dp.message(Form.rename_code)
async def rename_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    if code not in load_data()["medias"]:
        return await message.answer("❌ Kod topilmadi:")
    await state.update_data(rename_code=code)
    await state.set_state(Form.rename_name)
    await message.answer("Yangi media nomini yuboring:")


@dp.message(Form.rename_name)
async def rename_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    saved = await state.get_data()
    data = load_data()
    data["medias"][saved["rename_code"]]["name"] = (message.text or "").strip()
    save_data(data)
    await state.clear()
    await message.answer("✅ media nomi o'zgartirildi.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "recode_media")
async def recode_start(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.recode_old, "Eski media kodini yuboring:")


@dp.message(Form.recode_old)
async def recode_old(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    old = (message.text or "").strip()
    if old not in load_data()["medias"]:
        return await message.answer("❌ Eski kod topilmadi:")
    await state.update_data(recode_old=old)
    await state.set_state(Form.recode_new)
    await message.answer("Yangi, band bo'lmagan kodni yuboring:")


@dp.message(Form.recode_new)
async def recode_new(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    new = (message.text or "").strip()
    saved = await state.get_data()
    data = load_data()
    if not new or new in data["medias"]:
        return await message.answer("❌ Yangi kod bo'sh yoki band:")
    data["medias"][new] = data["medias"].pop(saved["recode_old"])
    save_data(data)
    await state.clear()
    await message.answer(f"✅ Kod {new} qilib o'zgartirildi.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "add_admin")
async def add_admin_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Bu bo‘lim faqat bot egasi uchun.", show_alert=True)
    await ask(call, state, Form.admin_id_add, "Admin qilinadigan foydalanuvchining Telegram ID raqamini yuboring:")


@dp.message(Form.admin_id_add)
async def add_admin_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        return await message.answer("Faqat raqam yuboring:")
    data = load_data()
    if user_id not in data["admins"]:
        data["admins"].append(user_id)
    data.setdefault("admin_permissions", {}).setdefault(str(user_id), ["add_content", "add_episode"])
    save_data(data)
    await state.clear()
    await message.answer(f"✅ Adminlar ro'yxatiga qo'shildi: <code>{user_id}</code>", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "del_admin")
async def del_admin_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Bu bo‘lim faqat bot egasi uchun.", show_alert=True)
    await ask(call, state, Form.admin_id_del, "Adminlikdan olinadigan Telegram ID raqamini yuboring:")


@dp.message(Form.admin_id_del)
async def del_admin_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        return await message.answer("Faqat raqam yuboring:")
    data = load_data()
    if user_id == OWNER_ID:
        return await message.answer("❌ Asosiy adminni o'chirib bo'lmaydi.")
    if user_id in data["admins"]:
        data["admins"].remove(user_id)
        save_data(data)
    await state.clear()
    await message.answer("✅ Admin ro'yxati yangilandi.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "add_channel")
async def add_channel_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Bu bo‘lim faqat bot egasi uchun.", show_alert=True)
    await ask(call, state, Form.channel_add,
        "Kanalni yuboring. Ochiq kanal: @username\n"
        "Maxfiy kanal: -100...ID | https://t.me/+taklif_havolasi\n"
        "Masalan: -1001234567890 | https://t.me/+AbCdEfGh\n"
        "Bot kanal ichida administrator bo'lishi kerak. Maxfiy kanal uchun ID va taklif havolasi ikkalasi ham kerak:")


@dp.message(Form.channel_add)
async def add_channel_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    channel = (message.text or "").strip()
    data = load_data()
    if not channel:
        return await message.answer("❌ Kanal ma'lumoti bo'sh. Qayta yuboring yoki /cancel bosing.")
    chat_ref, invite_url, raw = parse_channel_entry(channel)
    if isinstance(chat_ref, str) and chat_ref.startswith("https://t.me/+"):
        return await message.answer("❌ Maxfiy kanal uchun ID ham kerak: -100...ID | https://t.me/+...")
    if not (str(chat_ref).startswith("@") or (isinstance(chat_ref, int) and str(chat_ref).startswith("-100"))):
        return await message.answer("❌ Kanal formati noto'g'ri. @username yoki -100...ID | invite URL yuboring.")
    if isinstance(chat_ref, int) and not invite_url:
        return await message.answer("⚠️ Kanal ID qabul qilindi, lekin tugma chiqishi uchun taklif havolasi ham kerak.\n"
            "Quyidagi formatda yuboring: -100...ID | https://t.me/+taklif_havolasi\n"
            "Yoki /cancel qilib, to'g'ri formatda qayta boshlang.")
    if raw not in data["channels"]:
        data["channels"].append(raw)
        save_data(data)
    await state.clear()
    await message.answer("✅ Kanal ro'yxatga qo'shildi. Tugma va obunani tekshirish uchun bot o'sha kanalda administrator bo'lishi kerak.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "del_channel")
async def del_channel_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Bu bo‘lim faqat bot egasi uchun.", show_alert=True)
    await ask(call, state, Form.channel_del, "O'chiriladigan kanal @username yoki ID'sini yuboring:")


@dp.message(Form.channel_del)
async def del_channel_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    channel = (message.text or "").strip()
    data = load_data()
    # Kanalni ID, username yoki ID|invite formatida o'chirish mumkin.
    target_ref, _target_invite, target_raw = parse_channel_entry(channel)
    matches = []
    for entry in data.get("channels", []):
        ref, _invite, raw = parse_channel_entry(entry)
        if raw == target_raw or str(ref) == str(target_ref):
            matches.append(entry)
    for entry in matches:
        data["channels"].remove(entry)
    if matches:
        save_data(data)
    await state.clear()
    if matches:
        await message.answer("✅ Kanal ro'yxatdan o'chirildi.", reply_markup=admin_menu(message.from_user.id))
    else:
        await message.answer("❌ Bunday kanal topilmadi. Kanal ID/username'ni tekshiring.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "start_text")
async def start_text_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Bu bo‘lim faqat bot egasi uchun.", show_alert=True)
    await ask(call, state, Form.start_text, "Yangi /start xabarini yuboring. HTML teglardan foydalanishingiz mumkin:")


@dp.message(Form.start_text)
async def start_text_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    data = load_data()
    data["start_text"] = message.html_text or message.text or ""
    save_data(data)
    await state.clear()
    await message.answer("✅ /start xabari o'zgartirildi.", reply_markup=admin_menu(message.from_user.id))


@dp.callback_query(F.data == "referral")
async def referral_callback(call: CallbackQuery):
    try:
        me = await call.bot.get_me()
        link = f"https://t.me/{me.username}?start={call.from_user.id}"
        data = load_data()
        referrals = sum(1 for u in data.get("users", {}).values() if str(u.get("referrer", "")) == str(call.from_user.id))
        reward = data.get("settings", {}).get("referral_reward", 5000)
        text = (f"👥 <b>Referal</b>\n\nDo‘stlaringizga havolani yuboring:\n<code>{link}</code>\n\n"
                f"👤 Taklif qilganlar: {referrals}\n💰 Har bir referal uchun: {reward} so‘m")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")]])
        try:
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            await call.message.answer(text, reply_markup=kb)
    finally:
        await call.answer()


@dp.callback_query(F.data == "account")
async def account_callback(call: CallbackQuery):
    data = load_data()
    user = data.setdefault("users", {}).get(str(call.from_user.id), {})
    balance = int(user.get("balance", 0) or 0)
    vip_until = int(user.get("vip_until", 0) or 0)
    vip_status = "✅ Ha" if vip_until > now_ts() else "❌ Yo‘q"
    vip_date = fmt_date(vip_until) if vip_until > now_ts() else "—"
    text = (f"👤 <b>Hisobim</b>\n\n🆔 ID: <code>{call.from_user.id}</code>\n"
            f"💰 Balans: <b>{balance:,} so‘m</b>\n👑 VIP obunachi: <b>{vip_status}</b>\n📅 VIP muddati: <b>{vip_date}</b>".replace(",", " "))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")], [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")]])
    try: await call.message.edit_text(text, reply_markup=kb)
    except Exception: await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "owner_notify_toggle")
async def owner_notify_toggle(call: CallbackQuery):
    if not is_owner(call.from_user.id): return await call.answer("Faqat owner uchun", show_alert=True)
    data = load_data(); cfg = data.setdefault("settings", {})
    cfg["notify_new_content"] = not bool(cfg.get("notify_new_content", True)); save_data(data)
    await call.message.answer("📣 Yangi kontent xabarlari " + ("yoqildi." if cfg["notify_new_content"] else "o‘chirildi."), reply_markup=owner_menu())
    await call.answer()


@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat adminlar uchun", show_alert=True)
    data = load_data()
    users = list(data.get("users", {}).values())
    now = now_ts()
    def seen_in(days):
        return sum(1 for u in users if int(u.get("last_seen", u.get("joined_at", 0)) or 0) >= now - days * 86400)
    def joined_in(days):
        return sum(1 for u in users if int(u.get("joined_at", 0) or 0) >= now - days * 86400)
    active30 = seen_in(30)
    inactive = max(0, len(users) - active30)
    medias = list(data.get("medias", {}).values())
    movie_count = sum(1 for m in medias if m.get("media_type") == "movie")
    anime_count = sum(1 for m in medias if m.get("media_type") == "anime")
    cartoon_count = sum(1 for m in medias if m.get("media_type") == "cartoon")
    series_count = sum(1 for m in medias if m.get("media_type") in {"series", "drama"})
    downloads = data.get("downloads", {})
    def downloads_in(days):
        return sum(1 for t in downloads.values() if isinstance(t, (int, float)) and t >= now - days * 86400) if isinstance(downloads, dict) else 0
    await call.message.answer(
        f"📊 <b>Statistika</b>\n• Obunachilar soni: {len(users)} ta\n• Faol obunachilar: {active30} ta\n• Tark etganlar: {inactive} ta\n\n"
        f"📈 <b>Obunachilar qo‘shilishi</b>\n• Oxirgi 24 soat: +{joined_in(1)} obunachi\n• Oxirgi 7 kun: +{joined_in(7)} obunachi\n• Oxirgi 30 kun: +{joined_in(30)} obunachi\n\n"
        f"📊 <b>Faollik</b>\n• Oxirgi 24 soatda faol: {seen_in(1)} ta\n• Oxirgi 7 kun faol: {seen_in(7)} ta\n• Oxirgi 30 kun faol: {active30} ta\n\n"
        f"📥 <b>Yuklanishlar</b>\n• Oxirgi 24 soat: {downloads_in(1)} ta\n• Oxirgi 7 kun: {downloads_in(7)} ta\n• Oxirgi 30 kun: {downloads_in(30)} ta\n\n"
        f"🎬 Kinolar soni: {movie_count} ta\n🍥 Anime: {anime_count} ta\n🎨 Multfilm: {cartoon_count} ta\n📺 Serial/drama: {series_count} ta"
    )
    await call.answer()


@dp.callback_query(F.data == "appearance")
async def appearance_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat admin va owner uchun", show_alert=True)
    data = load_data()
    colors = data.get("settings", {}).get("button_colors", {})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔎 Qidirish tugmalari: {colors.get('search', 'standart')}", callback_data="color_group:search")],
        [InlineKeyboardButton(text=f"🎬 Kontent tugmalari: {colors.get('content', 'standart')}", callback_data="color_group:content")],
        [InlineKeyboardButton(text=f"⬅️ Katalog chap strelkasi: {colors.get('page_left', 'standart')}", callback_data="color_group:page_left")],
        [InlineKeyboardButton(text=f"➡️ Katalog o‘ng strelkasi: {colors.get('page_right', 'standart')}", callback_data="color_group:page_right")],
        [InlineKeyboardButton(text=f"🔢 1/3 sahifa ko‘rsatkichi: {colors.get('page_indicator', 'standart')}", callback_data="color_group:page_indicator")],
        [InlineKeyboardButton(text=f"⬅️ Orqaga tugmalari: {colors.get('back', 'standart')}", callback_data="color_group:back")],
        [InlineKeyboardButton(text=f"🧩 Boshqa tugmalar: {colors.get('other', 'standart')}", callback_data="color_group:other")],
        [InlineKeyboardButton(text="🌈 Barcha tugmalar", callback_data="color_group:all")],
        [InlineKeyboardButton(text="♻️ Ranglarni tozalash", callback_data="color_reset")],
        [InlineKeyboardButton(text="✏️ Tugma nomlarini o‘zgartirish", callback_data="button_text_menu")],
        [InlineKeyboardButton(text="⬅️ Owner paneli", callback_data="admin_home")]
    ])
    await call.message.answer("🎨 <b>Chiroyli ko‘rinish</b>\nTugma guruhini tanlang, keyin rangini belgilang. Telegram inline tugmalarda faqat standart, ko‘k (primary), yashil (success) va qizil (danger) uslublarni beradi; boshqa rangni kod bilan majburlab bo‘lmaydi.", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("color_group:"))
async def color_group_select(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat admin va owner uchun", show_alert=True)
    group = call.data.split(":", 1)[1]
    if group not in {"search", "content", "back", "other", "all", "page_left", "page_right", "page_indicator"}:
        return await call.answer("Noto‘g‘ri guruh", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟥 Qizil", callback_data=f"color_set:{group}:danger")],
        [InlineKeyboardButton(text="🟩 Yashil", callback_data=f"color_set:{group}:success")],
        [InlineKeyboardButton(text="🟦 Ko‘k", callback_data=f"color_set:{group}:primary")],
        [InlineKeyboardButton(text="⚪ Standart rang", callback_data=f"color_set:{group}:default")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="appearance")]
    ])
    await call.message.answer("Rangni tanlang:", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("color_set:"))
async def color_set_save(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat admin va owner uchun", show_alert=True)
    _, group, color = call.data.split(":", 2)
    if group not in {"search", "content", "back", "other", "all", "page_left", "page_right", "page_indicator"} or color not in {"danger", "success", "primary", "default"}:
        return await call.answer("Noto‘g‘ri rang", show_alert=True)
    data = load_data()
    colors = data.setdefault("settings", {}).setdefault("button_colors", {})
    if color == "default": colors.pop(group, None)
    else: colors[group] = color
    save_data(data)
    await appearance_menu(call)
    await call.answer("Rang saqlandi")


# Sahifalash joylashuvini o‘zgartirish funksiyasi olib tashlandi; standart ⬅️ 1/3 ➡️ saqlanadi.

@dp.callback_query(F.data == "color_reset")
async def color_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat admin va owner uchun", show_alert=True)
    data = load_data()
    data.setdefault("settings", {}).pop("button_colors", None)
    save_data(data)
    await appearance_menu(call)
    await call.answer("Ranglar tozalandi")


# Tugma matnlarini owner panelidan almashtirish mumkin. Kalitlar callback_data bilan bog'langan.
EDITABLE_BUTTONS = {
    "search_menu": "🔎 Kino va serial izlash",
    "guide": "📖 Qo‘llanma",
    "search": "🔢 Kod bo‘yicha qidirish",
    "search_name": "🔠 Nom bo‘yicha qidirish",
    "list": "📚 Kino va seriallar ro‘yxati",
    "movie_list": "🎞 Kinolar ro‘yxati",
    "home": "⬅️ Orqaga / asosiy menyu",
    "add_media": "➕ Kino/serial qo‘shish",
    "add_movie": "➕ Kino qo‘shish",
    "add_series": "➕ Serial qo‘shish",
    "add_episode": "➕ Qism qo‘shish",
    "del_episode": "🗑 Qism o‘chirish",
    "rename_media": "✏️ Nomini o‘zgartirish",
    "recode_media": "🔢 Kodini o‘zgartirish",
    "del_media": "🗑 Kino/serial o‘chirish",
    "stats": "📊 Statistika",
    "broadcast": "📣 Hammaga xabar yuborish",
}

@dp.callback_query(F.data == "button_text_menu")
async def button_text_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat admin va owner uchun", show_alert=True)
    data = load_data()
    saved = data.get("settings", {}).get("button_texts", {})
    rows = []
    for key, default_label in EDITABLE_BUTTONS.items():
        label = saved.get(key, default_label)
        shown = label if len(label) <= 32 else label[:29] + "…"
        rows.append([InlineKeyboardButton(text=shown, callback_data=f"button_text_edit:{key}")])
    rows.append([InlineKeyboardButton(text="♻️ Nomlarni standartga qaytarish", callback_data="button_text_reset")])
    rows.append([InlineKeyboardButton(text="⬅️ Ko‘rinish sozlamalari", callback_data="appearance")])
    await call.message.answer(
        "✏️ <b>Tugma nomlarini o‘zgartirish</b>\nO‘zgartirmoqchi bo‘lgan tugmani tanlang. Matn va oddiy emoji yozishingiz mumkin.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await call.answer()

@dp.callback_query(F.data.startswith("button_text_edit:"))
async def button_text_edit(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat admin va owner uchun", show_alert=True)
    key = call.data.split(":", 1)[1]
    if key not in EDITABLE_BUTTONS:
        return await call.answer("Tugma topilmadi", show_alert=True)
    await state.update_data(button_text_key=key)
    await state.set_state(Form.button_text_value)
    await call.message.answer(
        f"Tanlangan tugma: <b>{EDITABLE_BUTTONS[key]}</b>\nYangi nomini yuboring (1–64 belgi). /cancel — bekor qilish.\n\n"
        "Eslatma: Telegram inline tugma matnida Premium custom emoji ID/entity qo‘llab-quvvatlanmaydi. Oddiy emoji va matn ishlaydi."
    )
    await call.answer()

@dp.message(Form.button_text_value)
async def button_text_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    new_text = (message.text or "").strip()
    if not new_text:
        return await message.answer("Matn bo‘sh bo‘lmasin. Yangi nom yuboring yoki /cancel bosing.")
    if len(new_text) > 64:
        return await message.answer("Tugma nomi 64 belgidan oshmasin. Qisqaroq nom yuboring.")
    state_data = await state.get_data()
    key = state_data.get("button_text_key")
    if key not in EDITABLE_BUTTONS:
        await state.clear()
        return await message.answer("Tugma topilmadi. Qaytadan urinib ko‘ring.")
    data = load_data()
    texts = data.setdefault("settings", {}).setdefault("button_texts", {})
    if new_text == EDITABLE_BUTTONS[key]:
        texts.pop(key, None)
    else:
        texts[key] = new_text
    save_data(data)
    await state.clear()
    await message.answer("✅ Tugma nomi saqlandi. Menyuni qayta ochganda yangi nom ko‘rinadi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Yana nom o‘zgartirish", callback_data="button_text_menu")],
        [InlineKeyboardButton(text="🎨 Ko‘rinish sozlamalari", callback_data="appearance")],
    ]))

@dp.callback_query(F.data == "button_text_reset")
async def button_text_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat admin va owner uchun", show_alert=True)
    data = load_data()
    data.setdefault("settings", {}).pop("button_texts", None)
    save_data(data)
    await button_text_menu(call)
    await call.answer("Tugma nomlari standartga qaytarildi")


@dp.callback_query(F.data == "broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    await state.set_state(Form.broadcast_message)
    await call.message.answer("📣 Barcha foydalanuvchilarga yuboriladigan xabarni matn, rasm yoki video sifatida yuboring. Bekor qilish: /cancel")
    await call.answer()


@dp.message(Form.broadcast_message)
async def broadcast_send(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("Faqat owner uchun.")
    if (message.text or "").strip() == "/cancel":
        await state.clear()
        return await message.answer("Yuborish bekor qilindi.", reply_markup=owner_menu())
    data = load_data()
    recipients = list(data.get("users", {}).keys())
    sent = failed = 0
    await state.clear()
    for uid in recipients:
        try:
            await bot.copy_message(chat_id=int(uid), from_chat_id=message.chat.id, message_id=message.message_id)
            sent += 1
        except Exception as exc:
            failed += 1
            logging.info("Broadcast %s ga yuborilmadi: %s", uid, exc)
        await asyncio.sleep(0.04)
    await message.answer(f"📣 Xabar yuborish tugadi.\n✅ Yetkazildi: {sent}\n❌ Yetkazilmadi: {failed}", reply_markup=owner_menu())


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery):
    data = load_data()
    if await check_subscription(call.from_user.id):
        await send_start(call.message, call.from_user.id)
        await call.answer("✅ Obuna tasdiqlandi")
    else:
        await call.answer("Hamma kanallarga obuna bo'ling.", show_alert=True)


@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    current = await state.get_state()
    await state.clear()
    if current:
        text = "✅ Joriy amal bekor qilindi."
        if is_admin(message.from_user.id):
            return await message.answer(text, reply_markup=admin_menu(message.from_user.id))
        return await message.answer(text, reply_markup=main_menu(message.from_user.id))
    await message.answer("Bekor qilinadigan amal yo'q.")


@dp.message(F.text.in_({"▶️ Davom ettirish", "⭐ Yoqtirganlarim"}))
async def main_reply_keyboard_routes(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    await state.clear()
    data = load_data()
    user = data.get("users", {}).get(str(message.from_user.id), {})
    if text == "▶️ Davom ettirish":
        last = user.get("last_watched") or {}
        code = str(last.get("code", ""))
        item = data.get("medias", {}).get(code)
        if item:
            await send_media_card(message.chat.id, code, item)
        else:
            await message.answer("Hali tomosha qilgan narsangiz yo‘q.")
    elif text == "⭐ Yoqtirganlarim":
        codes = user.get("favorites", [])
        rows = []
        for code in codes:
            item = data.get("medias", {}).get(str(code))
            if item:
                rows.append([InlineKeyboardButton(text=f"🎬 {item['name']} · {code}", callback_data=f"show:{code}")])
        rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="home")])
        await message.answer("⭐ <b>Yoqtirganlarim</b>" if len(rows) > 1 else "⭐ Hozircha saqlangan narsa yo‘q.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.message()
async def fallback(message: Message):
    if message.text and message.text.isdigit():
        if not await require_subscription_message(message):
            return
        data = load_data()
        code = message.text.strip()
        if code in data["medias"]:
            media = data["medias"][code]
            await send_media_card(message.chat.id, code, media)
        else:
            await message.answer("❌ Kod topilmadi. Qayta tekshiring.")
    elif message.text and message.text.strip() and not message.text.startswith("/"):
        query = message.text.strip().casefold()
        if query in {"🔎 kino va serial izlash", "kino va serial izlash", "🔎 kino izlash", "kino izlash"}:
            await message.answer("🔎 Kino yoki serial nomini yozing. Qismlar baza kanalidan avtomatik topiladi.", reply_markup=main_menu(message.from_user.id))
            return
        if not await require_subscription_message(message):
            return
        data = load_data()
        matches = [(code, item) for code, item in data.get("medias", {}).items() if query in item.get("name", "").casefold() and media_available_for_user(message.from_user.id, item, data)]
        catalog_matches = [(key, item) for key, item in data.get("channel_catalog", {}).items() if query in item.get("name", "").casefold() or query in item.get("search_text", "").casefold()]
        if matches or catalog_matches:
            rows = [[InlineKeyboardButton(text=f"{'🎞' if item.get('media_type') == 'movie' else '🍥'} {item.get('name', 'Nomsiz')} · {code}", callback_data=f"show:{code}")] for code, item in matches[:30]]
            rows += [[InlineKeyboardButton(text=f"🎬 {item.get('name', 'Nomsiz kino')} · baza kanal", callback_data=f"catalog_show:{key}")] for key, item in catalog_matches[:30]]
            rows.append([InlineKeyboardButton(text="🔎 Yana qidirish", callback_data="search_name")])
            await message.answer("🔎 <b>Topilgan kino va seriallar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await message.answer("❌ Bu nom bo'yicha hech narsa topilmadi. Nomning boshqa qismini yozib ko'ring.", reply_markup=main_menu(message.from_user.id))
    elif message.text:
        await message.answer("Menyudan foydalaning.", reply_markup=main_menu(message.from_user.id))


async def health_check(request: web.Request) -> web.Response:
    """Render health check uchun oddiy HTTP endpoint."""
    return web.Response(text="KinoSerial bot is running", status=200)


async def start_web_server():
    # Render PORT qiymatini o'zi beradi; odatda 10000 bo'ladi.
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logging.info("Health server 0.0.0.0:%s portida ishga tushdi", port)
    return runner




async def vip_expiry_monitor():
    """VIP muddati tugashini avtomatik to'xtatadi va 24 soat oldin bir marta ogohlantiradi."""
    while True:
        try:
            data=load_data(); users=data.setdefault("users",{}); changed=False; now=now_ts()
            for uid, user in users.items():
                until=int(user.get("vip_until",0) or 0)
                if until <= 0:
                    continue
                if until <= now:
                    user["vip_until"]=0
                    user.pop("vip_warned_until",None)
                    changed=True
                    try: await bot.send_message(int(uid), "⏰ VIP obunangiz muddati tugadi. VIP imkoniyatlari avtomatik o'chirildi.")
                    except Exception: pass
                elif until-now <= 86400 and int(user.get("vip_warned_until",0) or 0) != until:
                    try:
                        await bot.send_message(int(uid), f"⚠️ VIP obunangiz {fmt_date(until)} da tugaydi — 1 kundan kam vaqt qoldi. Balansingizni to'ldirib, VIPni uzaytirishingiz mumkin.")
                        user["vip_warned_until"]=until; changed=True
                    except Exception:
                        # Xabar yuborilmasa keyingi aylanishda qayta urinadi.
                        pass
            if changed: save_data(data)
        except Exception:
            logging.exception("VIP expiry monitor error")
        await asyncio.sleep(60)

async def main():
    if not OWNER_ID:
        raise RuntimeError(
            "OWNER_ID topilmadi yoki noto'g'ri. Render Environment bo'limida "
            "OWNER_ID ga Telegram raqamli user ID ni yozing (masalan, 123456789), "
            "yoki ADMIN_ID ga kamida bitta raqamli ID kiriting. @username yozmang."
        )
    data = load_data()
    if OWNER_ID not in data.get("admins", []):
        data.setdefault("admins", []).append(OWNER_ID)
        save_data(data)

    # ADMIN_ID va OWNER_ID ro'yxatini doimiy adminlar ro'yxatiga qo'shamiz.
    for admin_id in [OWNER_ID] + ADMIN_IDS:
        if admin_id and admin_id not in data.setdefault("admins", []):
            data["admins"].append(admin_id)
    save_data(data)
    runner = await start_web_server()
    vip_monitor_task = asyncio.create_task(vip_expiry_monitor())
    try:
        from aiogram.types import BotCommandScopeChat
        await bot.set_my_commands([BotCommand(command="start", description="Botni boshlash")])
        owner_commands = [BotCommand(command="start", description="Botni boshlash"), BotCommand(command="owner", description="Owner paneli"), BotCommand(command="admin", description="Admin paneli"), BotCommand(command="backup", description="Zaxira olish")]
        await bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID))
        for admin_id in set(ADMIN_IDS + [int(x) for x in data.get("admins", []) if str(x).isdigit()]):
            if admin_id and admin_id != OWNER_ID:
                await bot.set_my_commands([BotCommand(command="start", description="Botni boshlash"), BotCommand(command="admin", description="Admin paneli")], scope=BotCommandScopeChat(chat_id=admin_id))
        await dp.start_polling(bot)
    finally:
        vip_monitor_task.cancel()
        try: await vip_monitor_task
        except asyncio.CancelledError: pass
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
