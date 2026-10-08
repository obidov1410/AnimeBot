import asyncio
import html
import io
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import (BufferedInputFile, CallbackQuery, ChatJoinRequest,
                           InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)
from aiohttp import web

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("animebot")

# ----------------------------------------------------------------- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "10000"))
MAIN_ADMIN = int(os.getenv("MAIN_ADMIN_ID", "7041471070"))
EXTRA_ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "2025400572").replace(" ", "").split(",") if x}
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # ixtiyoriy: "Web Animes" tugmasi uchun
TZ = ZoneInfo("Asia/Tashkent")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN environment variable topilmadi!")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL environment variable topilmadi!")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
dp = Dispatcher()
router = Router()
dp.include_router(router)

pool: asyncpg.Pool = None  # type: ignore
BOT_USERNAME = ""
db_admins: set[int] = set()
settings: dict[str, str] = {}
steps: dict[int, str] = {}
tmp: dict[int, dict] = {}
bg_tasks: set = set()
title_cache: dict[str, str] = {}

DEFAULTS = {
    "key1": "🔎 Anime izlash", "key2": "💎 VIP", "key3": "💰 Hisobim",
    "key4": "➕ Pul kiritish", "key5": "📚 Qo'llanma", "key6": "💵 Reklama va Homiylik",
    "valyuta": "so'm", "vip": "25000", "holat": "on", "start": "✨", "qollanma": "",
    "homiy": "", "content": "false", "studio_name": "", "instagram": "", "youtube": "",
    "anime_kanal": "@username",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id BIGINT PRIMARY KEY, sana TEXT, pul BIGINT DEFAULT 0,
    banned BOOLEAN DEFAULT FALSE, vip_until TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS animelar(
    id SERIAL PRIMARY KEY, nom TEXT, rams TEXT, rams_type TEXT DEFAULT 'photo',
    qismi TEXT, davlat TEXT, tili TEXT, yili TEXT, janri TEXT, ani_type TEXT,
    qidiruv INT DEFAULT 0, sana TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS anime_datas(
    pk SERIAL PRIMARY KEY, anime_id INT, qism INT, file_id TEXT,
    sana TIMESTAMPTZ DEFAULT now(), UNIQUE(anime_id, qism));
CREATE TABLE IF NOT EXISTS channels(
    id SERIAL PRIMARY KEY, channel_id TEXT, channel_type TEXT, channel_link TEXT);
CREATE TABLE IF NOT EXISTS join_requests(
    channel_id TEXT, user_id BIGINT, PRIMARY KEY(channel_id, user_id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS admins(user_id BIGINT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS payments(
    id SERIAL PRIMARY KEY, name TEXT, wallet TEXT, addition TEXT);
"""

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
ADD_FLOW = [
    ("nom", "🍿 Anime nomini kiriting:"),
    ("qismi", "🎥 Jami qismlar sonini kiriting:"),
    ("davlat", "🌍 Qaysi davlat ishlab chiqarganini kiriting:"),
    ("tili", "🇺🇿 Qaysi tilda ekanligini kiriting:"),
    ("yili", "📆 Qaysi yilda ishlab chiqarilganini kiriting:"),
    ("janri", "🎞 Janrlarini kiriting:\n\n<i>Na'muna: Drama, Fantastika, Sarguzash</i>"),
    ("ani_type", "🎙️ Fandub nomini kiriting:\n\n<i>Na'muna: @AnimeLiveUz</i>"),
]
EDIT_FIELDS = {"nom": "Nomini", "qismi": "Qismini", "davlat": "Davlatini", "tili": "Tilini",
               "yili": "Yilini", "janri": "Janrini", "ani_type": "Fandub nomini"}
SET_KEYS = {"valyuta": "valyutani", "vip": "VIP narxini (oylik, raqam)", "studio_name": "studia nomini",
            "start": "boshlang'ich matnni", "qollanma": "qo'llanma matnini", "homiy": "homiy matnini"}

# ---------------------------------------------------------------- HELPERS ---
def S(key: str) -> str:
    return settings.get(key, DEFAULTS.get(key, ""))


async def set_setting(key: str, value: str):
    settings[key] = value
    await pool.execute(
        "INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        key, value)


def is_admin(uid: int) -> bool:
    return uid == MAIN_ADMIN or uid in EXTRA_ADMINS or uid in db_admins


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def b(text, cb=None, url=None):
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=cb)


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rkb(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


def chunk(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


ADMIN_PANEL = rkb([
    ["*️⃣ Birlamchi sozlamalar"], ["📊 Statistika", "✉ Xabar Yuborish"], ["📬 Post tayyorlash"],
    ["🎥 Animelar sozlash", "💳 Hamyonlar"], ["🔎 Foydalanuvchini boshqarish"],
    ["📢 Kanallar", "🎛 Tugmalar", "📃 Matnlar"], ["📋 Adminlar", "🤖 Bot holati"], ["◀️ Orqaga"]])
BACK_KB = rkb([["🗄 Boshqarish"]])
ADMIN_TEXTS = {"*️⃣ Birlamchi sozlamalar", "📊 Statistika", "✉ Xabar Yuborish", "📬 Post tayyorlash",
               "🎥 Animelar sozlash", "💳 Hamyonlar", "🔎 Foydalanuvchini boshqarish", "📢 Kanallar",
               "🎛 Tugmalar", "📃 Matnlar", "📋 Adminlar", "🤖 Bot holati"}


def main_menu(uid: int):
    k = [S(f"key{i}") for i in range(1, 7)]
    rows = [[k[0]], [k[1], k[2]], [k[3], k[4]], [k[5]]]
    if is_admin(uid):
        rows.append(["🗄 Boshqarish"])
    return rkb(rows)


async def edit(msg: Message, text: str, kb=None):
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass


async def safe_delete(msg: Message):
    try:
        await msg.delete()
    except Exception:
        pass


async def ask(uid: int, text: str, step: str, kb=BACK_KB):
    steps[uid] = step
    await bot.send_message(uid, text, reply_markup=kb)


async def admins_alert(text: str):
    for a in {MAIN_ADMIN, *EXTRA_ADMINS, *db_admins}:
        try:
            await bot.send_message(a, text)
        except Exception:
            pass


def anime_channels() -> list[str]:
    return [x.strip() for x in S("anime_kanal").split("\n") if x.strip()]


async def is_vip(uid: int) -> bool:
    return bool(await pool.fetchval("SELECT vip_until > now() FROM users WHERE user_id=$1", uid))


def protect() -> bool:
    return S("content") == "true"


def media_of(m: Message):
    if m.photo:
        return "photo", m.photo[-1].file_id
    if m.video:
        return ("video", m.video.file_id) if m.video.duration <= 60 else ("long", None)
    return None, None


# ----------------------------------------------------- MAJBURIY OBUNA ------
async def unsubscribed(uid: int):
    rows = await pool.fetch("SELECT * FROM channels ORDER BY id")
    btns = []
    for ch in rows:
        cid, ctype, link = ch["channel_id"], ch["channel_type"], ch["channel_link"]
        joined = False
        if ctype == "request":
            joined = bool(await pool.fetchval(
                "SELECT 1 FROM join_requests WHERE channel_id=$1 AND user_id=$2", cid, uid))
        try:
            m = await bot.get_chat_member(int(cid), uid)
            if m.status in ("member", "administrator", "creator", "restricted"):
                joined = True
        except Exception:
            if ctype != "request":
                continue  # bot tekshira olmasa, foydalanuvchini to'smaymiz
        if joined:
            continue
        if ctype == "request":
            btns.append(("📨 So'rov yuborish", link))
        else:
            if cid not in title_cache:
                try:
                    title_cache[cid] = (await bot.get_chat(int(cid))).title or "Kanal"
                except Exception:
                    title_cache[cid] = "Kanal"
            btns.append((title_cache[cid], link))
    return btns


async def ensure_sub(uid: int, payload: str = "") -> bool:
    if await is_vip(uid):
        return True
    btns = await unsubscribed(uid)
    if not btns:
        return True
    rows = [[b(t, url=u)] for t, u in btns]
    if S("instagram"):
        rows.append([b("📸 Instagram", url=S("instagram"))])
    if S("youtube"):
        rows.append([b("📺 YouTube", url=S("youtube"))])
    rows.append([b("✅ Tekshirish", f"chk:{payload}")])
    await bot.send_message(
        uid, "<b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling yoki so'rov yuboring❗️</b>",
        reply_markup=ikb(rows))
    return False


# --------------------------------------------------------- ANIME KARTA ------
async def send_card(chat_id: int, aid: int, uid: int, count: bool = True):
    r = await pool.fetchrow("SELECT * FROM animelar WHERE id=$1", aid)
    if not r:
        await bot.send_message(chat_id, "❌ Ma'lumot topilmadi.")
        return
    cs = r["qidiruv"]
    if count:
        cs = await pool.fetchval("UPDATE animelar SET qidiruv=qidiruv+1 WHERE id=$1 RETURNING qidiruv", aid)
    kanal = (anime_channels() or [""])[0]
    cap = (f"<b>🎬 Nomi: {esc(r['nom'])}</b>\n\n🎥 Qismi: {esc(r['qismi'])}\n🌍 Davlati: {esc(r['davlat'])}\n"
           f"🇺🇿 Tili: {esc(r['tili'])}\n📆 Yili: {esc(r['yili'])}\n🎞 Janri: {esc(r['janri'])}\n\n"
           f"🔍 Qidirishlar soni: {cs}\n\n🍿 {esc(kanal)}")
    rows = [[b("📥 YUKLAB OLISH", f"ep:{aid}:1")]]
    if is_admin(uid):
        rows.append([b("🗑 Animeni o'chirish", f"da:{aid}")])
    send = bot.send_video if r["rams_type"] == "video" else bot.send_photo
    await send(chat_id, r["rams"], caption=cap, reply_markup=ikb(rows), protect_content=protect())


async def show_episode(c: CallbackQuery, aid: int, qism: int):
    ep = await pool.fetchrow("SELECT * FROM anime_datas WHERE anime_id=$1 AND qism=$2", aid, qism)
    if not ep:
        ep = await pool.fetchrow("SELECT * FROM anime_datas WHERE anime_id=$1 ORDER BY qism LIMIT 1", aid)
        if not ep:
            await c.answer("💔 Qismlar topilmadi.", show_alert=True)
            return
        qism = ep["qism"]
    name = await pool.fetchval("SELECT nom FROM animelar WHERE id=$1", aid)
    page = (qism - 1) // 25
    eps = await pool.fetch(
        "SELECT qism FROM anime_datas WHERE anime_id=$1 AND qism BETWEEN $2 AND $3 ORDER BY qism",
        aid, page * 25 + 1, page * 25 + 25)
    btns = [b(f"[📀] - {e['qism']}", "null") if e["qism"] == qism else b(str(e["qism"]), f"ep:{aid}:{e['qism']}")
            for e in eps]
    rows = chunk(btns, 4)
    if is_admin(c.from_user.id):
        rows.append([b(f"🗑 {qism}-qismni o'chirish", f"de:{aid}:{qism}")])
    rows.append([b("⬅️ Oldingi", f"pg:{aid}:{page - 1}"), b("❌ Yopish", "close"),
                 b("➡️ Keyingi", f"pg:{aid}:{page + 1}")])
    await safe_delete(c.message)
    await bot.send_video(c.message.chat.id, ep["file_id"], caption=f"<b>{esc(name)}</b>\n\n{qism}-qism",
                         reply_markup=ikb(rows), protect_content=protect())


def anime_list_kb(rows):
    return ikb([[b(f"{i}. {r['nom']}", f"la:{r['id']}")] for i, r in enumerate(rows, 1)])


# ---------------------------------------------------------- ADMIN POST ------
async def send_post(chat_id, r):
    link = f"https://t.me/{BOT_USERNAME}?start={r['id']}"
    rows = [[b("🔹 Tomosha qilish 🔹", url=link)]]
    if WEBAPP_URL:
        rows.append([b("🌐 Web Animes", url=WEBAPP_URL)])
    cap = (f"<b>✽ ──...──:•°⛩°•:──...──╮\n🏷️ Anime nomi: </b>{esc(r['nom'])}\n"
           f"<b>🖋️ Janri:</b> {esc(r['janri'])}\n<b>🎞️ Qismlar soni:</b> {esc(r['qismi'])}\n"
           f"<b>🎙️ Ovoz berdi:</b> {esc(r['ani_type'])}\n<b>💭 Tili:</b> {esc(r['tili'])}")
    send = bot.send_video if r["rams_type"] == "video" else bot.send_photo
    await send(chat_id, r["rams"], caption=cap, reply_markup=ikb(rows), protect_content=protect())


async def user_card(uid_t: int):
    r = await pool.fetchrow("SELECT * FROM users WHERE user_id=$1", uid_t)
    if not r:
        return None, None
    vip = await is_vip(uid_t)
    text = (f"<b>Foydalanuvchi topildi!\n\nID:</b> <a href='tg://user?id={uid_t}'>{uid_t}</a>\n"
            f"<b>Balans: {r['pul']} {esc(S('valyuta'))}\nHolati: {'💎 VIP' if vip else 'Oddiy'}</b>")
    kb = ikb([
        [b("🔕 Bandan olish" if r["banned"] else "🔔 Banlash", f"ban:{uid_t}")],
        [b("❌ VIP dan olish" if vip else "💎 VIP ga qo'shish", f"vip:{uid_t}")],
        [b("➕ Pul qo'shish", f"plus:{uid_t}"), b("➖ Pul ayirish", f"minus:{uid_t}")]])
    return text, kb


async def broadcast(src_chat: int, mid: int, admin_id: int):
    ids = [r[0] for r in await pool.fetch("SELECT user_id FROM users")]
    ok = fail = 0
    for i, u in enumerate(ids):
        try:
            await bot.copy_message(u, src_chat, mid)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(u, src_chat, mid)
                ok += 1
            except Exception:
                fail += 1
        except Exception:
            fail += 1
        if (i + 1) % 25 == 0:
            await asyncio.sleep(1)
    await bot.send_message(admin_id, f"<b>✅ Xabar yuborish tugallandi!</b>\n\nYetkazildi: {ok}\nXato: {fail}")


# --------------------------------------------------------- MIDDLEWARE -------
class Guard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = event.from_user
        if isinstance(event, Message) and event.chat.type != "private":
            return
        if u is None or u.is_bot:
            return
        banned = await pool.fetchval(
            "INSERT INTO users(user_id, sana) VALUES($1,$2) "
            "ON CONFLICT(user_id) DO UPDATE SET user_id=EXCLUDED.user_id RETURNING banned",
            u.id, datetime.now(TZ).strftime("%d.%m.%Y"))
        if banned:
            return
        if S("holat") == "off" and not is_admin(u.id):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ Bot vaqtinchalik o'chirilgan!", show_alert=True)
            else:
                await event.answer("⛔️ <b>Bot vaqtinchalik o'chirilgan!</b>\n\n"
                                   "<i>Botda ta'mirlash ishlari olib borilayotgan bo'lishi mumkin!</i>")
            return
        return await handler(event, data)


router.message.outer_middleware(Guard())
router.callback_query.outer_middleware(Guard())


@router.chat_join_request()
async def on_join_request(e: ChatJoinRequest):
    await pool.execute("INSERT INTO join_requests(channel_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
                       str(e.chat.id), e.from_user.id)


# ------------------------------------------------------------ /start --------
def start_text(m: Message) -> str:
    now = datetime.now(TZ)
    t = S("start")
    for k, v in {"%first%": esc(m.from_user.first_name), "%id%": str(m.from_user.id), "%botname%": BOT_USERNAME,
                 "%hour%": now.strftime("%H:%M"), "%date%": now.strftime("%d.%m.%Y")}.items():
        t = t.replace(k, v)
    return t


@router.message(Command("start"))
async def cmd_start(m: Message, command: CommandObject):
    uid = m.from_user.id
    steps.pop(uid, None)
    arg = command.args
    if arg and arg.isdigit():
        if await ensure_sub(uid, arg):
            await send_card(m.chat.id, int(arg), uid)
        return
    await m.answer(start_text(m), reply_markup=main_menu(uid))


@router.message(Command("panel"))
async def cmd_panel(m: Message):
    if is_admin(m.from_user.id):
        steps.pop(m.from_user.id, None)
        await m.answer("<b>Admin paneliga xush kelibsiz!</b>", reply_markup=ADMIN_PANEL)


# --------------------------------------------------------- MESSAGES ---------
@router.message()
async def on_message(m: Message):
    uid = m.from_user.id
    text = m.text or ""
    admin = is_admin(uid)

    if text == "◀️ Orqaga":
        steps.pop(uid, None)
        await m.answer(start_text(m), reply_markup=main_menu(uid))
        return
    if text == "🗄 Boshqarish" and admin:
        steps.pop(uid, None)
        tmp.pop(uid, None)
        await m.answer("<b>Admin paneliga xush kelibsiz!</b>", reply_markup=ADMIN_PANEL)
        return

    step = steps.get(uid)
    if step and await handle_step(m, uid, step, text):
        return

    if admin and text in ADMIN_TEXTS:
        await admin_button(m, uid, text)
        return

    # --- foydalanuvchi tugmalari
    if text == S("key1"):
        if not await ensure_sub(uid):
            return
        rows = [
            [b("🏷Anime nomi orqali", "s:name"), b("⏱ So'ngi yuklanganlar", "s:last")],
            [b("💬Janr orqali qidirish", "s:genre")],
            [b("📌Kod orqali", "s:code"), b("👁️ Eng ko'p ko'rilgan", "s:top")],
        ]
        if WEBAPP_URL:
            rows.append([InlineKeyboardButton(text="🌐 Web Animes", web_app={"url": WEBAPP_URL})])
        rows.append([b("📚Barcha animelar", "s:all")])
        await m.answer("<b>🔍Qidiruv tipini tanlang :</b>", reply_markup=ikb(rows))
        return

    if text == S("key2"):
        if not await ensure_sub(uid):
            return
        await vip_menu(m, uid)
        return

    if text == S("key3"):
        if not await ensure_sub(uid):
            return
        pul = await pool.fetchval("SELECT pul FROM users WHERE user_id=$1", uid)
        await m.answer(f"#ID: <code>{uid}</code>\nBalans: {pul} {esc(S('valyuta'))}")
        return

    if text == S("key4"):
        if not await ensure_sub(uid):
            return
        pays = await pool.fetch("SELECT id,name FROM payments ORDER BY id")
        if not pays:
            await m.answer("😔 To'lov tizimlari topilmadi!")
            return
        kb = ikb(chunk([b(p["name"], f"pay:{p['id']}") for p in pays], 2))
        await m.answer("<b>💳 To'lov tizimlarni birini tanlang:</b>", reply_markup=kb)
        return

    if text == S("key5"):
        if not await ensure_sub(uid):
            return
        t = S("qollanma")
        await m.answer(t.replace("%user%", "").replace("%botname%", BOT_USERNAME).replace("%id%", str(uid))
                       if t else "<b>🙁 Qo'llanma qo'shilmagan!</b>")
        return

    if text == S("key6"):
        if not await ensure_sub(uid):
            return
        if S("homiy"):
            await m.answer(S("homiy"), reply_markup=ikb([[b("☎️ Administrator", url=f"tg://user?id={MAIN_ADMIN}")]]))
        else:
            await m.answer("<b>🙁 Homiylik qo'shilmagan!</b>")
        return

    # --- standart: nom bo'yicha qidirish
    if text and not text.startswith("/"):
        rows = await pool.fetch("SELECT id,nom FROM animelar WHERE nom ILIKE $1 ORDER BY id LIMIT 10", f"%{text}%")
        if rows:
            await m.reply("<b>⬇️ Qidiruv natijalari:</b>", reply_markup=anime_list_kb(rows))


async def vip_menu(m: Message, uid: int):
    key2 = S("key2")
    narx = int(S("vip") or 0)
    buy_rows = [[b(f"{d} kun - {narx * d // 30} {S('valyuta')}", f"shop:{d}")] for d in (30, 60, 90)]
    until = await pool.fetchval("SELECT vip_until FROM users WHERE user_id=$1", uid)
    if until and until > datetime.now(TZ):
        await m.answer(f"<b>Siz {esc(key2)} sotib olgansiz!</b>\n\n⏳ Amal qilish muddati "
                       f"{until.astimezone(TZ).strftime('%d.%m.%Y')} gacha",
                       reply_markup=ikb([[b("🗓️ Uzaytirish", "uz")]]))
        return
    await m.answer(f"<b>{esc(key2)}'ga ulanish\n\n{esc(key2)}da qanday imkoniyatlar bor?\n"
                   "• Hech qanday reklamalarsiz botdan foydalanasiz\n"
                   "• Majburiy obunalik so'ralmaydi\n• Janr, so'nggi va TOP qidiruvlar ochiladi</b>",
                   reply_markup=ikb(buy_rows))


# ------------------------------------------------------ ADMIN TUGMALAR ------
async def admin_button(m: Message, uid: int, text: str):
    if text == "📊 Statistika":
        total = await pool.fetchval("SELECT count(*) FROM users")
        vips = await pool.fetchval("SELECT count(*) FROM users WHERE vip_until > now()")
        animes = await pool.fetchval("SELECT count(*) FROM animelar")
        await m.answer(f"👥 <b>Foydalanuvchilar:</b> {total} ta\n💎 <b>VIP:</b> {vips} ta\n🎬 <b>Animelar:</b> {animes} ta")
    elif text == "✉ Xabar Yuborish":
        await ask(uid, "<b>📤 Foydalanuvchilarga yuboriladigan xabarni botga yuboring!</b>", "send")
    elif text == "📬 Post tayyorlash":
        await ask(uid, "<b>🆔 Anime kodini kiriting:</b>", "post")
    elif text == "🔎 Foydalanuvchini boshqarish":
        await ask(uid, "<b>Kerakli foydalanuvchining ID raqamini kiriting:</b>", "uid")
    elif text == "🎥 Animelar sozlash":
        await m.answer("<b>Quyidagilardan birini tanlang:</b>", reply_markup=ikb([
            [b("➕ Anime qo'shish", "an:add")], [b("📥 Qism qo'shish", "an:ep")],
            [b("📝 Anime tahrirlash", "an:edit")]]))
    elif text == "📢 Kanallar":
        await m.answer("<b>Quyidagilardan birini tanlang:</b>", reply_markup=ikb([
            [b("🔐 Majburiy obunalar", "chm")], [b("📌 Qo'shimcha kanalar", "qk")]]))
    elif text == "📋 Adminlar":
        rows = [[b("📑 Ro'yxat", "admlist")]]
        if uid == MAIN_ADMIN:
            rows = [[b("➕ Yangi admin qo'shish", "admadd")],
                    [b("📑 Ro'yxat", "admlist"), b("🗑 O'chirish", "admrem")]]
        await m.answer("<b>Quyidagilardan birini tanlang:</b>", reply_markup=ikb(rows))
    elif text == "🤖 Bot holati":
        on = S("holat") != "off"
        await m.answer(f"<b>Hozirgi holat:</b> {'Yoqilgan' if on else 'Oʻchirilgan'}",
                       reply_markup=ikb([[b("O'chirish" if on else "Yoqish", "bot")]]))
    elif text == "*️⃣ Birlamchi sozlamalar":
        await m.answer(*basic_settings())
    elif text == "📃 Matnlar":
        await m.answer("<b>Quyidagilardan birini tanlang:</b>", reply_markup=ikb([
            [b("Boshlang'ich matni", "txt:start")], [b("Qo'llanma", "txt:qollanma")],
            [b("🔖 Homiy matni", "txt:homiy")]]))
    elif text == "🎛 Tugmalar":
        await m.answer("<b>Quyidagilardan birini tanlang:</b>", reply_markup=ikb([
            [b("🖥 Asosiy menyudagi tugmalar", "keys")], [b("⚠️ O'z holiga qaytarish", "keyreset")]]))
    elif text == "💳 Hamyonlar":
        t, kb = await pays_menu()
        await m.answer(t, reply_markup=kb)


def basic_settings():
    t = (f"<b>Hozirgi birlamchi sozlamalar:</b>\n\n<i>1. Valyuta - {esc(S('valyuta'))}\n"
         f"2. VIP narxi - {esc(S('vip'))} {esc(S('valyuta'))}\n3. Studia nomi - {esc(S('studio_name'))}</i>")
    kb = ikb([[b("1", "set:valyuta"), b("2", "set:vip"), b("3", "set:studio_name")],
              [b("🔒 Kontent cheklash" if not protect() else "🔓 Kontent ulashish", "content")]])
    return t, kb


async def pays_menu():
    pays = await pool.fetch("SELECT id,name FROM payments ORDER BY id")
    rows = [[b(f"{p['name']} - ni o'chirish", f"paydel:{p['id']}")] for p in pays]
    rows.append([b("➕ Yangi to'lov tizimi qo'shish", "payadd")])
    return "<b>Quyidagilardan birini tanlang:</b>", ikb(rows)


# ------------------------------------------------------------ STEPS ---------
async def handle_step(m: Message, uid: int, step: str, text: str) -> bool:
    t = tmp.setdefault(uid, {})

    # ---- foydalanuvchi bosqichlari
    if step == "s_code":
        if text.isdigit():
            steps.pop(uid, None)
            if await ensure_sub(uid, text):
                await send_card(m.chat.id, int(text), uid)
        else:
            await m.answer("<b>📌 Faqat raqam (anime kodi) yuboring.</b>")
        return True
    if step == "s_genre":
        if not await is_vip(uid):
            steps.pop(uid, None)
            return False
        rows = await pool.fetch("SELECT id,nom FROM animelar WHERE janri ILIKE $1 LIMIT 10", f"%{text}%")
        if not rows:
            await m.answer(f"<b>[ {esc(text)} ] janriga tegishli anime topilmadi😔</b>\n\n• Boshqa janrni yuboring")
        else:
            steps.pop(uid, None)
            await m.reply("<b>⬇️ Qidiruv natijalari:</b>", reply_markup=anime_list_kb(rows))
        return True

    if not is_admin(uid):
        steps.pop(uid, None)
        return False

    # ---- admin bosqichlari
    if step == "send":
        steps.pop(uid, None)
        await m.answer("<b>✅ Xabar yuborish boshlandi!</b>", reply_markup=ADMIN_PANEL)
        task = asyncio.create_task(broadcast(m.chat.id, m.message_id, uid))
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)
        return True

    if step == "post":
        if not text.isdigit() or not await pool.fetchval("SELECT 1 FROM animelar WHERE id=$1", int(text)):
            await m.answer("<b>❌ Anime topilmadi, kodni qayta kiriting:</b>")
            return True
        chans = anime_channels()
        rows = [[b(f"📤 {ch} ga yuborish", f"snd:{i}:{text}")] for i, ch in enumerate(chans)]
        rows.append([b("📡 BARCHA kanallarga yuborish", f"snd:all:{text}")])
        await m.answer("📬 Qaysi kanalga yuborilsin?", reply_markup=ikb(rows))
        return True

    if step == "uid":
        if not text.isdigit():
            await m.answer("<b>Faqat raqam yuboring:</b>")
            return True
        txt, kb = await user_card(int(text))
        if not txt:
            await m.answer("<b>Foydalanuvchi topilmadi.</b>\n\nQayta urinib ko'ring:")
            return True
        steps.pop(uid, None)
        await m.answer(txt, reply_markup=kb)
        return True

    if step.startswith(("plus:", "minus:")):
        sign, target = step.split(":")
        try:
            amount = int(text)
        except ValueError:
            await m.answer("<b>Faqat raqamlardan foydalaning!</b>")
            return True
        delta = amount if sign == "plus" else -amount
        await pool.execute("UPDATE users SET pul = pul + $1 WHERE user_id=$2", delta, int(target))
        steps.pop(uid, None)
        v = esc(S("valyuta"))
        await m.answer(f"<b>Foydalanuvchi hisobiga {delta:+d} {v} o'zgartirildi!</b>", reply_markup=ADMIN_PANEL)
        try:
            await bot.send_message(int(target), f"<b>Adminlar tomonidan hisobingiz {delta:+d} {v}ga o'zgartirildi!</b>")
        except Exception:
            pass
        return True

    if step == "addch_id":
        raw = text.replace("-100", "")
        if not raw.isdigit():
            await m.answer("<b>Kanal IDsi faqat raqamlardan iborat bo'lishi kerak!</b>")
            return True
        t["ch_id"] = "-100" + raw
        steps[uid] = "addch_link"
        await m.answer("<b>🔗Kanal havolasini kiriting !</b>")
        return True
    if step == "addch_link":
        if not re.match(r"https://(t\.me|telegram\.me|telegram\.dog)/", text):
            await m.answer("<b>📍Faqat Telegram havolasi qabul qilinadi!</b>")
            return True
        t["ch_link"] = text
        steps.pop(uid, None)
        await m.answer("<b>⚠️Ushbu kanal zayafka kanal sifatida qo'shilsinmi?</b>", reply_markup=ikb([
            [b("✅Ha", "addch:request"), b("❌Yo'q", "addch:lock")], [b("🚫Bekor qilish", "cancel")]]))
        return True

    if step == "ak_add":
        if not text.startswith("@"):
            await m.answer("<b>❗️To'g'ri formatda yuboring.</b> Namuna: <code>@kanalim</code>")
            return True
        lst = anime_channels()
        if text in lst:
            await m.answer("<b>❗️Bu kanal allaqachon mavjud!</b>")
            return True
        lst.append(text)
        await set_setting("anime_kanal", "\n".join(lst))
        steps.pop(uid, None)
        await m.answer(f"✅ <b>Kanal qo'shildi:</b> <code>{esc(text)}</code>", reply_markup=ADMIN_PANEL)
        return True

    if step.startswith("social:"):
        net = step.split(":")[1]
        host = ("instagram.com",) if net == "insta" else ("youtube.com", "youtu.be")
        if not text.startswith("https://") or not any(h in text for h in host):
            await m.answer("<b>❌ To'g'ri havola yuboring!</b>\n\nMasalan: <code>https://www.instagram.com/nom</code>"
                           if net == "insta" else
                           "<b>❌ To'g'ri havola yuboring!</b>\n\nMasalan: <code>https://www.youtube.com/@nom</code>")
            return True
        await set_setting("instagram" if net == "insta" else "youtube", text)
        steps.pop(uid, None)
        await m.answer("✅ Havola saqlandi.", reply_markup=ADMIN_PANEL)
        return True

    if step in ("adm_add", "adm_rem") and uid == MAIN_ADMIN:
        if not text.isdigit():
            await m.answer("<b>Faqat raqam yuboring:</b>")
            return True
        target = int(text)
        if step == "adm_add":
            await pool.execute("INSERT INTO admins(user_id) VALUES($1) ON CONFLICT DO NOTHING", target)
            db_admins.add(target)
            await m.answer(f"<code>{target}</code> <b>adminlar ro'yxatiga qo'shildi!</b>", reply_markup=ADMIN_PANEL)
        else:
            await pool.execute("DELETE FROM admins WHERE user_id=$1", target)
            db_admins.discard(target)
            await m.answer(f"<code>{target}</code> <b>adminlar ro'yxatidan olib tashlandi!</b>", reply_markup=ADMIN_PANEL)
        steps.pop(uid, None)
        return True

    if step.startswith("set:"):
        key = step.split(":")[1]
        if not text:
            return True
        if key == "vip" and not text.isdigit():
            await m.answer("<b>Faqat raqam yuboring!</b>")
            return True
        await set_setting(key, text)
        steps.pop(uid, None)
        await m.answer("<b>✅ Saqlandi.</b>", reply_markup=ADMIN_PANEL)
        return True

    if step.startswith("key:"):
        key = "key" + step.split(":")[1]
        if text:
            await set_setting(key, text)
            steps.pop(uid, None)
            await m.answer(f"<b>Qabul qilindi!</b>\n\n<i>Tugma nomi</i> <b>{esc(text)}</b> <i>ga o'zgartirildi.</i>",
                           reply_markup=ADMIN_PANEL)
        return True

    if step == "pay_name":
        t["pay_name"] = text
        steps[uid] = "pay_wallet"
        await m.answer("<b>Ushbu to'lov tizimidagi hamyoningiz raqamini yuboring:</b>")
        return True
    if step == "pay_wallet":
        t["pay_wallet"] = text
        steps[uid] = "pay_add"
        await m.answer("<b>Ushbu to'lov tizimi orqali hisobni to'ldirish bo'yicha ma'lumotni yuboring:</b>")
        return True
    if step == "pay_add":
        await pool.execute("INSERT INTO payments(name,wallet,addition) VALUES($1,$2,$3)",
                           t.get("pay_name"), t.get("pay_wallet"), text)
        steps.pop(uid, None)
        await m.answer("<b>Yangi to'lov tizimi qo'shildi!</b>", reply_markup=ADMIN_PANEL)
        return True

    # ---- anime qo'shish
    if step.startswith("add:") and step != "add:pic":
        if not text:
            return True
        i = int(step.split(":")[1])
        if i == 0 and EMOJI_RE.search(text):
            await m.answer("<b>⚠️ Anime qo'shishda emoji va maxsus belgilardan foydalanish taqiqlangan!</b>\n\nQayta urining")
            return True
        t[ADD_FLOW[i][0]] = text
        if i + 1 < len(ADD_FLOW):
            steps[uid] = f"add:{i + 1}"
            await m.answer(f"<b>{ADD_FLOW[i + 1][1]}</b>" if "<i>" not in ADD_FLOW[i + 1][1] else ADD_FLOW[i + 1][1])
        else:
            steps[uid] = "add:pic"
            await m.answer("<b>🏞 Rasmini yoki 60 soniyadan oshmagan video yuboring:</b>")
        return True

    if step == "add:pic":
        kind, fid = media_of(m)
        if kind == "long":
            await m.answer("<b>⚠️ Video 60 soniyadan oshmasligi kerak!</b>")
            return True
        if not kind:
            await m.answer("<b>⚠️ Iltimos, rasm yoki 60 soniyadan oshmagan video yuboring!</b>")
            return True
        code = await pool.fetchval(
            "INSERT INTO animelar(nom,rams,rams_type,qismi,davlat,tili,yili,janri,ani_type) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id",
            t["nom"], fid, kind, t["qismi"], t["davlat"], t["tili"], t["yili"], t["janri"], t["ani_type"])
        steps.pop(uid, None)
        tmp.pop(uid, None)
        await m.answer(f"<b>✅ Anime qo'shildi!</b>\n\n<b>Anime kodi:</b> <code>{code}</code>", reply_markup=ADMIN_PANEL)
        return True

    # ---- qism qo'shish
    if step == "ep_code":
        if text.isdigit() and await pool.fetchval("SELECT 1 FROM animelar WHERE id=$1", int(text)):
            steps[uid] = f"ep_video:{text}"
            await m.answer("<b>🎥 Ushbu kodga tegishli anime qismini yuboring:</b>")
        else:
            await m.answer("<b>❗ Anime mavjud emas, qayta urinib ko'ring!</b>")
        return True
    if step.startswith("ep_video:"):
        aid = int(step.split(":")[1])
        if not m.video:
            await m.answer("<b>❗Faqat video yuboring.</b>")
            return True
        n = (await pool.fetchval("SELECT COALESCE(MAX(qism),0) FROM anime_datas WHERE anime_id=$1", aid)) + 1
        await pool.execute("INSERT INTO anime_datas(anime_id,file_id,qism) VALUES($1,$2,$3)", aid, m.video.file_id, n)
        await m.answer(f"<b>✅ {aid} raqamli animega {n}-qism yuklandi!</b>\n\n"
                       "<i>Yana yuklash uchun keyingi qismni yuborsangiz bo'ldi</i>")
        return True

    # ---- tahrirlash
    if step.startswith("ecode:"):
        kind = step.split(":")[1]
        if not (text.isdigit() and await pool.fetchval("SELECT 1 FROM animelar WHERE id=$1", int(text))):
            await m.answer("<b>❗ Anime mavjud emas, qayta urinib ko'ring!</b>")
            return True
        if kind == "animes":
            steps.pop(uid, None)
            rows = [[b(f"{lbl} tahrirlash", f"ef:{f}:{text}")] for f, lbl in EDIT_FIELDS.items()]
            rows.append([b("Anime rasmini tahrirlash", f"ef:image:{text}")])
            rows.append([b("Animeni o'chirish", f"da:{text}")])
            await m.answer("<b>❓ Nimani tahrirlamoqchisiz?</b>", reply_markup=ikb(rows))
        else:
            steps[uid] = f"eqism:{text}"
            await m.answer("<b>Qism raqamini yuboring:</b>")
        return True
    if step.startswith("eqism:"):
        aid = int(step.split(":")[1])
        if text.isdigit() and await pool.fetchval(
                "SELECT 1 FROM anime_datas WHERE anime_id=$1 AND qism=$2", aid, int(text)):
            steps.pop(uid, None)
            await m.answer("<b>❓ Nimani tahrirlamoqchisiz?</b>", reply_markup=ikb([
                [b("Qism raqamini tahrirlash", f"ee:qism:{aid}:{text}")],
                [b("Videoni tahrirlash", f"ee:file:{aid}:{text}")]]))
        else:
            await m.answer(f"<b>❗ Ushbu animeda bunday qism mavjud emas, qayta urinib ko'ring.</b>")
        return True
    if step.startswith("edit:"):
        _, field, aid = step.split(":")
        aid = int(aid)
        if field == "image":
            kind, fid = media_of(m)
            if kind == "long":
                await m.answer("<b>⚠️ Video 60 soniyadan oshmasligi kerak!</b>")
                return True
            if not kind:
                await m.answer("<b>⚠️ Iltimos, rasm yoki 60 soniyadan oshmagan video yuboring!</b>")
                return True
            await pool.execute("UPDATE animelar SET rams=$1, rams_type=$2 WHERE id=$3", fid, kind, aid)
        elif field in EDIT_FIELDS and text:
            if field in ("qismi", "yili") and not text.isdigit():
                await m.answer("<b>❗Faqat raqamlardan foydalaning.</b>")
                return True
            await pool.execute(f"UPDATE animelar SET {field}=$1 WHERE id=$2", text, aid)  # field - whitelist
        else:
            return True
        steps.pop(uid, None)
        await m.answer("<b>✅ Saqlandi.</b>", reply_markup=ADMIN_PANEL)
        return True
    if step.startswith("eedit:"):
        _, field, aid, qism = step.split(":")
        if field == "file":
            if not m.video:
                await m.answer("<b>❗Faqat videodan foydalaning.</b>")
                return True
            await pool.execute("UPDATE anime_datas SET file_id=$1 WHERE anime_id=$2 AND qism=$3",
                               m.video.file_id, int(aid), int(qism))
        else:
            if not text.isdigit():
                await m.answer("<b>❗Faqat raqamlardan foydalaning.</b>")
                return True
            try:
                await pool.execute("UPDATE anime_datas SET qism=$1 WHERE anime_id=$2 AND qism=$3",
                                   int(text), int(aid), int(qism))
            except asyncpg.UniqueViolationError:
                await m.answer("<b>❗Bu raqamli qism allaqachon mavjud.</b>")
                return True
        steps.pop(uid, None)
        await m.answer("<b>✅ Saqlandi.</b>", reply_markup=ADMIN_PANEL)
        return True

    return False


# --------------------------------------------------------- CALLBACKS --------
PUBLIC = {"chk", "s", "la", "ep", "pg", "close", "null", "uz", "shop", "pay", "payback"}


@router.callback_query()
async def on_callback(c: CallbackQuery):
    uid = c.from_user.id
    p = (c.data or "").split(":")
    act = p[0]
    msg = c.message
    admin = is_admin(uid)
    if act not in PUBLIC and not admin:
        await c.answer("⛔️ Ruxsat yo'q", show_alert=True)
        return
    try:
        if await route_callback(c, uid, act, p, msg):
            return
    except Exception:
        log.exception("callback xatosi: %s", c.data)
    try:
        await c.answer()
    except Exception:
        pass


async def route_callback(c: CallbackQuery, uid: int, act: str, p: list, msg: Message):
    v = esc(S("valyuta"))

    # ---------- umumiy
    if act == "null":
        return False
    if act == "close":
        await safe_delete(msg)
        return False

    if act == "chk":
        await safe_delete(msg)
        payload = p[1] if len(p) > 1 else ""
        if await ensure_sub(uid, payload):
            if payload.isdigit():
                await send_card(uid, int(payload), uid)
            else:
                await bot.send_message(uid, S("start"), reply_markup=main_menu(uid))
        return False

    if act == "s":
        kind = p[1]
        if kind == "name":
            await bot.send_message(uid, "<b>Anime nomini yuboring:</b>")
        elif kind in ("last", "top", "genre") and not await is_vip(uid):
            await c.answer(f"Ushbu funksiyadan foydalanish uchun {S('key2')} sotib olishingiz zarur!", show_alert=True)
            return True
        elif kind == "last":
            rows = await pool.fetch("SELECT id,nom FROM animelar ORDER BY sana DESC, id DESC LIMIT 10")
            await edit(msg, "<b>⬇️ Qidiruv natijalari:</b>", anime_list_kb(rows))
        elif kind == "top":
            rows = await pool.fetch("SELECT id,nom FROM animelar ORDER BY qidiruv DESC LIMIT 10")
            await edit(msg, "<b>⬇️ Qidiruv natijalari:</b>", anime_list_kb(rows))
        elif kind == "genre":
            await safe_delete(msg)
            steps[uid] = "s_genre"
            await bot.send_message(uid, "<b>🔍 Qidirish uchun anime janrini yuboring.</b>\n📌Namuna: Syonen")
        elif kind == "code":
            await safe_delete(msg)
            steps[uid] = "s_code"
            await bot.send_message(uid, "<b>📌 Anime kodini kiriting:</b>")
        elif kind == "all":
            rows = await pool.fetch("SELECT id,nom,janri FROM animelar ORDER BY id")
            txt = f"{BOT_USERNAME} anime botida mavjud barcha animelar ro'yxati\nBarcha animelar soni : {len(rows)} ta\n\n"
            for i, r in enumerate(rows, 1):
                txt += f"---- | {i} | ----\nAnime kodi : {r['id']}\nNomi : {r['nom']}\nJanri : {r['janri']}\n\n"
            await safe_delete(msg)
            await bot.send_document(uid, BufferedInputFile(txt.encode(), "animelar.txt"),
                                    caption=f"<b>📝{esc(BOT_USERNAME)} botida {len(rows)} ta anime mavjud</b>")
        return False

    if act == "la":
        await safe_delete(msg)
        await send_card(uid, int(p[1]), uid, count=False)
        return False

    if act == "ep":
        await show_episode(c, int(p[1]), int(p[2]))
        return False

    if act == "pg":
        aid, page = int(p[1]), int(p[2])
        if page < 0:
            await c.answer("💔 Qismlar topilmadi.", show_alert=True)
            return True
        first = await pool.fetchval(
            "SELECT qism FROM anime_datas WHERE anime_id=$1 AND qism BETWEEN $2 AND $3 ORDER BY qism LIMIT 1",
            aid, page * 25 + 1, page * 25 + 25)
        if not first:
            await c.answer("💔 Qismlar topilmadi.", show_alert=True)
            return True
        await show_episode(c, aid, first)
        return False

    if act == "uz":
        narx = int(S("vip") or 0)
        await edit(msg, "<b>❗ Obunani necha kunga uzaytirmoqchisiz?</b>", ikb(
            [[b(f"{d} kun - {narx * d // 30} {S('valyuta')}", f"shop:{d}")] for d in (30, 60, 90)]))
        return False

    if act == "shop":
        days = int(p[1])
        price = int(S("vip") or 0) * days // 30
        was_vip = await is_vip(uid)
        res = await pool.fetchval(
            "UPDATE users SET pul = pul - $1, "
            "vip_until = GREATEST(COALESCE(vip_until, now()), now()) + make_interval(days => $2) "
            "WHERE user_id=$3 AND pul >= $1 RETURNING 1", price, days, uid)
        if not res:
            await c.answer("Hisobingizda yetarli mablag' mavjud emas!", show_alert=True)
            return True
        await edit(msg, "<b>💎 VIP - statusni muvaffaqiyatli uzaytirdingiz.</b>" if was_vip
                   else "<b>💎 VIP - statusga muvaffaqiyatli o'tdingiz.</b>")
        if not was_vip:
            await admins_alert(f"<a href='tg://user?id={uid}'>Foydalanuvchi</a> {days} kunlik obuna sotib oldi!")
        return False

    if act == "pay":
        r = await pool.fetchrow("SELECT * FROM payments WHERE id=$1", int(p[1]))
        if r:
            await edit(msg, f"<b>💳 To'lov tizimi:</b> {esc(r['name'])}\n\n<b>Hamyon:</b> <code>{esc(r['wallet'])}</code>\n"
                            f"<b>Izoh:</b> <code>{uid}</code>\n\n{esc(r['addition'])}",
                       ikb([[b("☎️ Administator", url=f"tg://user?id={MAIN_ADMIN}")],
                            [b("◀️ Orqaga", "payback")]]))
        return False
    if act == "payback":
        pays = await pool.fetch("SELECT id,name FROM payments ORDER BY id")
        await edit(msg, "<b>💳 To'lov tizimlarni birini tanlang:</b>",
                   ikb(chunk([b(x["name"], f"pay:{x['id']}") for x in pays], 2)))
        return False

    # ---------- admin
    if act == "cancel":
        await safe_delete(msg)
        await bot.send_message(uid, "<b>✅Bekor qilindi !</b>", reply_markup=ADMIN_PANEL)
        return False

    if act == "an":
        await safe_delete(msg)
        if p[1] == "add":
            tmp[uid] = {}
            await ask(uid, f"<b>{ADD_FLOW[0][1]}</b>", "add:0")
        elif p[1] == "ep":
            await ask(uid, "<b>🔢 Anime kodini kiriting:</b>", "ep_code")
        else:
            await bot.send_message(uid, "<b>Tahrirlamoqchi bo'lgan bo'limni tanlang:</b>", reply_markup=ikb([
                [b("Anime ma'lumotlarini", "edt:animes")], [b("Anime qismini", "edt:eps")]]))
        return False
    if act == "edt":
        await safe_delete(msg)
        await ask(uid, "<b>Anime kodini kiriting:</b>", f"ecode:{p[1]}")
        return False
    if act == "ef":
        await safe_delete(msg)
        field, aid = p[1], p[2]
        if field == "image":
            await ask(uid, "<b>Yangi rasm yoki 60 soniyadan oshmagan videoni yuboring:</b>", f"edit:image:{aid}")
        elif field in EDIT_FIELDS:
            await ask(uid, "<b>Yangi qiymatini kiriting:</b>", f"edit:{field}:{aid}")
        return False
    if act == "ee":
        await safe_delete(msg)
        await ask(uid, "<b>Yangi qiymatini kiriting:</b>" if p[1] == "qism" else "<b>Yangi videoni yuboring:</b>",
                  f"eedit:{p[1]}:{p[2]}:{p[3]}")
        return False

    if act == "da":
        await edit(msg, "<b>❗Animeni va uning barcha qismlarini o'chirishga ishonchingiz komilmi?</b>", ikb([
            [b("✅ Tasdiqlash", f"dac:{p[1]}")], [b("❌ Bekor qilish", "close")]]))
        return False
    if act == "dac":
        await pool.execute("DELETE FROM anime_datas WHERE anime_id=$1", int(p[1]))
        await pool.execute("DELETE FROM animelar WHERE id=$1", int(p[1]))
        await safe_delete(msg)
        await bot.send_message(uid, "<b>✅ Anime va barcha qismlari o'chirildi!</b>")
        return False
    if act == "de":
        await edit(msg, f"<b>❗{p[2]}-qismni o'chirishga ishonchingiz komilmi?</b>", ikb([
            [b("✅ Tasdiqlash", f"dec:{p[1]}:{p[2]}")], [b("❌ Bekor qilish", "close")]]))
        return False
    if act == "dec":
        await pool.execute("DELETE FROM anime_datas WHERE anime_id=$1 AND qism=$2", int(p[1]), int(p[2]))
        await safe_delete(msg)
        await bot.send_message(uid, f"<b>✅ {p[2]}-qism o'chirildi!</b>")
        return False

    if act == "snd":
        r = await pool.fetchrow("SELECT * FROM animelar WHERE id=$1", int(p[2]))
        if not r:
            await c.answer("❌ Anime topilmadi!", show_alert=True)
            return True
        chans = anime_channels()
        targets = chans if p[1] == "all" else [chans[int(p[1])]] if int(p[1]) < len(chans) else []
        sent, failed = [], []
        for ch in targets:
            try:
                await send_post(ch, r)
                sent.append(ch)
            except Exception as e:
                failed.append(f"{ch} ({e})")
        await safe_delete(msg)
        txt = f"✅ Post yuborildi: {', '.join(sent) or '—'}"
        if failed:
            txt += "\n⚠️ Xato: " + ", ".join(failed)
        await bot.send_message(uid, txt, reply_markup=ADMIN_PANEL)
        await send_post(uid, r)
        return False

    # --- foydalanuvchini boshqarish
    if act in ("ban", "vip", "plus", "minus", "u"):
        target = int(p[1])
        if act == "ban":
            if target == MAIN_ADMIN or is_admin(target):
                await c.answer("Adminlarni bloklash mumkin emas!", show_alert=True)
                return True
            await pool.execute("UPDATE users SET banned = NOT banned WHERE user_id=$1", target)
        elif act == "vip":
            if await is_vip(target):
                await pool.execute("UPDATE users SET vip_until=NULL WHERE user_id=$1", target)
            else:
                await pool.execute("UPDATE users SET vip_until = now() + interval '30 days' WHERE user_id=$1", target)
        elif act in ("plus", "minus"):
            await safe_delete(msg)
            word = "qo'shmoqchisiz" if act == "plus" else "ayirmoqchisiz"
            await ask(uid, f"<a href='tg://user?id={target}'>{target}</a> <b>ning hisobiga qancha pul {word}?</b>",
                      f"{act}:{target}")
            return False
        txt, kb = await user_card(target)
        if txt:
            await edit(msg, txt, kb)
        return False

    # --- kanallar
    if act == "chm":
        await edit(msg, "<b>🔐Majburiy obunalarni sozlash bo'limidasiz:</b>", ikb([
            [b("➕ Qo'shish", "cha")], [b("📑 Ro'yxat", "chl"), b("🗑 O'chirish", "chd")],
            [b("🔙Ortga", "chback")]]))
        return False
    if act == "chback":
        await edit(msg, "<b>Quyidagilardan birini tanlang:</b>", ikb([
            [b("🔐 Majburiy obunalar", "chm")], [b("📌 Qo'shimcha kanalar", "qk")]]))
        return False
    if act == "cha":
        await safe_delete(msg)
        tmp[uid] = {}
        await ask(uid, "<b>💬Kanal IDsini yuboring !</b>", "addch_id")
        return False
    if act == "addch":
        t = tmp.get(uid, {})
        if "ch_id" not in t:
            await c.answer("Ma'lumot topilmadi, qaytadan boshlang.", show_alert=True)
            return True
        await pool.execute("INSERT INTO channels(channel_id,channel_type,channel_link) VALUES($1,$2,$3)",
                           t["ch_id"], p[1], t["ch_link"])
        tmp.pop(uid, None)
        await safe_delete(msg)
        await bot.send_message(uid, "<b>✅Majburiy obunaga kanal ulandi!</b>", reply_markup=ADMIN_PANEL)
        return False
    if act in ("chl", "chd", "chdel"):
        if act == "chdel":
            await pool.execute("DELETE FROM channels WHERE id=$1", int(p[1]))
            await c.answer("Kanal uzildi✔️")
        rows = await pool.fetch("SELECT * FROM channels ORDER BY id")
        if not rows:
            await c.answer("Hech qanday kanallar ulanmagan!", show_alert=True)
            return True
        txt = "<b>📢 Kanallar ro'yxati:</b>\n"
        for i, r in enumerate(rows, 1):
            txt += f"\n<b>{i}.</b> {esc(r['channel_link'])} | {r['channel_type']}"
        txt += f"\n\n<b>Ulangan kanallar soni:</b> {len(rows)} ta"
        kb_rows = []
        if act in ("chd", "chdel"):
            kb_rows = chunk([b(f"🗑️{i}", f"chdel:{r['id']}") for i, r in enumerate(rows, 1)], 5)
        kb_rows.append([b("🔙Ortga", "chm")])
        await edit(msg, txt, ikb(kb_rows))
        return True if act == "chdel" else False

    if act == "qk":
        await edit(msg, "<b>Qo'shimcha kanallar sozlash bo'limidasiz:</b>", ikb([
            [b("🎥 Anime kanal", "ak")], [b("🎁 Ijtimoiy tarmoqlar", "soc")], [b("◀️ Orqaga", "chback")]]))
        return False
    if act == "ak":
        await edit(msg, "📢 <b>Anime kanal ustida qanday amal bajaramiz?</b>", ikb([
            [b("➕ Qo'shish", "aka")], [b("📃 Ro'yhat", "akl"), b("🗑 O'chirish", "akd")]]))
        return False
    if act == "aka":
        await safe_delete(msg)
        await ask(uid, "📨 <b>Kanal usernamesini yuboring</b>\nNamuna: <code>@kanal_username</code>", "ak_add")
        return False
    if act == "akl":
        lst = anime_channels()
        await edit(msg, "📃 Ro'yxatda kanal yo'q." if not lst else
                   "📃 <b>Anime kanallar ro'yxati:</b>\n\n" + "\n".join(f"{i}. <code>{esc(x)}</code>" for i, x in enumerate(lst, 1)))
        return False
    if act == "akd":
        lst = anime_channels()
        if not lst:
            await edit(msg, "🗑 Ro'yxatda kanal yo'q.")
        else:
            await edit(msg, "🗑 <b>O'chirmoqchi bo'lgan kanalni tanlang:</b>",
                       ikb([[b(x, f"akdel:{i}")] for i, x in enumerate(lst)]))
        return False
    if act == "akdel":
        lst = anime_channels()
        i = int(p[1])
        if i < len(lst):
            removed = lst.pop(i)
            await set_setting("anime_kanal", "\n".join(lst))
            await edit(msg, f"✅ <b>{esc(removed)}</b> kanali o'chirildi.")
        return False

    if act == "soc":
        await edit(msg, "🌐 O'zingizga kerakli 🎁 ijtimoiy tarmoqni tanlang!", ikb([
            [b("📸 Instagram", "socm:insta")], [b("🎥 YouTube", "socm:yt")]]))
        return False
    if act == "socm":
        net = p[1]
        await edit(msg, "📸 Instagram ustida qanday amal bajaramiz? 👇" if net == "insta"
                   else "🎥 YouTube ustida qanday amal bajaramiz? 👇", ikb([
                       [b("➕ Qo'shish", f"socset:{net}"), b("🗑 O'chirish", f"socdel:{net}")],
                       [b("📃 Ko'rish", f"socshow:{net}")]]))
        return False
    if act in ("socset", "socdel", "socshow"):
        net = p[1]
        key = "instagram" if net == "insta" else "youtube"
        if act == "socset":
            await safe_delete(msg)
            await ask(uid, "🔗 <b>Havolani yuboring (https://...)</b>", f"social:{net}")
        elif act == "socdel":
            if S(key):
                await set_setting(key, "")
                await edit(msg, "✅ Havola o'chirildi! 🗑️")
            else:
                await edit(msg, "❌ Havola mavjud emas!")
        else:
            await edit(msg, f"🌟 <b>Havola:</b>\n\n{esc(S(key))}" if S(key) else "🌟 <b>Havola mavjud emas</b>")
        return False

    # --- adminlar
    if act == "admlist":
        allad = [MAIN_ADMIN, *sorted(EXTRA_ADMINS | db_admins)]
        await edit(msg, "<b>👮 Adminlar ro'yxati:</b>\n" + "\n".join(f"<code>{a}</code>" for a in allad))
        return False
    if act in ("admadd", "admrem") and uid == MAIN_ADMIN:
        await safe_delete(msg)
        await ask(uid, "<b>Kerakli foydalanuvchi ID raqamini yuboring:</b>", "adm_add" if act == "admadd" else "adm_rem")
        return False

    # --- bot holati / sozlamalar / matnlar / tugmalar / hamyon
    if act == "bot":
        await set_setting("holat", "off" if S("holat") != "off" else "on")
        await edit(msg, "<b>Muvaffaqiyatli o'zgartirildi!</b>")
        return False
    if act == "set" and p[1] in SET_KEYS:
        await safe_delete(msg)
        await ask(uid, f"📝 <b>Yangi {SET_KEYS[p[1]]} kiriting:</b>", f"set:{p[1]}")
        return False
    if act == "content":
        await set_setting("content", "false" if protect() else "true")
        await edit(msg, *basic_settings())
        return False
    if act == "txt" and p[1] in ("start", "qollanma", "homiy"):
        await safe_delete(msg)
        await ask(uid, f"📝 <b>Yangi matnni yuboring:</b>", f"set:{p[1]}")
        return False
    if act == "keys":
        rows = [[b(S("key1"), "keyedit:1")], [b(S("key2"), "keyedit:2"), b(S("key3"), "keyedit:3")],
                [b(S("key4"), "keyedit:4"), b(S("key5"), "keyedit:5")], [b(S("key6"), "keyedit:6")]]
        await edit(msg, "<b>Quyidagilardan birini tanlang:</b>", ikb(rows))
        return False
    if act == "keyedit":
        await safe_delete(msg)
        await ask(uid, "<b>Tugma uchun yangi nom yuboring:</b>", f"key:{p[1]}")
        return False
    if act == "keyreset":
        await edit(msg, "<b>Barcha tahrirlangan tugmalar birlamchi holatga qaytariladi.</b>\n\n<i>Rozimisiz?</i>",
                   ikb([[b("✅ Roziman", "keyresetok")]]))
        return False
    if act == "keyresetok":
        for i in range(1, 7):
            await set_setting(f"key{i}", DEFAULTS[f"key{i}"])
        await edit(msg, "<b>Tugma sozlamalari birlamchi holatga qaytarildi.</b>")
        return False
    if act == "payadd":
        await safe_delete(msg)
        tmp[uid] = {}
        await ask(uid, "<b>Yangi to'lov tizimi nomini yuboring:</b>", "pay_name")
        return False
    if act == "paydel":
        await pool.execute("DELETE FROM payments WHERE id=$1", int(p[1]))
        txt, kb = await pays_menu()
        await edit(msg, "<b>To'lov tizimi o'chirildi!</b>\n\n" + txt, kb)
        return False

    return False


# -------------------------------------------------------------- WEB ---------
async def health(_: web.Request):
    return web.Response(text="OK")


async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web server %s-portda ishga tushdi", PORT)


# -------------------------------------------------------------- MAIN --------
async def main():
    global pool, BOT_USERNAME
    await start_web()  # avval port ochiladi, Render tezda "live" deb topadi

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as con:
        await con.execute(SCHEMA)
    for r in await pool.fetch("SELECT key,value FROM settings"):
        settings[r["key"]] = r["value"]
    db_admins.update(r[0] for r in await pool.fetch("SELECT user_id FROM admins"))

    BOT_USERNAME = (await bot.get_me()).username
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("Bot @%s polling rejimida ishga tushdi", BOT_USERNAME)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
