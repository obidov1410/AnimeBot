import asyncio, logging, os, sqlite3
from contextlib import closing
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

logging.basicConfig(level=logging.INFO)
BOT_TOKEN=os.getenv("BOT_TOKEN")
OWNER_ID=int(os.getenv("OWNER_ID","0"))
DB_PATH=os.getenv("DB_PATH","anime_bot.db")
PORT=int(os.getenv("PORT","10000"))
if not BOT_TOKEN or not OWNER_ID:
    raise RuntimeError("BOT_TOKEN and OWNER_ID are required.")

bot=Bot(BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp=Dispatcher(storage=MemoryStorage()); router=Router(); dp.include_router(router)

def con(): return sqlite3.connect(DB_PATH)
def init_db():
    with closing(con()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT,joined_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS animes(code INTEGER PRIMARY KEY,name TEXT NOT NULL,channel_id TEXT NOT NULL,message_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS subscriptions(channel_id TEXT PRIMARY KEY,channel_name TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        """)
        c.execute("INSERT OR IGNORE INTO settings VALUES('enabled','1')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('start_text','👋 <b>Anime botga xush kelibsiz!</b>\n\n🔢 Anime kodini yuboring.')")
        c.commit()

def setting(k,d=""):
    with closing(con()) as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone()
        return r[0] if r else d
def set_setting(k,v):
    with closing(con()) as c:
        c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v)); c.commit()
def is_admin(uid):
    if uid==OWNER_ID:return True
    with closing(con()) as c:return c.execute("SELECT 1 FROM admins WHERE user_id=?",(uid,)).fetchone() is not None
def save_user(m):
    with closing(con()) as c:
        old=c.execute("SELECT 1 FROM users WHERE user_id=?",(m.from_user.id,)).fetchone()
        c.execute("INSERT OR IGNORE INTO users(user_id,username) VALUES(?,?)",(m.from_user.id,m.from_user.username)); c.commit()
    return old is None
def next_code():
    with closing(con()) as c:return c.execute("SELECT COALESCE(MAX(code),0)+1 FROM animes").fetchone()[0]
def get_anime(code):
    with closing(con()) as c:return c.execute("SELECT code,name,channel_id,message_id FROM animes WHERE code=?",(code,)).fetchone()

def user_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔢 Anime kodini kiritish")],
        [KeyboardButton(text="ℹ️ Bot haqida"),KeyboardButton(text="📢 Kanallar")]
    ],resize_keyboard=True)

def panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Anime qo‘shish",callback_data="add")],
        [InlineKeyboardButton(text="📝 Nomini o‘zgartirish",callback_data="rename"),
         InlineKeyboardButton(text="🗑 O‘chirish",callback_data="delete")],
        [InlineKeyboardButton(text="📋 Anime ro‘yxati",callback_data="list"),
         InlineKeyboardButton(text="📊 Statistika",callback_data="stats")],
        [InlineKeyboardButton(text="👑 Adminlar",callback_data="admins"),
         InlineKeyboardButton(text="📢 Majburiy obuna",callback_data="subs")],
        [InlineKeyboardButton(text="✏️ Start xabari",callback_data="start"),
         InlineKeyboardButton(text="⏸ Botni yoqish/o‘chirish",callback_data="toggle")]
    ])
def back(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Admin panel",callback_data="panel")]])

async def sub_ok(uid):
    with closing(con()) as c: rows=c.execute("SELECT channel_id FROM subscriptions").fetchall()
    for (cid,) in rows:
        try:
            m=await bot.get_chat_member(cid,uid)
            if m.status in ("left","kicked"): return False
        except: return False
    return True
async def sub_kb():
    with closing(con()) as c: rows=c.execute("SELECT channel_id,channel_name FROM subscriptions").fetchall()
    b=[]
    for cid,name in rows:
        try:
            chat=await bot.get_chat(cid)
            url=f"https://t.me/{chat.username}" if chat.username else None
            if url:b.append([InlineKeyboardButton(text=f"📢 {name or chat.title}",url=url)])
        except: pass
    b.append([InlineKeyboardButton(text="✅ Tekshirish",callback_data="check")])
    return InlineKeyboardMarkup(inline_keyboard=b)

class Add(StatesGroup): post=State(); name=State()
class Rename(StatesGroup): code=State(); name=State()
class Delete(StatesGroup): code=State()
class Admin(StatesGroup): uid=State()
class Sub(StatesGroup): channel=State()
class StartEdit(StatesGroup): text=State()

@router.message(CommandStart())
async def start(m:Message):
    new=save_user(m)
    if not await sub_ok(m.from_user.id) and not is_admin(m.from_user.id):
        await m.answer("📢 Avval majburiy kanallarga obuna bo‘ling.",reply_markup=await sub_kb()); return
    if setting("enabled","1")!="1" and not is_admin(m.from_user.id):
        await m.answer("⏸ Bot vaqtincha to‘xtatilgan."); return
    await m.answer(setting("start_text"),reply_markup=user_kb())
    if new:
        with closing(con()) as c: total=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        try: await bot.send_message(OWNER_ID,f"🆕 Yangi foydalanuvchi\n👤 @{m.from_user.username or 'yo‘q'}\n🔢 ID: <code>{m.from_user.id}</code>\n👥 Jami: {total}")
        except: pass

@router.callback_query(F.data=="check")
async def check(q:CallbackQuery):
    if await sub_ok(q.from_user.id): await q.message.answer("✅ Obuna tasdiqlandi!",reply_markup=user_kb())
    else: await q.answer("❌ Hali obuna bo‘lmagansiz.",show_alert=True)

@router.message(F.text=="🔢 Anime kodini kiritish")
async def code_help(m:Message): await m.answer("🔢 Anime kodini yuboring:")
@router.message(F.text=="ℹ️ Bot haqida")
async def about(m:Message): await m.answer("🎌 Anime Bot\n\nFaqat anime kodi orqali qidirish mumkin.",reply_markup=user_kb())
@router.message(F.text=="📢 Kanallar")
async def channels(m:Message): await m.answer("📢 Majburiy obuna:",reply_markup=await sub_kb())

@router.message(F.text.regexp(r"^\d+$"))
async def find(m:Message):
    if not await sub_ok(m.from_user.id) and not is_admin(m.from_user.id):
        await m.answer("📢 Avval obuna bo‘ling.",reply_markup=await sub_kb()); return
    a=get_anime(int(m.text))
    if not a: await m.answer("❌ Anime kodi topilmadi."); return
    code,name,cid,mid=a
    await m.answer(f"🎬 <b>{name}</b>\n🔢 Kod: <code>{code}</code>")
    try: await bot.copy_message(m.chat.id,cid,mid)
    except: await m.answer("❌ Kanal postini yuborib bo‘lmadi.")

@router.message(Command("admin"))
async def admin_cmd(m:Message):
    if is_admin(m.from_user.id): await m.answer("⚙️ <b>Admin panel</b>",reply_markup=panel_kb())

@router.callback_query(F.data=="panel")
async def panel(q:CallbackQuery):
    if is_admin(q.from_user.id): await q.message.edit_text("⚙️ <b>Admin panel</b>",reply_markup=panel_kb())

@router.callback_query(F.data=="add")
async def add_start(q:CallbackQuery,state:FSMContext):
    if not is_admin(q.from_user.id):return
    await state.set_state(Add.post); await q.message.answer("📨 Kanalga joylangan anime postini <b>forward</b> qilib yuboring.")
@router.message(Add.post)
async def add_post(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id) or not m.forward_origin:
        await m.answer("❌ Kanal postini forward qiling."); return
    o=m.forward_origin; chat=getattr(o,"chat",None); mid=getattr(o,"message_id",None)
    if not chat or not mid: await m.answer("❌ Kanal postini aniqlab bo‘lmadi."); return
    code=next_code(); await state.update_data(code=code,cid=str(chat.id),mid=mid)
    await state.set_state(Add.name); await m.answer(f"🔢 Avtomatik kod: <code>{code}</code>\n📝 Anime nomini yuboring:")
@router.message(Add.name)
async def add_name(m:Message,state:FSMContext):
    d=await state.get_data(); await state.update_data(name=m.text.strip())
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Tasdiqlash",callback_data="confirm_add"),InlineKeyboardButton(text="❌ Bekor",callback_data="cancel")]])
    await m.answer(f"📋 Kod: {d['code']}\n🎬 Nomi: {m.text}\n\nTasdiqlaysizmi?",reply_markup=kb)
@router.callback_query(F.data=="confirm_add")
async def confirm_add(q:CallbackQuery,state:FSMContext):
    d=await state.get_data()
    with closing(con()) as c:c.execute("INSERT INTO animes VALUES(?,?,?,?)",(d["code"],d["name"],d["cid"],d["mid"]));c.commit()
    await state.clear(); await q.message.edit_text("✅ Anime qo‘shildi.",reply_markup=back())
@router.callback_query(F.data=="cancel")
async def cancel(q:CallbackQuery,state:FSMContext):
    await state.clear(); await q.message.edit_text("❌ Bekor qilindi.",reply_markup=back())

@router.callback_query(F.data=="rename")
async def ren_start(q:CallbackQuery,state:FSMContext):
    if not is_admin(q.from_user.id):return
    await state.set_state(Rename.code); await q.message.answer("🔢 Anime kodini yuboring:")
@router.message(Rename.code)
async def ren_code(m:Message,state:FSMContext):
    if not m.text.isdigit() or not get_anime(int(m.text)): await m.answer("❌ Kod topilmadi."); return
    await state.update_data(code=int(m.text)); await state.set_state(Rename.name); await m.answer("📝 Yangi nom:")
@router.message(Rename.name)
async def ren_name(m:Message,state:FSMContext):
    d=await state.get_data(); await state.update_data(name=m.text.strip())
    await m.answer(f"🎬 Yangi nom: <b>{m.text}</b>\nTasdiqlash uchun /confirmname, bekor qilish uchun /cancel")
@router.message(Command("confirmname"),Rename.name)
async def ren_confirm(m:Message,state:FSMContext):
    d=await state.get_data()
    with closing(con()) as c:c.execute("UPDATE animes SET name=? WHERE code=?",(d["name"],d["code"]));c.commit()
    await state.clear(); await m.answer("✅ Nom o‘zgartirildi.")

@router.callback_query(F.data=="delete")
async def del_start(q:CallbackQuery,state:FSMContext):
    if not is_admin(q.from_user.id):return
    await state.set_state(Delete.code); await q.message.answer("🗑 O‘chiriladigan anime kodini yuboring:")
@router.message(Delete.code)
async def del_code(m:Message,state:FSMContext):
    if not m.text.isdigit() or not get_anime(int(m.text)): await m.answer("❌ Kod topilmadi."); return
    await state.update_data(code=int(m.text)); await m.answer("⚠️ Tasdiqlash uchun /confirmdelete, bekor qilish uchun /cancel")
@router.message(Command("confirmdelete"),Delete.code)
async def del_confirm(m:Message,state:FSMContext):
    d=await state.get_data()
    with closing(con()) as c:c.execute("DELETE FROM animes WHERE code=?",(d["code"],));c.commit()
    await state.clear(); await m.answer("✅ Anime o‘chirildi.")

@router.callback_query(F.data=="list")
async def listing(q:CallbackQuery):
    if not is_admin(q.from_user.id):return
    with closing(con()) as c:r=c.execute("SELECT code,name FROM animes ORDER BY code DESC LIMIT 50").fetchall()
    text="📋 <b>Animelar</b>\n\n"+("\n".join(f"<code>{x}</code> — {n}" for x,n in r) or "Hozircha yo‘q.")
    await q.message.edit_text(text,reply_markup=back())

@router.callback_query(F.data=="stats")
async def stats(q:CallbackQuery):
    if not is_admin(q.from_user.id):return
    with closing(con()) as c:
        u=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; a=c.execute("SELECT COUNT(*) FROM animes").fetchone()[0]; ad=c.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    await q.message.edit_text(f"📊 <b>Statistika</b>\n\n👥 {u} foydalanuvchi\n🎬 {a} anime\n👑 {ad} admin",reply_markup=back())

@router.callback_query(F.data=="admins")
async def admins(q:CallbackQuery):
    if q.from_user.id!=OWNER_ID:return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Admin qo‘shish",callback_data="addadmin")],[InlineKeyboardButton(text="➖ Admin o‘chirish",callback_data="deladmin")],[InlineKeyboardButton(text="⬅️ Orqaga",callback_data="panel")]])
    await q.message.edit_text("👑 <b>Adminlar</b>",reply_markup=kb)
@router.callback_query(F.data=="addadmin")
async def addadmin(q:CallbackQuery,state:FSMContext):
    if q.from_user.id!=OWNER_ID:return
    await state.set_state(Admin.uid); await q.message.answer("Telegram ID yuboring:")
@router.message(Admin.uid)
async def addadmin_save(m:Message,state:FSMContext):
    if m.from_user.id!=OWNER_ID or not m.text.isdigit():return
    with closing(con()) as c:c.execute("INSERT OR IGNORE INTO admins VALUES(?)",(int(m.text),));c.commit()
    await state.clear(); await m.answer("✅ Admin qo‘shildi.")
@router.callback_query(F.data=="deladmin")
async def deladmin(q:CallbackQuery,state:FSMContext):
    if q.from_user.id!=OWNER_ID:return
    await state.set_state(Admin.uid); await q.message.answer("O‘chiriladigan admin ID:")
@router.message(Admin.uid)
async def deladmin_save(m:Message,state:FSMContext):
    if m.from_user.id!=OWNER_ID:return
    if m.text.isdigit():
        with closing(con()) as c:c.execute("DELETE FROM admins WHERE user_id=?",(int(m.text),));c.commit()
    await state.clear(); await m.answer("✅ Bajarildi.")

@router.callback_query(F.data=="subs")
async def subs(q:CallbackQuery):
    if not is_admin(q.from_user.id):return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Kanal qo‘shish",callback_data="addsub")],[InlineKeyboardButton(text="➖ Kanal o‘chirish",callback_data="delsub")],[InlineKeyboardButton(text="⬅️ Orqaga",callback_data="panel")]])
    await q.message.edit_text("📢 <b>Majburiy obuna</b>",reply_markup=kb)
@router.callback_query(F.data=="addsub")
async def addsub(q:CallbackQuery,state:FSMContext):
    await state.set_state(Sub.channel); await q.message.answer("Kanal @username yoki -100... ID'sini yuboring:")
@router.message(Sub.channel)
async def addsub_save(m:Message,state:FSMContext):
    try:
        chat=await bot.get_chat(m.text.strip())
        with closing(con()) as c:c.execute("INSERT OR REPLACE INTO subscriptions VALUES(?,?)",(str(chat.id),chat.title));c.commit()
        await m.answer(f"✅ {chat.title} qo‘shildi.")
    except: await m.answer("❌ Kanal topilmadi. Bot kanalga admin bo‘lishi kerak.")
    await state.clear()
@router.callback_query(F.data=="delsub")
async def delsub(q:CallbackQuery,state:FSMContext):
    await state.set_state(Sub.channel); await q.message.answer("Kanal ID'sini yuboring:")
@router.message(Sub.channel)
async def delsub_save(m:Message,state:FSMContext):
    with closing(con()) as c:c.execute("DELETE FROM subscriptions WHERE channel_id=?",(m.text.strip(),));c.commit()
    await state.clear(); await m.answer("✅ Kanal o‘chirildi.")

@router.callback_query(F.data=="start")
async def edit_start(q:CallbackQuery,state:FSMContext):
    if not is_admin(q.from_user.id):return
    await state.set_state(StartEdit.text); await q.message.answer("✏️ Yangi start xabarini yuboring:")
@router.message(StartEdit.text)
async def save_start(m:Message,state:FSMContext):
    set_setting("start_text",m.text); await state.clear(); await m.answer("✅ Start xabari yangilandi.")

@router.callback_query(F.data=="toggle")
async def toggle(q:CallbackQuery):
    if q.from_user.id!=OWNER_ID:return
    v="0" if setting("enabled","1")=="1" else "1"; set_setting("enabled",v)
    await q.message.edit_text(("▶️ Bot yoqildi." if v=="1" else "⏸ Bot to‘xtatildi."),reply_markup=back())

async def root(_): return web.Response(text="Anime bot is running.")
async def health(_): return web.json_response({"status":"ok"})
async def webserver():
    app=web.Application(); app.router.add_get("/",root); app.router.add_get("/health",health)
    runner=web.AppRunner(app); await runner.setup(); await web.TCPSite(runner,"0.0.0.0",PORT).start(); return runner

async def main():
    init_db(); runner=await webserver()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup(); await bot.session.close()

if __name__=="__main__": asyncio.run(main())
