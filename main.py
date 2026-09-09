import asyncio
import os
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

# Bothost automatically provides the Telegram bot token as BOT_TOKEN.
# Fallback names are supported for compatibility with Bothost configurations.
TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("API_TOKEN")
    or os.getenv("TOKEN")
)

if not TOKEN:
    raise RuntimeError("Telegram bot token was not provided by the hosting platform")

# These settings are built into the application; no manual env variables are needed.
ADMIN_ID = 8872934046
DB_PATH = "data/shop.db"
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

DEFAULT_TEXTS = {
    "welcome": "Привет, {name}\nТут ты можешь приобрести все что душе угодно.",
    "catalog": "Каталог",
    "reviews": "Отзывы",
    "support": "Поддержка",
    "admin": "Админ панель",
    "stats": "Статистика",
    "stock": "Склад",
    "broadcast": "Рассылка",
    "edit_text": "Изменить текст",
    "choose_type": "Выберите тип товара",
    "accounts": "Аккаунты",
    "stars": "Звезды",
    "change_button": "Изменить текст кнопки",
    "change_message": "Изменить текст сообщения",
}

COUNTRY_FLAGS = {
    "1": "🇺🇸", "7": "🇷🇺", "20": "🇪🇬", "27": "🇿🇦", "30": "🇬🇷", "31": "🇳🇱",
    "32": "🇧🇪", "33": "🇫🇷", "34": "🇪🇸", "36": "🇭🇺", "39": "🇮🇹", "40": "🇷🇴",
    "41": "🇨🇭", "43": "🇦🇹", "44": "🇬🇧", "45": "🇩🇰", "46": "🇸🇪", "47": "🇳🇴",
    "48": "🇵🇱", "49": "🇩🇪", "51": "🇵🇪", "52": "🇲🇽", "53": "🇨🇺", "54": "🇦🇷",
    "55": "🇧🇷", "56": "🇨🇱", "57": "🇨🇴", "58": "🇻🇪", "60": "🇲🇾", "61": "🇦🇺",
    "62": "🇮🇩", "63": "🇵🇭", "64": "🇳🇿", "65": "🇸🇬", "66": "🇹🇭", "81": "🇯🇵",
    "82": "🇰🇷", "84": "🇻🇳", "86": "🇨🇳", "90": "🇹🇷", "91": "🇮🇳", "92": "🇵🇰",
    "93": "🇦🇫", "94": "🇱🇰", "95": "🇲🇲", "98": "🇮🇷", "212": "🇲🇦", "213": "🇩🇿",
    "216": "🇹🇳", "218": "🇱🇾", "234": "🇳🇬", "254": "🇰🇪", "255": "🇹🇿", "380": "🇺🇦",
    "381": "🇷🇸", "420": "🇨🇿", "421": "🇸🇰", "423": "🇱🇮", "852": "🇭🇰", "853": "🇲🇴",
    "886": "🇹🇼", "972": "🇮🇱", "971": "🇦🇪", "995": "🇬🇪", "998": "🇺🇿",
}

class AddProduct(StatesGroup):
    kind = State()
    name = State()
    price_rub = State()
    price_stars = State()
    stock = State()

class Broadcast(StatesGroup):
    text = State()

class EditText(StatesGroup):
    key = State()
    value = State()

class Payment(StatesGroup):
    screenshot = State()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def db():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn

async def init_db():
    conn = await db()
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS texts (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        price_rub REAL NOT NULL,
        price_stars INTEGER NOT NULL,
        stock INTEGER NOT NULL DEFAULT 0,
        sales INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        payment TEXT NOT NULL,
        amount REAL NOT NULL,
        screenshot_file_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL
    );
    """)
    for key, value in DEFAULT_TEXTS.items():
        await conn.execute("INSERT OR IGNORE INTO texts(key,value) VALUES(?,?)", (key, value))
    await conn.commit()
    await conn.close()

async def text(key: str) -> str:
    conn = await db()
    row = await conn.execute_fetchone("SELECT value FROM texts WHERE key=?", (key,))
    await conn.close()
    return row[0] if row else DEFAULT_TEXTS.get(key, key)

async def save_user(message: Message):
    user = message.from_user
    conn = await db()
    await conn.execute(
        "INSERT INTO users(id,username,first_name,created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
        (user.id, user.username, user.first_name, datetime.now().isoformat()),
    )
    await conn.commit()
    await conn.close()

async def home_keyboard(user_id: int):
    kb = ReplyKeyboardBuilder()
    kb.button(text=await text("catalog"))
    kb.adjust(1)
    kb.row(
        ReplyKeyboardBuilder().button(text=await text("reviews")).buttons[0],
        ReplyKeyboardBuilder().button(text=await text("support")).buttons[0],
    )
    if is_admin(user_id):
        kb.button(text=await text("admin"))
        kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await save_user(message)
    name = message.from_user.username or message.from_user.first_name or "пользователь"
    await message.answer((await text("welcome")).format(name=name), reply_markup=await home_keyboard(message.from_user.id))

@dp.message(Command("adm"))
async def adm(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Админ панель", reply_markup=admin_keyboard())

def admin_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text=DEFAULT_TEXTS["stats"])
    kb.button(text=DEFAULT_TEXTS["stock"])
    kb.button(text=DEFAULT_TEXTS["broadcast"])
    kb.button(text=DEFAULT_TEXTS["edit_text"])
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)

async def catalog_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text=await text("accounts"))
    kb.button(text=await text("stars"))
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

@dp.message(F.text)
async def text_router(message: Message, state: FSMContext):
    await save_user(message)
    value = message.text
    if value == await text("catalog"):
        await message.answer(await text("choose_type"), reply_markup=await catalog_keyboard())
    elif value == await text("reviews"):
        await message.answer("Отзывы пока пусты.")
    elif value == await text("support"):
        await message.answer("По всем вопросам напишите администратору.")
    elif is_admin(message.from_user.id) and value == await text("admin"):
        await message.answer("Админ панель", reply_markup=admin_keyboard())
    elif is_admin(message.from_user.id) and value == DEFAULT_TEXTS["stats"]:
        await show_stats(message)
    elif is_admin(message.from_user.id) and value == DEFAULT_TEXTS["stock"]:
        await show_stock(message)
    elif is_admin(message.from_user.id) and value == DEFAULT_TEXTS["broadcast"]:
        await state.set_state(Broadcast.text)
        await message.answer("Отправьте текст рассылки.")
    elif is_admin(message.from_user.id) and value == DEFAULT_TEXTS["edit_text"]:
        await message.answer("Что изменить?", reply_markup=edit_keyboard())
    elif is_admin(message.from_user.id) and value == DEFAULT_TEXTS["accounts"]:
        await show_products(message, "account")
    elif is_admin(message.from_user.id) and value == DEFAULT_TEXTS["stars"]:
        await show_products(message, "stars")
    elif value == await text("accounts"):
        await show_products(message, "account")
    elif value == await text("stars"):
        await show_products(message, "stars")

def edit_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text=DEFAULT_TEXTS["change_button"])
    kb.button(text=DEFAULT_TEXTS["change_message"])
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

async def show_products(message: Message, kind: str):
    conn = await db()
    rows = await conn.execute_fetchall("SELECT * FROM products WHERE kind=? AND stock>0 ORDER BY id", (kind,))
    await conn.close()
    if not rows:
        await message.answer("Товаров в наличии нет.")
        return
    kb = InlineKeyboardBuilder()
    for p in rows:
        if kind == "account":
            code = p["name"].lstrip("+").split()[0]
            flag = ""
            for length in (3, 2, 1):
                flag = COUNTRY_FLAGS.get(code[:length], "")
                if flag:
                    break
            label = f"+{code} {flag} {p['price_rub']:.0f}₽"
        else:
            label = f"{p['name']} / {p['price_rub']:.0f}₽"
        kb.button(text=label, callback_data=f"product:{p['id']}")
    kb.adjust(1)
    await message.answer("Выберите товар", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("product:"))
async def product_view(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split(":")[1])
    conn = await db()
    p = await conn.execute_fetchone("SELECT * FROM products WHERE id=? AND stock>0", (product_id,))
    await conn.close()
    if not p:
        await call.answer("Товар закончился", show_alert=True)
        return
    await state.update_data(product_id=product_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Звезды", callback_data="pay:stars")],
        [InlineKeyboardButton(text="₽ рубли", callback_data="pay:rub")],
    ])
    await call.message.answer(f"Выбранный аккаунт: {p['name']}\nЦена: {p['price_rub']:.0f}₽ / {p['price_stars']} Stars", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "pay:stars")
async def pay_stars(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    conn = await db()
    p = await conn.execute_fetchone("SELECT * FROM products WHERE id=? AND stock>0", (data.get("product_id"),))
    await conn.close()
    if not p:
        await call.answer("Товар закончился", show_alert=True)
        return
    await state.update_data(payment="stars", amount=p["price_stars"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="отправить подтверждение", callback_data="confirm_payment")]])
    await call.message.answer(f"отправьте {p['price_stars']} звезд на аккаунт @fegote", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "pay:rub")
async def pay_rub(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    conn = await db()
    p = await conn.execute_fetchone("SELECT * FROM products WHERE id=? AND stock>0", (data.get("product_id"),))
    await conn.close()
    if not p:
        await call.answer("Товар закончился", show_alert=True)
        return
    await state.update_data(payment="rub", amount=p["price_rub"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="отправить подтверждение", callback_data="confirm_payment")]])
    await call.message.answer(f"переведите {p['price_rub']:.0f}₽ на реквизиты:\n+79313716777\n• Т-банк\n• Тимур/Наталья", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "confirm_payment")
async def confirm_payment(call: CallbackQuery, state: FSMContext):
    await state.set_state(Payment.screenshot)
    await call.message.answer("Отправьте скрин оплаты одним сообщением.")
    await call.answer()

@dp.message(Payment.screenshot, F.photo)
async def payment_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_id")
    conn = await db()
    p = await conn.execute_fetchone("SELECT * FROM products WHERE id=?", (product_id,))
    if not p:
        await conn.close()
        await state.clear()
        return
    now = datetime.now()
    cur = await conn.execute(
        "INSERT INTO purchases(user_id,product_id,product_name,payment,amount,screenshot_file_id,created_at) VALUES(?,?,?,?,?,?,?)",
        (message.from_user.id, product_id, p["name"], data.get("payment", ""), data.get("amount", 0), message.photo[-1].file_id, now.isoformat()),
    )
    purchase_id = cur.lastrowid
    await conn.commit()
    await conn.close()
    username = f"@{message.from_user.username}" if message.from_user.username else "нет"
    caption = (f"Новая покупка! 🧾\nкупили: {p['name']}\nайди: {message.from_user.id}\n"
               f"юзернейм: {username}\nцена: {data.get('amount')} { 'Stars' if data.get('payment') == 'stars' else '₽'}\n"
               f"время покупки по мск: {now.strftime('%d.%m.%Y %H:%M')}\nзаявка: #{purchase_id}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одобрить", callback_data=f"purchase:approve:{purchase_id}"),
         InlineKeyboardButton(text="Отклонить", callback_data=f"purchase:reject:{purchase_id}")]
    ])
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption, reply_markup=kb)
    await message.answer("Заявка отправлена администратору. Ожидайте подтверждения.")
    await state.clear()

@dp.message(Payment.screenshot)
async def payment_not_photo(message: Message):
    await message.answer("Нужен именно скрин оплаты в виде фотографии.")

@dp.callback_query(F.data.startswith("purchase:"))
async def purchase_action(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    _, action, purchase_id = call.data.split(":")
    purchase_id = int(purchase_id)
    conn = await db()
    purchase = await conn.execute_fetchone("SELECT * FROM purchases WHERE id=?", (purchase_id,))
    if not purchase or purchase["status"] != "pending":
        await conn.close()
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    if action == "approve":
        await conn.execute("UPDATE purchases SET status='approved' WHERE id=?", (purchase_id,))
        await conn.execute("UPDATE products SET stock=stock-1, sales=sales+1 WHERE id=? AND stock>0", (purchase["product_id"],))
        status_text = "Одобрено"
    else:
        await conn.execute("UPDATE purchases SET status='rejected' WHERE id=?", (purchase_id,))
        status_text = "Отклонено"
    await conn.commit()
    await conn.close()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"Заявка #{purchase_id}: {status_text}")
    try:
        await bot.send_message(purchase["user_id"], f"Ваша заявка #{purchase_id}: {status_text}.")
    except Exception:
        pass
    await call.answer()

async def show_stats(message: Message):
    conn = await db()
    total = await conn.execute_fetchone("SELECT COUNT(*) FROM purchases WHERE status='approved'")
    rub = await conn.execute_fetchone("SELECT COALESCE(SUM(amount),0) FROM purchases WHERE status='approved' AND payment='rub'")
    stars = await conn.execute_fetchone("SELECT COALESCE(SUM(amount),0) FROM purchases WHERE status='approved' AND payment='stars'")
    today = datetime.now().date().isoformat()
    today_rub = await conn.execute_fetchone("SELECT COALESCE(SUM(amount),0) FROM purchases WHERE status='approved' AND payment='rub' AND date(created_at)=?", (today,))
    today_stars = await conn.execute_fetchone("SELECT COALESCE(SUM(amount),0) FROM purchases WHERE status='approved' AND payment='stars' AND date(created_at)=?", (today,))
    users = await conn.execute_fetchone("SELECT COUNT(*) FROM users")
    best = await conn.execute_fetchall("SELECT product_name, COUNT(*) c FROM purchases WHERE status='approved' GROUP BY product_id ORDER BY c DESC LIMIT 5")
    await conn.close()
    best_text = "\n".join(f"{i+1}. {r['product_name']} — {r['c']}" for i, r in enumerate(best)) or "нет"
    await message.answer(
        f"Всего продаж: {total[0]}\nЗаработано ₽: {rub[0]:.0f}\nЗаработано Stars: {stars[0]:.0f}\n"
        f"Заработано за сегодня: {today_rub[0]:.0f}₽ / {today_stars[0]:.0f} Stars\n"
        f"Количество пользователей: {users[0]}\n\nСамые продаваемые товары:\n{best_text}"
    )

async def show_stock(message: Message):
    conn = await db()
    rows = await conn.execute_fetchall("SELECT * FROM products ORDER BY kind,id")
    await conn.close()
    if not rows:
        await message.answer("Склад пуст.", reply_markup=add_product_keyboard())
        return
    lines = []
    for p in rows:
        if p["kind"] == "account":
            lines.append(f"{p['name']} {p['price_rub']:.0f}₽/{p['price_stars']}звезд {p['stock']} шт")
        else:
            lines.append(f"{p['name']} / {p['price_rub']:.0f}₽ — {p['stock']} шт")
    await message.answer("\n".join(lines), reply_markup=add_product_keyboard())

def add_product_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Добавить товар", callback_data="add_product")]])

@dp.callback_query(F.data == "add_product")
async def add_product_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(AddProduct.kind)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Аккаунт", callback_data="addkind:account")],
        [InlineKeyboardButton(text="Stars", callback_data="addkind:stars")]
    ])
    await call.message.answer("Выберите тип товара", reply_markup=kb)
    await call.answer()

@dp.callback_query(AddProduct.kind, F.data.startswith("addkind:"))
async def add_kind(call: CallbackQuery, state: FSMContext):
    kind = call.data.split(":")[1]
    await state.update_data(kind=kind)
    await state.set_state(AddProduct.name)
    await call.message.answer("Введите название товара. Для аккаунта можно написать, например: +1")
    await call.answer()

@dp.message(AddProduct.name)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddProduct.price_rub)
    await message.answer("Введите цену в рублях.")

@dp.message(AddProduct.price_rub)
async def add_rub(message: Message, state: FSMContext):
    try: price = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Введите число, например 50")
        return
    await state.update_data(price_rub=price)
    await state.set_state(AddProduct.price_stars)
    await message.answer("Введите цену в Stars.")

@dp.message(AddProduct.price_stars)
async def add_stars(message: Message, state: FSMContext):
    try: price = int(message.text)
    except ValueError:
        await message.answer("Введите целое число, например 60")
        return
    await state.update_data(price_stars=price)
    await state.set_state(AddProduct.stock)
    await message.answer("Введите количество на складе.")

@dp.message(AddProduct.stock)
async def add_stock(message: Message, state: FSMContext):
    try: stock = int(message.text)
    except ValueError:
        await message.answer("Введите целое число.")
        return
    data = await state.get_data()
    conn = await db()
    await conn.execute("INSERT INTO products(kind,name,price_rub,price_stars,stock,sales,created_at) VALUES(?,?,?,?,?,0,?)",
                       (data["kind"], data["name"], data["price_rub"], data["price_stars"], stock, datetime.now().isoformat()))
    await conn.commit()
    await conn.close()
    await state.clear()
    await message.answer("Товар добавлен на склад.", reply_markup=admin_keyboard())

@dp.message(Broadcast.text)
async def do_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    conn = await db()
    users = await conn.execute_fetchall("SELECT id FROM users")
    await conn.close()
    sent = 0
    for user in users:
        try:
            await bot.send_message(user["id"], message.text)
            sent += 1
        except Exception:
            pass
    await state.clear()
    await message.answer(f"Рассылка завершена. Отправлено: {sent}", reply_markup=admin_keyboard())

@dp.message(F.text == DEFAULT_TEXTS["change_button"])
async def change_button(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(edit_mode="button")
    await state.set_state(EditText.key)
    await message.answer("Введите ключ кнопки: catalog, reviews, support, admin, stats, stock, broadcast, edit_text, accounts или stars")

@dp.message(F.text == DEFAULT_TEXTS["change_message"])
async def change_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(edit_mode="message")
    await state.set_state(EditText.key)
    await message.answer("Введите ключ сообщения: welcome, choose_type или другой ключ из списка")

@dp.message(EditText.key)
async def edit_key(message: Message, state: FSMContext):
    key = message.text.strip()
    if key not in DEFAULT_TEXTS:
        await message.answer("Такого ключа нет. Попробуйте ещё раз.")
        return
    await state.update_data(key=key)
    await state.set_state(EditText.value)
    await message.answer("Введите новый текст.")

@dp.message(EditText.value)
async def edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = await db()
    await conn.execute("INSERT INTO texts(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (data["key"], message.text))
    await conn.commit()
    await conn.close()
    await state.clear()
    await message.answer("Текст изменён. Изменение применяется сразу.", reply_markup=admin_keyboard())

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
