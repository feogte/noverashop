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
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

# Pyrogram для работы с файлами аккаунтов и перехвата кода
from pyrogram import Client, filters

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "12345"))      # API_ID от my.telegram.org
API_HASH = os.getenv("API_HASH", "your_hash")   # API_HASH от my.telegram.org

if not TOKEN:
    raise RuntimeError("Telegram bot token was not provided by the hosting platform")

ADMIN_ID = 8872934046
SUB_CHANNEL_ID = -1003922108499
DB_PATH = "data/shop.db"
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path("sessions").mkdir(parents=True, exist_ok=True)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
MSK = ZoneInfo("Europe/Moscow")
SUB_INVITE_LINK = ""
REFERRAL_GOAL = 20

# Глобальный словарь активных клиентов Pyrogram для перехвата кода
ACTIVE_CLIENTS = {}

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
    CREATE TABLE IF NOT EXISTS referral_links(id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL UNIQUE, code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS referral_joins(id INTEGER PRIMARY KEY AUTOINCREMENT, referral_id INTEGER NOT NULL, invited_user_id INTEGER NOT NULL UNIQUE, verified_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS referral_claims(id INTEGER PRIMARY KEY AUTOINCREMENT, referral_id INTEGER NOT NULL UNIQUE, owner_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pending_referrals(user_id INTEGER PRIMARY KEY, referral_code TEXT NOT NULL, created_at TEXT NOT NULL);
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
    kb=ReplyKeyboardBuilder(); [kb.button(text=TEXTS[k]) for k in ("catalog","reviews","support","referral")]
    if is_admin(uid): kb.button(text=TEXTS["admin"])
    kb.adjust(1,2,1,1,1); return kb.as_markup(resize_keyboard=True)

def admin_kb():
    kb=ReplyKeyboardBuilder(); [kb.button(text=TEXTS[k]) for k in ("stats","stock","broadcast","edit_text")]; kb.button(text="🎁 Реферальные ссылки"); kb.button(text="Назад"); kb.adjust(2,2,1,1,1); return kb.as_markup(resize_keyboard=True)

def catalog_kb():
    kb=ReplyKeyboardBuilder(); kb.button(text=TEXTS["accounts"]); kb.button(text=TEXTS["stars"]); kb.button(text="Назад"); kb.adjust(2,1); return kb.as_markup(resize_keyboard=True)

def inline_back(callback_data): return InlineKeyboardButton(text="⬅️ Назад",callback_data=callback_data)

@dp.message(CommandStart())
async def start(m,state):
    await state.clear(); name=m.from_user.username or m.from_user.first_name or "пользователь"; await m.answer((await text("welcome")).format(name=name),reply_markup=home_kb(m.from_user.id))

# --- ДОБАВЛЕНИЕ АККАУНТА (1 -> 2 -> 3 -> 4 ЭТАПЫ) ---
@dp.callback_query(F.data=="add_product")
async def add_start(call,state):
    if not is_admin(call.from_user.id): return
    await state.set_state(AddProduct.kind)
    await call.message.answer("Выберите тип товара", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Аккаунт", callback_data="addkind:account")],
        [InlineKeyboardButton(text="Stars", callback_data="addkind:stars")],
        [inline_back("back:admin")]
    ]))
    await call.answer()

@dp.callback_query(AddProduct.kind, F.data.startswith("addkind:"))
async def add_kind(call, state):
    await state.update_data(kind=call.data.split(":")[1])
    await state.set_state(AddProduct.name)
    await call.message.answer("1 этап — Название товара (остается как есть):")
    await call.answer()

@dp.message(AddProduct.name)
async def add_name(m, state):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProduct.price_rub)
    await m.answer("2 этап — Цена в ₽ (остается как есть):")

@dp.message(AddProduct.price_rub)
async def add_rub(m, state):
    try: price = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите число, например 50")
    await state.update_data(price_rub=price)
    await state.set_state(AddProduct.price_stars)
    await m.answer("3 этап — Цена в звездах (остается как есть):")

@dp.message(AddProduct.price_stars)
async def add_stars(m, state):
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
async def add_account_file(m, state):
    file_id = m.document.file_id
    file_name = m.document.file_name or f"session_{secrets.token_hex(4)}.session"
    save_path = Path("sessions") / file_name
    
    # Загружаем файл на сервер
    file_info = await bot.get_file(file_id)
    await bot.download_file(file_info.file_path, save_path)
    
    await state.update_data(file_id=file_id, session_path=str(save_path))
    await state.set_state(AddProduct.phone)
    await m.answer("Введите номер телефона этого аккаунта (например +79991112233):")

@dp.message(AddProduct.phone)
async def add_finish(m, state):
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

# --- ОДОБРЕНИЕ ЗАЯВКИ И ОЖИДАНИЕ КЛИКА «Забрать аккаунт» ---
@dp.callback_query(F.data.startswith("purchase:"))
async def purchase_action(call):
    if not is_admin(call.from_user.id): return
    _, action, pid = call.data.split(":"); pid = int(pid)
    conn = await db()
    p = await one(conn, "SELECT * FROM purchases WHERE id=?", (pid,))
    if not p or p["status"] != "pending":
        await conn.close()
        return await call.answer("Заявка уже обработана", show_alert=True)
        
    if action == "approve":
        cur = await conn.execute("UPDATE products SET stock=stock-1, sales=sales+1 WHERE id=? AND stock>0", (p["product_id"],))
        if cur.rowcount == 0:
            await cur.close(); await conn.close()
            return await call.answer("Товар уже закончился", show_alert=True)
        await cur.close()
        await conn.execute("UPDATE purchases SET status='approved' WHERE id=?", (pid,))
        await conn.commit(); await conn.close()
        
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(f"Заявка #{pid}: Одобрено")
        
        # Точный текст из ТЗ при одобрении админом
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Забрать аккаунт", callback_data=f"claim_acc:{pid}")]])
        try:
            await bot.send_message(
                p["user_id"],
                f"Ваша заявка #{pid} одобрена:\nчто бы забрать аккаунт нажмите на кнопку ниже",
                reply_markup=kb
            )
        except Exception: pass
    else:
        await conn.execute("UPDATE purchases SET status='rejected' WHERE id=?", (pid,))
        await conn.commit(); await conn.close()
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(f"Заявка #{pid}: Отклонено")
    await call.answer()

# --- КЛИК «Забрать аккаунт» + ФОНОВЫЙ PYROGRAM СЛУШАТЕЛЬ КОДА ---
@dp.callback_query(F.data.startswith("claim_acc:"))
async def claim_account(call):
    pid = int(call.data.split(":")[1])
    conn = await db()
    purch = await one(conn, "SELECT * FROM purchases WHERE id=?", (pid,))
    if not purch or purch["user_id"] != call.from_user.id:
        await conn.close()
        return await call.answer("Заявка не найдена", show_alert=True)
        
    prod = await one(conn, "SELECT * FROM products WHERE id=?", (purch["product_id"],))
    await conn.close()
    
    phone_number = prod["phone"] if prod and prod["phone"] else "+70000000000"
    session_file = prod["session_path"] if prod else None
    
    # Отправляем сообщение по ТЗ
    await call.message.answer(f"{phone_number}\nкогда вы отправите код на аккаунт , мы пришлем его вам")
    await call.answer()
    
    # Запуск перехватчика Pyrogram в фоновом режиме (если файл сессии прикреплен)
    if session_file and os.path.exists(session_file):
        asyncio.create_task(listen_for_login_code(session_file, call.from_user.id))

async def listen_for_login_code(session_path: str, user_id: int):
    """Фоновый клиент Pyrogram для слушания входящих СМС/Telegram кодов."""
    session_name = Path(session_path).stem
    client = Client(session_name, api_id=API_ID, api_hash=API_HASH, workdir=str(Path(session_path).parent))
    
    @client.on_message(filters.me | filters.service | filters.private)
    async def code_handler(cli, message):
        # Ищем 5-6 значный код в теле сообщения (или от сервисного контакта Telegram 77700)
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
        # Слушаем входящие сообщения в течение 10 минут, затем отключаемся
        await asyncio.sleep(600)
        await client.stop()
    except Exception:
        pass

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
