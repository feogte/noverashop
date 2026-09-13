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
from aiogram.filters import Command, CommandStart, StateFilter
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
SUB_CHANNEL_ID = -1003922108499
DB_PATH = os.getenv("DB_PATH", "data/shop.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path("sessions").mkdir(parents=True, exist_ok=True)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
MSK = ZoneInfo("Europe/Moscow")

TEXTS = {
    "welcome": "Привет, {name}\nТут ты можешь приобрести все что душе угодно.",
    "catalog": "Каталог", "reviews": "Отзывы", "support": "Поддержка",
    "admin": "Админ панель", "stats": "Статистика", "stock": "Склад",
    "broadcast": "Рассылка", "edit_text": "Изменить текст",
    "choose_type": "Выберите тип товара", "accounts": "Аккаунты", "stars": "Звезды",
    "change_button": "Изменить текст кнопки", "change_message": "Изменить текст сообщения",
    "referral": "🎁 Рефералка",
}

class AddProduct(StatesGroup):
    kind = State()
    name = State()
    price_rub = State()
    price_stars = State()
    account_file = State()
    phone = State()

class Broadcast(StatesGroup):
    text = State()
class EditText(StatesGroup):
    key = State(); value = State()
class Payment(StatesGroup):
    screenshot = State()

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

async def text(key):
    conn = await db(); row = await one(conn, "SELECT value FROM texts WHERE key=?", (key,)); await conn.close()
    return row["value"] if row else TEXTS.get(key, key)

async def init_db():
    conn = await db()
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS texts(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, price_rub REAL NOT NULL, price_stars INTEGER NOT NULL, stock INTEGER NOT NULL DEFAULT 0, sales INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, product_name TEXT NOT NULL, payment TEXT NOT NULL, amount REAL NOT NULL, screenshot_file_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
    """)
    
    for col_def in ["file_id TEXT", "phone TEXT", "session_path TEXT"]:
        try: await conn.execute(f"ALTER TABLE products ADD COLUMN {col_def}")
        except Exception: pass

    try: await conn.execute("ALTER TABLE purchases ADD COLUMN account_id INTEGER")
    except Exception: pass

    for k, v in TEXTS.items():
        await conn.execute("INSERT OR IGNORE INTO texts(key,value) VALUES(?,?)", (k, v))
    await conn.commit(); await conn.close()

def home_kb(uid):
    kb = ReplyKeyboardBuilder()
    [kb.button(text=TEXTS[k]) for k in ("catalog", "reviews", "support", "referral")]
    if is_admin(uid): kb.button(text=TEXTS["admin"])
    kb.adjust(1, 2, 1, 1, 1); return kb.as_markup(resize_keyboard=True)

def admin_kb():
    kb = ReplyKeyboardBuilder()
    [kb.button(text=TEXTS[k]) for k in ("stats", "stock", "broadcast", "edit_text")]
    kb.button(text="Назад"); kb.adjust(2, 2, 1); return kb.as_markup(resize_keyboard=True)

def catalog_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text=TEXTS["accounts"]); kb.button(text=TEXTS["stars"]); kb.button(text="Назад")
    kb.adjust(2, 1); return kb.as_markup(resize_keyboard=True)

def inline_back(callback_data): return InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)

# Сброс состояния при /start
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    name = m.from_user.username or m.from_user.first_name or "пользователь"
    await m.answer((await text("welcome")).format(name=name), reply_markup=home_kb(m.from_user.id))

# Сброс состояния по кнопке Назад
@dp.message(F.text == "Назад")
async def back_home(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Главное меню", reply_markup=home_kb(m.from_user.id))

# Маршрутизатор по кнопкам
@dp.message(F.text)
async def router(m: Message, state: FSMContext):
    v = m.text
    if v == await text("catalog"): 
        await state.clear()
        await m.answer(await text("choose_type"), reply_markup=catalog_kb())
    elif v == await text("reviews"): 
        await m.answer("Отзывы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть отзывы", url="https://t.me/repacrisov")]]))
    elif v == await text("support"): 
        await m.answer("Поддержка:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать в поддержку", url="https://t.me/fegote")]]))
    elif v == await text("referral"):
        await m.answer("Раздел на тех работах\n\nНовости — @noverashop", reply_markup=home_kb(m.from_user.id))
    elif is_admin(m.from_user.id) and v == await text("admin"): 
        await state.clear()
        await m.answer("Админ панель", reply_markup=admin_kb())
    elif is_admin(m.from_user.id) and v == TEXTS["stock"]:
        await stock(m)
    elif v == await text("accounts"): 
        await products(m, "account")
    elif v == await text("stars"): 
        await products(m, "stars")

async def stock(m):
    conn = await db()
    rows = await all_rows(conn, "SELECT * FROM products ORDER BY kind, id")
    await conn.close()
    if not rows:
        return await m.answer("Склад пуст.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Добавить товар", callback_data="add_product")]]))
    kb = InlineKeyboardBuilder()
    for p in rows:
        kb.button(text=f"🗑 {p['name']} ({p['stock']} шт)", callback_data=f"delete_product:{p['id']}")
    kb.button(text="➕ Добавить товар", callback_data="add_product")
    kb.adjust(1)
    await m.answer("Управление складом:", reply_markup=kb.as_markup())

async def products(m, kind):
    conn = await db()
    rows = await all_rows(conn, "SELECT * FROM products WHERE kind=? AND stock>0 ORDER BY id", (kind,))
    await conn.close()
    if not rows: return await m.answer("Товаров в наличии нет.", reply_markup=catalog_kb())
    kb = InlineKeyboardBuilder()
    for p in rows: 
        kb.button(text=f"{p['name']} — {p['price_rub']:.0f}₽ / {p['price_stars']}⭐", callback_data=f"product:{p['id']}")
    kb.adjust(1)
    await m.answer("Выберите товар:", reply_markup=kb.as_markup())

# --- ДОБАВЛЕНИЕ АККАУНТА ---
@dp.callback_query(F.data == "add_product")
async def add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(AddProduct.kind)
    await call.message.answer("Выберите тип товара", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Аккаунт", callback_data="addkind:account")],
        [InlineKeyboardButton(text="Stars", callback_data="addkind:stars")]
    ]))
    await call.answer()

@dp.callback_query(AddProduct.kind, F.data.startswith("addkind:"))
async def add_kind(call: CallbackQuery, state: FSMContext):
    await state.update_data(kind=call.data.split(":")[1])
    await state.set_state(AddProduct.name)
    await call.message.answer("1 этап — Название товара:")
    await call.answer()

@dp.message(AddProduct.name)
async def add_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProduct.price_rub)
    await m.answer("2 этап — Цена в ₽:")

@dp.message(AddProduct.price_rub)
async def add_rub(m: Message, state: FSMContext):
    try: price = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите число, например 50")
    await state.update_data(price_rub=price)
    await state.set_state(AddProduct.price_stars)
    await m.answer("3 этап — Цена в звездах:")

@dp.message(AddProduct.price_stars)
async def add_stars(m: Message, state: FSMContext):
    try: price = int(m.text)
    except ValueError: return await m.answer("Введите целое число, например 60")
    await state.update_data(price_stars=price)
    
    d = await state.get_data()
    if d.get("kind") == "account":
        await state.set_state(AddProduct.account_file)
        await m.answer("4 этап — отправьте файл аккаунта (Pyrogram)")
    else:
        await state.set_state(AddProduct.phone)
        await m.answer("Введите количество товара:")

@dp.message(AddProduct.account_file, F.document)
async def add_account_file(m: Message, state: FSMContext):
    file_id = m.document.file_id
    file_name = m.document.file_name or f"session_{secrets.token_hex(4)}.session"
    save_path = Path("sessions") / file_name
    
    file_info = await bot.get_file(file_id)
    await bot.download_file(file_info.file_path, save_path)
    
    await state.update_data(file_id=file_id, session_path=str(save_path))
    await state.set_state(AddProduct.phone)
    await m.answer("Введите номер телефона этого аккаунта (например +79991112233):")

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
    await m.answer("Товар добавлен на склад ✅", reply_markup=admin_kb())

# --- ВЫДАЧА И СЛУШАТЕЛЬ ---
@dp.callback_query(F.data.startswith("claim_acc:"))
async def claim_account(call: CallbackQuery):
    await call.answer()
    pid = int(call.data.split(":")[1])
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (pid,))
    if not purch:
        await conn.close(); return
        
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (purch["product_id"],))
    await conn.close()
    
    phone_number = prod["phone"] if prod and prod["phone"] else "+70000000000"
    session_file = prod["session_path"] if prod else None
    
    await call.message.answer(f"{phone_number}\nкогда вы отправите код на аккаунт , мы пришлем его вам")
    
    if session_file and os.path.exists(session_file):
        asyncio.create_task(listen_for_login_code(session_file, call.from_user.id))

async def listen_for_login_code(session_path: str, user_id: int):
    session_name = Path(session_path).stem
    client = Client(session_name, api_id=API_ID, api_hash=API_HASH, workdir=str(Path(session_path).parent))
    
    @client.on_message(filters.me | filters.service | filters.private)
    async def code_handler(cli, message):
        if message.text:
            codes = re.findall(r'\b\d{5,6}\b', message.text)
            if codes:
                found_code = codes[0]
                try:
                    await bot.send_message(user_id, f"Ваш код - {found_code}")
                except Exception: pass
                await cli.stop()

    try:
        await client.start()
        await asyncio.sleep(600)
        await client.stop()
    except Exception: pass

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
