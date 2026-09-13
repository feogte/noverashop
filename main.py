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
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "12345"))
API_HASH = os.getenv("API_HASH", "your_hash")

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

# --- ГЕНЕРАТОР ФЛАГОВ ДЛЯ ВСЕХ СТРАН МИРА ---
PHONE_FLAGS = {
    "1": "🇺🇸", "7": "🇷🇺", "380": "🇺🇦", "375": "🇧🇾", "77": "🇰🇿", "76": "🇰🇿",
    "998": "🇺🇿", "992": "🇹🇯", "996": "🇰🇬", "994": "🇦🇿", "374": "🇦🇲", "995": "🇬🇪",
    "373": "🇲🇩", "371": "🇱🇻", "370": "🇱🇹", "372": "🇪🇪", "44": "🇬🇧", "49": "🇩🇪",
    "33": "🇫🇷", "34": "🇪🇸", "39": "🇮🇹", "48": "🇵🇱", "90": "🇹🇷", "62": "🇮🇩",
    "84": "🇻🇳", "66": "🇹🇭", "63": "🇵🇭", "91": "🇮🇳", "86": "🇨🇳", "81": "🇯🇵",
    "82": "🇰🇷", "55": "🇧🇷", "52": "🇲🇽", "20": "🇪🇬", "27": "🇿🇦", "234": "🇳🇬"
}

def get_flag(text: str) -> str:
    if not text:
        return "📱 "
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
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, price_rub REAL NOT NULL, price_stars INTEGER NOT NULL, stock INTEGER NOT NULL DEFAULT 0, sales INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, file_id TEXT, phone TEXT, session_path TEXT);
    CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, product_name TEXT NOT NULL, payment TEXT NOT NULL, amount REAL NOT NULL, screenshot_file_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
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
    kb.button(text="Рассылка")
    kb.button(text="Назад")
    kb.adjust(2, 1, 1)
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
    account_file = State()
    phone = State()

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
        await call.answer("Спасибо за подписку! 🎉", show_alert=True)
        try: await call.message.delete()
        except Exception: pass
        name = call.from_user.username or call.from_user.first_name or "пользователь"
        await call.message.answer(f"Привет, {name}!\nТут ты можешь приобрести все что душе угодно.", reply_markup=home_kb(call.from_user.id))
    else:
        await call.answer("Вы всё еще не подписаны на канал! ❌", show_alert=True)

# --- СТАРТ И НАВИГАЦИЯ ---
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    if not await check_sub(m.from_user.id):
        return await send_sub_request(m)

    conn = await db()
    await conn.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?)", (m.from_user.id, m.from_user.username, m.from_user.first_name, now_msk().isoformat()))
    await conn.commit(); await conn.close()
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
        flag = get_flag(p["phone"] or p["name"])
        btn_text = f"{flag}{p['name']} — {p['price_rub']:.0f}₽"
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

    flag = get_flag(prod["phone"] or prod["name"])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Купить за {prod['price_rub']:.0f}₽", callback_data=f"pay:{prod['id']}:rub")],
        [InlineKeyboardButton(text=f"Купить за {prod['price_stars']}⭐ Stars", callback_data=f"pay:{prod['id']}:stars")]
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
    amount = prod["price_rub"] if pay_type == "rub" else prod["price_stars"]

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
    pay_symbol = "₽" if pay_type == "rub" else "⭐"
    flag = get_flag(prod["phone"] or prod["name"])
    
    caption = f"📦 <b>Новая заявка на покупку #{purch_id}</b>\n\nПокупатель: {user_info}\nТовар: {flag}{prod['name']}\nСумма: {amount} {pay_symbol}"
    
    if m.photo:
        await bot.send_photo(ADMIN_ID, photo_id, caption=caption, reply_markup=adm_kb)
    else:
        await bot.send_document(ADMIN_ID, photo_id, caption=caption, reply_markup=adm_kb)

# --- ОБРАБОТКА ЗАЯВКИ АДМИНОМ ---
@dp.callback_query(F.data.startswith("adm_approve:"))
async def approve_purchase(call: CallbackQuery):
    await call.answer("Принято!")
    purch_id = int(call.data.split(":")[1])
    
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (purch_id,))
    
    if not purch or purch["status"] != "pending":
        await conn.close()
        return await call.message.edit_caption(caption=call.message.caption + "\n\n⚠️ Заявка уже обработана.")

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

# --- ВЫДАЧА И PYROGRAM СЛУШАТЕЛЬ С ОШИБКАМИ И ТАЙМЕРАМИ ---
@dp.callback_query(F.data.startswith("claim_acc:"))
async def claim_account(call: CallbackQuery):
    await call.answer()
    pid = int(call.data.split(":")[1])
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (pid,))
    
    if not purch:
        await conn.close()
        return await call.message.answer("Заявка на покупку не найдена.")
        
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (purch["product_id"],))
    await conn.close()
    
    phone_number = prod["phone"] if (prod and prod["phone"]) else "Номер не указан"
    session_file = prod["session_path"] if prod else None
    
    await call.message.answer(
        f"📱 <b>Номер аккаунта:</b> <code>{phone_number}</code>\n\n"
        f"Отправьте запрос кода авторизации в Telegram. Как только код придет, бот перешлит его сюда."
    )
    
    if session_file and os.path.exists(session_file):
        asyncio.create_task(safe_listen_for_login_code(session_file, call.from_user.id))
    else:
        await call.message.answer("⚠️ Файл `.session` для этого аккаунта не найден на сервере.")

async def safe_listen_for_login_code(session_path: str, user_id: int):
    session_name = Path(session_path).stem
    session_dir = str(Path(session_path).parent)
    
    client = Client(
        session_name, 
        api_id=API_ID, 
        api_hash=API_HASH, 
        workdir=session_dir,
        in_memory=False
    )
    
    try:
        @client.on_message(filters.me | filters.service | filters.private)
        async def code_handler(cli, message):
            if message.text:
                codes = re.findall(r'\b\d{5,6}\b', message.text)
                if codes:
                    found_code = codes[0]
                    try:
                        await bot.send_message(user_id, f"🔑 <b>Ваш код авторизации:</b> <code>{found_code}</code>")
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
            "❌ <b>Ошибка:</b> Данная сессия недействительна (аккаунт слетел или был завершен).\nПожалуйста, обратитесь в поддержку с чеком покупки."
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
        await bot.send_message(
            user_id, 
            "⚠️ Произошла ошибка при получении кода. Если код не придет в течение пары минут, обратитесь в поддержку."
        )

async def send_post_purchase_info(user_id: int):
    await asyncio.sleep(30)
    msg_1 = (
        "спасибо за покупку! 🖤\n"
        "советуем не менять юз/описание/никнейм в течении суток.\n"
        "на аккаунте возможно есть 1 человек - это бот, для выдачи кодов, он не может выкинуть вас с аккаунта, через сутки после покупки можете спокойно кикать его.\n"
        "помните! мы не отвечаем за слет аккаунта/сессий (гарантия 1 час)"
    )
    try:
        await bot.send_message(user_id, msg_1)
    except Exception:
        pass

    await asyncio.sleep(5)
    msg_2 = "Пожалуйста! оставьте отзыв с юзом @fegote (писать ему в личку)"
    try:
        await bot.send_message(user_id, msg_2)
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
    await conn.close()
    await m.answer(f"📊 <b>Статистика бота:</b>\n\n👥 Пользователей: {u_cnt}\n🛍 Покупок: {p_cnt}")

@dp.message(F.text == "Склад")
async def stock_cmd(m: Message):
    if not is_admin(m.from_user.id): return
    await stock(m)

async def stock(m):
    conn = await db()
    rows = await all_rows(conn, "SELECT * FROM products ORDER BY kind, id")
    await conn.close()
    if not rows:
        return await m.answer("Склад пуст.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить товар", callback_data="add_product")]]))
    
    kb = InlineKeyboardBuilder()
    for p in rows:
        flag = get_flag(p["phone"] or p["name"])
        kb.button(text=f"🗑 {flag}{p['name']} ({p['stock']} шт)", callback_data=f"delete_product:{p['id']}")
    kb.button(text="➕ Добавить товар", callback_data="add_product")
    kb.adjust(1)
    await m.answer("Управление складом (нажмите на товар для удаления):", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("delete_product:"))
async def delete_product_handler(call: CallbackQuery):
    if not is_admin(call.from_user.id): 
        return await call.answer("Отказано в доступе", show_alert=True)
        
    pid = int(call.data.split(":")[1])
    conn = await db()
    await conn.execute("DELETE FROM products WHERE id=?", (pid,))
    await conn.commit()
    await conn.close()
    
    await call.answer("Товар удален со склада ✅", show_alert=True)
    await stock(call.message)

# --- ДОБАВЛЕНИЕ ТОВАРА (FSM) ---
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
    await call.message.answer("1 этап — Введите название товара:")
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
    
    d = await state.get_data()
    if d.get("kind") == "account":
        await state.set_state(AddProduct.account_file)
        await m.answer("4 этап — Отправьте `.session` файл аккаунта документом:")
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
    
    await state.update_data(file_id=file_id, session_path=str(save_path))
    await state.set_state(AddProduct.phone)
    await m.answer("5 этап — Введите номер телефона (например +79991112233):")

@dp.message(AddProduct.phone)
async def add_finish(m: Message, state: FSMContext):
    d = await state.get_data()
    conn = await db()
    
    if d.get("kind") == "account":
        phone = m.text.strip()
        await conn.execute(
            "INSERT INTO products(kind, name, price_rub, price_stars, stock, sales, created_at, file_id, session_path, phone) VALUES(?,?,?,?,1,0,?,?,?,?)",
            (d["kind"], d["name"], d["price_rub"], d["price_stars"], now_msk().isoformat(), d.get("file_id"), d.get("session_path"), phone)
        )
    else:
        try: stock_cnt = int(m.text)
        except ValueError: return await m.answer("Введите целое число.")
        await conn.execute(
            "INSERT INTO products(kind, name, price_rub, price_stars, stock, sales, created_at) VALUES(?,?,?,?,?,0,?)",
            (d["kind"], d["name"], d["price_rub"], d["price_stars"], stock_cnt, now_msk().isoformat())
        )
        
    await conn.commit(); await conn.close()
    await state.clear()
    await m.answer("Товар успешно добавлен на склад ✅", reply_markup=admin_kb())

# --- РАССЫЛКА ---
@dp.message(F.text == "Рассылка")
async def broadcast_cmd(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await state.set_state(Broadcast.text)
    await m.answer("Введите текст сообщения для рассылки:")

@dp.message(Broadcast.text)
async def broadcast_finish(m: Message, state: FSMContext):
    await state.clear()
    conn = await db()
    users = await all_rows(conn, "SELECT id FROM users")
    await conn.close()
    count = 0
    for u in users:
        try:
            await bot.send_message(u["id"], m.text)
            count += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await m.answer(f"✅ Рассылка завершена. Доставлено: {count} пользователям.")

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
