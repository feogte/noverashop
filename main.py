import asyncio
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

from pyrogram import Client, filters
from pyrogram.errors import AuthKeyUnregistered, UserDeactivated, SessionPasswordNeeded

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("8887215013:AAHnKafRzAr6SjWyJL-9EhYNg6rHKN_M2t4")
API_ID = int(os.getenv("API_ID", "31799721"))
API_HASH = os.getenv("API_HASH", "eb2181220b3b8a0b6a7f93cd8075a559")

if not TOKEN:
    raise RuntimeError("Telegram bot token was not provided")

ADMIN_ID = int(os.getenv("ADMIN_ID", "8872934046"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003922108499"))

DB_PATH = os.getenv("DB_PATH", "data/shop.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path("sessions").mkdir(parents=True, exist_ok=True)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
MSK = ZoneInfo("Europe/Moscow")

def is_admin(uid): return uid == ADMIN_ID
def now_msk(): return datetime.now(MSK)

# --- ГЕНЕРАТОР ФЛАГОВ И ПРОВЕРКА НА ИМЕЮЩИЙСЯ ЭМОДЗИ ---
PHONE_FLAGS = {
    "1": "🇺🇸", "7": "🇷🇺", "380": "🇺🇦", "375": "🇧🇾", "77": "🇰🇿", "76": "🇰🇿",
    "998": "🇺🇿", "992": "🇹🇯", "996": "🇰🇬", "994": "🇦🇿", "374": "🇦🇲", "995": "🇬🇪",
    "373": "🇲🇩", "371": "🇱🇻", "370": "🇱🇹", "372": "🇪🇪", "44": "🇬🇧", "49": "🇩🇪",
    "33": "🇫🇷", "34": "🇪🇸", "39": "🇮🇹", "48": "🇵🇱", "90": "🇹🇷", "62": "🇮🇩",
    "84": "🇻🇳", "66": "🇹🇭", "63": "🇵🇭", "91": "🇮🇳", "86": "🇨🇳", "81": "🇯🇵",
    "82": "🇰🇷", "55": "🇧🇷", "52": "🇲🇽", "20": "🇪🇬", "27": "🇿🇦", "234": "🇳🇬"
}

FLAG_REGEX = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")

def get_flag(text: str) -> str:
    if not text:
        return "📱 "
    if FLAG_REGEX.search(text):
        return ""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return "📱 "
    for prefix_len in (3, 2, 1):
        prefix = digits[:prefix_len]
        if prefix in PHONE_FLAGS:
            return f"{PHONE_FLAGS[prefix]} "
    return "🌐 "

def extract_code_prefix(phone_or_name: str) -> str:
    digits = re.sub(r"\D", "", phone_or_name or "")
    if not digits:
        return phone_or_name or "+1"
    for length in (3, 2, 1):
        pref = digits[:length]
        if pref in PHONE_FLAGS:
            return f"+{pref}"
    return f"+{digits[0]}" if digits else "+1"

async def check_sub(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("creator", "administrator", "member")
    except Exception:
        return False

async def db():
    conn = await aiosqlite.connect(DB_PATH); conn.row_factory = aiosqlite.Row; return conn

async def one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    try: return await cur.fetchone()
    finally: await cur.close()

async def all_rows(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    try: return await cur.fetchall()
    finally: await cur.close()

async def init_db():
    conn = await db()
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, price_rub REAL NOT NULL, price_stars INTEGER NOT NULL, price_kzt REAL NOT NULL DEFAULT 0, stock INTEGER NOT NULL DEFAULT 0, sales INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, file_id TEXT, phone TEXT, session_path TEXT);
    CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, product_name TEXT NOT NULL, payment TEXT NOT NULL, amount REAL NOT NULL, screenshot_file_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
    """)
    try:
        await conn.execute("ALTER TABLE products ADD COLUMN price_kzt REAL NOT NULL DEFAULT 0")
    except Exception:
        pass
    await conn.commit(); await conn.close()
def home_kb(uid):
    kb = ReplyKeyboardBuilder()
    kb.button(text="Каталог")
    kb.button(text="Отзывы")
    kb.button(text="Поддержка")
    kb.button(text="🎁 Рефералка")
    if is_admin(uid): 
        kb.button(text="Админ панель")
    kb.adjust(1, 2, 1, 1)
    return kb.as_markup(resize_keyboard=True)

def admin_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="Статистика")
    kb.button(text="Склад")
    kb.button(text="🎁 Подарить аккаунт")
    kb.button(text="Рассылка")
    kb.button(text="Назад")
    kb.adjust(2, 1, 1, 1)
    return kb.as_markup(resize_keyboard=True)

def catalog_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="Аккаунты")
    kb.button(text="Звезды")
    kb.button(text="Назад")
    kb.adjust(2, 1)
    return kb.as_markup(resize_keyboard=True)

class AddProduct(StatesGroup):
    kind = State()
    name = State()
    price_rub = State()
    price_stars = State()
    price_kzt = State()
    account_file = State()
    phone = State()

class BulkAddProduct(StatesGroup):
    uploading = State()
    pricing = State()

class GiftAccount(StatesGroup):
    user_id = State()
    session_file = State()
    phone = State()
    confirm = State()

class EditProduct(StatesGroup):
    price_rub = State()
    price_stars = State()
    price_kzt = State()
    stock = State()

class Broadcast(StatesGroup):
    text = State()

class PaymentState(StatesGroup):
    waiting_for_proof = State()

async def send_sub_request(message_or_call):
    chat_info = await bot.get_chat(CHANNEL_ID)
    invite_link = chat_info.invite_link or (f"https://t.me/{chat_info.username}" if chat_info.username else None)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=invite_link or "https://t.me/")],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")]
    ])
    
    text_msg = "⚠️ <b>Для использования бота необходимо быть подписанным на наш канал!</b>\n\nПодпишитесь и нажмите кнопку «Я подписался»."
    
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.answer(text_msg, reply_markup=kb)
    else:
        await message_or_call.answer(text_msg, reply_markup=kb)

@dp.callback_query(F.data == "check_subscription")
async def recheck_sub(call: CallbackQuery, state: FSMContext):
    if await check_sub(call.from_user.id):
        conn = await db()
        await conn.execute(
            "INSERT OR IGNORE INTO users VALUES(?,?,?,?)",
            (call.from_user.id, call.from_user.username, call.from_user.first_name, now_msk().isoformat())
        )
        await conn.commit()
        await conn.close()

        await call.answer("Спасибо за подписку! 🎉", show_alert=True)
        try: await call.message.delete()
        except Exception: pass
        name = call.from_user.username or call.from_user.first_name or "пользователь"
        await call.message.answer(f"Привет, {name}!\nТут ты можешь приобрести все что душе угодно.", reply_markup=home_kb(call.from_user.id))
    else:
        await call.answer("Вы всё еще не подписаны на канал! ❌", show_alert=True)

@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    
    conn = await db()
    await conn.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?)", (m.from_user.id, m.from_user.username, m.from_user.first_name, now_msk().isoformat()))
    await conn.commit(); await conn.close()

    if not await check_sub(m.from_user.id):
        return await send_sub_request(m)

    name = m.from_user.username or m.from_user.first_name or "пользователь"
    await m.answer(f"Привет, {name}!\nТут ты можешь приобрести все что душе угодно.", reply_markup=home_kb(m.from_user.id))

@dp.message(F.text == "Назад")
async def back_home(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Главное меню", reply_markup=home_kb(m.from_user.id))

@dp.message(F.text == "Каталог")
async def open_catalog(m: Message):
    if not await check_sub(m.from_user.id): return await send_sub_request(m)
    await m.answer("Выберите тип товара:", reply_markup=catalog_kb())

@dp.message(F.text == "Отзывы")
async def open_reviews(m: Message):
    if not await check_sub(m.from_user.id): return await send_sub_request(m)
    await m.answer("Отзывы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть отзывы", url="https://t.me/repacrisov")]]))

@dp.message(F.text == "Поддержка")
async def open_support(m: Message):
    if not await check_sub(m.from_user.id): return await send_sub_request(m)
    await m.answer("Поддержка:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать в поддержку", url="https://t.me/fegote")]]))

@dp.message(F.text == "🎁 Рефералка")
async def open_ref(m: Message):
    if not await check_sub(m.from_user.id): return await send_sub_request(m)
    await m.answer("Раздел на тех работах\n\nНовости — @noverashop", reply_markup=home_kb(m.from_user.id))

@dp.message(F.text == "Аккаунты")
async def show_accounts(m: Message):
    if not await check_sub(m.from_user.id): return await send_sub_request(m)
    await products(m, "account")

@dp.message(F.text == "Звезды")
async def show_stars(m: Message):
    if not await check_sub(m.from_user.id): return await send_sub_request(m)
    await products(m, "stars")

async def products(m: Message, kind: str):
    conn = await db()
    rows = await all_rows(conn, "SELECT * FROM products WHERE kind=? AND stock>0 ORDER BY id", (kind,))
    await conn.close()
    if not rows: 
        return await m.answer("Товаров в наличии нет.", reply_markup=catalog_kb())
    
    kb = InlineKeyboardBuilder()
    
    if kind == "account":
        grouped = {}
        for p in rows:
            code = extract_code_prefix(p["phone"] or p["name"])
            if code not in grouped:
                grouped[code] = p
        
        for code, p in grouped.items():
            flag = get_flag(code)
            btn_text = f"{flag}{code} [{p['price_rub']:.0f}₽]"
            kb.button(text=btn_text, callback_data=f"buy_prefix:{code}")
    else:
        for p in rows:
            flag = get_flag(p["name"]) or get_flag(p["phone"] or "")
            btn_text = f"{flag}{p['name']} [{p['price_rub']:.0f}₽]"
            kb.button(text=btn_text, callback_data=f"product:{p['id']}")
        
    kb.adjust(1)
    await m.answer("Выберите товар для покупки:", reply_markup=kb.as_markup())
@dp.callback_query(F.data.startswith("buy_prefix:"))
async def buy_prefix_select(call: CallbackQuery):
    if not await check_sub(call.from_user.id): return await send_sub_request(call)
    await call.answer()
    prefix = call.data.split(":", 1)[1]
    
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE kind='account' AND stock>0 AND (phone LIKE ? OR name LIKE ?) LIMIT 1", (f"{prefix}%", f"{prefix}%"))
    await conn.close()

    if not prod:
        return await call.message.answer("К сожалению, аккаунты с этим кодом закончились.")

    flag = get_flag(prefix)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Купить за {prod['price_rub']:.0f}₽", callback_data=f"pay:{prod['id']}:rub")],
        [InlineKeyboardButton(text=f"Купить за {prod['price_stars']}⭐ Stars", callback_data=f"pay:{prod['id']}:stars")],
        [InlineKeyboardButton(text=f"Купить за {prod['price_kzt']:.0f}₸", callback_data=f"pay:{prod['id']}:kzt")]
    ])
    await call.message.answer(f"🛒 <b>Покупка аккаунта: {flag}{prefix}</b>\n\nВыберите способ оплаты:", reply_markup=kb)

@dp.callback_query(F.data.startswith("product:"))
async def select_product(call: CallbackQuery):
    if not await check_sub(call.from_user.id): return await send_sub_request(call)
    await call.answer()
    pid = int(call.data.split(":")[1])
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (pid,))
    await conn.close()

    if not prod or prod["stock"] <= 0:
        return await call.message.answer("К сожалению, этого товара уже нет в наличии.")

    flag = get_flag(prod["name"]) or get_flag(prod["phone"] or "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Купить за {prod['price_rub']:.0f}₽", callback_data=f"pay:{prod['id']}:rub")],
        [InlineKeyboardButton(text=f"Купить за {prod['price_stars']}⭐ Stars", callback_data=f"pay:{prod['id']}:stars")],
        [InlineKeyboardButton(text=f"Купить за {prod['price_kzt']:.0f}₸", callback_data=f"pay:{prod['id']}:kzt")]
    ])
    await call.message.answer(f"🛒 <b>Покупка: {flag}{prod['name']}</b>\n\nВыберите способ оплаты:", reply_markup=kb)

@dp.callback_query(F.data.startswith("pay:"))
async def process_payment_info(call: CallbackQuery, state: FSMContext):
    if not await check_sub(call.from_user.id): return await send_sub_request(call)
    await call.answer()
    _, pid_str, pay_type = call.data.split(":")
    pid = int(pid_str)
    
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (pid,))
    await conn.close()

    if not prod or prod["stock"] <= 0:
        return await call.message.answer("Товар закончился.")

    await state.update_data(product_id=pid, pay_type=pay_type)
    await state.set_state(PaymentState.waiting_for_proof)

    if pay_type == "rub":
        text_msg = (
            f"Переведите <b>{prod['price_rub']:.0f} ₽</b> на реквизиты:\n"
            f"<code>+79313716777</code>\n"
            f"• Т-Банк\n"
            f"• Наталья/Тимур\n\n"
            f"После оплаты <b>отправьте скриншот или чек оплаты</b> ответным сообщением в этот чат."
        )
    elif pay_type == "kzt":
        text_msg = (
            f"Переведите <b>{prod['price_kzt']:.0f} ₸</b> на реквизиты:\n"
            f"<code>4400430355263416</code>\n"
            f"• Банк: Kaspi\n"
            f"• Получатель: Мерейхан.Т\n\n"
            f"После оплаты <b>отправьте скриншот или чек оплаты</b> ответным сообщением в этот чат."
        )
    else:
        text_msg = (
            f"Отправьте <b>{prod['price_stars']} ⭐</b> на аккаунт @fegote\n\n"
            f"После отправки <b>отправьте скриншот подтверждения</b> ответным сообщением в этот чат."
        )

    await call.message.answer(text_msg)

@dp.message(PaymentState.waiting_for_proof, F.photo | F.document)
async def process_payment_proof(m: Message, state: FSMContext):
    if not await check_sub(m.from_user.id): return await send_sub_request(m)
    data = await state.get_data()
    pid = data["product_id"]
    pay_type = data["pay_type"]
    
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (pid,))
    if not prod or prod["stock"] <= 0:
        await conn.close()
        await state.clear()
        return await m.answer("Товар закончился.")

    photo_id = m.photo[-1].file_id if m.photo else m.document.file_id
    if pay_type == "rub":
        amount = prod["price_rub"]
        pay_symbol = "₽"
    elif pay_type == "kzt":
        amount = prod["price_kzt"]
        pay_symbol = "₸"
    else:
        amount = prod["price_stars"]
        pay_symbol = "⭐"

    cur = await conn.execute(
        "INSERT INTO purchases(user_id, product_id, product_name, payment, amount, screenshot_file_id, status, created_at) VALUES(?,?,?,?,?,?,'pending',?)",
        (m.from_user.id, pid, prod["name"], pay_type, amount, photo_id, now_msk().isoformat())
    )
    purch_id = cur.lastrowid
    await conn.commit()
    await conn.close()
    await state.clear()

    await m.answer("⏳ <b>Заявка отправлена администратору на проверку.</b> Ожидайте подтверждения.")

    adm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_approve:{purch_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_reject:{purch_id}")
        ]
    ])
    
    user_info = f"@{m.from_user.username}" if m.from_user.username else f"ID {m.from_user.id}"
    flag = get_flag(prod["name"]) or get_flag(prod["phone"] or "")
    
    caption = f"📦 <b>Новая заявка на покупку #{purch_id}</b>\n\nПокупатель: {user_info}\nТовар: {flag}{prod['name']}\nСумма: {amount:.0f} {pay_symbol}"
    
    if m.photo:
        await bot.send_photo(ADMIN_ID, photo_id, caption=caption, reply_markup=adm_kb)
    else:
        await bot.send_document(ADMIN_ID, photo_id, caption=caption, reply_markup=adm_kb)

@dp.callback_query(F.data.startswith("adm_approve:"))
async def approve_purchase(call: CallbackQuery):
    await call.answer("Принято!")
    purch_id = int(call.data.split(":")[1])
    
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (purch_id,))
    
    if not purch or purch["status"] != "pending":
        await conn.close()
        return await call.message.edit_caption(caption=call.message.caption + "\n\n⚠️ Заявка уже обработана.")

    prod = await one(conn, "SELECT * FROM products WHERE id=?", (purch["product_id"],))
    if prod and prod["kind"] == "account":
        await conn.execute("DELETE FROM products WHERE id=?", (purch["product_id"],))
    elif prod and prod["stock"] <= 1:
        await conn.execute("DELETE FROM products WHERE id=?", (purch["product_id"],))
    elif prod:
        await conn.execute("UPDATE products SET stock = stock - 1, sales = sales + 1 WHERE id=?", (purch["product_id"],))
        
    await conn.execute("UPDATE purchases SET status = 'approved' WHERE id=?", (purch_id,))
    await conn.commit()
    await conn.close()

    await call.message.edit_caption(caption=call.message.caption + "\n\n✅ <b>ОДОБРЕНО</b>")

    claim_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Получить аккаунт / Код", callback_data=f"claim_acc:{purch_id}")]
    ])
    await bot.send_message(purch["user_id"], "✅ <b>Ваш платеж успешно подтвержден!</b> Нажмите кнопку ниже, чтобы забрать товар:", reply_markup=claim_kb)

@dp.callback_query(F.data.startswith("adm_reject:"))
async def reject_purchase(call: CallbackQuery):
    await call.answer("Отклонено.")
    purch_id = int(call.data.split(":")[1])
    
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (purch_id,))
    if purch:
        await conn.execute("UPDATE purchases SET status = 'rejected' WHERE id=?", (purch_id,))
        await conn.commit()
    await conn.close()

    await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>ОТКЛОНЕНО</b>")
    if purch:
        await bot.send_message(purch["user_id"], "❌ Ваша оплата была отклонена администратором.")
@dp.callback_query(F.data.startswith("claim_acc:"))
async def claim_account(call: CallbackQuery):
    await call.answer()
    purch_id = int(call.data.split(":")[1])
    
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (purch_id,))
    
    if not purch:
        await conn.close()
        return await call.message.answer("Заявка на покупку не найдена.")
        
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (purch["product_id"],))
    await conn.close()
    
    session_file = prod["session_path"] if prod else None
    user_mention = f"@{call.from_user.username}" if call.from_user.username else f"ID {call.from_user.id}"

    if session_file and os.path.exists(session_file):
        await call.message.answer("⏳ <i>Подключаемся к аккаунту...</i>")
        asyncio.create_task(safe_listen_for_login_code(session_file, call.from_user.id, user_mention))
    else:
        await call.message.answer("⚠️ Файл `.session` для этого аккаунта не найден на сервере.")

@dp.callback_query(F.data.startswith("claim_gift:"))
async def claim_gift_account(call: CallbackQuery):
    await call.answer()
    prod_id = int(call.data.split(":")[1])
    
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (prod_id,))
    await conn.close()
    
    if not prod:
        return await call.message.answer("Подарок не найден или уже был удален.")

    session_file = prod["session_path"]
    user_mention = f"@{call.from_user.username}" if call.from_user.username else f"ID {call.from_user.id}"

    if session_file and os.path.exists(session_file):
        await call.message.answer("⏳ <i>Подключаемся к аккаунту...</i>")
        asyncio.create_task(safe_listen_for_login_code(session_file, call.from_user.id, user_mention))
    else:
        await call.message.answer("⚠️ Файл `.session` не найден на сервере.")

@dp.callback_query(F.data.startswith("more_code:"))
async def req_more_code(call: CallbackQuery):
    await call.answer("Ожидаем ещё один код...")
    session_path = call.data.split(":", 1)[1]
    user_mention = f"@{call.from_user.username}" if call.from_user.username else f"ID {call.from_user.id}"
    await call.message.answer("⏳ <i>Подключаемся для получения нового кода...</i>")
    asyncio.create_task(safe_listen_for_login_code(session_path, call.from_user.id, user_mention))

async def safe_listen_for_login_code(session_path: str, user_id: int, user_mention: str):
    session_name = Path(session_path).stem
    session_dir = str(Path(session_path).parent)
    
    client = Client(
        session_name, 
        api_id=API_ID, 
        api_hash=API_HASH, 
        workdir=session_dir,
        in_memory=False
    )
    
    login_notified = False

    try:
        await client.start()
        
        me = await client.get_me()
        phone_number = f"+{me.phone_number}" if (me and me.phone_number) else "Номер не определен"

        await bot.send_message(
            user_id,
            f"📱 <b>Номер аккаунта:</b> <code>{phone_number}</code>\n\n"
            f"Отправьте запрос кода авторизации в Telegram. Как только код придет, бот перешлет его сюда."
        )

        @client.on_message(filters.me | filters.service | filters.private)
        async def code_handler(cli, message):
            nonlocal login_notified
            
            if not login_notified and ("устройство" in (message.text or "").lower() or "вход" in (message.text or "").lower() or (message.from_user and message.from_user.is_self)):
                login_notified = True
                admin_text = (
                    f"🔔 <b>Вход в аккаунт произведен!</b>\n\n"
                    f"📱 Аккаунт: <code>{phone_number}</code>\n"
                    f"👤 Покупатель/Получатель: {user_mention}"
                )
                try: await bot.send_message(ADMIN_ID, admin_text)
                except Exception: pass

            if message.text:
                codes = re.findall(r'\b\d{5,6}\b', message.text)
                if codes:
                    found_code = codes[0]
                    try:
                        await bot.send_message(user_id, f"🔑 <b>Ваш код авторизации:</b> <code>{found_code}</code>")
                        
                        kb_more = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="еще один код", callback_data=f"more_code:{session_path}")]
                        ])
                        await bot.send_message(user_id, "Код не пришел?", reply_markup=kb_more)
                        
                        if not login_notified:
                            login_notified = True
                            admin_text = (
                                f"🔔 <b>Вход в аккаунт произведен!</b>\n\n"
                                f"📱 Аккаунт: <code>{phone_number}</code>\n"
                                f"👤 Покупатель/Получатель: {user_mention}"
                            )
                            try: await bot.send_message(ADMIN_ID, admin_text)
                            except Exception: pass

                        asyncio.create_task(send_post_purchase_info(user_id))
                    except Exception: 
                        pass
                    await cli.stop()

        await asyncio.sleep(600)
        if client.is_connected:
            await client.stop()

    except (AuthKeyUnregistered, UserDeactivated):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Написать в поддержку", url="https://t.me/fegote")]
        ])
        await bot.send_message(
            user_id, 
            "❌ <b>Аккаунт недействителен.</b>\n\nНапишите в поддержку для решения проблемы.",
            reply_markup=kb
        )
        await bot.send_message(
            ADMIN_ID, 
            f"⚠️ <b>Внимание!</b> Слетел аккаунт у пользователя <code>{user_id}</code>.\nФайл: <code>{session_path}</code>"
        )
    except SessionPasswordNeeded:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Написать в поддержку", url="https://t.me/fegote")]
        ])
        await bot.send_message(
            user_id, 
            "❌ <b>Ошибка:</b> На аккаунте установлен облачный пароль (2FA).\nНапишите в поддержку для решения проблемы.",
            reply_markup=kb
        )
    except Exception as e:
        print(f"Ошибка Pyrogram сессии {session_path}: {e}")

async def send_post_purchase_info(user_id: int):
    await asyncio.sleep(7)
    msg_1 = (
        "Спасибо за покупку!\n"
        "не меняйте описание, юз, ник, аватарку в течении суток\n"
        "гарантия на аккаунт - 1 час"
    )
    try:
        await bot.send_message(user_id, msg_1)
    except Exception:
        pass

    await asyncio.sleep(2)
    msg_2 = "Пожалуйста, оставьте отзыв с юзом @fegote!"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оставить отзыв", url="https://t.me/fegote")]
    ])
    try:
        await bot.send_message(user_id, msg_2, reply_markup=kb)
    except Exception:
        pass
@dp.message(F.text == "Админ панель")
async def admin_panel(m: Message):
    if not is_admin(m.from_user.id): return
    await m.answer("Админ панель", reply_markup=admin_kb())

@dp.message(F.text == "Статистика")
async def stats_cmd(m: Message):
    if not is_admin(m.from_user.id): return
    conn = await db()
    u_cnt = (await one(conn, "SELECT COUNT(*) as c FROM users"))["c"]
    p_cnt = (await one(conn, "SELECT COUNT(*) as c FROM purchases WHERE status='approved'"))["c"]
    await conn.close()
    await m.answer(f"📊 <b>Статистика бота:</b>\n\n👥 Пользователей: {u_cnt}\n🛍 Покупок: {p_cnt}")

@dp.message(F.text == "🎁 Подарить аккаунт")
async def gift_acc_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await state.set_state(GiftAccount.user_id)
    await m.answer("🎁 <b>1 этап</b> — Введите <b>Telegram ID</b> пользователя, которому хотите подарить аккаунт:")

@dp.message(GiftAccount.user_id)
async def gift_acc_user_id(m: Message, state: FSMContext):
    try:
        target_id = int(m.text.strip())
    except ValueError:
        return await m.answer("❌ Введите корректный числовой Telegram ID пользователя.")
    
    await state.update_data(target_user_id=target_id)
    await state.set_state(GiftAccount.session_file)
    await m.answer("🎁 <b>2 этап</b> — Отправьте <b>.session файл Pyrogram</b> документом:")

@dp.message(GiftAccount.session_file, F.document)
async def gift_acc_session(m: Message, state: FSMContext):
    file_id = m.document.file_id
    file_name = m.document.file_name or f"gift_{secrets.token_hex(4)}.session"
    save_path = Path("sessions") / file_name
    
    file_info = await bot.get_file(file_id)
    await bot.download_file(file_info.file_path, save_path)
    
    await state.update_data(file_id=file_id, session_path=str(save_path))
    
    data = await state.get_data()
    
    text = (
        f"🎁 <b>Подтверждение подарка:</b>\n\n"
        f"👤 <b>Получатель (ID):</b> <code>{data['target_user_id']}</code>\n"
        f"📁 <b>Файл:</b> <code>{Path(save_path).name}</code>\n\n"
        f"Отправить подарок пользователю?"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Отправить", callback_data="gift_confirm_send")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="gift_cancel")]
    ])
    
    await state.set_state(GiftAccount.confirm)
    await m.answer(text, reply_markup=kb)

@dp.callback_query(GiftAccount.confirm, F.data == "gift_confirm_send")
async def gift_confirm_send(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    
    target_id = data["target_user_id"]
    session_path = data["session_path"]
    file_id = data.get("file_id")
    
    conn = await db()
    cur = await conn.execute(
        "INSERT INTO products(kind, name, price_rub, price_stars, price_kzt, stock, sales, created_at, file_id, session_path, phone) VALUES('account', 'Подарок', 0, 0, 0, 0, 1, ?, ?, ?, '')",
        (now_msk().isoformat(), file_id, session_path)
    )
    prod_id = cur.lastrowid
    await conn.commit(); await conn.close()
    
    await state.clear()
    await call.message.edit_text("✅ <b>Подарок успешно отправлен пользователю!</b>")
    
    gift_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Забрать подарок", callback_data=f"claim_gift:{prod_id}")]
    ])
    
    gift_text = (
        "Вы получили аккаунт от Noverashop!\n"
        "Забрать аккаунт вы можете по кнопке ниже:"
    )
    
    try:
        await bot.send_message(target_id, gift_text, reply_markup=gift_kb)
    except Exception as e:
        await call.message.answer(f"⚠️ Не удалось доставить сообщение пользователю в ЛС: {e}")

@dp.callback_query(GiftAccount.confirm, F.data == "gift_cancel")
async def gift_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Отменено.")
    await call.message.edit_text("❌ Отправка подарка отменена.", reply_markup=None)

@dp.message(F.text == "Склад")
async def stock_cmd(m: Message):
    if not is_admin(m.from_user.id): return
    await stock(m)

async def stock(m):
    conn = await db()
    rows = await all_rows(conn, "SELECT * FROM products ORDER BY kind, id")
    await conn.close()
    
    kb = InlineKeyboardBuilder()
    if rows:
        grouped = {}
        for p in rows:
            if p["kind"] == "account":
                code = extract_code_prefix(p["phone"] or p["name"])
                grouped[code] = grouped.get(code, 0) + p["stock"]
            else:
                grouped[p["name"]] = grouped.get(p["name"], 0) + p["stock"]
                
        for name, st in grouped.items():
            flag = get_flag(name)
            kb.button(text=f"{flag}{name} ({st} шт)", callback_data="ignore_click")
            
    kb.button(text="➕ Добавить товар", callback_data="add_product")
    kb.button(text="Загрузить пачку", callback_data="add_bulk_pack")
    kb.adjust(1)
    await m.answer("Управление складом:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "ignore_click")
async def ignore_click_h(call: CallbackQuery):
    await call.answer()

@dp.callback_query(F.data == "add_bulk_pack")
async def start_bulk_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(BulkAddProduct.uploading)
    await state.update_data(files=[], prefixes=set())
    
    finish_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить загрузку пачки", callback_data="finish_bulk_upload")]
    ])
    await call.message.answer("Скидывайте `.session` файлы пачкой. Бот сам считает номера. Нажмите кнопку ниже по окончании:", reply_markup=finish_kb)
    await call.answer()

@dp.message(BulkAddProduct.uploading, F.document)
async def process_bulk_file(m: Message, state: FSMContext):
    file_id = m.document.file_id
    file_name = m.document.file_name or f"session_{secrets.token_hex(4)}.session"
    save_path = Path("sessions") / file_name
    
    file_info = await bot.get_file(file_id)
    await bot.download_file(file_info.file_path, save_path)
    
    full_phone = ""
    detected_code = "+1"
    
    try:
        session_name = save_path.stem
        client = Client(session_name, api_id=API_ID, api_hash=API_HASH, workdir=str(save_path.parent), in_memory=False)
        await client.connect()
        me = await client.get_me()
        if me and me.phone_number:
            full_phone = f"+{me.phone_number}"
            detected_code = extract_code_prefix(full_phone)
        await client.disconnect()
    except Exception:
        full_phone = extract_code_prefix(file_name)
        detected_code = extract_code_prefix(file_name)

    data = await state.get_data()
    files_list = data.get("files", [])
    prefixes_set = set(data.get("prefixes", []))
    
    files_list.append({
        "file_id": file_id, 
        "path": str(save_path), 
        "code": detected_code, 
        "phone": full_phone,
        "name": file_name
    })
    prefixes_set.add(detected_code)
    
    await state.update_data(files=files_list, prefixes=list(prefixes_set))

@dp.callback_query(BulkAddProduct.uploading, F.data == "finish_bulk_upload")
async def finish_bulk_upload_h(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    prefixes = data.get("prefixes", [])
    
    if not prefixes:
        await call.answer("Вы не загрузили ни одного файла!", show_alert=True)
        return
        
    await state.update_data(remaining_prefixes=list(prefixes), price_map={})
    await state.set_state(BulkAddProduct.pricing)
    
    next_pref = prefixes[0]
    await call.message.answer(f"Введите цену для {next_pref}\nФормат: <code>хх₽, хх звезд, хх тг</code>")
    await call.answer()

@dp.message(BulkAddProduct.pricing)
async def process_bulk_pricing(m: Message, state: FSMContext):
    text = m.text.lower()
    
    rub_match = re.search(r'(\d+[\.,]?\d*)\s*(?:₽|руб|rub)', text)
    stars_match = re.search(r'(\d+)\s*(?:⭐|звезд|stars)', text)
    kzt_match = re.search(r'(\d+[\.,]?\d*)\s*(?:₸|тг|kzt)', text)
    
    if not (rub_match or stars_match or kzt_match):
        nums = re.findall(r'\d+[\.,]?\d*', text)
        if len(nums) >= 3:
            p_rub = float(nums[0].replace(',', '.'))
            p_stars = int(float(nums[1]))
            p_kzt = float(nums[2].replace(',', '.'))
        else:
            return await m.answer("Не удалось определить цены. Введите в формате: <code>100₽, 50 звезд, 500 тг</code>")
    else:
        p_rub = float(rub_match.group(1).replace(',', '.')) if rub_match else 0.0
        p_stars = int(stars_match.group(1)) if stars_match else 0
        p_kzt = float(kzt_match.group(1).replace(',', '.')) if kzt_match else 0.0

    data = await state.get_data()
    remaining = data["remaining_prefixes"]
    price_map = data.get("price_map", {})
    
    curr_pref = remaining.pop(0)
    price_map[curr_pref] = {"rub": p_rub, "stars": p_stars, "kzt": p_kzt}
    
    if remaining:
        await state.update_data(remaining_prefixes=remaining, price_map=price_map)
        next_pref = remaining[0]
        await m.answer(f"Введите цену для {next_pref}\nФормат: <code>хх₽, хх звезд, хх тг</code>")
    else:
        files = data["files"]
        conn = await db()
        count_added = 0
        
        for f_item in files:
            code = f_item["code"]
            phone_num = f_item["phone"]
            prices = price_map.get(code, {"rub": 0, "stars": 0, "kzt": 0})
            
            await conn.execute(
                "INSERT INTO products(kind, name, price_rub, price_stars, price_kzt, stock, sales, created_at, file_id, session_path, phone) VALUES('account', ?, ?, ?, ?, 1, 0, ?, ?, ?, ?)",
                (phone_num or code, prices["rub"], prices["stars"], prices["kzt"], now_msk().isoformat(), f_item["file_id"], f_item["path"], phone_num or code)
            )
            count_added += 1
            
        await conn.commit()
        await conn.close()
        await state.clear()
        
        await m.answer(f"✅ Успешно обработано и добавлено аккаунтов: <b>{count_added} шт.</b>", reply_markup=admin_kb())

@dp.callback_query(F.data == "add_product")
async def add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(AddProduct.kind)
    await call.message.answer("Выберите тип товара:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Аккаунт", callback_data="addkind:account")],
        [InlineKeyboardButton(text="Stars", callback_data="addkind:stars")]
    ]))
    await call.answer()

@dp.callback_query(AddProduct.kind, F.data.startswith("addkind:"))
async def add_kind(call: CallbackQuery, state: FSMContext):
    await state.update_data(kind=call.data.split(":")[1])
    await state.set_state(AddProduct.name)
    await call.message.answer("1 этап — Введите название товара (например, +1 555-1234):")
    await call.answer()

@dp.message(AddProduct.name)
async def add_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProduct.price_rub)
    await m.answer("2 этап — Введите цену в ₽:")

@dp.message(AddProduct.price_rub)
async def add_rub(m: Message, state: FSMContext):
    try: price = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите число, например 50")
    await state.update_data(price_rub=price)
    await state.set_state(AddProduct.price_stars)
    await m.answer("3 этап — Введите цену в звездах ⭐:")

@dp.message(AddProduct.price_stars)
async def add_stars(m: Message, state: FSMContext):
    try: price = int(m.text)
    except ValueError: return await m.answer("Введите целое число, например 60")
    await state.update_data(price_stars=price)
    await state.set_state(AddProduct.price_kzt)
    await m.answer("4 этап — Введите цену в тенге ₸:")

@dp.message(AddProduct.price_kzt)
async def add_kzt(m: Message, state: FSMContext):
    try: price = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите число, например 3000")
    await state.update_data(price_kzt=price)
    
    d = await state.get_data()
    if d.get("kind") == "account":
        await state.set_state(AddProduct.account_file)
        await m.answer("5 этап — Отправьте `.session` файл аккаунта документом:")
    else:
        await state.set_state(AddProduct.phone)
        await m.answer("5 этап — Введите количество товара на складе:")

@dp.message(AddProduct.account_file, F.document)
async def add_account_file(m: Message, state: FSMContext):
    file_id = m.document.file_id
    file_name = m.document.file_name or f"session_{secrets.token_hex(4)}.session"
    save_path = Path("sessions") / file_name
    
    file_info = await bot.get_file(file_id)
    await bot.download_file(file_info.file_path, save_path)
    
    d = await state.get_data()
    product_name = d["name"]
    kind = d["kind"]
    
    conn = await db()
    await conn.execute(
        "INSERT INTO products(kind, name, price_rub, price_stars, price_kzt, stock, sales, created_at, file_id, session_path, phone) VALUES(?,?,?,?,?,1,0,?,?,?,'')",
        (kind, product_name, d["price_rub"], d["price_stars"], d["price_kzt"], now_msk().isoformat(), file_id, str(save_path))
    )
    msg_text = f"✅ <b>Товар «{product_name}» успешно создан!</b>\nКоличество: <b>1 шт.</b>"
        
    await conn.commit()
    await conn.close()
    
    await state.clear()
    await m.answer(msg_text, reply_markup=admin_kb())

@dp.message(AddProduct.phone)
async def add_finish_stars(m: Message, state: FSMContext):
    d = await state.get_data()
    conn = await db()
    
    try: stock_cnt = int(m.text)
    except ValueError: return await m.answer("Введите целое число.")
    
    await conn.execute(
        "INSERT INTO products(kind, name, price_rub, price_stars, price_kzt, stock, sales, created_at) VALUES(?,?,?,?,?,?,0,?)",
        (d["kind"], d["name"], d["price_rub"], d["price_stars"], d["price_kzt"], stock_cnt, now_msk().isoformat())
    )
        
    await conn.commit(); await conn.close()
    await state.clear()
    await m.answer("Товар успешно добавлен на склад ✅", reply_markup=admin_kb())

@dp.message(F.text == "Рассылка")
async def broadcast_cmd(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): 
        return
    await state.set_state(Broadcast.text)
    await m.answer(
        "📢 <b>Отправьте сообщение для рассылки:</b>\n\n"
        "Вы можете отправить текст с премиум-эмодзи, форматированием, фото, видео, гифку или стикер."
    )

@dp.message(Broadcast.text)
async def broadcast_finish(m: Message, state: FSMContext):
    await state.clear()
    
    conn = await db()
    users = await all_rows(conn, "SELECT id FROM users")
    await conn.close()
    
    count = 0
    blocked = 0
    
    status_msg = await m.answer("⏳ Рассылка запущена...")
    
    for u in users:
        try:
            await bot.copy_message(
                chat_id=u["id"],
                from_chat_id=m.chat.id,
                message_id=m.message_id
            )
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            blocked += 1
            
    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"👤 Успешно доставлено: <b>{count}</b>\n"
        f"🚫 Не доставлено (бот заблокирован): <b>{blocked}</b>"
    )

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
