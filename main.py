import asyncio
import os
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

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Telegram bot token was not provided by the hosting platform")

ADMIN_ID = 8872934046
SUB_CHANNEL_ID = -1003922108499
DB_PATH = "data/shop.db"
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
MSK = ZoneInfo("Europe/Moscow")
SUB_INVITE_LINK = ""

TEXTS = {
    "welcome": "Привет, {name}\nТут ты можешь приобрести все что душе угодно.",
    "catalog": "Каталог", "reviews": "Отзывы", "support": "Поддержка",
    "admin": "Админ панель", "stats": "Статистика", "stock": "Склад",
    "broadcast": "Рассылка", "edit_text": "Изменить текст",
    "choose_type": "Выберите тип товара", "accounts": "Аккаунты", "stars": "Звезды",
    "change_button": "Изменить текст кнопки", "change_message": "Изменить текст сообщения",
}
FLAGS = {"1":"🇺🇸","7":"🇷🇺","20":"🇪🇬","27":"🇿🇦","30":"🇬🇷","31":"🇳🇱","32":"🇧🇪","33":"🇫🇷","34":"🇪🇸","36":"🇭🇺","39":"🇮🇹","40":"🇷🇴","41":"🇨🇭","43":"🇦🇹","44":"🇬🇧","45":"🇩🇰","46":"🇸🇪","47":"🇳🇴","48":"🇵🇱","49":"🇩🇪","51":"🇵🇪","52":"🇲🇽","53":"🇨🇺","54":"🇦🇷","55":"🇧🇷","56":"🇨🇱","57":"🇨🇴","58":"🇻🇪","60":"🇲🇾","61":"🇦🇺","62":"🇮🇩","63":"🇵🇭","64":"🇳🇿","65":"🇸🇬","66":"🇹🇭","81":"🇯🇵","82":"🇰🇷","84":"🇻🇳","86":"🇨🇳","90":"🇹🇷","91":"🇮🇳","92":"🇵🇰","93":"🇦🇫","94":"🇱🇰","95":"🇲🇲","98":"🇮🇷","212":"🇲🇦","213":"🇩🇿","216":"🇹🇳","218":"🇱🇾","234":"🇳🇬","254":"🇰🇪","255":"🇹🇿","380":"🇺🇦","381":"🇷🇸","420":"🇨🇿","421":"🇸🇰","423":"🇱🇮","852":"🇭🇰","853":"🇲🇴","886":"🇹🇼","972":"🇮🇱","971":"🇦🇪","995":"🇬🇪","998":"🇺🇿"}

class AddProduct(StatesGroup):
    kind = State(); name = State(); price_rub = State(); price_stars = State(); stock = State()
class Broadcast(StatesGroup):
    text = State()
class EditText(StatesGroup):
    key = State(); value = State()
class Payment(StatesGroup):
    screenshot = State()

def is_admin(uid):
    return uid == ADMIN_ID

def now_msk():
    return datetime.now(MSK)

async def db():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn

async def one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    try:
        return await cur.fetchone()
    finally:
        await cur.close()

async def all_rows(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    try:
        return await cur.fetchall()
    finally:
        await cur.close()

async def init_db():
    conn = await db()
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS texts(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, price_rub REAL NOT NULL, price_stars INTEGER NOT NULL, stock INTEGER NOT NULL DEFAULT 0, sales INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, product_name TEXT NOT NULL, payment TEXT NOT NULL, amount REAL NOT NULL, screenshot_file_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
    """)
    for k, v in TEXTS.items():
        await conn.execute("INSERT OR IGNORE INTO texts(key,value) VALUES(?,?)", (k, v))
    await conn.commit(); await conn.close()

async def update_user_count_description():
    try:
        conn = await db(); count = (await one(conn, "SELECT COUNT(*) c FROM users"))["c"]; await conn.close()
        await bot.set_my_short_description(short_description=f"👥 Пользователей: {count}")
    except Exception:
        pass

async def save_user(m):
    u = m.from_user
    conn = await db()
    await conn.execute("""INSERT INTO users(id,username,first_name,created_at) VALUES(?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
    (u.id, u.username, u.first_name, now_msk().isoformat()))
    await conn.commit(); await conn.close()
    await update_user_count_description()

async def save_user_id(user):
    conn = await db()
    await conn.execute("""INSERT INTO users(id,username,first_name,created_at) VALUES(?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
    (user.id, user.username, user.first_name, now_msk().isoformat()))
    await conn.commit(); await conn.close()
    await update_user_count_description()

async def prepare_subscription_link():
    global SUB_INVITE_LINK
    SUB_INVITE_LINK = os.getenv("SUBSCRIPTION_URL", "").strip()
    if SUB_INVITE_LINK:
        return
    try:
        link = await bot.create_chat_invite_link(chat_id=SUB_CHANNEL_ID, name="Novera Shop")
        SUB_INVITE_LINK = link.invite_link
    except Exception:
        SUB_INVITE_LINK = ""

async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(chat_id=SUB_CHANNEL_ID, user_id=user_id)
        status = getattr(member.status, "value", member.status)
        if status in {"member", "administrator", "creator"}:
            return True
        if status == "restricted" and getattr(member, "is_member", False):
            return True
        return False
    except Exception as e:
        print(f"[SUBSCRIPTION CHECK ERROR] user={user_id}: {type(e).__name__}: {e}")
        return False

async def subscription_gate(event):
    if await is_subscribed(event.from_user.id):
        return True
    rows = []
    if SUB_INVITE_LINK:
        rows.append([InlineKeyboardButton(text="📢 Подписаться на канал", url=SUB_INVITE_LINK)])
    rows.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="sub:check")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    msg = "Для использования бота необходимо подписаться на канал.\n\nПодпишитесь и нажмите «Проверить подписку»."
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(msg, reply_markup=markup)
    else:
        await event.answer(msg, reply_markup=markup)
    return False

def home_kb(uid):
    kb = ReplyKeyboardBuilder()
    kb.button(text=TEXTS["catalog"])
    kb.button(text=TEXTS["reviews"])
    kb.button(text=TEXTS["support"])
    if is_admin(uid):
        kb.button(text=TEXTS["admin"])
    kb.adjust(1, 2, 1)
    return kb.as_markup(resize_keyboard=True)

def admin_kb():
    kb = ReplyKeyboardBuilder()
    for k in ("stats", "stock", "broadcast", "edit_text"):
        kb.button(text=TEXTS[k])
    kb.button(text="Назад"); kb.adjust(2, 2, 1)
    return kb.as_markup(resize_keyboard=True)

def catalog_kb():
    kb = ReplyKeyboardBuilder(); kb.button(text=TEXTS["accounts"]); kb.button(text=TEXTS["stars"]); kb.button(text="Назад"); kb.adjust(2, 1)
    return kb.as_markup(resize_keyboard=True)

def edit_kb():
    kb = ReplyKeyboardBuilder(); kb.button(text=TEXTS["change_button"]); kb.button(text=TEXTS["change_message"]); kb.button(text="Назад"); kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

def inline_back(callback_data):
    return InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)

@dp.callback_query(F.data == "sub:check")
async def subscription_check(call):
    subscribed = await is_subscribed(call.from_user.id)
    if subscribed:
        await save_user_id(call.from_user)
        await call.answer("Подписка подтверждена ✅")
        await call.message.answer("Подписка подтверждена. Добро пожаловать!", reply_markup=home_kb(call.from_user.id))
    else:
        await call.answer("Подписка ещё не найдена. Подпишитесь на канал и нажмите кнопку ещё раз.", show_alert=True)

@dp.message(CommandStart())
async def start(m, state):
    if not await subscription_gate(m): return
    await state.clear(); await save_user(m)
    name = m.from_user.username or m.from_user.first_name or "пользователь"
    await m.answer((await text("welcome")).format(name=name), reply_markup=home_kb(m.from_user.id))

@dp.message(Command("adm"))
async def adm(m, state):
    if not await subscription_gate(m): return
    if is_admin(m.from_user.id):
        await state.clear(); await m.answer("Админ панель", reply_markup=admin_kb())

@dp.message(StateFilter(None), F.text == "Назад")
async def back_home(m, state):
    if not await subscription_gate(m): return
    await state.clear(); await m.answer("Главное меню", reply_markup=home_kb(m.from_user.id))

@dp.message(F.text == "Назад")
async def back_from_state(m, state):
    if not await subscription_gate(m): return
    await state.clear(); await m.answer("Главное меню", reply_markup=home_kb(m.from_user.id))

@dp.message(StateFilter(None), F.text)
async def router(m, state):
    if not await subscription_gate(m): return
    await save_user(m); v = m.text
    if v == await text("catalog"):
        await m.answer(await text("choose_type"), reply_markup=catalog_kb())
    elif v == await text("reviews"):
        await m.answer("Отзывы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть отзывы", url="https://t.me/repacrisov")]]))
    elif v == await text("support"):
        await m.answer("Поддержка:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать в поддержку", url="https://t.me/fegote")]]))
    elif is_admin(m.from_user.id) and v == await text("admin"):
        await m.answer("Админ панель", reply_markup=admin_kb())
    elif is_admin(m.from_user.id) and v == TEXTS["stats"]: await stats(m)
    elif is_admin(m.from_user.id) and v == TEXTS["stock"]: await stock(m)
    elif is_admin(m.from_user.id) and v == TEXTS["broadcast"]:
        await state.set_state(Broadcast.text); await m.answer("Отправьте текст рассылки.\nДля отмены нажмите «Назад».")
    elif is_admin(m.from_user.id) and v == TEXTS["edit_text"]: await m.answer("Что изменить?", reply_markup=edit_kb())
    elif v == await text("accounts"): await products(m, "account")
    elif v == await text("stars"): await products(m, "stars")

async def products(m, kind):
    conn = await db(); rows = await all_rows(conn, "SELECT * FROM products WHERE kind=? AND stock>0 ORDER BY id", (kind,)); await conn.close()
    if not rows: return await m.answer("Товаров в наличии нет.", reply_markup=catalog_kb())
    kb = InlineKeyboardBuilder()
    for p in rows:
        if kind == "account":
            code = p["name"].lstrip("+").split()[0]; flag = next((FLAGS.get(code[:n], "") for n in (3, 2, 1) if FLAGS.get(code[:n])), ""); label = f"+{code} {flag} {p['price_rub']:.0f}₽"
        else: label = f"{p['name']} / {p['price_rub']:.0f}₽"
        kb.button(text=label, callback_data=f"product:{p['id']}")
    kb.button(text="⬅️ Назад", callback_data="back:catalog"); kb.adjust(1)
    await m.answer("Выберите товар", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "back:catalog")
async def back_catalog(call, state):
    if not await subscription_gate(call): return
    await state.clear(); await call.answer(); await call.message.answer(await text("choose_type"), reply_markup=catalog_kb())

@dp.callback_query(F.data.startswith("product:"))
async def product(call, state):
    if not await subscription_gate(call): return
    pid = int(call.data.split(":")[1]); conn = await db(); p = await one(conn, "SELECT * FROM products WHERE id=? AND stock>0", (pid,)); await conn.close()
    if not p: return await call.answer("Товар закончился", show_alert=True)
    await state.update_data(product_id=pid, kind=p["kind"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Звезды", callback_data="pay:stars")], [InlineKeyboardButton(text="₽ рубли", callback_data="pay:rub")], [inline_back(f"back:products:{p['kind']}")]])
    await call.message.answer(f"Выбранный товар: {p['name']}\nЦена: {p['price_rub']:.0f}₽ / {p['price_stars']} Stars", reply_markup=kb); await call.answer()

@dp.callback_query(F.data.startswith("back:products:"))
async def back_products(call, state):
    if not await subscription_gate(call): return
    kind = call.data.split(":", 2)[2]; await state.clear(); await call.answer(); await products(call.message, kind)

@dp.callback_query(F.data == "pay:stars")
async def pay_stars(call, state):
    if not await subscription_gate(call): return
    d = await state.get_data(); conn = await db(); p = await one(conn, "SELECT * FROM products WHERE id=? AND stock>0", (d.get("product_id"),)); await conn.close()
    if not p: return await call.answer("Товар закончился", show_alert=True)
    await state.update_data(payment="stars", amount=p["price_stars"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="отправить подтверждение", callback_data="confirm_payment")], [inline_back(f"back:products:{p['kind']}")]])
    await call.message.answer(f"отправьте {p['price_stars']} звезд на аккаунт @fegote", reply_markup=kb); await call.answer()

@dp.callback_query(F.data == "pay:rub")
async def pay_rub(call, state):
    if not await subscription_gate(call): return
    d = await state.get_data(); conn = await db(); p = await one(conn, "SELECT * FROM products WHERE id=? AND stock>0", (d.get("product_id"),)); await conn.close()
    if not p: return await call.answer("Товар закончился", show_alert=True)
    await state.update_data(payment="rub", amount=p["price_rub"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="отправить подтверждение", callback_data="confirm_payment")], [inline_back(f"back:products:{p['kind']}")]])
    await call.message.answer(f"переведите {p['price_rub']:.0f}₽ на реквизиты:\n+79313716777\n• Т-банк\n• Тимур/Наталья", reply_markup=kb); await call.answer()

@dp.callback_query(F.data == "confirm_payment")
async def confirm(call, state):
    if not await subscription_gate(call): return
    await state.set_state(Payment.screenshot); await call.message.answer("Отправьте скрин оплаты одним сообщением.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[inline_back("back:payment")]])); await call.answer()

@dp.callback_query(F.data == "back:payment")
async def back_payment(call, state):
    if not await subscription_gate(call): return
    d = await state.get_data(); kind = d.get("kind", "account"); await state.clear(); await call.answer(); await products(call.message, kind)

@dp.message(Payment.screenshot, F.photo)
async def screenshot(m, state):
    if not await subscription_gate(m): return
    d = await state.get_data(); conn = await db(); p = await one(conn, "SELECT * FROM products WHERE id=? AND stock>0", (d.get("product_id"),))
    if not p: await conn.close(); await state.clear(); return await m.answer("Товар закончился.")
    now = now_msk(); cur = await conn.execute("INSERT INTO purchases(user_id,product_id,product_name,payment,amount,screenshot_file_id,created_at) VALUES(?,?,?,?,?,?,?)", (m.from_user.id, p["id"], p["name"], d.get("payment", ""), d.get("amount", 0), m.photo[-1].file_id, now.isoformat())); pid = cur.lastrowid; await cur.close(); await conn.commit(); await conn.close()
    username = f"@{m.from_user.username}" if m.from_user.username else "нет"
    caption = f"Новая покупка! 🧾\nкупили: {p['name']}\nайди: {m.from_user.id}\nюзернейм: {username}\nцена: {d.get('amount')} {'Stars' if d.get('payment')=='stars' else '₽'}\nвремя покупки по мск: {now.strftime('%d.%m.%Y %H:%M')}\nзаявка: #{pid}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data=f"purchase:approve:{pid}"), InlineKeyboardButton(text="Отклонить", callback_data=f"purchase:reject:{pid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=caption, reply_markup=kb); await m.answer("Заявка отправлена администратору. Ожидайте подтверждения."); await state.clear()

@dp.message(Payment.screenshot)
async def not_photo(m):
    if await subscription_gate(m): await m.answer("Нужен именно скрин оплаты в виде фотографии.")

@dp.callback_query(F.data.startswith("purchase:"))
async def purchase_action(call):
    if not await subscription_gate(call): return
    if not is_admin(call.from_user.id): return
    _, action, pid = call.data.split(":"); pid = int(pid); conn = await db(); p = await one(conn, "SELECT * FROM purchases WHERE id=?", (pid,))
    if not p or p["status"] != "pending": await conn.close(); return await call.answer("Заявка уже обработана", show_alert=True)
    if action == "approve":
        cur = await conn.execute("UPDATE products SET stock=stock-1,sales=sales+1 WHERE id=? AND stock>0", (p["product_id"],))
        if cur.rowcount == 0: await cur.close(); await conn.close(); return await call.answer("Товар уже закончился", show_alert=True)
        await cur.close(); await conn.execute("UPDATE purchases SET status='approved' WHERE id=?", (pid,)); status = "Одобрено"
    else:
        await conn.execute("UPDATE purchases SET status='rejected' WHERE id=?", (pid,)); status = "Отклонено"
    await conn.commit(); await conn.close(); await call.message.edit_reply_markup(reply_markup=None); await call.message.answer(f"Заявка #{pid}: {status}")
    try: await bot.send_message(p["user_id"], f"Ваша заявка #{pid}: {status}.")
    except Exception: pass
    await call.answer()

async def stats(m):
    conn = await db(); total = await one(conn, "SELECT COUNT(*) c FROM purchases WHERE status='approved'"); rub = await one(conn, "SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='rub'"); stars = await one(conn, "SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='stars'"); users = await one(conn, "SELECT COUNT(*) c FROM users")
    today = now_msk().date().isoformat(); tr = await one(conn, "SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='rub' AND date(created_at)=?", (today,)); ts = await one(conn, "SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='stars' AND date(created_at)=?", (today,)); best = await all_rows(conn, "SELECT product_name,COUNT(*) c FROM purchases WHERE status='approved' GROUP BY product_id ORDER BY c DESC LIMIT 5"); await conn.close()
    besttxt = "\n".join(f"{i+1}. {r['product_name']} — {r['c']}" for i, r in enumerate(best)) or "нет"
    await m.answer(f"Всего продаж: {total['c']}\nЗаработано ₽: {rub['v']:.0f}\nЗаработано Stars: {stars['v']:.0f}\n\nЗа сегодня:\n₽: {tr['v']:.0f}\nStars: {ts['v']:.0f}\n\nКоличество пользователей: {users['c']}\n\nСамые продаваемые товары:\n{besttxt}", reply_markup=admin_kb())

async def stock(m):
    conn = await db(); rows = await all_rows(conn, "SELECT * FROM products ORDER BY kind,id"); await conn.close(); lines = []
    for p in rows:
        lines.append(f"{p['name']} {p['price_rub']:.0f}₽/{p['price_stars']}звезд {p['stock']} шт" if p["kind"] == "account" else f"{p['name']} / {p['price_rub']:.0f}₽ — {p['stock']} шт")
    await m.answer("\n".join(lines) if lines else "Склад пуст.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Добавить товар", callback_data="add_product")], [inline_back("back:admin")]]))

@dp.callback_query(F.data == "back:admin")
async def back_admin(call, state):
    if not await subscription_gate(call): return
    if not is_admin(call.from_user.id): return
    await state.clear(); await call.answer(); await call.message.answer("Админ панель", reply_markup=admin_kb())

@dp.callback_query(F.data == "add_product")
async def add_start(call, state):
    if not await subscription_gate(call): return
    if not is_admin(call.from_user.id): return
    await state.set_state(AddProduct.kind); await call.message.answer("Выберите тип товара", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Аккаунт", callback_data="addkind:account")], [InlineKeyboardButton(text="Stars", callback_data="addkind:stars")], [inline_back("back:admin")]])); await call.answer()

@dp.callback_query(AddProduct.kind, F.data.startswith("addkind:"))
async def add_kind(call, state):
    if not await subscription_gate(call): return
    await state.update_data(kind=call.data.split(":")[1]); await state.set_state(AddProduct.name); await call.message.answer("Введите название товара. Для аккаунта, например: +1"); await call.answer()

@dp.message(AddProduct.name)
async def add_name(m, state):
    if not await subscription_gate(m): return
    if not is_admin(m.from_user.id): return
    await state.update_data(name=m.text.strip()); await state.set_state(AddProduct.price_rub); await m.answer("Введите цену в рублях.")

@dp.message(AddProduct.price_rub)
async def add_rub(m, state):
    if not await subscription_gate(m): return
    try: price = float(m.text.replace(",", "."))
    except ValueError: return await m.answer("Введите число, например 50")
    await state.update_data(price_rub=price); await state.set_state(AddProduct.price_stars); await m.answer("Введите цену в Stars.")

@dp.message(AddProduct.price_stars)
async def add_stars(m, state):
    if not await subscription_gate(m): return
    try: price = int(m.text)
    except ValueError: return await m.answer("Введите целое число, например 60")
    await state.update_data(price_stars=price); await state.set_state(AddProduct.stock); await m.answer("Введите количество на складе.")

@dp.message(AddProduct.stock)
async def add_stock(m, state):
    if not await subscription_gate(m): return
    if not is_admin(m.from_user.id): return
    try: count = int(m.text)
    except ValueError: return await m.answer("Введите целое число.")
    if count < 0: return await m.answer("Количество не может быть отрицательным.")
    d = await state.get_data(); conn = await db(); await conn.execute("INSERT INTO products(kind,name,price_rub,price_stars,stock,sales,created_at) VALUES(?,?,?,?,?,0,?)", (d["kind"], d["name"], d["price_rub"], d["price_stars"], count, now_msk().isoformat())); await conn.commit(); await conn.close(); await state.clear(); await m.answer("Товар добавлен на склад.", reply_markup=admin_kb())

@dp.message(Broadcast.text)
async def broadcast(m, state):
    if not await subscription_gate(m): return
    if not is_admin(m.from_user.id): return
    conn = await db(); users = await all_rows(conn, "SELECT id FROM users"); await conn.close(); sent = 0
    for u in users:
        try: await bot.send_message(u["id"], m.text); sent += 1
        except Exception: pass
    await state.clear(); await m.answer(f"Рассылка завершена. Отправлено: {sent}", reply_markup=admin_kb())

@dp.message(F.text == TEXTS["change_button"])
async def change_button(m, state):
    if not await subscription_gate(m): return
    if not is_admin(m.from_user.id): return
    await state.update_data(edit_mode="button"); await state.set_state(EditText.key); await m.answer("Введите ключ кнопки: catalog, reviews, support, admin, stats, stock, broadcast, edit_text, accounts или stars")

@dp.message(F.text == TEXTS["change_message"])
async def change_message(m, state):
    if not await subscription_gate(m): return
    if not is_admin(m.from_user.id): return
    await state.update_data(edit_mode="message"); await state.set_state(EditText.key); await m.answer("Введите ключ сообщения: welcome, choose_type или другой ключ из списка")

@dp.message(EditText.key)
async def edit_key(m, state):
    if not await subscription_gate(m): return
    if not is_admin(m.from_user.id): return
    k = m.text.strip()
    if k not in TEXTS: return await m.answer("Такого ключа нет. Попробуйте ещё раз.")
    await state.update_data(key=k); await state.set_state(EditText.value); await m.answer("Введите новый текст.")

@dp.message(EditText.value)
async def edit_value(m, state):
    if not await subscription_gate(m): return
    if not is_admin(m.from_user.id): return
    d = await state.get_data(); conn = await db(); await conn.execute("INSERT INTO texts(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (d["key"], m.text)); await conn.commit(); await conn.close(); await state.clear(); await m.answer("Текст изменён. Изменение применяется сразу.", reply_markup=admin_kb())

async def main():
    await init_db(); await prepare_subscription_link(); await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
