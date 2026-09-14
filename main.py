import asyncio
import os
import sqlite3
import datetime
import logging
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile
)

# =====================================================================
# ⚙️ БЛОК НАСТРОЕК (ЗАПОЛНИТЕ ЭТИ ДАННЫЕ)
# =====================================================================
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"            # Токен от @BotFather
ADMIN_IDS = [8872934046]                  # Ваш Telegram ID (через запятую, если несколько)

SUB_CHANNEL_ID = -1001234567890           # ID канала для обязательной подписки (с -100)
SUB_CHANNEL_URL = "https://t.me/noverashop" # Ссылка на ваш канал

API_ID = 31799721                         # API_ID с my.telegram.org
API_HASH = "eb2181220b3b8a0b6a7f93cd8075a559" # API_HASH с my.telegram.org

# РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ КАРТОЙ
PAY_CARD_PHONE = "+79313716777"
PAY_CARD_BANK = "Т-Банк"
PAY_CARD_HOLDER = "Тимур / Наталья"

# ПРОЧИЕ НАСТРОЙКИ
DB_PATH = "data/bot_database.db"
REFERRAL_GOAL = 20
# =====================================================================

# Инициализация
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Определение состояний FSM
class AddProduct(StatesGroup):
    kind = State()
    name = State()
    price_rub = State()
    price_stars = State()
    session_file = State()
    phone_number = State()

class Broadcast(StatesGroup):
    text = State()

class EditText(StatesGroup):
    key = State()
    text = State()

# Работа с базой данных
async def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_msk():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Проверка подписки
async def subscription_gate(m: Message) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=SUB_CHANNEL_ID, user_id=m.from_user.id)
        if member.status in ["creator", "administrator", "member"]:
            return True
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        return True

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=SUB_CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]
    ])
    await m.answer("⚠️ Для использования бота необходимо подписаться на наш канал!", reply_markup=kb)
    return False

# Клавиатуры
def main_kb(is_adm: bool = False):
    buttons = [
        [KeyboardButton(text="🛒 Купить аккаунт"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🔗 Реферальная система"), KeyboardButton(text="ℹ️ О нас")]
    ]
    if is_adm:
        buttons.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"), InlineKeyboardButton(text="📦 Склад", callback_data="adm_stock")],
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="adm_add_product"), InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="adm_referrals"), InlineKeyboardButton(text="✏️ Тексты", callback_data="adm_texts")]
    ])

# Стартовые хэндлеры
@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    
    args = m.text.split()
    if len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
        if ref_id != m.from_user.id:
            conn = await db()
            cursor = conn.execute("SELECT id FROM users WHERE user_id = ?", (m.from_user.id,))
            user = cursor.fetchone()
            if not user:
                conn.execute("INSERT OR IGNORE INTO users(user_id, referrer_id) VALUES(?, ?)", (m.from_user.id, ref_id))
                conn.commit()
            conn.close()

    conn = await db()
    conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (m.from_user.id,))
    conn.commit()
    conn.close()

    if not await subscription_gate(m):
        return

    await m.answer("Добро пожаловать в магазин аккаунтов!", reply_markup=main_kb(is_admin(m.from_user.id)))

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(cb: CallbackQuery):
    try:
        member = await bot.get_chat_member(chat_id=SUB_CHANNEL_ID, user_id=cb.from_user.id)
        if member.status in ["creator", "administrator", "member"]:
            await cb.message.delete()
            await cb.message.answer("Спасибо за подписку!", reply_markup=main_kb(is_admin(cb.from_user.id)))
        else:
            await cb.answer("Вы всё еще не подписались на канал!", show_alert=True)
    except Exception:
        await cb.answer("Ошибка проверки. Попробуйте позже.", show_alert=True)

# Админ-панель: процесс добавления товара
@dp.callback_query(F.data == "adm_add_product")
async def adm_add_product_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AddProduct.kind)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Telegram", callback_data="kind_telegram")],
        [InlineKeyboardButton(text="Другое", callback_data="kind_other")]
    ])
    await cb.message.edit_text("Выберите тип товара:", reply_markup=kb)

@dp.callback_query(AddProduct.kind, F.data.startswith("kind_"))
async def add_kind(cb: CallbackQuery, state: FSMContext):
    kind = cb.data.split("_")[1]
    await state.update_data(kind=kind)
    await state.set_state(AddProduct.name)
    await cb.message.edit_text("Введите название товара (например: Telegram RU +7):")

@dp.message(AddProduct.name, F.text)
async def add_name(m: Message, state: FSMContext):
    if not await subscription_gate(m) or not is_admin(m.from_user.id):
        return
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProduct.price_rub)
    await m.answer("Введите цену в рублях (₽):")

@dp.message(AddProduct.price_rub, F.text)
async def add_price_rub(m: Message, state: FSMContext):
    if not await subscription_gate(m) or not is_admin(m.from_user.id):
        return
    if not m.text.isdigit():
        await m.answer("Пожалуйста, введите число!")
        return
    await state.update_data(price_rub=int(m.text))
    await state.set_state(AddProduct.price_stars)
    await m.answer("Введите цену в Telegram Stars (⭐):")

@dp.message(AddProduct.price_stars, F.text)
async def add_price_stars(m: Message, state: FSMContext):
    if not await subscription_gate(m) or not is_admin(m.from_user.id):
        return
    if not m.text.isdigit():
        await m.answer("Пожалуйста, введите число!")
        return
    await state.update_data(price_stars=int(m.text))
    await state.set_state(AddProduct.session_file)
    await m.answer("Отправьте файл сессии (.session):")

@dp.message(AddProduct.session_file, F.document)
async def add_session(m: Message, state: FSMContext):
    if not await subscription_gate(m) or not is_admin(m.from_user.id): 
        return
    
    os.makedirs("data/sessions", exist_ok=True)
    file_id = m.document.file_id
    file_name = f"data/sessions/{m.document.file_name}"
    
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, file_name)
    
    await state.update_data(session_file=file_name)
    await state.set_state(AddProduct.phone_number)
    await m.answer("Введите номер телефона аккаунта (например, +79991112233):")

@dp.message(AddProduct.phone_number, F.text)
async def add_phone(m: Message, state: FSMContext):
    if not await subscription_gate(m) or not is_admin(m.from_user.id): 
        return
    
    phone = m.text.strip()
    d = await state.get_data()
    
    conn = await db()
    conn.execute(
        "INSERT INTO products(kind, name, price_rub, price_stars, stock, sales, session_file, phone_number, created_at) "
        "VALUES(?, ?, ?, ?, 1, 0, ?, ?, ?)",
        (d["kind"], d["name"], d["price_rub"], d["price_stars"], d["session_file"], phone, now_msk().isoformat())
    )
    conn.commit()
    conn.close()
    
    await state.clear()
    await m.answer("✅ Аккаунт успешно добавлен на склад!", reply_markup=main_kb(True))
# Покупка и выбор способа оплаты
@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(cb: CallbackQuery):
    prod_id = int(cb.data.split("_")[1])
    conn = await db()
    cursor = conn.execute("SELECT * FROM products WHERE id = ? AND stock > 0", (prod_id,))
    product = cursor.fetchone()
    conn.close()

    if not product:
        await cb.answer("К сожалению, этот товар закончился!", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Карта ({product['price_rub']}₽)", callback_data=f"pay_card_{prod_id}")],
        [InlineKeyboardButton(text=f"⭐ Stars ({product['price_stars']}⭐)", callback_data=f"pay_stars_{prod_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_pay")]
    ])
    await cb.message.edit_text(f"Выбираем способ оплаты для: **{product['name']}**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("pay_card_"))
async def pay_card(cb: CallbackQuery):
    prod_id = int(cb.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил (Отправить чек)", callback_data=f"check_pay_{prod_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_pay")]
    ])
    await cb.message.edit_text(
        "Реквизиты для оплаты:\n\n"
        f"📱 **Телефон:** `{PAY_CARD_PHONE}`\n"
        f"🏦 **Банк:** {PAY_CARD_BANK}\n"
        f"👤 **Получатель:** {PAY_CARD_HOLDER}\n\n"
        "После оплаты пришлите скриншот чека в этот чат.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("check_pay_"))
async def check_pay_req(cb: CallbackQuery):
    await cb.answer("Отправьте скриншот чека сообщением в чат!", show_alert=True)

# Обработка отправленного скриншота чека
@dp.message(F.photo)
async def handle_screenshot(m: Message):
    if not await subscription_gate(m):
        return

    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm_approve_{m.from_user.id}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_deny_{m.from_user.id}")]
        ])
        await bot.send_photo(
            admin_id,
            photo=m.photo[-1].file_id,
            caption=f"Чек от пользователя @{m.from_user.username or 'без_юзернейма'} (ID: `{m.from_user.id}`)",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    await m.answer("Ваш чек отправлен на проверку администратору. Ожидайте подтверждения!")

# Подтверждение оплаты администратором
@dp.callback_query(F.data.startswith("adm_approve_"))
async def adm_approve(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    user_id = int(cb.data.split("_")[2])
    
    conn = await db()
    cursor = conn.execute("SELECT * FROM products WHERE stock > 0 LIMIT 1")
    product = cursor.fetchone()
    
    if product:
        conn.execute("UPDATE products SET stock = stock - 1, sales = sales + 1 WHERE id = ?", (product["id"],))
        conn.commit()
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Забрать аккаунт", callback_data=f"get_acc_{product['id']}")]
        ])
        await bot.send_message(user_id, "✅ Ваша оплата подтверждена! Нажмите кнопку ниже для получения данных:", reply_markup=kb)
        await cb.message.edit_caption(caption=cb.message.caption + "\n\n🟢 **ОДОБРЕНО**", parse_mode="Markdown")
    else:
        await cb.answer("Нет товаров в наличии!", show_alert=True)
    conn.close()

# Выдача кода 2FA через Pyrogram
async def listen_for_telegram_code(session_path: str, user_id: int):
    client = Client(session_path, api_id=API_ID, api_hash=API_HASH)
    try:
        await client.connect()
        await bot.send_message(user_id, "⏳ Ожидаем код авторизации от Telegram...")
        
        for _ in range(30):
            async for message in client.get_chat_history(777000, limit=1):
                if message.text:
                    await bot.send_message(user_id, f"📥 **Ваш код авторизации:** `{message.text}`", parse_mode="Markdown")
                    await client.disconnect()
                    return
            await asyncio.sleep(10)
        
        await bot.send_message(user_id, "⚠️ Время ожидания кода истекло. Обратитесь в поддержку.")
        await client.disconnect()
    except Exception as e:
        logging.error(f"Pyrogram error: {e}")
        await bot.send_message(user_id, "❌ Ошибка при получении кода с аккаунта.")

@dp.callback_query(F.data.startswith("get_acc_"))
async def get_acc(cb: CallbackQuery):
    prod_id = int(cb.data.split("_")[2])
    conn = await db()
    cursor = conn.execute("SELECT * FROM products WHERE id = ?", (prod_id,))
    product = cursor.fetchone()
    conn.close()

    if product:
        await cb.message.edit_text(f"📱 **Номер телефона:** `{product['phone_number']}`\n\nЗапустите вход в аккаунт, сейчас бот пришлет код...", parse_mode="Markdown")
        asyncio.create_task(listen_for_telegram_code(product['session_file'], cb.from_user.id))
    else:
        await cb.answer("Ошибка получения данных.", show_alert=True)

# Главный роутер меню (располагается НИЖЕ всех FSM-состояний)
@dp.message(StateFilter(None), F.text)
async def router(m: Message, state: FSMContext):
    if not await subscription_gate(m):
        return

    text = m.text
    if text == "🛒 Купить аккаунт":
        conn = await db()
        cursor = conn.execute("SELECT * FROM products WHERE stock > 0")
        products = cursor.fetchall()
        conn.close()

        if not products:
            await m.answer("На данный момент нет товаров в наличии.")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{p['name']} — {p['price_rub']}₽", callback_data=f"buy_{p['id']}")] for p in products
        ])
        await m.answer("Выберите товар для покупки:", reply_markup=kb)

    elif text == "👤 Профиль":
        await m.answer(f"👤 **Ваш профиль**\nID: `{m.from_user.id}`\nИмя: {m.from_user.first_name}", parse_mode="Markdown")

    elif text == "⚙️ Админ-панель" and is_admin(m.from_user.id):
        await m.answer("Панель администратора:", reply_markup=admin_kb())

# Запуск приложения
async def main():
    os.makedirs("data/sessions", exist_ok=True)
    
    conn = await db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            referrer_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            name TEXT,
            price_rub INTEGER,
            price_stars INTEGER,
            stock INTEGER,
            sales INTEGER,
            session_file TEXT,
            phone_number TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    logging.info("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
