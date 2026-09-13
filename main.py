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

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "12345"))
API_HASH = os.getenv("API_HASH", "your_hash")

if not TOKEN:
    raise RuntimeError("Telegram bot token was not provided")

ADMIN_ID = int(os.getenv("ADMIN_ID", "8872934046"))
DB_PATH = os.getenv("DB_PATH", "data/shop.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path("sessions").mkdir(parents=True, exist_ok=True)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
MSK = ZoneInfo("Europe/Moscow")

def is_admin(uid): return uid == ADMIN_ID
def now_msk(): return datetime.now(MSK)

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
    CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, product_name TEXT NOT NULL, payment TEXT NOT NULL, amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'approved', created_at TEXT NOT NULL);
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

# --- СТАРТ И НАВИГАЦИЯ ---
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
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
    await m.answer("Выберите тип товара:", reply_markup=catalog_kb())

@dp.message(F.text == "Отзывы")
async def open_reviews(m: Message):
    await m.answer("Отзывы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть отзывы", url="https://t.me/repacrisov")]]))

@dp.message(F.text == "Поддержка")
async def open_support(m: Message):
    await m.answer("Поддержка:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать в поддержку", url="https://t.me/fegote")]]))

@dp.message(F.text == "🎁 Рефералка")
async def open_ref(m: Message):
    await m.answer("Раздел на тех работах\n\nНовости — @noverashop", reply_markup=home_kb(m.from_user.id))

@dp.message(F.text == "Аккаунты")
async def show_accounts(m: Message):
    await products(m, "account")

@dp.message(F.text == "Звезды")
async def show_stars(m: Message):
    await products(m, "stars")

async def products(m: Message, kind: str):
    conn = await db()
    rows = await all_rows(conn, "SELECT * FROM products WHERE kind=? AND stock>0 ORDER BY id", (kind,))
    await conn.close()
    if not rows: 
        return await m.answer("Товаров в наличии нет.", reply_markup=catalog_kb())
    
    kb = InlineKeyboardBuilder()
    for p in rows: 
        kb.button(text=f"{p['name']} — {p['price_rub']:.0f}₽ / {p['price_stars']}⭐", callback_data=f"product:{p['id']}")
    kb.adjust(1)
    await m.answer("Выберите товар для покупки:", reply_markup=kb.as_markup())

# --- ПОКУПКА ТОВАРА (ОБРАБОТКА КНОПОК) ---
@dp.callback_query(F.data.startswith("product:"))
async def select_product(call: CallbackQuery):
    await call.answer()
    pid = int(call.data.split(":")[1])
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (pid,))
    await conn.close()

    if not prod or prod["stock"] <= 0:
        return await call.message.answer("К сожалению, этого товара уже нет в наличии.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Купить за {prod['price_rub']:.0f}₽", callback_data=f"buy:{prod['id']}:rub")],
        [InlineKeyboardButton(text=f"Купить за {prod['price_stars']}⭐ Stars", callback_data=f"buy:{prod['id']}:stars")]
    ])
    await call.message.answer(f"🛒 <b>Покупка: {prod['name']}</b>\n\nВыберите способ оплаты:", reply_markup=kb)

@dp.callback_query(F.data.startswith("buy:"))
async def process_buy(call: CallbackQuery):
    await call.answer()
    _, pid_str, pay_type = call.data.split(":")
    pid = int(pid_str)
    
    conn = await db()
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (pid,))
    if not prod or prod["stock"] <= 0:
        await conn.close()
        return await call.message.answer("Товар уже распродан.")

    amount = prod["price_rub"] if pay_type == "rub" else prod["price_stars"]
    
    # Создаем запись покупки
    cur = await conn.execute(
        "INSERT INTO purchases(user_id, product_id, product_name, payment, amount, status, created_at) VALUES(?,?,?,?,?,'approved',?)",
        (call.from_user.id, prod["id"], prod["name"], pay_type, amount, now_msk().isoformat())
    )
    purch_id = cur.lastrowid
    
    # Уменьшаем количество на складе
    await conn.execute("UPDATE products SET stock = stock - 1, sales = sales + 1 WHERE id=?", (pid,))
    await conn.commit()
    await conn.close()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Получить аккаунт / Код", callback_data=f"claim_acc:{purch_id}")]
    ])
    
    await call.message.answer(f"✅ <b>Оплата прошла успешно!</b>\nПокупка #{purch_id} оформлена.", reply_markup=kb)

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
        kb.button(text=f"🗑 {p['name']} ({p['stock']} шт)", callback_data=f"delete_product:{p['id']}")
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

# --- ВЫДАЧА И PYROGRAM СЛУШАТЕЛЬ ---
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
    
    try:
        client = Client(
            session_name, 
            api_id=API_ID, 
            api_hash=API_HASH, 
            workdir=session_dir,
            in_memory=False
        )
        
        @client.on_message(filters.me | filters.service | filters.private)
        async def code_handler(cli, message):
            if message.text:
                codes = re.findall(r'\b\d{5,6}\b', message.text)
                if codes:
                    found_code = codes[0]
                    try:
                        await bot.send_message(user_id, f"🔑 <b>Ваш код авторизации:</b> <code>{found_code}</code>")
                    except Exception: 
                        pass
                    await cli.stop()

        await client.start()
        await asyncio.sleep(600)
        if client.is_connected:
            await client.stop()
    except Exception as e:
        print(f"Ошибка Pyrogram сессии {session_path}: {e}")

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
