import asyncio
import json
import logging
import os
from pathlib import Path
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ================== SOZLAMALAR ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else 0
DATA_FILE = Path("data.json")
# Anime postlari saqlanadigan kanal ID sini STORAGE_CHANNEL_ID environment variable orqali berish mumkin.
CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID", "").strip()
# =================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def default_data():
    return {
        "admins": [OWNER_ID],
        "start_text": "🏴‍☠️ <b>ANIME OLAMIGA XUSH KELIBSIZ!</b>\n\n🍿 Sevimli animelaringizni biz bilan tomosha qiling!\n\n🔎 Anime topish uchun quyidagi menyudan foydalaning.\n✨ <b>ANIME UZ</b>",
        "channels": [],
        "animes": {}
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
    return data


def save_data(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_admin(user_id: int) -> bool:
    return user_id in load_data()["admins"]


async def send_start(message: Message):
    data = load_data()
    await message.answer(data["start_text"], reply_markup=main_menu())


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Anime izlash", callback_data="search_menu")],
        [InlineKeyboardButton(text="📖 Qo'llanma", callback_data="guide")],
    ])


def search_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Kod bo‘yicha qidirish", callback_data="search"), InlineKeyboardButton(text="🔠 Nom bo‘yicha qidirish", callback_data="search_name")],
        [InlineKeyboardButton(text="📚 Animelar ro‘yxati", callback_data="list")],
        [InlineKeyboardButton(text="🛑 Orqaga", callback_data="home")],
    ])


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Anime qo'shish", callback_data="add_anime")],
        [
            InlineKeyboardButton(text="➕ Qism qo'shish", callback_data="add_episode"),
            InlineKeyboardButton(text="🗑 Qism o'chirish", callback_data="del_episode"),
        ],
        [
            InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data="rename_anime"),
            InlineKeyboardButton(text="🔢 Kodini o'zgartirish", callback_data="recode_anime"),
        ],
        [InlineKeyboardButton(text="🗑 Anime o'chirish", callback_data="del_anime")],
        [
            InlineKeyboardButton(text="👑 Admin qo'shish", callback_data="add_admin"),
            InlineKeyboardButton(text="🚫 Admin o'chirish", callback_data="del_admin"),
        ],
        [
            InlineKeyboardButton(text="📢 Kanal qo'shish", callback_data="add_channel"),
            InlineKeyboardButton(text="➖ Kanal o'chirish", callback_data="del_channel"),
        ],
        [InlineKeyboardButton(text="📝 /start xabarini o'zgartirish", callback_data="start_text")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
    ])


def episode_keyboard(code: str, episodes: dict):
    """Qism raqamlarini ixcham 5 ustunli jadvalda chiqaradi."""
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
    rows.append([InlineKeyboardButton(text="🛑 Orqaga", callback_data="search_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def anime_caption(code: str, anime: dict) -> str:
    description = (anime.get("description") or "").strip()
    base = f"🎬 <b>{anime['name']}</b>\n\n🔢 Anime kodi: <code>{code}</code>\n🎞 Qismlar: {len(anime.get('episodes', {}))}"
    if description:
        base += "\n\n" + description
    return base[:1024]


async def send_anime_card(chat_id: int, code: str, anime: dict):
    caption = anime_caption(code, anime)
    markup = episode_keyboard(code, anime.get("episodes", {}))
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
    if await check_subscription(message.from_user.id):
        return True
    await message.answer(
        "🔒 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling.</b>",
        reply_markup=subscription_keyboard()
    )
    return False


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    if not await require_subscription_message(message):
        return
    await send_start(message)


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Sizda admin huquqi yo'q.")
    await message.answer("👑 <b>Admin paneli</b>", reply_markup=admin_menu())


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
        await call.message.edit_text("🔎 <b>Anime izlash</b>\n\nKerakli usulni tanlang:", reply_markup=search_menu())
    except Exception:
        await call.message.answer("🔎 <b>Anime izlash</b>\n\nKerakli usulni tanlang:", reply_markup=search_menu())
    await call.answer()


@dp.callback_query(F.data == "guide")
async def guide(call: CallbackQuery):
    text = "📖 <b>Qo'llanma</b>\n\n1️⃣ Anime izlashni bosing.\n2️⃣ 🔢 Kod bo‘yicha qidirish, 🔠 Nom bo‘yicha qidirish yoki 📚 Animelar ro‘yxatini tanlang.\n3️⃣ Anime sahifasidan kerakli qism raqamini bosing.\n\n🛑 Orqaga tugmasi oldingi menyuga qaytaradi."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛑 Orqaga", callback_data="home")]])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


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
    matches = [(code, anime) for code, anime in data["animes"].items() if query in anime["name"].casefold()]
    if not matches:
        return await message.answer("❌ Bu nom bo'yicha anime topilmadi.", reply_markup=search_menu())
    rows = [[InlineKeyboardButton(text=f"🎬 {anime['name']} · {code}", callback_data=f"show:{code}")] for code, anime in matches[:80]]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
    await message.answer("🔎 <b>Topilgan animelar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


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
        return await message.answer("❌ Bu kod bo'yicha anime topilmadi.")
    await send_anime_card(message.chat.id, code, anime)


@dp.callback_query(F.data.startswith("episode:"))
async def send_episode(call: CallbackQuery):
    if not await check_subscription(call.from_user.id):
        await call.message.answer(
            "🔒 Avval kanallarga obuna bo'ling.", reply_markup=subscription_keyboard()
        )
        return await call.answer()
    try:
        _, code, number = call.data.split(":", 2)
        episode = load_data()["animes"][code]["episodes"][number]
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
    if not data["animes"]:
        await call.message.answer("📭 Hozircha anime qo'shilmagan.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")]]))
    else:
        rows = [[InlineKeyboardButton(text=f"🎬 {a['name']} · {code}", callback_data=f"show:{code}")]
                for code, a in list(data["animes"].items())[:80]]
        rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="search_menu")])
        await call.message.answer("📚 <b>Barcha animelar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data.startswith("show:"))
async def show_anime(call: CallbackQuery):
    code = call.data.split(":", 1)[1]
    data = load_data()
    if code not in data["animes"]:
        return await call.answer("Anime topilmadi", show_alert=True)
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
    await message.answer("🎬 Anime nomini yuboring:")


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
    data["animes"][code] = {"name": name, "description": description, "poster_file_id": poster, "episodes": {}}
    save_data(data)
    await state.update_data(batch_code=code, batch_count=0)
    await state.set_state(Form.episode_batch)
    prompt = await message.answer(
        f"✅ <b>{name}</b> (kod <code>{code}</code>) yaratildi.\n\n"
        "Endi barcha qismlarni kanal postlaridan <b>1, 2, 3...</b> tartibida ketma-ket forward qiling. "
        "Har bir forward avtomatik keyingi qism raqamiga biriktiriladi.\n\n"
        "Tugatgach /done yuboring. Bekor qilish: /cancel"
    )
    await state.update_data(progress_message_id=prompt.message_id)

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
    await ask(call, state, Form.admin_id_add, "Admin qilinadigan foydalanuvchining Telegram ID raqamini yuboring:")


@dp.message(Form.admin_id_add)
async def add_admin_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    await ask(call, state, Form.admin_id_del, "Adminlikdan olinadigan Telegram ID raqamini yuboring:")


@dp.message(Form.admin_id_del)
async def del_admin_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    await ask(call, state, Form.channel_add,
        "Kanalni yuboring. Ochiq kanal: @username\n"
        "Maxfiy kanal: -100...ID | https://t.me/+taklif_havolasi\n"
        "Masalan: -1001234567890 | https://t.me/+AbCdEfGh\n"
        "Bot kanal ichida administrator bo'lishi kerak. Maxfiy kanal uchun ID va taklif havolasi ikkalasi ham kerak:")


@dp.message(Form.channel_add)
async def add_channel_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    await ask(call, state, Form.channel_del, "O'chiriladigan kanal @username yoki ID'sini yuboring:")


@dp.message(Form.channel_del)
async def del_channel_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    await ask(call, state, Form.start_text, "Yangi /start xabarini yuboring. HTML teglardan foydalanishingiz mumkin:")


@dp.message(Form.start_text)
async def start_text_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    if await check_subscription(call.from_user.id):
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


@dp.message()
async def fallback(message: Message):
    if message.text and message.text.isdigit():
        if not await require_subscription_message(message):
            return
        data = load_data()
        code = message.text.strip()
        if code in data["animes"]:
            anime = data["animes"][code]
            await send_anime_card(message.chat.id, code, anime)
        else:
            await message.answer("❌ Kod topilmadi. Qayta tekshiring.")
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


async def main():
    if not BOT_TOKEN or not OWNER_ID:
        raise RuntimeError("Render Environment Variables bo'limida BOT_TOKEN va OWNER_ID ni kiriting.")
    data = load_data()
    if OWNER_ID not in data.get("admins", []):
        data.setdefault("admins", []).append(OWNER_ID)
        save_data(data)

    runner = await start_web_server()
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
