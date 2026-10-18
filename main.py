import asyncio
import json
import logging
import os
import re
import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton as TelegramInlineKeyboardButton, ReplyKeyboardRemove
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
    """Owner sozlagan tugma nomi va rangini qo'llaydi."""
    try:
        path = Path(os.getenv("DATA_FILE", "data.json"))
        if path.exists():
            _cfg = json.loads(path.read_text(encoding="utf-8")).get("settings", {})
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
    if cb in ("home", "admin_home") or "Orqaga" in text or "Asosiy menyu" in text:
        return "back"
    if cb.startswith(("episode:", "show:", "season_show:", "catalog_show:")) or cb in ("list", "movie_list", "continue_watching"):
        return "content"
    if cb.startswith(("vip_", "uservip:", "userunvip:")) or "VIP" in text:
        return "vip"
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
REFERRAL_REWARD = 199
VIP_MONTH_PRICE = 25000
VIP_MAX_MONTHS = 6
VIP_MONTH_DAYS = 30
ADMIN_CONTACT = "@AnimBot_Admin"
# Anime postlari saqlanadigan kanal ID sini STORAGE_CHANNEL_ID environment variable orqali berish mumkin.
CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID", "").strip()
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
            output.append(small_caps(token))
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


def default_data():
    return {
        "admins": [OWNER_ID],
        "start_text": "🍿 <b>KINO VA SERIAL OLAMIGA XUSH KELIBSIZ!</b>\n\nSevimli kino, serial, multfilm va animelaringizni qidiring va tomosha qiling.\n\n🔎 <b>Kino olami</b>",
        "channels": [],
        "animes": {},
        "channel_catalog": {},
        "users": {},
        "banned_users": [],
        "referral_events": {},
        "settings": {"referral_reward": 199, "vip_month_price": 25000, "vip_max_months": 6, "vip_month_days": 30, "vip_early_days": 3, "admin_contact": "@AnimBot_Admin", "regular_channel_id": "", "vip_channel_id": "", "button_colors": {}, "button_texts": {}}
    }


def load_data():
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
    for anime in data.get("animes", {}).values():
        anime.setdefault("seasons", {"1": {"episodes": anime.get("episodes", {})}})
        anime.setdefault("genres", "")
        anime.setdefault("status", "")
        anime.setdefault("quality", "")
        anime.setdefault("voice", "")
    # Avvaldan saqlangan animelarni yangi qoida bo'yicha darhol ochiq qoldiramiz.
    for anime in data.get("animes", {}).values():
        anime.setdefault("created_at", 0)
    for user_id, user in data.get("users", {}).items():
        user.setdefault("balance", 0)
        user.setdefault("referrals", [])
        user.setdefault("referred_by", None)
        user.setdefault("referral_rewarded", False)
        user.setdefault("vip_until", 0)
        user.setdefault("vip_warning_sent_for", 0)
        user.setdefault("favorites", [])
        user.setdefault("last_watched", None)
        user.setdefault("last_seen", user.get("joined_at", 0))
    return data


def get_setting(name: str):
    return load_data().get("settings", {}).get(name, default_data()["settings"].get(name))


def save_data(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID) and user_id == OWNER_ID


def is_admin(user_id: int) -> bool:
    # Adminlar faqat anime/qism boshqaruvini bajaradi; owner barcha huquqlarga ega.
    return is_owner(user_id) or user_id in ADMIN_IDS or user_id in load_data().get("admins", [])


def now_ts() -> int:
    return int(time.time())


def is_vip(user_id: int, data=None) -> bool:
    data = data or load_data()
    user = data.get("users", {}).get(str(user_id), {})
    return int(user.get("vip_until", 0) or 0) > now_ts()


def ensure_user(data, tg_user):
    uid = str(tg_user.id)
    is_new = uid not in data.setdefault("users", {})
    if is_new:
        data["users"][uid] = {
            "id": tg_user.id, "username": getattr(tg_user, "username", None),
            "first_name": getattr(tg_user, "first_name", "") or "",
            "balance": 0, "referrals": [], "referred_by": None,
            "referral_rewarded": False, "vip_until": 0, "vip_warning_sent_for": 0,
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


def account_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Referal havolam", callback_data="referral")],
        [InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")],
        [InlineKeyboardButton(text="💰 Hisob to'ldirish", callback_data="balance_help")],
        [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="home")]
    ])


def vip_keyboard():
    price = int(get_setting("vip_month_price"))
    max_months = int(get_setting("vip_max_months"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{months} oy — {months * price:,} so'm".replace(",", " "), callback_data=f"vip_months:{months}")]
        for months in range(1, max_months + 1)
    ] + [[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")]])


def vip_confirm_keyboard(months: int):
    amount = months * int(get_setting("vip_month_price"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash 1/2", callback_data=f"vip_confirm1:{months}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="home")]
    ])


def vip_second_confirm_keyboard(months: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Yakuniy tasdiqlash", callback_data=f"vip_confirm2:{months}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="home")]
    ])


async def process_referral_reward(user_id: int):
    """Majburiy kanallar tasdiqlangandan keyin referal mukofotini bir marta beradi."""
    data = load_data()
    user = data.get("users", {}).get(str(user_id))
    if not user or user.get("referral_rewarded"):
        return
    referrer_id = user.get("referred_by")
    if not referrer_id or int(referrer_id) == int(user_id):
        return
    if not await check_subscription(user_id):
        return
    referrer = data.get("users", {}).get(str(referrer_id))
    if not referrer:
        return
    user["referral_rewarded"] = True
    reward = int(data.get("settings", {}).get("referral_reward", REFERRAL_REWARD))
    referrer["balance"] = int(referrer.get("balance", 0)) + reward
    referrer.setdefault("referrals", [])
    if int(user_id) not in [int(x) for x in referrer["referrals"]]:
        referrer["referrals"].append(int(user_id))
    save_data(data)
    try:
        await bot.send_message(int(referrer_id),
            f"🎉 Yangi referalingiz obuna shartlarini bajardi!\n"
            f"👤 Foydalanuvchi ID: <code>{user_id}</code>\n"
            f"💰 Hisobingizga {reward} so'm qo'shildi.")
    except Exception as exc:
        logging.info("Referal xabari yuborilmadi: %s", exc)


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
    await message.answer(data["start_text"], reply_markup=main_menu(menu_user_id))


def main_menu(user_id=None):
    # Qidiruvga tegishli katalog, sevimlilar va davom ettirish ichki menyuda.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Kino va video izlash", callback_data="search_menu")],
        [InlineKeyboardButton(text="👤 Hisobim", callback_data="account"), InlineKeyboardButton(text="👥 Referal", callback_data="referral")],
        [InlineKeyboardButton(text="💰 Balans", callback_data="balance_help"), InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")],
        [InlineKeyboardButton(text="❓ VIP o‘zi nima?", callback_data="vip_info")],
        [InlineKeyboardButton(text="📖 Qo‘llanma", callback_data="guide")],
    ])


def main_reply_keyboard():
    """Eski pastki klaviatura endi ishlatilmaydi."""
    return ReplyKeyboardRemove()


def search_menu(user_id=None):
    vip = bool(user_id and is_vip(user_id))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Kod bo‘yicha qidirish", callback_data="search"), InlineKeyboardButton(text="🔠 Nom bo‘yicha qidirish", callback_data="search_name")],
        [InlineKeyboardButton(text="🎞 Kinolar ro‘yxati", callback_data="movie_list")],
        [InlineKeyboardButton(text="📺 Seriallar ro‘yxati", callback_data="series_list")],
        [InlineKeyboardButton(text="🧸 Multfilmlar ro‘yxati", callback_data="cartoon_list")],
        [InlineKeyboardButton(text="🍥 Animelar ro‘yxati", callback_data="list")],
        [InlineKeyboardButton(text=("▶️ Davom ettirish" if vip else "🔒 Davom ettirish"), callback_data="continue_watching")],
        [InlineKeyboardButton(text=("♥️ Sevimlilar" if vip else "🔒 Sevimlilar"), callback_data="favorites")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")],
    ])


def admin_menu():
    """Oddiy admin menyusi: faqat anime va qismlarni boshqarish."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Anime qo'shish", callback_data="add_anime"), InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="add_movie")],
        [InlineKeyboardButton(text="➕ Serial qo'shish", callback_data="add_series"), InlineKeyboardButton(text="➕ Multfilm qo'shish", callback_data="add_cartoon")],
        [InlineKeyboardButton(text="➕ Qism qo'shish", callback_data="add_episode"),
         InlineKeyboardButton(text="🗑 Qism o'chirish", callback_data="del_episode")],
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data="rename_anime"),
         InlineKeyboardButton(text="🔢 Kodini o'zgartirish", callback_data="recode_anime")],
        [InlineKeyboardButton(text="🗑 Anime o'chirish", callback_data="del_anime")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text="🎨 Chiroyli ko‘rinish", callback_data="appearance")],
    ])


def owner_menu():
    """Faqat egaga ko'rinadigan to'liq boshqaruv menyusi."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Anime qo'shish", callback_data="add_anime"), InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="add_movie")],
        [InlineKeyboardButton(text="➕ Serial qo'shish", callback_data="add_series"), InlineKeyboardButton(text="➕ Multfilm qo'shish", callback_data="add_cartoon")],
        [InlineKeyboardButton(text="➕ Qism qo'shish", callback_data="add_episode"), InlineKeyboardButton(text="🗑 Qism o'chirish", callback_data="del_episode")],
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data="rename_anime"), InlineKeyboardButton(text="🔢 Kodini o'zgartirish", callback_data="recode_anime")],
        [InlineKeyboardButton(text="🗑 Anime o'chirish", callback_data="del_anime")],
        [InlineKeyboardButton(text="👑 Admin qo'shish", callback_data="add_admin"), InlineKeyboardButton(text="🚫 Admin o'chirish", callback_data="del_admin")],
        [InlineKeyboardButton(text="📢 Kanal qo'shish", callback_data="add_channel"), InlineKeyboardButton(text="➖ Kanal o'chirish", callback_data="del_channel")],
        [InlineKeyboardButton(text="📝 /start xabarini o'zgartirish", callback_data="start_text")],
        [InlineKeyboardButton(text="👥 Obunachilarni boshqarish", callback_data="manage_users")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text="🎨 Chiroyli ko‘rinish", callback_data="appearance")],
        [InlineKeyboardButton(text="📣 Hammaga xabar yuborish", callback_data="broadcast")],
        [InlineKeyboardButton(text="📚 Baza kanalini sozlash", callback_data="setcfg:catalog_channel_id")],
        [InlineKeyboardButton(text="⚙️ Bot sozlamalari", callback_data="owner_settings")],
    ])


def normalize_media_title(value: str) -> str:
    """Sarlavhani kanal captioni va botdagi anime nomi bilan solishtirish uchun normallashtiradi."""
    value = re.sub(r"#\w+", " ", value or "", flags=re.UNICODE)
    value = re.sub(r"^(nom|title|kino|anime)\s*:\s*", "", value.strip(), flags=re.I)
    return " ".join(value.casefold().split()).strip(" .-–—:")


def catalog_item_for_anime(data: dict, anime: dict):
    target = normalize_media_title(anime.get("name", ""))
    if not target:
        return None, None
    for key, item in data.get("channel_catalog", {}).items():
        if normalize_media_title(item.get("name", "")) == target:
            return key, item
    return None, None


def episode_keyboard(code: str, episodes: dict, catalog_key=None, catalog_item=None, user_id=None, season="1", anime=None):
    """Qismlar faqat 1, 2, 3 ko‘rinishida; ko‘p faslda avval fasl tanlanadi."""
    rows, row = [], []
    anime = anime or {}
    seasons = anime.get("seasons") or {"1": {"episodes": episodes}}
    def sort_key(value):
        return (0, int(value)) if str(value).isdigit() else (1, str(value))
    season_keys = sorted(seasons, key=sort_key)
    tier = "vip" if user_id and is_vip(user_id) else "regular"
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
        separate_bases_configured = bool(get_setting("regular_channel_id") or get_setting("vip_channel_id"))
        for number, record in season_episodes.items():
            if isinstance(record, dict) and tier in record and isinstance(record[tier], dict):
                available_numbers.append(number)
            elif isinstance(record, dict) and "file_id" in record:
                # Ruchnoy qo'shilgan fayllar kanal sozlamalaridan mustaqil.
                available_numbers.append(number)
            elif isinstance(record, dict) and "chat_id" in record and not separate_bases_configured:
                # Eski bitta-bazali format faqat ikki baza sozlanmagan holatda ishlaydi.
                available_numbers.append(number)
        for number in sorted(available_numbers, key=sort_key):
            row.append(InlineKeyboardButton(text=("▶️ Tomosha qilish" if anime.get("media_type") == "movie" else str(number)), callback_data=f"episode:{code}:{season}:{number}"))
            if len(row) == 5:
                rows.append(row); row=[]
        if row: rows.append(row)
        if len(season_keys) > 1:
            rows.append([InlineKeyboardButton(text="⬅️ Fasllar", callback_data=f"season_show:{code}:choose")])
    vip = bool(user_id and is_vip(user_id))
    rows.append([InlineKeyboardButton(text=("♥️ Sevimlilarga qo‘shish" if vip else "🔒 Sevimlilar (VIP)"), callback_data=f"favorite_add:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def anime_caption(code: str, anime: dict) -> str:
    description = (anime.get("description") or "").strip()
    kind = "🎞 Kino" if anime.get("media_type") == "movie" else "📺 Serial" if anime.get("media_type") == "series" else "🧸 Multfilm" if anime.get("media_type") == "cartoon" else "🍥 Anime"
    seasons = anime.get("seasons") or {"1": {"episodes": anime.get("episodes", {})}}
    total_episodes = sum(len(value.get("episodes", {})) for value in seasons.values())
    base = f"{kind}: <b>{anime['name']}</b>\n\n🔢 Kod: <code>{code}</code>\n📺 Fasllar: {len(seasons)}\n🎞 Qismlar: {total_episodes}"
    if anime.get("status"):
        base += f"\n📊 Holati: {anime['status']}"
    if anime.get("quality"):
        base += f"\n🎥 Sifati: {anime['quality']}"
    if anime.get("genres"):
        base += f"\n🎭 Janri: {anime['genres']}"
    if anime.get("voice"):
        base += f"\n🎙 Ovoz: {anime['voice']}"
    if description:
        base += "\n\n" + description
    return base[:1024]


def anime_available_for_user(user_id: int, anime: dict, data=None) -> bool:
    data = data or load_data()
    if is_admin(user_id) or is_vip(user_id, data):
        return True
    created_at = int(anime.get("created_at", 0) or 0)
    return created_at == 0 or now_ts() >= created_at + int((data or load_data()).get("settings", {}).get("vip_early_days", 3)) * 24 * 60 * 60


async def send_anime_card(chat_id: int, code: str, anime: dict):
    data = load_data()
    caption = anime_caption(code, anime)
    seasons = anime.get("seasons") or {"1": {"episodes": anime.get("episodes", {})}}
    season_keys = sorted(seasons, key=lambda v: int(v) if str(v).isdigit() else 999999)
    first_season = season_keys[0] if season_keys else "1"
    season_data = seasons.get(first_season, {"episodes": anime.get("episodes", {})})
    markup = episode_keyboard(code, season_data.get("episodes", {}), user_id=chat_id, season=("choose" if len(season_keys) > 1 else first_season), anime=anime)
    poster = anime.get("poster_file_id")
    if poster:
        await bot.send_photo(chat_id, poster, caption=caption, reply_markup=markup)
    else:
        await bot.send_message(chat_id, caption, reply_markup=markup)


class Form(StatesGroup):
    search_code = State()
    search_name = State()
    anime_code = State()
    anime_name = State()
    anime_info = State()
    anime_details = State()
    anime_season_count = State()
    episode_code = State()
    episode_season = State()
    episode_batch = State()
    delete_code = State()
    delete_episode_code = State()
    delete_episode_number = State()
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
    admin_vip_months = State()
    admin_balance_amount = State()
    owner_setting_value = State()
    broadcast_message = State()
    button_text_value = State()


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
    rows.append([InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def require_subscription_message(message: Message) -> bool:
    data = load_data()
    if int(message.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        await message.answer("⛔ Siz botdan foydalanishdan chetlatilgansiz.")
        return False
    if is_vip(message.from_user.id, data) or await check_subscription(message.from_user.id):
        await process_referral_reward(message.from_user.id)
        return True
    await message.answer(
        "🔒 <b>Botdan foydalanishdan oldin majburiy kanallarga obuna bo'ling.</b>",
        reply_markup=subscription_keyboard()
    )
    return False


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await notify_new_user(message.from_user)
    data = load_data()
    user, _ = ensure_user(data, message.from_user)
    # /start REFERRER_ID deep-link: referal egasini faqat birinchi marta biriktiramiz.
    parts = (message.text or "").split(maxsplit=1)
    referral_added = False
    referrer_id = None
    if len(parts) > 1 and parts[1].strip().isdigit() and not user.get("referred_by"):
        referrer_id = int(parts[1].strip())
        if referrer_id != message.from_user.id and str(referrer_id) in data.get("users", {}):
            user["referred_by"] = referrer_id
            referral_added = True
    save_data(data)
    if referral_added:
        try:
            await bot.send_message(referrer_id,
                f"👥 Sizning referal havolangiz orqali yangi foydalanuvchi keldi (ID: <code>{message.from_user.id}</code>).\n"
                f"💰 {get_setting('referral_reward')} so'm mukofot majburiy kanallarga obuna bo'lgach beriladi.")
        except Exception as exc:
            logging.info("Referal kirish xabari yuborilmadi: %s", exc)
    if int(message.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await message.answer("⛔ Siz botdan foydalanishdan chetlatilgansiz.")
    if not is_vip(message.from_user.id, data) and not await check_subscription(message.from_user.id):
        await message.answer(
            "🔒 <b>Botdan foydalanishdan oldin majburiy kanallarga obuna bo‘ling.</b>",
            reply_markup=subscription_keyboard()
        )
        return
    await process_referral_reward(message.from_user.id)
    await send_start(message)


@dp.callback_query(F.data == "account")
async def account_callback(call: CallbackQuery):
    data = load_data()
    user, _ = ensure_user(data, call.from_user)
    save_data(data)
    uid = str(call.from_user.id)
    referrals = len(user.get("referrals", []))
    vip_until = int(user.get("vip_until", 0) or 0)
    vip_text = f"Faol, {fmt_date(vip_until)} gacha" if vip_until > now_ts() else "Faol emas"
    text = ("👤 <b>Hisobim</b>\n\n"
            f"🆔 ID: <code>{call.from_user.id}</code>\n"
            f"💰 Balans: <b>{int(user.get('balance', 0)):,}</b> so'm\n".replace(",", " ") +
            f"👥 Referallar: <b>{referrals}</b> ta\n👑 VIP: <b>{vip_text}</b>")
    try:
        await call.message.edit_text(text, reply_markup=account_menu())
    except Exception:
        await call.message.answer(text, reply_markup=account_menu())
    await call.answer()


@dp.callback_query(F.data == "referral")
async def referral_callback(call: CallbackQuery):
    me = await bot.get_me()
    data = load_data()
    user, _ = ensure_user(data, call.from_user)
    save_data(data)
    link = f"https://t.me/{me.username}?start={call.from_user.id}"
    text = (
        f"👥 <b>Sizning referal havolangiz</b>\n\n<code>{link}</code>\n\n"
        f"Har bir referalingiz majburiy kanallarga obuna bo'lgach hisobingizga {get_setting('referral_reward')} so'm tushadi.\n"
        f"Referallar soni: {len(user.get('referrals', []))}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")]])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "vip_info")
async def vip_info_callback(call: CallbackQuery):
    text = (
        "👑 <b>VIP — tomoshadan ko‘proq zavq oling!</b>\n\n"
        "VIP obunasi orqali sevimli anime, serial va filmlaringizni yanada qulayroq tomosha qiling! 💎\n\n"
        "✨ <b>VIP imkoniyatlari:</b>\n\n"
        f"🚀 {int(get_setting('vip_early_days'))} kun oldin tomosha qilish\n"
        "🎬 720p–1080p sifatdagi videolar (manbada mavjud bo‘lsa)\n"
        "🔓 Majburiy kanallarga obunasiz kirish\n"
        "📤 Kontentni ulashish imkoniyati\n"
        "♥️ Sevimlilar va ▶️ Davom ettirish\n\n"
        f"💎 VIP narxi: oyiga {int(get_setting('vip_month_price')):,} so‘m".replace(",", " ")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "vip_buy")
async def vip_buy_callback(call: CallbackQuery):
    text = f"💎 <b>VIP olish</b>\nHar oy {int(get_setting('vip_month_price')):,} so'm. 1 oydan {int(get_setting('vip_max_months'))} oygacha:".replace(",", " ")
    try:
        await call.message.edit_text(text, reply_markup=vip_keyboard())
    except Exception:
        await call.message.answer(text, reply_markup=vip_keyboard())
    await call.answer()


@dp.callback_query(F.data.startswith("vip_months:"))
async def vip_months_callback(call: CallbackQuery):
    try:
        months = int(call.data.split(":", 1)[1])
    except ValueError:
        return await call.answer("Noto'g'ri muddat", show_alert=True)
    if months < 1 or months > int(get_setting("vip_max_months")):
        return await call.answer("VIP muddati 1–6 oy bo'lishi kerak", show_alert=True)
    amount = months * int(get_setting("vip_month_price"))
    await call.message.answer(
        f"Siz {months} oylik VIP obunani tanladingiz.\nNarxi: <b>{amount:,}</b> so'm.\n\n"
        "Davom etish uchun birinchi marta tasdiqlang.".replace(",", " "),
        reply_markup=vip_confirm_keyboard(months)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("vip_confirm1:"))
async def vip_confirm1_callback(call: CallbackQuery):
    months = int(call.data.split(":", 1)[1])
    if months < 1 or months > int(get_setting("vip_max_months")):
        return await call.answer("Noto'g'ri muddat", show_alert=True)
    amount = months * int(get_setting("vip_month_price"))
    data = load_data()
    user = data.get("users", {}).get(str(call.from_user.id), {})
    balance = int(user.get("balance", 0))
    if balance < amount:
        await call.message.answer(f"❌ Balansingiz yetarli emas.\nKerak: {amount:,} so'm\nBalans: {balance:,} so'm".replace(",", " "), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 Hisob to'ldirish", callback_data="balance_help")]]))
        return await call.answer()
    user["vip_pending_months"] = months
    user["vip_pending_until"] = now_ts() + 10 * 60
    save_data(data)
    await call.message.answer(f"⚠️ Ikkinchi tasdiqlash: {months} oylik VIP uchun {amount:,} so'm balansingizdan yechiladi. Tasdiqlaysizmi?".replace(",", " "), reply_markup=vip_second_confirm_keyboard(months))
    await call.answer()


@dp.callback_query(F.data.startswith("vip_confirm2:"))
async def vip_confirm2_callback(call: CallbackQuery):
    months = int(call.data.split(":", 1)[1])
    if months < 1 or months > int(get_setting("vip_max_months")):
        return await call.answer("Noto'g'ri muddat", show_alert=True)
    data = load_data()
    user = data.get("users", {}).get(str(call.from_user.id))
    amount = months * int(get_setting("vip_month_price"))
    if not user:
        return await call.answer("Hisob topilmadi. /start bosing.", show_alert=True)
    if int(user.get("balance", 0)) < amount:
        return await call.answer("Balansingiz yetarli emas.", show_alert=True)
    pending_months = int(user.get("vip_pending_months", 0) or 0)
    pending_until = int(user.get("vip_pending_until", 0) or 0)
    if pending_months != months or pending_until < now_ts():
        return await call.answer("Tasdiqlash muddati tugagan. VIP xaridini qaytadan boshlang.", show_alert=True)
    user["vip_pending_months"] = 0
    user["vip_pending_until"] = 0
    user["balance"] = int(user.get("balance", 0)) - amount
    start_at = max(now_ts(), int(user.get("vip_until", 0) or 0))
    user["vip_until"] = start_at + months * int(get_setting("vip_month_days")) * 24 * 60 * 60
    user["vip_warning_sent_for"] = 0
    save_data(data)
    await call.message.answer(f"✅ VIP muvaffaqiyatli faollashtirildi!\n📅 Tugash vaqti: {fmt_date(user['vip_until'])}\n💰 Yechilgan summa: {amount:,} so'm".replace(",", " "), reply_markup=account_menu())
    await call.answer("VIP faollashtirildi")


@dp.callback_query(F.data == "balance_help")
async def balance_help_callback(call: CallbackQuery):
    await call.message.answer(
        f"💰 <b>Hisobni to'ldirish</b>\n\nHisobingizni to'ldirish uchun admin bilan shaxsiy yozishmada kelishing: {get_setting('admin_contact')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Admin bilan bog'lanish", url=f"https://t.me/{str(get_setting('admin_contact')).lstrip('@')}")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "owner_settings")
async def owner_settings_callback(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    cfg = load_data().get("settings", {})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👥 Referal mukofoti: {cfg.get('referral_reward', 199)} so'm", callback_data="setcfg:referral_reward")],
        [InlineKeyboardButton(text=f"👑 VIP 1 oy narxi: {cfg.get('vip_month_price', 25000)} so'm", callback_data="setcfg:vip_month_price")],
        [InlineKeyboardButton(text=f"📆 VIP maksimal muddat: {cfg.get('vip_max_months', 6)} oy", callback_data="setcfg:vip_max_months")],
        [InlineKeyboardButton(text=f"⏳ VIP oy davomiyligi: {cfg.get('vip_month_days', 30)} kun", callback_data="setcfg:vip_month_days")],
        [InlineKeyboardButton(text=f"🎬 Erta ko'rish: {cfg.get('vip_early_days', 3)} kun", callback_data="setcfg:vip_early_days")],
        [InlineKeyboardButton(text=f"💬 To'lov admini: {cfg.get('admin_contact', '@AnimBot_Admin')}", callback_data="setcfg:admin_contact")],
        [InlineKeyboardButton(text=f"👥 Oddiy baza: {cfg.get('regular_channel_id') or 'sozlanmagan'}", callback_data="setcfg:regular_channel_id")],
        [InlineKeyboardButton(text=f"👑 VIP baza: {cfg.get('vip_channel_id') or 'sozlanmagan'}", callback_data="setcfg:vip_channel_id")],
        [InlineKeyboardButton(text=f"📚 Eski umumiy baza: {cfg.get('catalog_channel_id', 'sozlanmagan')}", callback_data="setcfg:catalog_channel_id")],
        [InlineKeyboardButton(text="⬅️ Owner paneli", callback_data="admin_home")]
    ])
    await call.message.answer("⚙️ <b>Bot sozlamalari</b>\nBu yerdagi o'zgarishlar data.json fayliga saqlanadi; kodni o'zgartirish yoki redeploy qilish shart emas.\n\nO'zgartirmoqchi bo'lgan sozlamani tanlang:", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("setcfg:"))
async def owner_setting_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Faqat owner uchun", show_alert=True)
    key = call.data.split(":", 1)[1]
    prompts = {
        "referral_reward": "Har bir muvaffaqiyatli referal uchun beriladigan mukofotni so'mda yuboring:",
        "vip_month_price": "VIPning 1 oylik narxini so'mda yuboring:",
        "vip_max_months": "Sotib olish mumkin bo'lgan maksimal VIP oy sonini yuboring (1–24):",
        "vip_month_days": "VIP hisobida bir oy necha kun bo'lishini yuboring (1–365):",
        "vip_early_days": "Yangi anime/kino VIP uchun necha kun oldin ochilishini yuboring (0–30):",
        "admin_contact": "To'lov uchun admin username yuboring, masalan @AnimBot_Admin:",
        "catalog_channel_id": "Eski umumiy baza kanalining @username yoki -100... ID sini yuboring:",
        "regular_channel_id": "Oddiy foydalanuvchilar bazasining @username yoki -100... ID sini yuboring. Bot kanalga admin bo‘lishi kerak:",
        "vip_channel_id": "VIP bazasining @username yoki -100... ID sini yuboring. Bot kanalga admin bo‘lishi kerak:",
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
    limits = {"referral_reward": (0, 1000000000), "vip_month_price": (0, 1000000000), "vip_max_months": (1, 24), "vip_month_days": (1, 365), "vip_early_days": (0, 30)}
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
    elif key in ("catalog_channel_id", "regular_channel_id", "vip_channel_id"):
        value = raw
        if not ((value.startswith("@") and len(value) > 1 and " " not in value) or (value.startswith("-100") and value[1:].isdigit())):
            return await message.answer("Kanal @username yoki -100... ID ko'rinishida bo'lsin. Qayta yuboring:")
    else:
        await state.clear()
        return await message.answer("Sozlama topilmadi.")
    data = load_data()
    data.setdefault("settings", {})[key] = value
    save_data(data)
    await state.clear()
    labels = {"referral_reward": "Referal mukofoti", "vip_month_price": "VIP 1 oy narxi", "vip_max_months": "VIP maksimal muddati", "vip_month_days": "VIP oy davomiyligi", "vip_early_days": "VIP erta ochilish muddati", "admin_contact": "To'lov admini", "catalog_channel_id": "Eski baza kanali", "regular_channel_id": "Oddiy baza kanali", "vip_channel_id": "VIP baza kanali"}
    await message.answer(f"✅ {labels[key]} yangilandi: <b>{value}{' so\'m' if key in ('referral_reward', 'vip_month_price') else ''}</b>\nO'zgarish darhol kuchga kirdi.", reply_markup=owner_menu())


@dp.callback_query(F.data == "manage_users")
async def manage_users_callback(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    await state.set_state(Form.manage_user_id)
    await call.message.answer("👥 <b>Obunachilarni boshqarish</b>\nBoshqariladigan foydalanuvchi ID sini yuboring:")
    await call.answer()


@dp.callback_query(F.data == "admin_home")
async def admin_home_callback(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    if is_owner(call.from_user.id):
        await call.message.answer("👑 <b>Owner paneli</b>", reply_markup=owner_menu())
    else:
        await call.message.answer("🛠 <b>Admin paneli</b>", reply_markup=admin_menu())
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
        return await message.answer("❌ Bu foydalanuvchi bazada topilmadi. U botda /start bosgan bo'lishi kerak.")
    vip = is_vip(user_id, data)
    banned = user_id in [int(x) for x in data.get("banned_users", [])]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Ban qilish" if not banned else "✅ Unban qilish", callback_data=f"userban:{user_id}")],
        [InlineKeyboardButton(text="👑 VIP berish", callback_data=f"uservip:{user_id}") , InlineKeyboardButton(text="➖ VIP olish", callback_data=f"userunvip:{user_id}")],
        [InlineKeyboardButton(text="💰 Balans to'ldirish", callback_data=f"userbalance:{user_id}")],
        [InlineKeyboardButton(text="⬅️ Admin menyu", callback_data="admin_home")]
    ])
    await message.answer(f"👤 <b>Foydalanuvchi</b>\nID: <code>{user_id}</code>\nUsername: @{user.get('username') or 'yo‘q'}\nBalans: {user.get('balance', 0)} so'm\nVIP: {'Faol' if vip else 'Faol emas'}\nVIP tugashi: {fmt_date(int(user.get('vip_until', 0) or 0))}\nBan: {'Ha' if banned else 'Yo‘q'}", reply_markup=kb)


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
async def user_vip_callback(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    user_id = int(call.data.split(":", 1)[1])
    await state.set_state(Form.admin_vip_months)
    await state.update_data(target_user_id=user_id, vip_action="add")
    await call.message.answer("VIP berish uchun oy sonini yuboring (1–6):")
    await call.answer()


@dp.callback_query(F.data.startswith("userunvip:"))
async def user_unvip_callback(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    user_id = int(call.data.split(":", 1)[1])
    data = load_data()
    if str(user_id) in data.get("users", {}):
        data["users"][str(user_id)]["vip_until"] = 0
        data["users"][str(user_id)]["vip_warning_sent_for"] = 0
        save_data(data)
    await call.answer("VIP olib tashlandi", show_alert=True)
    await show_user_management(call.message, user_id)


@dp.message(Form.admin_vip_months)
async def admin_vip_months_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Adminlar uchun.")
    try:
        months = int((message.text or "").strip())
    except ValueError:
        return await message.answer("1 dan 6 gacha oy sonini yuboring:")
    if months < 1 or months > 6:
        return await message.answer("1 dan 6 gacha oy sonini yuboring:")
    saved = await state.get_data()
    user_id = int(saved["target_user_id"])
    data = load_data()
    user = data.get("users", {}).get(str(user_id))
    if not user:
        await state.clear()
        return await message.answer("Foydalanuvchi topilmadi.")
    user["vip_until"] = max(now_ts(), int(user.get("vip_until", 0) or 0)) + months * int(get_setting("vip_month_days")) * 24 * 60 * 60
    user["vip_warning_sent_for"] = 0
    save_data(data)
    await state.clear()
    await message.answer(f"✅ {user_id} foydalanuvchiga {months} oy VIP berildi.", reply_markup=admin_menu())
    try:
        await bot.send_message(user_id, f"👑 Sizga admin tomonidan {months} oy VIP berildi. Tugash vaqti: {fmt_date(user['vip_until'])}")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("userbalance:"))
async def user_balance_callback(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    user_id = int(call.data.split(":", 1)[1])
    await state.set_state(Form.admin_balance_amount)
    await state.update_data(target_user_id=user_id)
    await call.message.answer("Hisobiga qo'shiladigan summani so'mda yuboring (faqat raqam):")
    await call.answer()


@dp.message(Form.admin_balance_amount)
async def admin_balance_amount_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Adminlar uchun.")
    try:
        amount = int((message.text or "").strip())
    except ValueError:
        return await message.answer("Faqat musbat raqam yuboring:")
    if amount <= 0:
        return await message.answer("Summa 0 dan katta bo'lishi kerak:")
    saved = await state.get_data()
    user_id = int(saved["target_user_id"])
    data = load_data()
    user = data.get("users", {}).get(str(user_id))
    if not user:
        await state.clear()
        return await message.answer("Foydalanuvchi topilmadi.")
    user["balance"] = int(user.get("balance", 0)) + amount
    save_data(data)
    await state.clear()
    await message.answer(f"✅ {user_id} hisobiga {amount:,} so'm qo'shildi.".replace(",", " "), reply_markup=admin_menu())
    try:
        await bot.send_message(user_id, f"💰 Hisobingiz admin tomonidan {amount:,} so'mga to'ldirildi.".replace(",", " "))
    except Exception:
        pass


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Sizda admin huquqi yo'q.")
    await message.answer("🛠 <b>Admin paneli</b>\nKino, serial, multfilm va anime kontentlarini boshqarish.", reply_markup=admin_menu())


@dp.message(Command("owner"))
async def owner_command(message: Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ Bu buyruq faqat bot egasi uchun.")
    await message.answer("👑 <b>Owner paneli</b>\nKino, serial, multfilm va anime kontentlarini boshqarish.", reply_markup=owner_menu())


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
        await call.message.edit_text("🔎 <b>Kino va video izlash</b>\n\nKod yoki nom orqali qidiring, ro‘yxatlardan foydalaning:", reply_markup=search_menu(call.from_user.id))
    except Exception:
        await call.message.answer("🔎 <b>Kino va video izlash</b>\n\nKod yoki nom orqali qidiring, ro‘yxatlardan foydalaning:", reply_markup=search_menu(call.from_user.id))
    await call.answer()


@dp.callback_query(F.data == "guide")
async def guide(call: CallbackQuery):
    text = "📖 <b>Qo'llanma</b>\n\n1️⃣ Anime va Kino izlashni bosing.\n2️⃣ Kod yoki nom orqali qidiring, Animelar/Kinolar ro‘yxatini oching.\n3️⃣ Kerakli anime qismi yoki kino videosini bosing. Yoqtirganlarga saqlang va Davom ettirish orqali qayting.\n\n🛑 Orqaga tugmasi oldingi menyuga qaytaradi."
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
        identity = re.sub(r"^(nom|title|kino|anime)\s*:\s*", "", identity, flags=re.I)
        identity = " ".join(identity.casefold().split())
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12] if identity else ""


@dp.channel_post()
async def index_storage_channel_post(message: Message):
    """Oddiy/VIP baza postlarini kod, fasl va qism bo‘yicha indekslaydi.

    Caption misoli: #anime_25 #season_2 #3 yoki #anime_25 #season_2 #ep_3.
    Bot ikkala bazaga ham admin qilib qo‘yilishi kerak.
    """
    data = load_data()
    settings = data.get("settings", {})
    channel_id = str(message.chat.id)
    regular_ref = str(settings.get("regular_channel_id") or "").strip()
    vip_ref = str(settings.get("vip_channel_id") or "").strip()
    legacy_ref = str(settings.get("catalog_channel_id", CHANNEL_ID) or "").strip()
    def matches(ref):
        if not ref:
            return False
        if ref.startswith("@"):
            return (message.chat.username or "").casefold() == ref[1:].casefold()
        return channel_id == ref
    tier = "vip" if matches(vip_ref) else "regular" if matches(regular_ref) else "legacy" if matches(legacy_ref) else ""
    shared_base = bool(regular_ref and not vip_ref and matches(regular_ref))
    if not tier:
        return
    text = (message.caption or message.text or "").strip()
    tags = re.findall(r"#([\w]+)", text, flags=re.UNICODE)
    code_tag = next((tag.split("_", 1)[1] for tag in tags if tag.casefold().startswith(("anime_", "kino_", "serial_", "movie_")) and tag.split("_", 1)[1].isdigit()), None)
    season_tag = next((tag.split("_", 1)[1] for tag in tags if tag.casefold().startswith(("season_", "fasl_")) and tag.split("_", 1)[1].isdigit()), "1")
    part_tag = next((tag.split("_", 1)[1] for tag in tags if tag.casefold().startswith(("ep_", "qism_")) and tag.split("_", 1)[1].isdigit()), None)
    if not part_tag:
        part_tag = next((tag for tag in tags if tag.isdigit()), None)
    if code_tag and code_tag in data.get("animes", {}) and part_tag:
        anime = data["animes"][code_tag]
        season = anime.setdefault("seasons", {}).setdefault(str(int(season_tag)), {"episodes": {}})
        season.setdefault("episodes", {}).setdefault(str(int(part_tag)), {})[tier] = {"chat_id": message.chat.id, "message_id": message.message_id}
        if shared_base:
            season["episodes"][str(int(part_tag))]["vip"] = season["episodes"][str(int(part_tag))][tier]
        # Legacy `episodes` mapping retained for old content and old callbacks.
        if str(int(season_tag)) == "1":
            anime.setdefault("episodes", {}).setdefault(str(int(part_tag)), {})[tier] = {"chat_id": message.chat.id, "message_id": message.message_id}
            if shared_base:
                anime["episodes"][str(int(part_tag))]["vip"] = anime["episodes"][str(int(part_tag))][tier]
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
    matched_code = next((code for code, item in data.get("animes", {}).items() if normalize_media_title(item.get("name", "")) == normalize_media_title(title)), None)
    if matched_code and old_part:
        anime = data["animes"][matched_code]
        season = anime.setdefault("seasons", {}).setdefault("1", {"episodes": {}})
        season.setdefault("episodes", {}).setdefault(str(int(old_part)), {})[tier] = {"chat_id": message.chat.id, "message_id": message.message_id}
        if shared_base:
            season["episodes"][str(int(old_part))]["vip"] = season["episodes"][str(int(old_part))][tier]
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
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    parts = item.get("parts", {})
    rows, row = [], []
    tier = "vip" if is_vip(call.from_user.id, data) else "regular"
    dual_bases = bool(data.get("settings", {}).get("regular_channel_id") or data.get("settings", {}).get("vip_channel_id"))
    visible_parts = {number: part for number, part in parts.items() if (part.get("tier") == tier) or (not part.get("tier") and not dual_bases)}
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
        tier = "vip" if is_vip(call.from_user.id, data) else "regular"
        dual_bases = bool(data.get("settings", {}).get("regular_channel_id") or data.get("settings", {}).get("vip_channel_id"))
        if (part.get("tier") and part.get("tier") != tier) or (dual_bases and not part.get("tier")):
            return await call.answer("Bu video boshqa baza uchun yoki bazasi aniqlanmagan.", show_alert=True)
    except (KeyError, ValueError):
        return await call.answer("Qism topilmadi.", show_alert=True)
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    user = data.setdefault("users", {}).setdefault(str(call.from_user.id), {})
    matched_code = next((code for code, anime in data.get("animes", {}).items() if normalize_media_title(anime.get("name", "")) == normalize_media_title(item.get("name", ""))), None)
    user["last_watched"] = {"catalog_key": key, "code": matched_code, "number": number, "media_type": "movie", "updated_at": now_ts()}
    save_data(data)
    try:
        await bot.copy_message(chat_id=call.from_user.id, from_chat_id=part["chat_id"], message_id=part["message_id"], protect_content=not is_vip(call.from_user.id, data))
        await call.answer(f"{number}-qism yuborildi")
    except Exception:
        logging.exception("Catalog part copy failed")
        await call.answer("Qism yuborilmadi. Botning kanal adminligi va postni tekshiring.", show_alert=True)


@dp.callback_query(F.data == "search_name")
async def search_name_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Form.search_name)
    await call.message.answer("🔤 Anime nomini yoki nomining bir qismini yozing:")
    await call.answer()


@dp.message(Form.search_name)
async def search_name_result(message: Message, state: FSMContext):
    await state.clear()
    if not await require_subscription_message(message):
        return
    query = (message.text or "").strip().casefold()
    data = load_data()
    matches = [(code, anime) for code, anime in data["animes"].items() if query in anime["name"].casefold() and anime_available_for_user(message.from_user.id, anime, data)]
    catalog_matches = [(key, item) for key, item in data.get("channel_catalog", {}).items() if query and (query in item.get("name", "").casefold() or query in item.get("search_text", "").casefold())]
    if not matches and not catalog_matches:
        return await message.answer("❌ Bu nom bo'yicha anime yoki kino topilmadi.", reply_markup=search_menu(call.from_user.id))
    rows = [[InlineKeyboardButton(text=f"{'🎞' if anime.get('media_type') == 'movie' else '🍥'} {anime['name']} · {code}", callback_data=f"show:{code}")] for code, anime in matches[:40]]
    registered_titles = {normalize_media_title(anime.get("name", "")) for _, anime in matches}
    rows += [[InlineKeyboardButton(text=f"🎬 {item.get('name', 'Nomsiz')} · baza", callback_data=f"catalog_show:{key}")] for key, item in catalog_matches[:40] if normalize_media_title(item.get("name", "")) not in registered_titles]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    await message.answer("🔎 <b>Topilgan anime va kinolar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "search")
async def search_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Form.search_code)
    await call.message.answer("🔢 Anime kodini yuboring:")
    await call.answer()


@dp.message(Form.search_code)
async def search_code(message: Message, state: FSMContext):
    await state.clear()
    if not await require_subscription_message(message):
        return
    data = load_data()
    code = message.text.strip() if message.text else ""
    anime = data["animes"].get(code)
    if not anime:
        return await message.answer("❌ Bu kod bo'yicha anime topilmadi yoki hozircha VIP uchun ochiq.")
    if not anime_available_for_user(message.from_user.id, anime, data):
        return await message.answer(f"⏳ Bu anime hozircha VIP foydalanuvchilar uchun ochiq. {int(get_setting('vip_early_days'))} kundan keyin hammaga ochiladi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")]]))
    await send_anime_card(message.chat.id, code, anime)


@dp.callback_query(F.data.startswith("season_show:"))
async def season_show(call: CallbackQuery):
    try:
        _, code, season = call.data.split(":", 2)
        data = load_data()
        anime = data["animes"][code]
        if not anime_available_for_user(call.from_user.id, anime, data):
            return await call.answer("Bu kontent hozircha VIP uchun ochiq.", show_alert=True)
        season_data = (anime.get("seasons") or {}).get(season, {"episodes": anime.get("episodes", {})})
        kb = episode_keyboard(code, season_data.get("episodes", {}), user_id=call.from_user.id, season=season, anime=anime)
        title = f"🎬 <b>{anime['name']}</b>\n\n📺 {season}-fasl — qismni tanlang:" if season != "choose" else f"🎬 <b>{anime['name']}</b>\n\n📺 Faslni tanlang:"
        await call.message.answer(title, reply_markup=kb)
        await call.answer()
    except (KeyError, ValueError):
        await call.answer("Fasl topilmadi.", show_alert=True)


@dp.callback_query(F.data.startswith("episode:"))
async def send_episode(call: CallbackQuery):
    data = load_data()
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
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
        anime = current_data["animes"][code]
        if not anime_available_for_user(call.from_user.id, anime, current_data):
            await call.answer("Bu anime hozircha VIP uchun ochiq.", show_alert=True)
            return
        season_data = (anime.get("seasons") or {}).get(season_number, {"episodes": anime.get("episodes", {})})
        episode_record = season_data.get("episodes", {}).get(number) or anime.get("episodes", {}).get(number)
        if not episode_record:
            await call.answer("Bu qism hali joylanmagan.", show_alert=True)
            return
        tier = "vip" if is_vip(call.from_user.id, current_data) else "regular"
        if isinstance(episode_record, dict) and tier in episode_record:
            episode = episode_record[tier]
        elif isinstance(episode_record, dict) and "file_id" in episode_record:
            # Yangi, ruchnoy qo'shilgan qism.
            episode = episode_record
        elif isinstance(episode_record, dict) and "regular" in episode_record:
            # VIP versiyasi yo'q bo'lsa oddiy versiyadan foydalanadi.
            episode = episode_record.get("regular") or episode_record.get("vip")
        elif isinstance(episode_record, dict) and "chat_id" in episode_record:
            # Eski kanalga bog'langan qismlar bilan moslik.
            episode = episode_record
        else:
            episode = None
        if not episode or not isinstance(episode, dict):
            await call.answer("Bu qism hali qo'shilmagan.", show_alert=True)
            return
        user = current_data.setdefault("users", {}).setdefault(str(call.from_user.id), {})
        user["last_watched"] = {"code": code, "season": season_number, "number": number, "media_type": anime.get("media_type", "anime"), "updated_at": now_ts()}
        save_data(current_data)
        protect = not is_vip(call.from_user.id, current_data)
        if episode.get("file_id"):
            media_type = episode.get("media_type", "video")
            file_id = episode["file_id"]
            caption = episode.get("caption") or f"🎬 <b>{anime.get('name', 'Kontent')}</b> — {number}-qism"
            send_method = {
                "video": bot.send_video,
                "document": bot.send_document,
                "audio": bot.send_audio,
                "animation": bot.send_animation,
                "photo": bot.send_photo,
                "voice": bot.send_voice,
            }.get(media_type, bot.send_video)
            await send_method(chat_id=call.from_user.id, **{media_type: file_id}, caption=caption, protect_content=protect)
        elif "chat_id" in episode and "message_id" in episode:
            await bot.copy_message(chat_id=call.from_user.id, from_chat_id=episode["chat_id"], message_id=episode["message_id"], protect_content=protect)
        else:
            await call.answer("Qism ma'lumoti noto'g'ri saqlangan.", show_alert=True)
            return
        await call.answer("Qism yuborildi")
    except Exception:
        await call.answer("❌ Qism yuborilmadi. Kanal va xabarni tekshiring.", show_alert=True)


@dp.callback_query(F.data == "list")
async def anime_list(call: CallbackQuery):
    data = load_data()
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    if not data["animes"]:
        await call.message.answer("📭 Hozircha anime qo'shilmagan.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")]]))
    else:
        visible_animes = [(code, a) for code, a in data["animes"].items() if a.get("media_type", "anime") == "anime" and anime_available_for_user(call.from_user.id, a, data)]
        rows = [[InlineKeyboardButton(text=f"🎬 {a['name']} · {code}", callback_data=f"show:{code}")]
                for code, a in visible_animes[:80]]
        rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
        await call.message.answer("🍥 <b>Barcha animelar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data == "movie_list")
async def movie_list(call: CallbackQuery):
    data = load_data()
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo‘ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    items = [(code, item) for code, item in data.get("animes", {}).items() if item.get("media_type") == "movie" and anime_available_for_user(call.from_user.id, item, data)]
    rows = [[InlineKeyboardButton(text=f"🎞 {item['name']} · {code}", callback_data=f"show:{code}")] for code, item in items[:80]]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    await call.message.answer("🎞 <b>Kinolar ro‘yxati</b>" if items else "📭 Hozircha kino qo‘shilmagan.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data == "series_list")
async def series_list(call: CallbackQuery):
    await media_category_list(call, "series", "📺 Seriallar ro‘yxati", "📭 Hozircha serial qo‘shilmagan.")


@dp.callback_query(F.data == "cartoon_list")
async def cartoon_list(call: CallbackQuery):
    await media_category_list(call, "cartoon", "🧸 Multfilmlar ro‘yxati", "📭 Hozircha multfilm qo‘shilmagan.")


async def media_category_list(call: CallbackQuery, media_type: str, title: str, empty_text: str):
    data = load_data()
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo‘ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    items = [(code, item) for code, item in data.get("animes", {}).items() if item.get("media_type", "anime") == media_type and anime_available_for_user(call.from_user.id, item, data)]
    rows = [[InlineKeyboardButton(text=f"🎬 {item['name']} · {code}", callback_data=f"show:{code}")] for code, item in items[:80]]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    await call.message.answer(f"<b>{title}</b>" if items else empty_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data.startswith("favorite_add:"))
async def favorite_add(call: CallbackQuery):
    data = load_data()
    if not is_vip(call.from_user.id, data):
        return await call.answer("🔒 Bu funksiya faqat VIP foydalanuvchilar uchun!", show_alert=False)
    code = call.data.split(":", 1)[1]
    if code not in data.get("animes", {}):
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
    if not is_vip(call.from_user.id, data):
        return await call.answer("🔒 Bu funksiya faqat VIP foydalanuvchilar uchun!", show_alert=False)
    user = data.get("users", {}).get(str(call.from_user.id), {})
    codes = user.get("favorites", [])
    rows = []
    for code in codes:
        item = data.get("animes", {}).get(str(code))
        if item:
            icon = "🎞" if item.get("media_type") == "movie" else "🍥"
            rows.append([InlineKeyboardButton(text=f"{icon} {item['name']} · {code}", callback_data=f"show:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="home")])
    await call.message.answer("⭐ <b>Yoqtirganlarim</b>" if len(rows) > 1 else "⭐ Hozircha saqlangan narsa yo‘q.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data == "continue_watching")
async def continue_watching(call: CallbackQuery):
    data = load_data()
    if not is_vip(call.from_user.id, data):
        return await call.answer("🔒 Bu funksiya faqat VIP foydalanuvchilar uchun!", show_alert=False)
    user = data.get("users", {}).get(str(call.from_user.id), {})
    last = user.get("last_watched") or {}
    code = str(last.get("code", ""))
    item = data.get("animes", {}).get(code)
    if item:
        await send_anime_card(call.from_user.id, code, item)
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
async def show_anime(call: CallbackQuery):
    code = call.data.split(":", 1)[1]
    data = load_data()
    if code not in data["animes"]:
        return await call.answer("Kontent topilmadi", show_alert=True)
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    if not anime_available_for_user(call.from_user.id, data["animes"][code], data):
        await call.message.answer(f"⏳ Bu anime {int(get_setting('vip_early_days'))} kundan keyin hammaga ochiladi. Hozir VIP foydalanuvchilar ko'ra oladi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")]]))
        return await call.answer()
    try:
        await call.message.edit_text("🎬 <b>Kontent ochildi.</b>")
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await send_anime_card(call.from_user.id, code, data["animes"][code])
    await call.answer()


async def ask(call: CallbackQuery, state: FSMContext, new_state: State, prompt: str):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Adminlar uchun.", show_alert=True)
    await state.set_state(new_state)
    await call.message.answer(prompt)
    await call.answer()


@dp.callback_query(F.data == "add_anime")
async def add_anime(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.anime_code, "🔢 Yangi anime uchun unikal kod yuboring:")


@dp.callback_query(F.data == "add_movie")
async def add_movie(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Adminlar uchun.", show_alert=True)
    await state.update_data(media_type="movie")
    await state.set_state(Form.anime_code)
    await call.message.answer("🔢 Yangi kino uchun takrorlanmaydigan kod yuboring:")
    await call.answer()


@dp.callback_query(F.data == "add_series")
async def add_series(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Adminlar uchun.", show_alert=True)
    await state.update_data(media_type="series")
    await state.set_state(Form.anime_code)
    await call.message.answer("🔢 Yangi serial uchun takrorlanmaydigan kod yuboring:")
    await call.answer()


@dp.callback_query(F.data == "add_cartoon")
async def add_cartoon(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Adminlar uchun.", show_alert=True)
    await state.update_data(media_type="cartoon")
    await state.set_state(Form.anime_code)
    await call.message.answer("🔢 Yangi multfilm uchun takrorlanmaydigan kod yuboring:")
    await call.answer()


@dp.message(Form.anime_code)
async def anime_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if not code or code in data["animes"]:
        return await message.answer("❌ Kod bo'sh yoki band. Boshqa kod yuboring:")
    await state.update_data(new_code=code)
    await state.set_state(Form.anime_name)
    saved = await state.get_data()
    await message.answer("🎞 Kino nomini yuboring:" if saved.get("media_type") == "movie" else "📺 Serial nomini yuboring:" if saved.get("media_type") == "series" else "🧸 Multfilm nomini yuboring:" if saved.get("media_type") == "cartoon" else "🍥 Anime nomini yuboring:")


@dp.message(Form.anime_name)
async def anime_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    name = (message.text or "").strip()
    if not name:
        return await message.answer("Anime nomini matn ko'rinishida yuboring:")
    await state.update_data(new_name=name)
    await state.set_state(Form.anime_info)
    await message.answer(
        "🖼 Endi poster rasmi va ma'lumotini yuboring.\n"
        "• Rasmni caption bilan yuboring; yoki\n"
        "• Faqat tavsif matnini yuboring.\n"
        "Rasm/tavsif kerak bo'lmasa /skip yuboring."
    )


@dp.message(Form.anime_info)
async def anime_info(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    poster = None
    description = ""
    if message.text and message.text.strip() == "/skip":
        pass
    elif message.photo:
        poster = message.photo[-1].file_id
        description = (message.caption or "").strip()
    elif message.text:
        description = message.text.strip()
    else:
        return await message.answer("Rasm yoki matn yuboring, yoki /skip bosing.")
    await state.update_data(pending_poster=poster, pending_description=description)
    await state.set_state(Form.anime_details)
    await message.answer(
        "📝 Qo‘shimcha ma’lumotlarni quyidagi ko‘rinishda yuboring:\n"
        "Janr: Ekshn, Fantastika\nHolati: Tugallangan\nSifati: 720p, 1080p\nOvoz: Anibla\n\n"
        "Kerak bo‘lmagan qatorlarni tashlab ketsangiz bo‘ladi. Hammasini o‘tkazish uchun /skip yuboring."
    )


@dp.message(Form.anime_details)
async def anime_details_save(message: Message, state: FSMContext):
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
    media_type = saved.get("media_type", "anime")
    data["animes"][code] = {
        "name": name, "description": saved.get("pending_description", ""),
        "poster_file_id": saved.get("pending_poster"), "episodes": {}, "seasons": {},
        "created_at": now_ts(), "media_type": media_type, **fields
    }
    save_data(data)
    await state.set_state(Form.anime_season_count)
    await message.answer("📺 Bu kontent nechta fasldan iborat? Faqat son yuboring. Kino yoki bir faslli multfilm uchun 1 deb yozing:")


async def notify_content_added(code: str, item: dict):
    """Yangi anime/kino qo‘shilganda barcha ro‘yxatdan o‘tgan foydalanuvchilarga xabar."""
    data = load_data()
    is_movie = item.get("media_type") == "movie"
    label = "KINO" if is_movie else "SERIAL" if item.get("media_type") == "series" else "MULTFILM" if item.get("media_type") == "cartoon" else "ANIME"
    text = (f"🎉 <b>BAZAMIZGA YANGI {label} QO‘SHILDI!</b>\n\n"
            f"🎬 Nomi: <b>{item.get('name', 'Nomsiz')}</b>\n"
            f"🔢 Kodi: <code>{code}</code>\n\n"
            "Tomosha qilish uchun quyidagi tugmani bosing!")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎬 Tomosha qilish", callback_data=f"show:{code}")]])
    for uid in list(data.get("users", {})):
        try:
            await bot.send_message(int(uid), text, reply_markup=kb)
        except Exception as exc:
            logging.info("New content notification failed for %s: %s", uid, exc)


@dp.message(Form.anime_season_count)
async def anime_season_count_save(message: Message, state: FSMContext):
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
    item = data.get("animes", {}).get(code)
    if not item:
        await state.clear()
        return await message.answer("Kontent topilmadi. Qaytadan qo‘shing.", reply_markup=admin_menu())
    item["seasons"] = {str(n): {"episodes": {}} for n in range(1, count + 1)}
    save_data(data)
    await state.clear()
    await notify_content_added(code, item)
    await message.answer(
        f"✅ <b>{item['name']}</b> (kod <code>{code}</code>) botga qo‘shildi.\n\n"
        f"📺 Fasllar: {count} ta\n\n"
        "Endi Admin panel → <b>Qism qo‘shish</b> ni bosing, kodini kiriting va videolarni botga ketma-ket yuboring.\n"
        "Qismlar avtomatik raqamlanadi. Kanal, hashtag yoki forward kerak emas.",
        reply_markup=admin_menu()
    )


@dp.callback_query(F.data == "add_episode")
async def add_episode(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.episode_code, "Qismlar qo‘shiladigan kino/serial/multfilm/anime kodini yuboring:")


@dp.message(Form.episode_code)
async def episode_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if code not in data["animes"]:
        return await message.answer("❌ Bunday kod topilmadi. Qayta yuboring:")
    item = data["animes"][code]
    seasons = item.get("seasons") or {"1": {"episodes": item.get("episodes", {})}}
    await state.update_data(batch_code=code)
    await state.set_state(Form.episode_season)
    await message.answer(
        f"🎬 <b>{item['name']}</b> tanlandi.\nMavjud fasllar: {', '.join(sorted(seasons, key=lambda x: int(x) if str(x).isdigit() else 999))}\n\n"
        "Qaysi faslga qism qo‘shamiz? Fasl raqamini yuboring (masalan: 2).\n"
        "Agar bu yangi fasl bo‘lsa ham raqamini yozing."
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
    item = data.get("animes", {}).get(code)
    if not item:
        await state.clear()
        return await message.answer("Kontent topilmadi. Qaytadan boshlang.", reply_markup=admin_menu())
    item.setdefault("seasons", {}).setdefault(season_no, {"episodes": {}})
    save_data(data)
    await state.update_data(batch_season=season_no, batch_count=0)
    await state.set_state(Form.episode_batch)
    prompt = await message.answer(
        f"🎬 <b>{item['name']}</b> · <b>{season_no}-fasl</b>\nVideolarni ketma-ket yuboring.\n"
        "Bot shu faslda avtomatik 1-qism, 2-qism, 3-qism deb raqamlaydi.\n"
        "Tugatish: /done · Bekor qilish: /cancel"
    )
    await state.update_data(progress_message_id=prompt.message_id)


@dp.message(Form.episode_batch)
async def episode_batch_message(message: Message, state: FSMContext):
    """Admin videolarni botning o'ziga yuboradi; kanal/forward bazasi talab qilinmaydi."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    if (message.text or "").strip().lower() in ("/done", "done", "tayyor"):
        saved = await state.get_data()
        count = int(saved.get("batch_count", 0))
        await state.clear()
        return await message.answer(f"✅ Yakunlandi. {count} ta qism saqlandi.", reply_markup=admin_menu())
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        return await message.answer("❎ Bekor qilindi.", reply_markup=admin_menu())

    media_type = None
    file_id = None
    caption = (message.caption or "").strip()
    if message.video:
        media_type, file_id = "video", message.video.file_id
    elif message.document:
        media_type, file_id = "document", message.document.file_id
    elif message.audio:
        media_type, file_id = "audio", message.audio.file_id
    elif message.animation:
        media_type, file_id = "animation", message.animation.file_id
    elif message.photo:
        media_type, file_id = "photo", message.photo[-1].file_id
    elif message.voice:
        media_type, file_id = "voice", message.voice.file_id
    else:
        return await message.answer("❌ Qism sifatida video yuboring (yoki video faylni hujjat qilib yuboring). Tugatish: /done · Bekor qilish: /cancel")

    saved = await state.get_data()
    code = str(saved.get("batch_code", ""))
    data = load_data()
    anime = data.get("animes", {}).get(code)
    if not anime:
        await state.clear()
        return await message.answer("❌ Kontent topilmadi. Qaytadan boshlang.", reply_markup=admin_menu())
    season_no = str(saved.get("batch_season", "1"))
    season = anime.setdefault("seasons", {}).setdefault(season_no, {"episodes": {}})
    episodes = season.setdefault("episodes", {})
    numeric = [int(n) for n in episodes if str(n).isdigit()]
    number = max(numeric, default=0) + 1
    record = {"file_id": file_id, "media_type": media_type, "caption": caption}
    episodes[str(number)] = record
    anime.setdefault("episodes", {})[str(number)] = record
    save_data(data)
    count = int(saved.get("batch_count", 0)) + 1
    await state.update_data(batch_count=count)
    progress_id = saved.get("progress_message_id")
    if progress_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=progress_id,
                text=(f"🎬 <b>{anime['name']}</b> · {season_no}-fasl\n✅ {count} ta qism saqlandi. Oxirgisi: <b>{number}-qism</b>.\n\n"
                      "Keyingi videolarni shu chatga ketma-ket yuboring.\nTugatish: /done · Bekor qilish: /cancel")
            )
        except Exception:
            pass
    else:
        await message.answer(f"✅ {number}-qism saqlandi. Keyingi videoni yuboring yoki /done yozing.")

@dp.callback_query(F.data == "del_anime")
async def del_anime(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.delete_code, "O'chiriladigan anime kodini yuboring:")


@dp.message(Form.delete_code)
async def delete_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if code not in data["animes"]:
        return await message.answer("❌ Anime kodi topilmadi:")
    name = data["animes"][code]["name"]
    del data["animes"][code]
    save_data(data)
    await state.clear()
    await message.answer(f"🗑 <b>{name}</b> o'chirildi.", reply_markup=admin_menu())


@dp.callback_query(F.data == "del_episode")
async def del_episode(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.delete_episode_code, "Qismi o'chiriladigan anime kodini yuboring:")


@dp.message(Form.delete_episode_code)
async def delete_episode_ask_number(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if code not in data["animes"]:
        return await message.answer("❌ Anime kodi topilmadi. Qayta yuboring:")
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
    if code not in data["animes"] or number not in data["animes"][code]["episodes"]:
        return await message.answer("❌ Bunday qism topilmadi. Qism raqamini qayta yuboring:")
    del data["animes"][code]["episodes"][number]
    save_data(data)
    await state.clear()
    await message.answer(f"🗑 {number}-qism o'chirildi.", reply_markup=admin_menu())


@dp.callback_query(F.data == "rename_anime")
async def rename_start(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.rename_code, "Nomi o'zgaradigan anime kodini yuboring:")


@dp.message(Form.rename_code)
async def rename_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    if code not in load_data()["animes"]:
        return await message.answer("❌ Kod topilmadi:")
    await state.update_data(rename_code=code)
    await state.set_state(Form.rename_name)
    await message.answer("Yangi anime nomini yuboring:")


@dp.message(Form.rename_name)
async def rename_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    saved = await state.get_data()
    data = load_data()
    data["animes"][saved["rename_code"]]["name"] = (message.text or "").strip()
    save_data(data)
    await state.clear()
    await message.answer("✅ Anime nomi o'zgartirildi.", reply_markup=admin_menu())


@dp.callback_query(F.data == "recode_anime")
async def recode_start(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.recode_old, "Eski anime kodini yuboring:")


@dp.message(Form.recode_old)
async def recode_old(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    old = (message.text or "").strip()
    if old not in load_data()["animes"]:
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
    if not new or new in data["animes"]:
        return await message.answer("❌ Yangi kod bo'sh yoki band:")
    data["animes"][new] = data["animes"].pop(saved["recode_old"])
    save_data(data)
    await state.clear()
    await message.answer(f"✅ Kod {new} qilib o'zgartirildi.", reply_markup=admin_menu())


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
        save_data(data)
    await state.clear()
    await message.answer(f"✅ Adminlar ro'yxatiga qo'shildi: <code>{user_id}</code>", reply_markup=admin_menu())


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
    await message.answer("✅ Admin ro'yxati yangilandi.", reply_markup=admin_menu())


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
    await message.answer("✅ Kanal ro'yxatga qo'shildi. Tugma va obunani tekshirish uchun bot o'sha kanalda administrator bo'lishi kerak.", reply_markup=admin_menu())


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
        await message.answer("✅ Kanal ro'yxatdan o'chirildi.", reply_markup=admin_menu())
    else:
        await message.answer("❌ Bunday kanal topilmadi. Kanal ID/username'ni tekshiring.", reply_markup=admin_menu())


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
    await message.answer("✅ /start xabari o'zgartirildi.", reply_markup=admin_menu())


@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Faqat adminlar uchun", show_alert=True)
    data = load_data()
    users = list(data.get("users", {}).values())
    cutoff = now_ts() - 30 * 24 * 60 * 60
    active = sum(1 for u in users if int(u.get("last_seen", u.get("joined_at", 0)) or 0) >= cutoff)
    inactive = max(0, len(users) - active)
    total_episodes = sum(sum(len(season.get("episodes", {})) for season in (a.get("seasons") or {}).values()) if a.get("seasons") else len(a.get("episodes", {})) for a in data["animes"].values())
    await call.message.answer(
        f"📊 <b>Statistika</b>\n👥 Jami foydalanuvchilar: {len(users)}\n"
        f"🟢 Faol (oxirgi 30 kun): {active}\n⚫ Faol emas (30 kundan beri): {inactive}\n"
        f"🎬 Anime/kino/serial: {len(data['animes'])}\n🎞 Qismlar: {total_episodes}\n"
        f"👑 Adminlar: {len(data['admins'])}\n📢 Kanallar: {len(data['channels'])}"
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
        [InlineKeyboardButton(text=f"💎 VIP tugmalari: {colors.get('vip', 'standart')}", callback_data="color_group:vip")],
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
    if group not in {"search", "content", "vip", "back", "other", "all"}:
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
    if group not in {"search", "content", "vip", "back", "other", "all"} or color not in {"danger", "success", "primary", "default"}:
        return await call.answer("Noto‘g‘ri rang", show_alert=True)
    data = load_data()
    colors = data.setdefault("settings", {}).setdefault("button_colors", {})
    if color == "default": colors.pop(group, None)
    else: colors[group] = color
    save_data(data)
    await appearance_menu(call)
    await call.answer("Rang saqlandi")


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
    "search_menu": "🔎 Anime va Kino izlash",
    "account": "👤 Hisobim",
    "referral": "👥 Referal",
    "balance_help": "💰 Balans / hisob to‘ldirish",
    "vip_buy": "💎 VIP olish",
    "vip_info": "❓ VIP o‘zi nima?",
    "guide": "📖 Qo‘llanma",
    "search": "🔢 Kod bo‘yicha qidirish",
    "search_name": "🔠 Nom bo‘yicha qidirish",
    "list": "📚 Animelar ro‘yxati",
    "movie_list": "🎞 Kinolar ro‘yxati",
    "continue_watching": "▶️ Davom ettirish",
    "favorites": "♥️ Sevimlilar",
    "home": "⬅️ Orqaga / asosiy menyu",
    "add_anime": "➕ Anime qo‘shish",
    "add_movie": "➕ Kino qo‘shish",
    "add_series": "➕ Serial qo‘shish",
    "add_episode": "➕ Qism qo‘shish",
    "del_episode": "🗑 Qism o‘chirish",
    "rename_anime": "✏️ Nomini o‘zgartirish",
    "recode_anime": "🔢 Kodini o‘zgartirish",
    "del_anime": "🗑 Anime o‘chirish",
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
    if is_vip(call.from_user.id, data) or await check_subscription(call.from_user.id):
        await process_referral_reward(call.from_user.id)
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
            return await message.answer(text, reply_markup=admin_menu())
        return await message.answer(text, reply_markup=main_menu(message.from_user.id))
    await message.answer("Bekor qilinadigan amal yo'q.")


@dp.message(F.text.in_({"👤 Hisobim", "👥 Referal havolam", "👑 VIP sotib olish", "💰 Hisob to'ldirish", "▶️ Davom ettirish", "⭐ Yoqtirganlarim"}))
async def main_reply_keyboard_routes(message: Message, state: FSMContext):
    """Pastki asosiy klaviaturadagi bo'limlarni tegishli amallarga yo'naltiradi."""
    text = (message.text or "").strip()
    # VIP haqida, xarid va hisob to'ldirish bo'limlari majburiy obuna ekranidan ham ochiq.
    if text in {"👤 Hisobim", "👥 Referal havolam"}:
        if not await require_subscription_message(message) and not is_vip(message.from_user.id):
            return
    await state.clear()
    data = load_data()
    user = data.get("users", {}).get(str(message.from_user.id), {})

    if text == "▶️ Davom ettirish":
        last = user.get("last_watched") or {}
        code = str(last.get("code", ""))
        item = data.get("animes", {}).get(code)
        if item:
            await send_anime_card(message.chat.id, code, item)
        else:
            await message.answer("Hali tomosha qilgan narsangiz yo‘q.")
    elif text == "⭐ Yoqtirganlarim":
        codes = user.get("favorites", [])
        rows = []
        for code in codes:
            item = data.get("animes", {}).get(str(code))
            if item:
                rows.append([InlineKeyboardButton(text=f"{'🎞' if item.get('media_type') == 'movie' else '🍥'} {item['name']} · {code}", callback_data=f"show:{code}")])
        rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="home")])
        await message.answer("⭐ <b>Yoqtirganlarim</b>" if len(rows) > 1 else "⭐ Hozircha saqlangan narsa yo‘q.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif text == "👤 Hisobim":
        referrals = len(user.get("referrals", []))
        vip_until = int(user.get("vip_until", 0) or 0)
        vip_text = f"Faol, {fmt_date(vip_until)} gacha" if vip_until > now_ts() else "Faol emas"
        await message.answer(
            ("👤 <b>Hisobim</b>\n\n"
             f"🆔 ID: <code>{message.from_user.id}</code>\n"
             f"💰 Balans: <b>{int(user.get('balance', 0)):,}</b> so'm\n".replace(",", " ") +
             f"👥 Referallar: <b>{referrals}</b> ta\n👑 VIP: <b>{vip_text}</b>")
        )
    elif text == "👥 Referal havolam":
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start={message.from_user.id}"
        await message.answer(
            f"👥 <b>Sizning referal havolangiz</b>\n\n<code>{link}</code>\n\n"
            f"Har bir referalingiz majburiy kanallarga obuna bo'lgach hisobingizga {get_setting('referral_reward')} so'm tushadi.\n"
            f"Referallar soni: {len(user.get('referrals', []))}"
        )
    elif text == "👑 VIP sotib olish":
        await message.answer(f"👑 <b>VIP muddatini tanlang</b>\nHar oy {int(get_setting('vip_month_price')):,} so'm. 1 oydan {int(get_setting('vip_max_months'))} oygacha:".replace(",", " "), reply_markup=vip_keyboard())
    elif text == "💰 Hisob to'ldirish":
        await message.answer(
            f"💰 <b>Hisobni to'ldirish</b>\n\nHisobingizni to'ldirish uchun admin bilan shaxsiy yozishmada kelishing: {get_setting('admin_contact')}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✉️ Admin bilan bog'lanish", url=f"https://t.me/{str(get_setting('admin_contact')).lstrip('@')}")]])
        )


@dp.message()
async def fallback(message: Message):
    if message.text and message.text.isdigit():
        if not await require_subscription_message(message):
            return
        data = load_data()
        code = message.text.strip()
        if code in data["animes"]:
            anime = data["animes"][code]
            if not anime_available_for_user(message.from_user.id, anime, data):
                await message.answer(f"⏳ Bu anime hozircha VIP foydalanuvchilar uchun ochiq. {int(get_setting('vip_early_days'))} kundan keyin hammaga ochiladi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_buy")]]))
            else:
                await send_anime_card(message.chat.id, code, anime)
        else:
            await message.answer("❌ Kod topilmadi. Qayta tekshiring.")
    elif message.text and message.text.strip() and not message.text.startswith("/"):
        query = message.text.strip().casefold()
        if query in {"🔎 anime va kino izlash", "anime va kino izlash", "🔎 anime izlash", "anime izlash"}:
            await message.answer("🔎 Anime yoki kino nomini yozing. Qismlar baza kanalidan avtomatik topiladi.", reply_markup=main_menu(message.from_user.id))
            return
        if not await require_subscription_message(message):
            return
        data = load_data()
        matches = [(code, item) for code, item in data.get("animes", {}).items() if query in item.get("name", "").casefold() and anime_available_for_user(message.from_user.id, item, data)]
        catalog_matches = [(key, item) for key, item in data.get("channel_catalog", {}).items() if query in item.get("name", "").casefold() or query in item.get("search_text", "").casefold()]
        if matches or catalog_matches:
            rows = [[InlineKeyboardButton(text=f"{'🎞' if item.get('media_type') == 'movie' else '🍥'} {item.get('name', 'Nomsiz')} · {code}", callback_data=f"show:{code}")] for code, item in matches[:30]]
            rows += [[InlineKeyboardButton(text=f"🎬 {item.get('name', 'Nomsiz kino')} · baza kanal", callback_data=f"catalog_show:{key}")] for key, item in catalog_matches[:30]]
            rows.append([InlineKeyboardButton(text="🔎 Yana qidirish", callback_data="search_name")])
            await message.answer("🔎 <b>Topilgan anime va kinolar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await message.answer("❌ Bu nom bo'yicha hech narsa topilmadi. Nomning boshqa qismini yozib ko'ring.", reply_markup=main_menu(message.from_user.id))
    elif message.text:
        await message.answer("Menyudan foydalaning.", reply_markup=main_menu(message.from_user.id))


async def health_check(request: web.Request) -> web.Response:
    """Render health check uchun oddiy HTTP endpoint."""
    return web.Response(text="AnimeBot is running", status=200)


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


async def vip_reminder_worker():
    """VIP tugashidan 24 soat oldin bir martalik ogohlantirish yuboradi."""
    while True:
        try:
            data = load_data()
            now = now_ts()
            changed = False
            for uid, user in data.get("users", {}).items():
                vip_until = int(user.get("vip_until", 0) or 0)
                warned_for = int(user.get("vip_warning_sent_for", 0) or 0)
                if vip_until > now and vip_until - now <= 24 * 60 * 60 and warned_for != vip_until:
                    try:
                        await bot.send_message(int(uid), "👑 <b>VIP obunangiz tugashiga 1 kun qoldi!</b>\n\nSizga taqdim etilgan VIP imkoniyatlaridan foydalanishda davom etish uchun obunangizni uzaytirishingiz mumkin. 💎\n\nSevimli anime, serial va kinolaringiz sizni kutmoqda!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Yana VIP olish", callback_data="vip_buy")]]))
                        user["vip_warning_sent_for"] = vip_until
                        changed = True
                    except Exception as exc:
                        logging.info("VIP ogohlantirishi %s ga yuborilmadi: %s", uid, exc)
            if changed:
                save_data(data)
        except Exception:
            logging.exception("VIP reminder worker xatosi")
        await asyncio.sleep(300)


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
    reminder_task = asyncio.create_task(vip_reminder_worker())
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
