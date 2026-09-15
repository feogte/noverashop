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
TOKEN = os.getenv("8887215013:AAHnKafRzAr6SjWyJL-9EhYNg6rHKN_M2t4") or os.getenv("TELEGRAM_BOT_TOKEN")
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
    # Разделили категории (products) и залитые аккаунты (account_stock)
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL);
    
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        kind TEXT NOT NULL, 
        name TEXT NOT NULL, 
        price_rub REAL NOT NULL, 
        price_stars INTEGER NOT NULL, 
        price_kzt REAL NOT NULL DEFAULT 0, 
        stock INTEGER NOT NULL DEFAULT 0, 
        sales INTEGER NOT NULL DEFAULT 0, 
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS account_stock(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        phone TEXT NOT NULL,
        session_path TEXT NOT NULL,
        file_id TEXT,
        is_sold INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        user_id INTEGER NOT NULL, 
        product_id INTEGER NOT NULL, 
        account_stock_id INTEGER,
        product_name TEXT NOT NULL, 
        payment TEXT NOT NULL, 
        amount REAL NOT NULL, 
        screenshot_file_id TEXT, 
        status TEXT NOT NULL DEFAULT 'pending', 
        created_at TEXT NOT NULL
    );
    """)
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

class AddProductCategory(StatesGroup):
    kind = State()
    name = State()
    price_rub = State()
    price_stars = State()
    price_kzt = State()

class MassUpload(StatesGroup):
    product_id = State()
    collecting_files = State()
    collecting_phones = State()

class GiftAccount(StatesGroup):
    user_id = State()
    session_file = State()
    phone = State()
    confirm = State()

class EditProduct(StatesGroup):
    price_rub = State()
    price_stars = State()
    price_kzt = State()

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
        await conn.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?)", (call.from_user.id, call.from_user.username, call.from_user.first_name, now_msk().isoformat()))
        await conn.commit(); await conn.close()
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

    if not await check_sub(m.from_user.id): return await send_sub_request(m)
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
    for p in rows: 
        flag = get_flag(p["name"])
        btn_text = f"{flag}{p['name']} [{p['price_rub']:.0f}₽]"
        kb.button(text=btn_text, callback_data=f"product:{p['id']}")
        
    kb.adjust(1)
    await m.answer("Выберите товар для покупки:", reply_markup=kb.as_markup())
# --- ПОКУПКА И ОПЛАТА С ЧЕКОМ ---
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

    flag = get_flag(prod["name"])
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
            f"После оплаты **отправьте скриншот или чек оплаты** ответным сообщением в этот чат."
        )
    elif pay_type == "kzt":
        text_msg = (
            f"Переведите <b>{prod['price_kzt']:.0f} ₸</b> на реквизиты:\n"
            f"<code>4400430041062222</code>\n"
            f"• Банк: Kaspi\n"
            f"• Получатель: Тимур/Виталий\n\n"
            f"После оплаты **отправьте скриншот или чек оплаты** ответным сообщением в этот чат."
        )
    else:
        text_msg = (
            f"Отправьте <b>{prod['price_stars']} ⭐</b> на аккаунт @fegote\n\n"
            f"После отправки **отправьте скриншот подтверждения** ответным сообщением в этот чат."
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
    flag = get_flag(prod["name"])
    
    caption = f"📦 <b>Новая заявка на покупку #{purch_id}</b>\n\nПокупатель: {user_info}\nТовар: {flag}{prod['name']}\nСумма: {amount:.0f} {pay_symbol}"
    
    if m.photo:
        await bot.send_photo(ADMIN_ID, photo_id, caption=caption, reply_markup=adm_kb)
    else:
        await bot.send_document(ADMIN_ID, photo_id, caption=caption, reply_markup=adm_kb)

# --- ОБРАБОТКА ЗАЯВКИ АДМИНОМ И РАНДОМНЫЙ ВЫБОР АККАУНТА ---
@dp.callback_query(F.data.startswith("adm_approve:"))
async def approve_purchase(call: CallbackQuery):
    await call.answer("Принято!")
    purch_id = int(call.data.split(":")[1])
    
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (purch_id,))
    
    if not purch or purch["status"] != "pending":
        await conn.close()
        return await call.message.edit_caption(caption=call.message.caption + "\n\n⚠️ Заявка уже обработана.")

    # 1. Забираем РАНДОМНЫЙ свободный аккаунт strictly из этой категории (product_id)
    acc = await one(conn, "SELECT * FROM account_stock WHERE product_id=? AND is_sold=0 ORDER BY RANDOM() LIMIT 1", (purch["product_id"],))

    if not acc:
        await conn.close()
        return await call.message.edit_caption(caption=call.message.caption + "\n\n⚠️ **ОШИБКА:** На складе кончились аккаунты из этой категории!")

    # 2. Помечаем аккаунт как проданный и уменьшаем stock у категории
    await conn.execute("UPDATE account_stock SET is_sold=1 WHERE id=?", (acc["id"],))
    await conn.execute("UPDATE products SET stock = stock - 1, sales = sales + 1 WHERE id=?", (purch["product_id"],))
    await conn.execute("UPDATE purchases SET status = 'approved', account_stock_id=? WHERE id=?", (acc["id"], purch_id))
    
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

# --- ВЫДАЧА И PYROGRAM СЛУШАТЕЛЬ ---
@dp.callback_query(F.data.startswith("claim_acc:"))
async def claim_account(call: CallbackQuery):
    await call.answer()
    pid = int(call.data.split(":")[1])
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (pid,))
    
    if not purch or not purch["account_stock_id"]:
        await conn.close()
        return await call.message.answer("Заявка на покупку или привязанный аккаунт не найдены.")
        
    acc = await one(conn, "SELECT * FROM account_stock WHERE id=?", (purch["account_stock_id"],))
    await conn.close()
    
    phone_number = acc["phone"] if acc else "Номер не указан"
    session_file = acc["session_path"] if acc else None
    
    await call.message.answer(
        f"📱 <b>Номер аккаунта:</b> <code>{phone_number}</code>\n\n"
        f"Отправьте запрос кода авторизации в Telegram. Как только код придет, бот перешлит его сюда."
    )
    
    user_mention = f"@{call.from_user.username}" if call.from_user.username else f"ID {call.from_user.id}"

    if session_file and os.path.exists(session_file):
        asyncio.create_task(safe_listen_for_login_code(session_file, call.from_user.id, phone_number, user_mention))
    else:
        await call.message.answer("⚠️ Файл `.session` для этого аккаунта не найден на сервере.")

# --- ОБРАБОТЧИК КНОПКИ «🎁 ЗАБРАТЬ ПОДАРОК» ---
@dp.callback_query(F.data.startswith("claim_gift:"))
async def claim_gift_account(call: CallbackQuery):
    await call.answer()
    acc_id = int(call.data.split(":")[1])
    
    conn = await db()
    acc = await one(conn, "SELECT * FROM account_stock WHERE id=?", (acc_id,))
    await conn.close()
    
    if not acc:
        return await call.message.answer("Подарок не найден или уже был удален.")

    phone_number = acc["phone"] or "Номер не указан"
    session_file = acc["session_path"]
    
    await call.message.answer(
        f"🎁 <b>Ваш подарочный аккаунт:</b>\n"
        f"📱 <b>Номер:</b> <code>{phone_number}</code>\n\n"
        f"Отправьте запрос кода авторизации в Telegram. Как только код придет, бот перешлит его сюда."
    )
    
    user_mention = f"@{call.from_user.username}" if call.from_user.username else f"ID {call.from_user.id}"

    if session_file and os.path.exists(session_file):
        asyncio.create_task(safe_listen_for_login_code(session_file, call.from_user.id, phone_number, user_mention))
    else:
        await call.message.answer("⚠️ Файл `.session` не найден на сервере.")

async def safe_listen_for_login_code(session_path: str, user_id: int, phone_number: str, user_mention: str):
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

        await client.start()
        await asyncio.sleep(600)
        if client.is_connected:
            await client.stop()

    except (AuthKeyUnregistered, UserDeactivated):
        await bot.send_message(
            user_id, 
            "❌ <b>Ошибка:</b> Данная сессия недействительна (аккаунт слетел или был завершен).\nПожалуйста, обратитесь в поддержку."
        )
        await bot.send_message(
            ADMIN_ID, 
            f"⚠️ <b>Внимание!</b> Невалидная сессия у пользователя <code>{user_id}</code>.\nФайл: <code>{session_path}</code>"
        )
    except SessionPasswordNeeded:
        await bot.send_message(
            user_id, 
            "❌ <b>Ошибка:</b> На аккаунте установлен облачный пароль (2FA).\nНапишите в поддержку для решения проблемы."
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
# --- АДМИН ПАНЕЛЬ И СКЛАД ---
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
    acc_cnt = (await one(conn, "SELECT COUNT(*) as c FROM account_stock WHERE is_sold=0"))["c"]
    await conn.close()
    await m.answer(f"📊 <b>Статистика бота:</b>\n\n👥 Пользователей: {u_cnt}\n🛍 Успешных покупок: {p_cnt}\n📱 Аккаунтов в наличии: {acc_cnt}")

# --- ФУНКЦИЯ «ПОДАРИТЬ АККАУНТ» ---
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
    await state.set_state(GiftAccount.phone)
    await m.answer("🎁 <b>3 этап</b> — Введите <b>номер телефона</b> аккаунта (например +1 555-1234):")

@dp.message(GiftAccount.phone)
async def gift_acc_phone(m: Message, state: FSMContext):
    phone = m.text.strip()
    await state.update_data(phone=phone)
    data = await state.get_data()
    
    text = (
        f"🎁 <b>Подтверждение подарка:</b>\n\n"
        f"👤 <b>Получатель (ID):</b> <code>{data['target_user_id']}</code>\n"
        f"📱 <b>Номер:</b> <code>{phone}</code>\n"
        f"📁 <b>Файл:</b> <code>{Path(data['session_path']).name}</code>\n\n"
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
    phone = data["phone"]
    session_path = data["session_path"]
    file_id = data.get("file_id")
    
    conn = await db()
    # Создаем скрытый продукт под подарок
    cur = await conn.execute(
        "INSERT INTO products(kind, name, price_rub, price_stars, price_kzt, stock, sales, created_at) VALUES('gift', ?, 0, 0, 0, 0, 1, ?)",
        (phone, now_msk().isoformat())
    )
    prod_id = cur.lastrowid
    
    # Заносим сам файл в базу аккаунтов
    cur_acc = await conn.execute(
        "INSERT INTO account_stock(product_id, phone, session_path, file_id, is_sold) VALUES(?, ?, ?, ?, 1)",
        (prod_id, phone, session_path, file_id)
    )
    acc_id = cur_acc.lastrowid
    
    await conn.commit(); await conn.close()
    
    await state.clear()
    await call.message.edit_text("✅ <b>Подарок успешно отправлен пользователю!</b>")
    
    gift_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Забрать подарок", callback_data=f"claim_gift:{acc_id}")]
    ])
    
    gift_text = "Вы получили аккаунт от Noverashop!\nЗабрать аккаунт вы можете по кнопке ниже:"
    
    try:
        await bot.send_message(target_id, gift_text, reply_markup=gift_kb)
    except Exception as e:
        await call.message.answer(f"⚠️ Сообщение не доставлено (пользователь заблокировал бота): {e}")

@dp.callback_query(GiftAccount.confirm, F.data == "gift_cancel")
async def gift_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Отменено.")
    await call.message.edit_text("❌ Отправка подарка отменена.", reply_markup=None)

# --- СКЛАД И МАССОВАЯ ЗАГРУЗКА ---
@dp.message(F.text == "Склад")
async def stock_cmd(m: Message):
    if not is_admin(m.from_user.id): return
    await stock(m)

async def stock(m):
    conn = await db()
    rows = await all_rows(conn, "SELECT * FROM products WHERE kind != 'gift' ORDER BY kind, id")
    await conn.close()
    if not rows:
        return await m.answer("Склад пуст.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Создать категорию", callback_data="add_category")]]))
    
    kb = InlineKeyboardBuilder()
    for p in rows:
        flag = get_flag(p["name"])
        kb.button(text=f"{flag}{p['name']} ({p['stock']} шт)", callback_data=f"manage_product:{p['id']}")
    kb.button(text="➕ Создать категорию", callback_data="add_category")
    kb.adjust(1)
    await m.answer("Управление складом (выберите категорию):", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("manage_product:"))
async def manage_product_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.answer()
    pid = int(call.data.split(":")[1])
    
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (pid,))
    await conn.close()
    
    if not prod:
        return await call.message.answer("Категория не найдена.")

    flag = get_flag(prod["name"])
    text = (
        f"📦 <b>Категория: {flag}{prod['name']}</b>\n\n"
        f"💰 Цены:\n"
        f"• Рубли: {prod['price_rub']:.0f}₽\n"
        f"• Звезды: {prod['price_stars']}⭐\n"
        f"• Тенге: {prod['price_kzt']:.0f}₸\n\n"
        f"📊 В наличии: <b>{prod['stock']} шт.</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Загрузить аккаунты (Массово)", callback_data=f"mass_upload:{pid}")],
        [InlineKeyboardButton(text="✏️ Изменить цену (₽)", callback_data=f"edit_price_rub:{pid}")],
        [InlineKeyboardButton(text="✏️ Изменить цену (⭐)", callback_data=f"edit_price_stars:{pid}")],
        [InlineKeyboardButton(text="✏️ Изменить цену (₸)", callback_data=f"edit_price_kzt:{pid}")],
        [InlineKeyboardButton(text="❌ Удалить категорию", callback_data=f"delete_product:{pid}")],
        [InlineKeyboardButton(text="⬅️ Назад к складу", callback_data="back_to_stock")]
    ])
    
    await call.message.edit_text(text, reply_markup=kb)

# --- ПРОЦЕСС МАССОВОЙ ЗАГРУЗКИ (FILES -> PHONES) ---
@dp.callback_query(F.data.startswith("mass_upload:"))
async def start_mass_upload(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    pid = int(call.data.split(":")[1])
    await state.update_data(product_id=pid, uploaded_files=[])
    await state.set_state(MassUpload.collecting_files)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Закончить отправку файлов", callback_data="finish_files_upload")]
    ])
    await call.message.answer(
        "📥 <b>Шаг 1: Загрузка файлов .session</b>\n\n"
        "Отправьте боту файлы `.session` (можно выделять и отправлять разом группами).\n"
        "Как только отправите все файлы — нажмите кнопку ниже.",
        reply_markup=kb
    )
    await call.answer()

@dp.message(MassUpload.collecting_files, F.document)
async def collect_files_handler(m: Message, state: FSMContext):
    if not m.document.file_name.endswith(".session"):
        return await m.answer(f"⚠️ Файл {m.document.file_name} не является .session файлом!")

    file_id = m.document.file_id
    file_name = m.document.file_name or f"session_{secrets.token_hex(4)}.session"
    save_path = Path("sessions") / file_name
    
    file_info = await bot.get_file(file_id)
    await bot.download_file(file_info.file_path, save_path)
    
    data = await state.get_data()
    files_list = data.get("uploaded_files", [])
    files_list.append({"file_id": file_id, "path": str(save_path)})
    await state.update_data(uploaded_files=files_list)

@dp.callback_query(MassUpload.collecting_files, F.data == "finish_files_upload")
async def finish_files_upload(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    files = data.get("uploaded_files", [])
    
    if not files:
        await call.answer("Вы не отправили ни одного .session файла!", show_alert=True)
        return

    await call.answer()
    await state.set_state(MassUpload.collecting_phones)
    await call.message.answer(
        f"✅ Принято файлов: <b>{len(files)} шт.</b>\n\n"
        f"📱 <b>Шаг 2: Номера телефонов</b>\n"
        f"Теперь отправьте ровно <b>{len(files)}</b> номеров телефонов (каждый номер с новой строки одним сообщением)."
    )

@dp.message(MassUpload.collecting_phones, F.text)
async def collect_phones_handler(m: Message, state: FSMContext):
    phones = [p.strip() for p in m.text.strip().split("\n") if p.strip()]
    data = await state.get_data()
    files = data.get("uploaded_files", [])
    pid = data["product_id"]

    if len(phones) != len(files):
        return await m.answer(
            f"❌ Количество номеров ({len(phones)}) не совпадает с количеством файлов ({len(files)})!\n"
            f"Пожалуйста, отправьте ровно {len(files)} номеров (по одному на строку)."
        )

    conn = await db()
    added_count = 0
    for file_info, phone in zip(files, phones):
        await conn.execute(
            "INSERT INTO account_stock(product_id, phone, session_path, file_id) VALUES(?, ?, ?, ?)",
            (pid, phone, file_info["path"], file_info["file_id"])
        )
        added_count += 1

    await conn.execute("UPDATE products SET stock = stock + ? WHERE id=?", (added_count, pid))
    await conn.commit()
    await conn.close()
    await state.clear()

    await m.answer(f"🎉 <b>Успешно добавлено {added_count} аккаунтов в категорию!</b>", reply_markup=admin_kb())

# --- СОЗДАНИЕ КАТЕГОРИИ ---
@dp.callback_query(F.data == "add_category")
async def add_cat_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(AddProductCategory.kind)
    await call.message.answer("Выберите тип категории:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Аккаунт", callback_data="addkind:account")],
        [InlineKeyboardButton(text="Stars", callback_data="addkind:stars")]
    ]))
    await call.answer()

@dp.callback_query(AddProductCategory.kind, F.data.startswith("addkind:"))
async def add_cat_kind(call: CallbackQuery, state: FSMContext):
    await state.update_data(kind=call.data.split(":")[1])
    await state.set_state(AddProductCategory.name)
    await call.message.answer("1 этап — Введите название категории (например 🇺🇸 США +1):")
    await call.answer()

@dp.message(AddProductCategory.name)
async def add_cat_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProductCategory.price_rub)
    await m.answer("2 этап — Введите цену в ₽:")

@dp.message(AddProductCategory.price_rub)
async def add_cat_rub(m: Message, state: FSMContext):
    try: price = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите число.")
    await state.update_data(price_rub=price)
    await state.set_state(AddProductCategory.price_stars)
    await m.answer("3 этап — Введите цену в звездах ⭐:")

@dp.message(AddProductCategory.price_stars)
async def add_cat_stars(m: Message, state: FSMContext):
    try: price = int(m.text)
    except ValueError: return await m.answer("Введите целое число.")
    await state.update_data(price_stars=price)
    await state.set_state(AddProductCategory.price_kzt)
    await m.answer("4 этап — Введите цену в тенге ₸:")

@dp.message(AddProductCategory.price_kzt)
async def add_cat_finish(m: Message, state: FSMContext):
    try: price = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите число.")
    
    d = await state.get_data()
    conn = await db()
    await conn.execute(
        "INSERT INTO products(kind, name, price_rub, price_stars, price_kzt, stock, sales, created_at) VALUES(?,?,?,?,?,0,0,?)",
        (d["kind"], d["name"], d["price_rub"], d["price_stars"], price, now_msk().isoformat())
    )
    await conn.commit(); await conn.close()
    await state.clear()
    await m.answer("Категория создана! Теперь вы можете загрузить в неё аккаунты через Склад.", reply_markup=admin_kb())

# --- РЕДАКТИРОВАНИЕ И УДАЛЕНИЕ ---
@dp.callback_query(F.data == "back_to_stock")
async def back_to_stock_handler(call: CallbackQuery):
    await call.answer()
    await stock(call.message)

@dp.callback_query(F.data.startswith("delete_product:"))
async def delete_product_handler(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    pid = int(call.data.split(":")[1])
    conn = await db()
    await conn.execute("DELETE FROM products WHERE id=?", (pid,))
    await conn.commit(); await conn.close()
    await call.answer("Категория удалена!", show_alert=True)
    await stock(call.message)

@dp.callback_query(F.data.startswith("edit_price_rub:"))
async def edit_rub_start(call: CallbackQuery, state: FSMContext):
    pid = int(call.data.split(":")[1])
    await state.update_data(edit_pid=pid)
    await state.set_state(EditProduct.price_rub)
    await call.message.answer("Введите новую цену в рублях (₽):")
    await call.answer()

@dp.message(EditProduct.price_rub)
async def edit_rub_finish(m: Message, state: FSMContext):
    data = await state.get_data()
    try: val = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите корректное число.")
    conn = await db()
    await conn.execute("UPDATE products SET price_rub=? WHERE id=?", (val, data["edit_pid"]))
    await conn.commit(); await conn.close()
    await state.clear()
    await m.answer("Цена (₽) обновлена! ✅", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("edit_price_stars:"))
async def edit_stars_start(call: CallbackQuery, state: FSMContext):
    pid = int(call.data.split(":")[1])
    await state.update_data(edit_pid=pid)
    await state.set_state(EditProduct.price_stars)
    await call.message.answer("Введите новую цену в звездах (⭐):")
    await call.answer()

@dp.message(EditProduct.price_stars)
async def edit_stars_finish(m: Message, state: FSMContext):
    data = await state.get_data()
    try: val = int(m.text)
    except ValueError: return await m.answer("Введите целое число.")
    conn = await db()
    await conn.execute("UPDATE products SET price_stars=? WHERE id=?", (val, data["edit_pid"]))
    await conn.commit(); await conn.close()
    await state.clear()
    await m.answer("Цена (⭐) обновлена! ✅", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("edit_price_kzt:"))
async def edit_kzt_start(call: CallbackQuery, state: FSMContext):
    pid = int(call.data.split(":")[1])
    await state.update_data(edit_pid=pid)
    await state.set_state(EditProduct.price_kzt)
    await call.message.answer("Введите новую цену в тенге (₸):")
    await call.answer()

@dp.message(EditProduct.price_kzt)
async def edit_kzt_finish(m: Message, state: FSMContext):
    data = await state.get_data()
    try: val = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите корректное число.")
    conn = await db()
    await conn.execute("UPDATE products SET price_kzt=? WHERE id=?", (val, data["edit_pid"]))
    await conn.commit(); await conn.close()
    await state.clear()
    await m.answer("Цена (₸) обновлена! ✅", reply_markup=admin_kb())

# --- РАССЫЛКА ---
@dp.message(F.text == "Рассылка")
async def broadcast_cmd(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await state.set_state(Broadcast.text)
    await m.answer("📢 <b>Отправьте сообщение для рассылки:</b>")

@dp.message(Broadcast.text)
async def broadcast_finish(m: Message, state: FSMContext):
    await state.clear()
    conn = await db()
    users = await all_rows(conn, "SELECT id FROM users")
    await conn.close()
    
    count, blocked = 0, 0
    status_msg = await m.answer("⏳ Рассылка запущена...")
    
    for u in users:
        try:
            await bot.copy_message(chat_id=u["id"], from_chat_id=m.chat.id, message_id=m.message_id)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            blocked += 1
            
    await status_msg.edit_text(f"✅ <b>Рассылка завершена!</b>\n\n👤 Успешно: <b>{count}</b>\n🚫 Заблокировали: <b>{blocked}</b>")

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
