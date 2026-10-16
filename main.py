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
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

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
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def default_data():
    return {
        "admins": [OWNER_ID],
        "start_text": "🍿 <b>ANIME VA KINO OLAMIGA XUSH KELIBSIZ!</b>\n\nSevimli anime va kinolaringizni qidiring, tomosha qiling va yoqtirganlaringizga saqlang.\n\n🔎 <b>Anime va Kino UZ</b>",
        "channels": [],
        "animes": {},
        "channel_catalog": {},
        "users": {},
        "banned_users": [],
        "referral_events": {},
        "settings": {"referral_reward": 199, "vip_month_price": 25000, "vip_max_months": 6, "vip_month_days": 30, "vip_early_days": 3, "admin_contact": "@AnimBot_Admin"}
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
            "favorites": [], "last_watched": None, "joined_at": now_ts()
        }
    else:
        user = data["users"][uid]
        user["username"] = getattr(tg_user, "username", None)
        user["first_name"] = getattr(tg_user, "first_name", "") or user.get("first_name", "")
    return data["users"][uid], is_new


def fmt_date(timestamp: int) -> str:
    if not timestamp:
        return "Faol emas"
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")


def account_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Referal havolam", callback_data="referral")],
        [InlineKeyboardButton(text="👑 VIP sotib olish", callback_data="vip_buy")],
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


async def send_start(message: Message):
    data = load_data()
    # Xabar ostida faqat anime qidirish va qo'llanma inline tugmalari qoladi.
    await message.answer(data["start_text"], reply_markup=main_menu())
    # Asosiy xizmatlar Telegram klaviaturasining pastki qismida doimiy turadi.
    await message.answer("📌 Asosiy menyu", reply_markup=main_reply_keyboard())


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Anime va Kino izlash", callback_data="search_menu")],
        [InlineKeyboardButton(text="📚 Animelar ro‘yxati", callback_data="list")],
        [InlineKeyboardButton(text="🎞 Kinolar ro‘yxati", callback_data="movie_list")],
        [InlineKeyboardButton(text="▶️ Davom ettirish", callback_data="continue_watching")],
        [InlineKeyboardButton(text="⭐ Yoqtirganlarim", callback_data="favorites")],
        [InlineKeyboardButton(text="📖 Qo'llanma", callback_data="guide")],
    ])


def main_reply_keyboard():
    """Pastki, doimiy Telegram klaviaturasi — asosiy xizmatlar."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Hisobim"), KeyboardButton(text="👥 Referal havolam")],
            [KeyboardButton(text="👑 VIP sotib olish")],
            [KeyboardButton(text="💰 Hisob to'ldirish")],
            [KeyboardButton(text="▶️ Davom ettirish"), KeyboardButton(text="⭐ Yoqtirganlarim")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        is_persistent=False,
        input_field_placeholder="Menyuni ochish uchun klaviatura belgisini bosing"
    )


def search_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Kod bo‘yicha qidirish", callback_data="search"), InlineKeyboardButton(text="🔠 Nom bo‘yicha qidirish", callback_data="search_name")],
        [InlineKeyboardButton(text="📚 Animelar ro‘yxati", callback_data="list")],
        [InlineKeyboardButton(text="🎞 Kinolar ro‘yxati", callback_data="movie_list")],
        [InlineKeyboardButton(text="▶️ Davom ettirish", callback_data="continue_watching")],
        [InlineKeyboardButton(text="⭐ Yoqtirganlarim", callback_data="favorites")],
        [InlineKeyboardButton(text="🛑 Orqaga", callback_data="home")],
    ])


def admin_menu():
    """Oddiy admin menyusi: faqat anime va qismlarni boshqarish."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Anime qo'shish", callback_data="add_anime"), InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="add_movie")],
        [InlineKeyboardButton(text="➕ Qism qo'shish", callback_data="add_episode"),
         InlineKeyboardButton(text="🗑 Qism o'chirish", callback_data="del_episode")],
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data="rename_anime"),
         InlineKeyboardButton(text="🔢 Kodini o'zgartirish", callback_data="recode_anime")],
        [InlineKeyboardButton(text="🗑 Anime o'chirish", callback_data="del_anime")],
    ])


def owner_menu():
    """Faqat egaga ko'rinadigan to'liq boshqaruv menyusi."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Anime qo'shish", callback_data="add_anime"), InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="add_movie")],
        [InlineKeyboardButton(text="➕ Qism qo'shish", callback_data="add_episode"), InlineKeyboardButton(text="🗑 Qism o'chirish", callback_data="del_episode")],
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data="rename_anime"), InlineKeyboardButton(text="🔢 Kodini o'zgartirish", callback_data="recode_anime")],
        [InlineKeyboardButton(text="🗑 Anime o'chirish", callback_data="del_anime")],
        [InlineKeyboardButton(text="👑 Admin qo'shish", callback_data="add_admin"), InlineKeyboardButton(text="🚫 Admin o'chirish", callback_data="del_admin")],
        [InlineKeyboardButton(text="📢 Kanal qo'shish", callback_data="add_channel"), InlineKeyboardButton(text="➖ Kanal o'chirish", callback_data="del_channel")],
        [InlineKeyboardButton(text="📝 /start xabarini o'zgartirish", callback_data="start_text")],
        [InlineKeyboardButton(text="👥 Obunachilarni boshqarish", callback_data="manage_users")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
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


def episode_keyboard(code: str, episodes: dict, catalog_key=None, catalog_item=None):
    """Bot ichida qo'shilgan qismlar va baza kanaldagi qismlarni bir xil menyuda ko'rsatadi."""
    rows, row = [], []
    def sort_key(value):
        return (0, int(value)) if str(value).isdigit() else (1, str(value))
    for number in sorted(episodes, key=sort_key):
        row.append(InlineKeyboardButton(text=str(number), callback_data=f"episode:{code}:{number}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if catalog_key and catalog_item:
        for number in sorted(catalog_item.get("parts", {}), key=sort_key):
            row.append(InlineKeyboardButton(text=f"▶️ {number}", callback_data=f"catalog_part:{catalog_key}:{number}"))
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([InlineKeyboardButton(text="⭐ Yoqtirganlarga qo‘shish", callback_data=f"favorite_add:{code}")])
    rows.append([InlineKeyboardButton(text="🛑 Orqaga", callback_data="search_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def anime_caption(code: str, anime: dict) -> str:
    description = (anime.get("description") or "").strip()
    kind = "🎞 Kino" if anime.get("media_type") == "movie" else "🍥 Anime"
    base = f"{kind}: <b>{anime['name']}</b>\n\n🔢 Kod: <code>{code}</code>\n🎞 Qismlar/video: {len(anime.get('episodes', {}))}"
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
    catalog_key, catalog_item = catalog_item_for_anime(data, anime)
    catalog_count = len(catalog_item.get("parts", {})) if catalog_item else 0
    caption = anime_caption(code, anime)
    if catalog_count:
        caption = caption.replace(f"🎞 Qismlar/video: {len(anime.get('episodes', {}))}", f"🎞 Qismlar/video: {len(anime.get('episodes', {})) + catalog_count}")
    markup = episode_keyboard(code, anime.get("episodes", {}), catalog_key, catalog_item)
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
    episode_code = State()
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
    rows.append([InlineKeyboardButton(text="👑 VIP sotib olish", callback_data="vip_buy")])
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
            "🔒 <b>Botdan foydalanishdan oldin majburiy kanallarga obuna bo'ling.</b>",
            reply_markup=subscription_keyboard()
        )
        return await message.answer("📌 Asosiy menyu", reply_markup=main_reply_keyboard())
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
    await call.message.answer(text, reply_markup=account_menu())
    await call.answer()


@dp.callback_query(F.data == "referral")
async def referral_callback(call: CallbackQuery):
    me = await bot.get_me()
    data = load_data()
    user, _ = ensure_user(data, call.from_user)
    save_data(data)
    link = f"https://t.me/{me.username}?start={call.from_user.id}"
    await call.message.answer(
        f"👥 <b>Sizning referal havolangiz</b>\n\n<code>{link}</code>\n\n"
        f"Har bir referalingiz majburiy kanallarga obuna bo'lgach hisobingizga {get_setting('referral_reward')} so'm tushadi.\n"
        f"Referallar soni: {len(user.get('referrals', []))}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Hisobim", callback_data="account")]])
    )
    await call.answer()


@dp.callback_query(F.data == "vip_info")
async def vip_info_callback(call: CallbackQuery):
    await call.message.answer(
        "👑 <b>VIP obuna nima beradi?</b>\n\n"
        "✅ Majburiy kanallarga obuna bo'lmasdan botdan foydalanish.\n"
        f"🎬 Yangi anime va kinolarni oddiy foydalanuvchilardan {int(get_setting('vip_early_days'))} kun oldin ko'rish.\n"
        f"💳 VIP narxi — har oy {int(get_setting('vip_month_price')):,} so'm.\n\nVIP muddati tugashidan 1 kun oldin eslatma yuboriladi.".replace(",", " "),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 VIP sotib olish", callback_data="vip_buy")],
            [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "vip_buy")
async def vip_buy_callback(call: CallbackQuery):
    await call.message.answer(f"👑 <b>VIP muddatini tanlang</b>\nHar oy {int(get_setting('vip_month_price')):,} so'm. 1 oydan {int(get_setting('vip_max_months'))} oygacha:".replace(",", " "), reply_markup=vip_keyboard())
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
            [InlineKeyboardButton(text="⬅️ Hisobim", callback_data="account")]
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
        [InlineKeyboardButton(text=f"📚 Baza kanali: {cfg.get('catalog_channel_id', 'sozlanmagan')}", callback_data="setcfg:catalog_channel_id")],
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
        "catalog_channel_id": "Baza kanalining @username yoki -100... ko'rinishidagi ID sini yuboring. Bot o'sha kanalga ADMIN bo'lishi shart. Masalan @my_movies yoki -1001234567890:"
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
    elif key == "catalog_channel_id":
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
    labels = {"referral_reward": "Referal mukofoti", "vip_month_price": "VIP 1 oy narxi", "vip_max_months": "VIP maksimal muddati", "vip_month_days": "VIP oy davomiyligi", "vip_early_days": "VIP erta ochilish muddati", "admin_contact": "To'lov admini", "catalog_channel_id": "Baza kanali"}
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
    await message.answer("🛠 <b>Admin paneli</b>\nFaqat anime va qismlarni boshqarish.", reply_markup=admin_menu())


@dp.message(Command("owner"))
async def owner_command(message: Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ Bu buyruq faqat bot egasi uchun.")
    await message.answer("👑 <b>Owner paneli</b>\nBarcha boshqaruv funksiyalari.", reply_markup=owner_menu())


@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    data = load_data()
    try:
        await call.message.edit_text(data["start_text"], reply_markup=main_menu())
    except Exception:
        await call.message.answer(data["start_text"], reply_markup=main_menu())
    await call.answer()


@dp.callback_query(F.data == "search_menu")
async def search_menu_open(call: CallbackQuery):
    try:
        await call.message.edit_text("🔎 <b>Anime va Kino izlash</b>\n\nKod yoki nom orqali qidiring, ro‘yxatlardan foydalaning:", reply_markup=search_menu())
    except Exception:
        await call.message.answer("🔎 <b>Anime va Kino izlash</b>\n\nKod yoki nom orqali qidiring, ro‘yxatlardan foydalaning:", reply_markup=search_menu())
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
    """Baza kanali postlarini avtomatik indekslaydi. Bot kanal admini bo'lishi kerak."""
    data = load_data()
    configured = str(data.get("settings", {}).get("catalog_channel_id", CHANNEL_ID) or "").strip()
    if not configured:
        return
    if configured.startswith("@"):
        if (message.chat.username or "").casefold() != configured[1:].casefold():
            return
    elif str(message.chat.id) != configured:
        return

    text = (message.caption or message.text or "").strip()
    if not text:
        return
    tags = re.findall(r"#([\w]+)", text, flags=re.UNICODE)
    part_tag = next((tag for tag in tags if tag.isdigit()), None)
    key = _catalog_key(text, tags)
    if not key:
        return

    item = data.setdefault("channel_catalog", {}).setdefault(key, {
        "name": "", "description": "", "search_text": "", "parts": {}, "updated_at": now_ts()
    })
    clean_lines = [re.sub(r"#([\w]+)", "", line).strip(" \t-|:") for line in text.splitlines()]
    clean_lines = [line for line in clean_lines if line]
    title = clean_lines[0] if clean_lines else ""
    if part_tag:
        if not title:
            title = item.get("name") or "Nomsiz kino"
        item["name"] = item.get("name") or title
        item.setdefault("parts", {})[str(int(part_tag))] = {"chat_id": message.chat.id, "message_id": message.message_id}
        item["search_text"] = (item.get("search_text", "") + " " + text).casefold()
    else:
        # Qism raqami bo'lmagan post kino/anime ma'lumoti deb olinadi.
        if title:
            item["name"] = title
        description = "\n".join(clean_lines[1:]).strip()
        if description:
            item["description"] = description
        item["info_chat_id"] = message.chat.id
        item["info_message_id"] = message.message_id
        item["search_text"] = (item.get("search_text", "") + " " + text).casefold()
    item["updated_at"] = now_ts()
    save_data(data)
    logging.info("Catalog indexed: channel=%s title=%s part=%s", message.chat.id, item.get("name"), part_tag or "info")


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
    for number in sorted(parts, key=lambda value: int(value) if str(value).isdigit() else 999999):
        row.append(InlineKeyboardButton(text=f"▶️ {number}-qism", callback_data=f"catalog_part:{key}:{number}"))
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
        await bot.copy_message(chat_id=call.from_user.id, from_chat_id=part["chat_id"], message_id=part["message_id"])
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
        return await message.answer("❌ Bu nom bo'yicha anime yoki kino topilmadi.", reply_markup=search_menu())
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
        return await message.answer(f"⏳ Bu anime hozircha VIP foydalanuvchilar uchun ochiq. {int(get_setting('vip_early_days'))} kundan keyin hammaga ochiladi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 VIP sotib olish", callback_data="vip_buy")]]))
    await send_anime_card(message.chat.id, code, anime)


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
        _, code, number = call.data.split(":", 2)
        current_data = load_data()
        anime = current_data["animes"][code]
        if not anime_available_for_user(call.from_user.id, anime, current_data):
            await call.answer("Bu anime hozircha VIP uchun ochiq.", show_alert=True)
            return
        episode = anime["episodes"][number]
        user = current_data.setdefault("users", {}).setdefault(str(call.from_user.id), {})
        user["last_watched"] = {"code": code, "number": number, "media_type": anime.get("media_type", "anime"), "updated_at": now_ts()}
        save_data(current_data)
        source_chat = episode["chat_id"]
        source_message = episode["message_id"]
        await bot.copy_message(
            chat_id=call.from_user.id,
            from_chat_id=source_chat,
            message_id=source_message
        )
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
        visible_animes = [(code, a) for code, a in data["animes"].items() if a.get("media_type", "anime") != "movie" and anime_available_for_user(call.from_user.id, a, data)]
        rows = [[InlineKeyboardButton(text=f"🎬 {a['name']} · {code}", callback_data=f"show:{code}")]
                for code, a in visible_animes[:80]]
        rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
        await call.message.answer("📚 <b>Barcha animelar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
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


@dp.callback_query(F.data.startswith("favorite_add:"))
async def favorite_add(call: CallbackQuery):
    code = call.data.split(":", 1)[1]
    data = load_data()
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
        return await call.answer("Anime topilmadi", show_alert=True)
    if int(call.from_user.id) in [int(x) for x in data.get("banned_users", [])]:
        return await call.answer("Botdan foydalanish taqiqlangan.", show_alert=True)
    if not is_vip(call.from_user.id, data) and not await check_subscription(call.from_user.id):
        await call.message.answer("🔒 Avval majburiy kanallarga obuna bo'ling.", reply_markup=subscription_keyboard())
        return await call.answer()
    if not anime_available_for_user(call.from_user.id, data["animes"][code], data):
        await call.message.answer(f"⏳ Bu anime {int(get_setting('vip_early_days'))} kundan keyin hammaga ochiladi. Hozir VIP foydalanuvchilar ko'ra oladi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 VIP sotib olish", callback_data="vip_buy")]]))
        return await call.answer()
    try:
        await call.message.edit_text("🎬 <b>Anime ochildi.</b>")
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
    await message.answer("🎞 Kino nomini yuboring:" if saved.get("media_type") == "movie" else "🍥 Anime nomini yuboring:")


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
        "🖼 Endi anime rasmi va ma'lumotini yuboring.\n"
        "• Rasmni caption bilan yuboring; yoki\n"
        "• Faqat tavsif matnini yuboring.\n"
        "Rasm/tavsif kerak bo'lmasa /skip yuboring."
    )


@dp.message(Form.anime_info)
async def anime_info(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    saved = await state.get_data()
    data = load_data()
    code, name = saved["new_code"], saved["new_name"]
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
    media_type = saved.get("media_type", "anime")
    data["animes"][code] = {"name": name, "description": description, "poster_file_id": poster, "episodes": {}, "created_at": now_ts(), "media_type": media_type}
    save_data(data)
    await state.clear()
    catalog_channel = get_setting("catalog_channel_id", CHANNEL_ID or "sozlanmagan")
    await message.answer(
        f"✅ <b>{name}</b> (kod <code>{code}</code>) botga qo‘shildi.\n\n"
        f"📚 Baza kanal: <code>{catalog_channel}</code>\n\n"
        "Endi qismlarni baza kanaliga video qilib joylang. Har bir video captioniga anime nomini va qism raqamini yozing, masalan:\n"
        f"<code>{name} #1</code>\n<code>{name} #2</code>\n<code>{name} #3</code>\n\n"
        "Bot qismlarni avtomatik topib, anime kartasidagi tugmalarga qo‘shadi. Kanalda bot admin bo‘lishi va baza kanali sozlangan bo‘lishi shart.\n"
        "Agar eski postlar bo‘lsa, bot ularni avtomatik indekslamaydi; yangi postlar kelganidan boshlab indekslaydi.",
        reply_markup=admin_menu()
    )

@dp.callback_query(F.data == "add_episode")
async def add_episode(call: CallbackQuery, state: FSMContext):
    await ask(call, state, Form.episode_code, "Qismlar qo'shiladigan anime kodini yuboring:")


@dp.message(Form.episode_code)
async def episode_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    code = (message.text or "").strip()
    data = load_data()
    if code not in data["animes"]:
        return await message.answer("❌ Bunday kodli anime yo'q. Qayta yuboring:")
    await state.update_data(batch_code=code, batch_count=0)
    await state.set_state(Form.episode_batch)
    prompt = await message.answer(
        f"🎬 <b>{data['animes'][code]['name']}</b> uchun barcha yangi qismlarni ketma-ket forward qiling.\n"
        "Qismlar mavjud raqamlardan keyin avtomatik raqamlanadi.\n"
        "Tugatgach /done yuboring. Bekor qilish: /cancel"
    )
    await state.update_data(progress_message_id=prompt.message_id)


@dp.message(Form.episode_batch)
async def episode_batch_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("⛔ Bu amal faqat adminlar uchun.")
    if (message.text or "").strip().lower() in ("/done", "done", "tayyor"):
        saved = await state.get_data()
        count = int(saved.get("batch_count", 0))
        code = saved.get("batch_code")
        await state.clear()
        return await message.answer(f"✅ Yakunlandi. {count} ta qism saqlandi.", reply_markup=admin_menu())
    origin = message.forward_origin
    source_chat = None
    source_message = None
    if origin and getattr(origin, "chat", None):
        source_chat = origin.chat.id
        source_message = getattr(origin, "message_id", None)
    if source_chat is None and message.forward_from_chat:
        source_chat = message.forward_from_chat.id
        source_message = message.forward_from_message_id
    if source_chat is None or source_message is None:
        return await message.answer("❌ Kanal postini forward qiling. Tugatish uchun /done, bekor qilish uchun /cancel.")
    saved = await state.get_data()
    code = saved.get("batch_code")
    data = load_data()
    if code not in data["animes"]:
        await state.clear()
        return await message.answer("❌ Anime topilmadi, amal bekor qilindi.", reply_markup=admin_menu())
    episodes = data["animes"][code].setdefault("episodes", {})
    numeric = [int(n) for n in episodes if str(n).isdigit()]
    number = max(numeric, default=0) + 1
    episodes[str(number)] = {"chat_id": source_chat, "message_id": source_message}
    save_data(data)
    count = int(saved.get("batch_count", 0)) + 1
    await state.update_data(batch_count=count)
    progress_id = saved.get("progress_message_id")
    if progress_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=progress_id,
                text=(f"🎬 <b>{data['animes'][code]['name']}</b>\n"
                      f"✅ {count} ta qism saqlandi. Oxirgisi: <b>{number}-qism</b>.\n\n"
                      "Keyingi qismlarni ketma-ket forward qilishda davom eting.\n"
                      "Tugatish: /done · Bekor qilish: /cancel")
            )
        except Exception:
            pass

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
    if not is_owner(call.from_user.id):
        return await call.answer("Adminlar uchun", show_alert=True)
    data = load_data()
    total_episodes = sum(len(a.get("episodes", {})) for a in data["animes"].values())
    await call.message.answer(
        f"📊 <b>Statistika</b>\n🎬 Anime: {len(data['animes'])}\n"
        f"🎞 Qismlar: {total_episodes}\n👑 Adminlar: {len(data['admins'])}\n"
        f"📢 Kanallar: {len(data['channels'])}"
    )
    await call.answer()


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery):
    data = load_data()
    if is_vip(call.from_user.id, data) or await check_subscription(call.from_user.id):
        await process_referral_reward(call.from_user.id)
        await send_start(call.message)
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
        return await message.answer(text, reply_markup=main_menu())
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
                await message.answer(f"⏳ Bu anime hozircha VIP foydalanuvchilar uchun ochiq. {int(get_setting('vip_early_days'))} kundan keyin hammaga ochiladi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 VIP sotib olish", callback_data="vip_buy")]]))
            else:
                await send_anime_card(message.chat.id, code, anime)
        else:
            await message.answer("❌ Kod topilmadi. Qayta tekshiring.")
    elif message.text and message.text.strip() and not message.text.startswith("/"):
        query = message.text.strip().casefold()
        if query in {"🔎 anime va kino izlash", "anime va kino izlash", "🔎 anime izlash", "anime izlash"}:
            await message.answer("🔎 Anime yoki kino nomini yozing. Qismlar baza kanalidan avtomatik topiladi.", reply_markup=main_menu())
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
            await message.answer("❌ Bu nom bo'yicha hech narsa topilmadi. Nomning boshqa qismini yozib ko'ring.", reply_markup=main_menu())
    elif message.text:
        await message.answer("Menyudan foydalaning.", reply_markup=main_menu())


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
                        await bot.send_message(int(uid), f"⚠️ <b>VIP obunangiz tugash arafasida!</b>\nMuddati: {fmt_date(vip_until)}\nDavom ettirish uchun VIP sotib olish menyusiga kiring.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 VIP uzaytirish", callback_data="vip_buy")]]))
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
