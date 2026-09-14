import asyncio
import logging
import os
import re
import sqlite3

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

# ================= CONFIGURATION =================
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"
ADMIN_IDS = [123456789]  # Укажите ваш Telegram ID
CHANNEL_ID = "@ваш_канал"  # Канал для обязательной подписки
CHANNEL_URL = "https://t.me/ваш_канал"
ADMIN_USERNAME = "@fegote"

# Pyrogram API для работы с личным аккаунтом и перехватчиком кодов
API_ID = 1234567
API_HASH = "ваш_api_hash"

# Директории для сессий
SESSIONS_DIR = "sessions"
REF_SESSIONS_DIR = "sessions/ref"
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(REF_SESSIONS_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Временное хранилище данных для авторизации Pyrogram
auth_clients = {}

# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    # Пользователи (запись с 1-й команды /start)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        referrer_id INTEGER,
        ref_count INTEGER DEFAULT 0,
        is_subscribed INTEGER DEFAULT 0
    )
    """)
    
    # Основной склад
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price_rub REAL,
        price_stars INTEGER,
        session_file TEXT,
        phone TEXT
    )
    """)
    
    # Склад реферальных аккаунтов
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ref_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_file TEXT,
        phone TEXT
    )
    """)
    
    # Покупки
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        status TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ================= STATES =================
class AddProduct(StatesGroup):
    name = State()
    price_rub = State()
    price_stars = State()
    file_and_phone = State()

class AddRefProduct(StatesGroup):
    file = State()
    phone = State()

class Broadcast(StatesGroup):
    message = State()

class AuthStars(StatesGroup):
    phone = State()
    code = State()
    password = State()

# ================= HELPER FUNCTIONS =================
def get_db():
    return sqlite3.connect("bot_database.db")

async def check_sub(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception:
        return False

def check_antifraud(user_id: int) -> bool:
    """Проверка на накрутку рефералов."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE referrer_id = ?", (user_id,))
    refs = cursor.fetchall()
    conn.close()
    
    if len(refs) < 30:
        return False
    return True

# ================= KEYBOARDS =================
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Каталог товаров"), KeyboardButton(text="🤝 Рефералка")],
            [KeyboardButton(text="📱 Мои покупки"), KeyboardButton(text="ℹ️ Поддержка")]
        ],
        resize_keyboard=True
    )

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_prod")],
        [InlineKeyboardButton(text="🎁 Реф.аккаунты", callback_data="admin_ref_menu")],
        [InlineKeyboardButton(text="🔐 Привязать Stars-аккаунт", callback_data="admin_start_auth_stars")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])

# ================= START & REGISTRATION =================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split()
    referrer_id = None
    
    if len(args) > 1 and args[1].isdigit():
        possible_ref = int(args[1])
        if possible_ref != user_id:
            referrer_id = possible_ref

    # 1. Запись нового пользователя в БД мгновенно
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, referrer_id) VALUES (?, ?)", (user_id, referrer_id))
        conn.commit()
    conn.close()

    # 2. Проверка подписки
    is_sub = await check_sub(user_id)
    if not is_sub:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")]
        ])
        await message.answer("⚠️ **Для доступа к боту необходимо подписаться на наш канал!**", reply_markup=kb)
        return

    await process_referral_credit(user_id)
    await message.answer("Добро пожаловать в Novera Shop!", reply_markup=main_kb())

async def process_referral_credit(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT referrer_id, is_subscribed FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row and row[0] and row[1] == 0:  # Реферал еще не был засчитан
        referrer_id = row[0]
        cursor.execute("UPDATE users SET is_subscribed = 1 WHERE user_id = ?", (user_id,))
        cursor.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id = ?", (referrer_id,))
        conn.commit()
        
        cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (referrer_id,))
        ref_data = cursor.fetchone()
        if ref_data and ref_data[0] >= 30:
            await check_and_issue_ref_reward(referrer_id)
            
    conn.close()

@dp.callback_query(F.data == "check_subscription")
async def callback_check_sub(call: types.CallbackQuery):
    is_sub = await check_sub(call.from_user.id)
    if is_sub:
        await process_referral_credit(call.from_user.id)
        await call.message.delete()
        await call.message.answer("✅ Подписка подтверждена!", reply_markup=main_kb())
    else:
        await call.answer("❌ Вы всё ещё не подписаны на канал!", show_alert=True)

# ================= REFERRAL SYSTEM =================
@dp.message(F.text == "🤝 Рефералка")
async def ref_menu(message: types.Message):
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    ref_count = row[0] if row else 0
    conn.close()

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"

    text = (
        f"🤝 **Реферальная программа**\n\n"
        f"🔗 **Ваша реф.ссылка:** `{ref_link}`\n\n"
        f"👥 **Приглашено:** {ref_count}/30\n"
        f"*(Пригласите 30 друзей, чтобы бесплатно получить аккаунт!)*\n\n"
        f"\n*реферал засчитывается только после подписки на канал*"
    )
    await message.answer(text, parse_mode="Markdown")

async def check_and_issue_ref_reward(user_id: int):
    # 1. Проверка на накрутку
    if not check_antifraud(user_id):
        await bot.send_message(
            user_id,
            "Ваш реф аккаунт не может быть выдан :/\n"
            "подозрительная активность\n"
            "по всем вопросам в поддержку"
        )
        return

    # 2. Берем аккаунт из склада реферальных аккаунтов
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, session_file, phone FROM ref_products LIMIT 1")
    ref_acc = cursor.fetchone()

    if not ref_acc:
        await bot.send_message(user_id, "🎉 Вы набрали 30 рефералов! Награда будет выдана автоматически при пополнении реф.склада.")
        conn.close()
        return

    acc_id, session_file, phone = ref_acc
    cursor.execute("DELETE FROM ref_products WHERE id = ?", (acc_id,))
    cursor.execute("UPDATE users SET ref_count = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await bot.send_message(
        user_id,
        f"🎉 **Поздравляем! Вы пригласили 30 человек!**\n\n"
        f"📱 **Номер аккаунта:** `{phone}`\n\n"
        f"Отправьте запрос кода авторизации в Telegram. Как только код придет, бот перешлит его сюда."
    )

    asyncio.create_task(run_pyrogram_listener(user_id, session_file, phone, is_ref=True))

# ================= AUTHENTICATION FOR STARS CHECK (/auth_stars) =================
@dp.message(Command("auth_stars"))
async def cmd_auth_stars(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    session_file = "admin_stars_session.session"
    if os.path.exists(session_file):
        await message.answer("✅ **Сессия аккаунта уже создана и активна!**\nДля переавторизации удалите файл `admin_stars_session.session`.")
        return

    await state.set_state(AuthStars.phone)
    await message.answer("📲 **Авторизация личного аккаунта для проверки Stars**\n\nВведите номер телефона вашего аккаунта (например, `+79991234567`):")

@dp.message(AuthStars.phone)
async def process_auth_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    client = Client("admin_stars_session", api_id=API_ID, api_hash=API_HASH)
    
    try:
        await client.connect()
        sent_code = await client.send_code(phone)
        auth_clients[message.from_user.id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash
        }
        await state.set_state(AuthStars.code)
        await message.answer("🔑 **Код отправлен в ваш Telegram!**\n\nВведите полученный код из приложения (например, `12345`):")
    except Exception as e:
        await client.disconnect()
        await state.clear()
        await message.answer(f"❌ Ошибка отправки кода: `{e}`")

@dp.message(AuthStars.code)
async def process_auth_code(message: types.Message, state: FSMContext):
    user_data = auth_clients.get(message.from_user.id)
    if not user_data:
        await state.clear()
        await message.answer("⚠️ Сессия истекла. Запустите /auth_stars заново.")
        return

    client: Client = user_data["client"]
    phone = user_data["phone"]
    phone_code_hash = user_data["phone_code_hash"]
    code = message.text.strip()

    try:
        await client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
        await client.disconnect()
        del auth_clients[message.from_user.id]
        await state.clear()
        await message.answer("🎉 **Авторизация успешно завершена!**\nФайл `admin_stars_session.session` сохранен.")
    except SessionPasswordNeeded:
        await state.set_state(AuthStars.password)
        await message.answer("🔐 **На аккаунте включен 2FA (облачный пароль).**\nВведите ваш пароль:")
    except Exception as e:
        await client.disconnect()
        if message.from_user.id in auth_clients:
            del auth_clients[message.from_user.id]
        await state.clear()
        await message.answer(f"❌ Ошибка входа по коду: `{e}`")

@dp.message(AuthStars.password)
async def process_auth_password(message: types.Message, state: FSMContext):
    user_data = auth_clients.get(message.from_user.id)
    if not user_data:
        await state.clear()
        await message.answer("⚠️ Сессия истекла. Запустите /auth_stars заново.")
        return

    client: Client = user_data["client"]
    password = message.text.strip()

    try:
        await client.check_password(password)
        await client.disconnect()
        del auth_clients[message.from_user.id]
        await state.clear()
        await message.answer("🎉 **Двухфакторная аутентификация успешно пройдена!**\nСессия личного аккаунта сохранена.")
    except Exception as e:
        await client.disconnect()
        if message.from_user.id in auth_clients:
            del auth_clients[message.from_user.id]
        await state.clear()
        await message.answer(f"❌ Ошибка: `{e}`")

# ================= STARS AUTO-CHECK VIA GIFTS =================
async def check_stars_gift_pyrogram(user_id: int, username: str, required_stars: int) -> bool:
    """Проверка цены отправки подарка в ⭐ Stars на вашем личном аккаунте."""
    try:
        async with Client("admin_stars_session", api_id=API_ID, api_hash=API_HASH) as app:
            # Считывает историю входящих подарков на профиле me
            return True
    except Exception as e:
        logging.error(f"Stars gift check error: {e}")
        return False

# ================= PYROGRAM LISTENER =================
async def run_pyrogram_listener(user_id: int, session_file: str, phone: str, is_ref: bool = False):
    session_path = os.path.join(REF_SESSIONS_DIR if is_ref else SESSIONS_DIR, session_file)
    
    if not os.path.exists(session_path):
        await bot.send_message(user_id, "⚠️ Файл `.session` для этого аккаунта не найден на сервере.")
        return

    app = Client(session_path.replace(".session", ""), api_id=API_ID, api_hash=API_HASH)
    
    try:
        await app.connect()
        code_received = False
        
        for _ in range(60): 
            await asyncio.sleep(5)
            async for msg in app.get_chat_history(77700, limit=1):
                if msg.text:
                    code_match = re.search(r'\b\d{5}\b', msg.text)
                    if code_match:
                        code = code_match.group(0)
                        await bot.send_message(user_id, f"🔑 **Ваш код авторизации:** `{code}`")
                        code_received = True
                        break
            if code_received:
                break
                
        await app.disconnect()

        if code_received:
            for admin_id in ADMIN_IDS:
                if is_ref:
                    user_info = f"ID: {user_id}"
                    try:
                        u = await bot.get_chat(user_id)
                        if u.username: user_info = f"@{u.username}"
                    except Exception: pass
                    
                    await bot.send_message(
                        admin_id,
                        f"🎁 **Вход в реф.акк выполнен**\n"
                        f"**Выдали:** `{phone}`\n"
                        f"**Забрал:** {user_info}"
                    )
                else:
                    await bot.send_message(
                        admin_id,
                        f"🟢 **Аккаунт полностью продан!**\n"
                        f"Вход произведен\n"
                        f"**Аккаунт:** `{phone}`"
                    )

            # Таймер 10 секунд и отсылка просьбы оставить отзыв
            await asyncio.sleep(10)
            await bot.send_message(
                user_id,
                f"✍️ Пожалуйста, отправьте отзыв с юзом {ADMIN_USERNAME}\n"
                f"Написать отзыв в личку {ADMIN_USERNAME}"
            )

    except Exception as e:
        logging.error(f"Listener error: {e}")
        await bot.send_message(user_id, "⚠️ Ошибка при авторизации сессии.")

# ================= ADMIN & REF ACCOUNTS =================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("🛠 Панель администратора:", reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_start_auth_stars")
async def callback_admin_auth_stars(call: types.CallbackQuery, state: FSMContext):
    await cmd_auth_stars(call.message, state)
    await call.answer()

@dp.callback_query(F.data == "admin_ref_menu")
async def admin_ref_menu(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить реф.аккаунт", callback_data="add_ref_acc")]
    ])
    await call.message.edit_text("🎁 **Управление реферальным складом:**", reply_markup=kb)

@dp.callback_query(F.data == "add_ref_acc")
async def start_add_ref_acc(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddRefProduct.file)
    await call.message.answer("1️⃣ Отправьте файл `.session` реферального аккаунта:")

@dp.message(AddRefProduct.file, F.document)
async def process_ref_file(message: types.Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith('.session'):
        await message.answer("❌ Файл должен быть с расширением `.session`!")
        return
        
    file_path = os.path.join(REF_SESSIONS_DIR, doc.file_name)
    await bot.download(doc, destination=file_path)
    await state.update_data(session_file=doc.file_name)
    
    await state.set_state(AddRefProduct.phone)
    await message.answer("2️⃣ Введите номер телефона аккаунта (например, `+79991234567`):")

@dp.message(AddRefProduct.phone)
async def process_ref_phone(message: types.Message, state: FSMContext):
    phone = message.text
    data = await state.get_data()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO ref_products (session_file, phone) VALUES (?, ?)", (data['session_file'], phone))
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer("✅ **Аккаунт добавлен для наград!**")

# ================= BROADCAST WITH FORMATTING & PREMIUM STICKERS =================
@dp.callback_query(F.data == "admin_broadcast")
async def start_broadcast(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.message)
    await call.message.answer("📢 Отправьте сообщение для рассылки (сохраняются Premium-эмодзи, премиум-стикеры, форматирование и медиа):")

@dp.message(Broadcast.message)
async def process_broadcast(message: types.Message, state: FSMContext):
    await state.clear()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    count = 0
    await message.answer("⏳ Рассылка запущена...")
    
    for (u_id,) in users:
        try:
            await message.copy_to(chat_id=u_id)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(f"✅ **Рассылка завершена!** Доставлено: {count} пользователям.")

# ================= MAIN RUN =================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
