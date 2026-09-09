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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Telegram bot token was not provided by the hosting platform")

ADMIN_ID = 8872934046
DB_PATH = "data/shop.db"
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

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
    kind=State(); name=State(); price_rub=State(); price_stars=State(); stock=State()
class Broadcast(StatesGroup):
    text=State()
class EditText(StatesGroup):
    key=State(); value=State()
class Payment(StatesGroup):
    screenshot=State()

def is_admin(uid): return uid == ADMIN_ID

async def db():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn

# aiosqlite has execute_fetchall(), but does not provide execute_fetchone().
# Use explicit cursors so the bot works with the installed aiosqlite version.
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
    conn=await db()
    await conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS texts(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, price_rub REAL NOT NULL, price_stars INTEGER NOT NULL, stock INTEGER NOT NULL DEFAULT 0, sales INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, product_name TEXT NOT NULL, payment TEXT NOT NULL, amount REAL NOT NULL, screenshot_file_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
    """)
    for k,v in TEXTS.items(): await conn.execute("INSERT OR IGNORE INTO texts(key,value) VALUES(?,?)",(k,v))
    await conn.commit(); await conn.close()

async def text(k):
    conn=await db(); row=await one(conn,"SELECT value FROM texts WHERE key=?",(k,)); await conn.close()
    return row["value"] if row else TEXTS.get(k,k)

async def save_user(m):
    u=m.from_user; conn=await db()
    await conn.execute("""INSERT INTO users(id,username,first_name,created_at) VALUES(?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name""",
    (u.id,u.username,u.first_name,datetime.now().isoformat()))
    await conn.commit(); await conn.close()

def admin_kb():
    kb=ReplyKeyboardBuilder()
    for k in ("stats","stock","broadcast","edit_text"): kb.button(text=TEXTS[k])
    kb.adjust(2,2); return kb.as_markup(resize_keyboard=True)

async def home_kb(uid):
    kb=ReplyKeyboardBuilder(); kb.button(text=await text("catalog")); kb.adjust(1)
    row=ReplyKeyboardBuilder(); row.button(text=await text("reviews")); row.button(text=await text("support"))
    for button in list(row.buttons): kb.add(button)
    kb.adjust(1,2)
    if is_admin(uid): kb.button(text=await text("admin")); kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

@dp.message(CommandStart())
async def start(m,state):
    await state.clear(); await save_user(m)
    name=m.from_user.username or m.from_user.first_name or "пользователь"
    await m.answer((await text("welcome")).format(name=name),reply_markup=await home_kb(m.from_user.id))

@dp.message(Command("adm"))
async def adm(m):
    if is_admin(m.from_user.id): await m.answer("Админ панель",reply_markup=admin_kb())

async def catalog_kb():
    kb=ReplyKeyboardBuilder(); kb.button(text=await text("accounts")); kb.button(text=await text("stars")); kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

@dp.message(F.text)
async def router(m,state):
    await save_user(m); v=m.text
    if v==await text("catalog"): await m.answer(await text("choose_type"),reply_markup=await catalog_kb())
    elif v==await text("reviews"): await m.answer("Отзывы пока пусты.")
    elif v==await text("support"): await m.answer("По всем вопросам напишите администратору.")
    elif is_admin(m.from_user.id) and v==await text("admin"): await m.answer("Админ панель",reply_markup=admin_kb())
    elif is_admin(m.from_user.id) and v==TEXTS["stats"]: await stats(m)
    elif is_admin(m.from_user.id) and v==TEXTS["stock"]: await stock(m)
    elif is_admin(m.from_user.id) and v==TEXTS["broadcast"]:
        await state.set_state(Broadcast.text); await m.answer("Отправьте текст рассылки.")
    elif is_admin(m.from_user.id) and v==TEXTS["edit_text"]: await m.answer("Что изменить?",reply_markup=edit_kb())
    elif v==await text("accounts"): await products(m,"account")
    elif v==await text("stars"): await products(m,"stars")

def edit_kb():
    kb=ReplyKeyboardBuilder(); kb.button(text=TEXTS["change_button"]); kb.button(text=TEXTS["change_message"]); kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

async def products(m,kind):
    conn=await db(); rows=await all_rows(conn,"SELECT * FROM products WHERE kind=? AND stock>0 ORDER BY id",(kind,)); await conn.close()
    if not rows: return await m.answer("Товаров в наличии нет.")
    kb=InlineKeyboardBuilder()
    for p in rows:
        if kind=="account":
            code=p["name"].lstrip("+").split()[0]
            flag=next((FLAGS.get(code[:n],"") for n in (3,2,1) if FLAGS.get(code[:n])),"")
            label=f"+{code} {flag} {p['price_rub']:.0f}₽"
        else: label=f"{p['name']} / {p['price_rub']:.0f}₽"
        kb.button(text=label,callback_data=f"product:{p['id']}")
    kb.adjust(1); await m.answer("Выберите товар",reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("product:"))
async def product(call,state):
    pid=int(call.data.split(":")[1]); conn=await db(); p=await one(conn,"SELECT * FROM products WHERE id=? AND stock>0",(pid,)); await conn.close()
    if not p: return await call.answer("Товар закончился",show_alert=True)
    await state.update_data(product_id=pid)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Звезды",callback_data="pay:stars")],[InlineKeyboardButton(text="₽ рубли",callback_data="pay:rub")]])
    await call.message.answer(f"Выбранный аккаунт: {p['name']}\nЦена: {p['price_rub']:.0f}₽ / {p['price_stars']} Stars",reply_markup=kb); await call.answer()

@dp.callback_query(F.data=="pay:stars")
async def pay_stars(call,state):
    d=await state.get_data(); conn=await db(); p=await one(conn,"SELECT * FROM products WHERE id=? AND stock>0",(d.get("product_id"),)); await conn.close()
    if not p: return await call.answer("Товар закончился",show_alert=True)
    await state.update_data(payment="stars",amount=p["price_stars"])
    await call.message.answer(f"отправьте {p['price_stars']} звезд на аккаунт @fegote",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="отправить подтверждение",callback_data="confirm_payment")]])); await call.answer()

@dp.callback_query(F.data=="pay:rub")
async def pay_rub(call,state):
    d=await state.get_data(); conn=await db(); p=await one(conn,"SELECT * FROM products WHERE id=? AND stock>0",(d.get("product_id"),)); await conn.close()
    if not p: return await call.answer("Товар закончился",show_alert=True)
    await state.update_data(payment="rub",amount=p["price_rub"])
    await call.message.answer(f"переведите {p['price_rub']:.0f}₽ на реквизиты:\n+79313716777\n• Т-банк\n• Тимур/Наталья",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="отправить подтверждение",callback_data="confirm_payment")]])); await call.answer()

@dp.callback_query(F.data=="confirm_payment")
async def confirm(call,state):
    await state.set_state(Payment.screenshot); await call.message.answer("Отправьте скрин оплаты одним сообщением."); await call.answer()

@dp.message(Payment.screenshot,F.photo)
async def screenshot(m,state):
    d=await state.get_data(); conn=await db(); p=await one(conn,"SELECT * FROM products WHERE id=?",(d.get("product_id"),))
    if not p: await conn.close(); await state.clear(); return
    now=datetime.now(); cur=await conn.execute("INSERT INTO purchases(user_id,product_id,product_name,payment,amount,screenshot_file_id,created_at) VALUES(?,?,?,?,?,?,?)",(m.from_user.id,p["id"],p["name"],d.get("payment",""),d.get("amount",0),m.photo[-1].file_id,now.isoformat())); pid=cur.lastrowid
    await cur.close(); await conn.commit(); await conn.close()
    username=f"@{m.from_user.username}" if m.from_user.username else "нет"
    caption=f"Новая покупка! 🧾\nкупили: {p['name']}\nайди: {m.from_user.id}\nюзернейм: {username}\nцена: {d.get('amount')} {'Stars' if d.get('payment')=='stars' else '₽'}\nвремя покупки по мск: {now.strftime('%d.%m.%Y %H:%M')}\nзаявка: #{pid}"
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить",callback_data=f"purchase:approve:{pid}"),InlineKeyboardButton(text="Отклонить",callback_data=f"purchase:reject:{pid}")]])
    await bot.send_photo(ADMIN_ID,m.photo[-1].file_id,caption=caption,reply_markup=kb); await m.answer("Заявка отправлена администратору. Ожидайте подтверждения."); await state.clear()

@dp.message(Payment.screenshot)
async def not_photo(m): await m.answer("Нужен именно скрин оплаты в виде фотографии.")

@dp.callback_query(F.data.startswith("purchase:"))
async def purchase_action(call):
    if not is_admin(call.from_user.id): return
    _,action,pid=call.data.split(":"); pid=int(pid); conn=await db(); p=await one(conn,"SELECT * FROM purchases WHERE id=?",(pid,))
    if not p or p["status"]!="pending": await conn.close(); return await call.answer("Заявка уже обработана",show_alert=True)
    if action=="approve":
        await conn.execute("UPDATE purchases SET status='approved' WHERE id=?",(pid,)); await conn.execute("UPDATE products SET stock=stock-1,sales=sales+1 WHERE id=? AND stock>0",(p["product_id"],)); status="Одобрено"
    else: await conn.execute("UPDATE purchases SET status='rejected' WHERE id=?",(pid,)); status="Отклонено"
    await conn.commit(); await conn.close(); await call.message.edit_reply_markup(reply_markup=None); await call.message.answer(f"Заявка #{pid}: {status}")
    try: await bot.send_message(p["user_id"],f"Ваша заявка #{pid}: {status}.")
    except Exception: pass
    await call.answer()

async def stats(m):
    conn=await db(); total=await one(conn,"SELECT COUNT(*) c FROM purchases WHERE status='approved'"); rub=await one(conn,"SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='rub'"); stars=await one(conn,"SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='stars'"); users=await one(conn,"SELECT COUNT(*) c FROM users")
    today=datetime.now().date().isoformat(); tr=await one(conn,"SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='rub' AND date(created_at)=?",(today,)); ts=await one(conn,"SELECT COALESCE(SUM(amount),0) v FROM purchases WHERE status='approved' AND payment='stars' AND date(created_at)=?",(today,)); best=await all_rows(conn,"SELECT product_name,COUNT(*) c FROM purchases WHERE status='approved' GROUP BY product_id ORDER BY c DESC LIMIT 5"); await conn.close()
    besttxt="\n".join(f"{i+1}. {r['product_name']} — {r['c']}" for i,r in enumerate(best)) or "нет"
    await m.answer(f"Всего продаж: {total['c']}\nЗаработано ₽: {rub['v']:.0f}\nЗаработано Stars: {stars['v']:.0f}\nЗа сегодня: {tr['v']:.0f}₽ / {ts['v']:.0f} Stars\nКоличество пользователей: {users['c']}\n\nСамые продаваемые товары:\n{besttxt}")

async def stock(m):
    conn=await db(); rows=await all_rows(conn,"SELECT * FROM products ORDER BY kind,id"); await conn.close(); lines=[]
    for p in rows: lines.append(f"{p['name']} {p['price_rub']:.0f}₽/{p['price_stars']}звезд {p['stock']} шт" if p["kind"]=="account" else f"{p['name']} / {p['price_rub']:.0f}₽ — {p['stock']} шт")
    await m.answer("\n".join(lines) if lines else "Склад пуст.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Добавить товар",callback_data="add_product")]]))

@dp.callback_query(F.data=="add_product")
async def add_start(call,state):
    if not is_admin(call.from_user.id): return
    await state.set_state(AddProduct.kind); await call.message.answer("Выберите тип товара",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Аккаунт",callback_data="addkind:account")],[InlineKeyboardButton(text="Stars",callback_data="addkind:stars")]])); await call.answer()

@dp.callback_query(AddProduct.kind,F.data.startswith("addkind:"))
async def add_kind(call,state):
    await state.update_data(kind=call.data.split(":")[1]); await state.set_state(AddProduct.name); await call.message.answer("Введите название товара. Для аккаунта можно написать, например: +1"); await call.answer()

@dp.message(AddProduct.name)
async def add_name(m,state): await state.update_data(name=m.text.strip()); await state.set_state(AddProduct.price_rub); await m.answer("Введите цену в рублях.")

@dp.message(AddProduct.price_rub)
async def add_rub(m,state):
    try: price=float(m.text.replace(",","."))
    except ValueError: return await m.answer("Введите число, например 50")
    await state.update_data(price_rub=price); await state.set_state(AddProduct.price_stars); await m.answer("Введите цену в Stars.")

@dp.message(AddProduct.price_stars)
async def add_stars(m,state):
    try: price=int(m.text)
    except ValueError: return await m.answer("Введите целое число, например 60")
    await state.update_data(price_stars=price); await state.set_state(AddProduct.stock); await m.answer("Введите количество на складе.")

@dp.message(AddProduct.stock)
async def add_stock(m,state):
    try: count=int(m.text)
    except ValueError: return await m.answer("Введите целое число.")
    d=await state.get_data(); conn=await db(); await conn.execute("INSERT INTO products(kind,name,price_rub,price_stars,stock,sales,created_at) VALUES(?,?,?,?,?,0,?)",(d["kind"],d["name"],d["price_rub"],d["price_stars"],count,datetime.now().isoformat())); await conn.commit(); await conn.close(); await state.clear(); await m.answer("Товар добавлен на склад.",reply_markup=admin_kb())

@dp.message(Broadcast.text)
async def broadcast(m,state):
    if not is_admin(m.from_user.id): return
    conn=await db(); users=await all_rows(conn,"SELECT id FROM users"); await conn.close(); sent=0
    for u in users:
        try: await bot.send_message(u["id"],m.text); sent+=1
        except Exception: pass
    await state.clear(); await m.answer(f"Рассылка завершена. Отправлено: {sent}",reply_markup=admin_kb())

@dp.message(F.text==TEXTS["change_button"])
async def change_button(m,state):
    if not is_admin(m.from_user.id): return
    await state.update_data(edit_mode="button"); await state.set_state(EditText.key); await m.answer("Введите ключ кнопки: catalog, reviews, support, admin, stats, stock, broadcast, edit_text, accounts или stars")

@dp.message(F.text==TEXTS["change_message"])
async def change_message(m,state):
    if not is_admin(m.from_user.id): return
    await state.update_data(edit_mode="message"); await state.set_state(EditText.key); await m.answer("Введите ключ сообщения: welcome, choose_type или другой ключ из списка")

@dp.message(EditText.key)
async def edit_key(m,state):
    k=m.text.strip()
    if k not in TEXTS: return await m.answer("Такого ключа нет. Попробуйте ещё раз.")
    await state.update_data(key=k); await state.set_state(EditText.value); await m.answer("Введите новый текст.")

@dp.message(EditText.value)
async def edit_value(m,state):
    d=await state.get_data(); conn=await db(); await conn.execute("INSERT INTO texts(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(d["key"],m.text)); await conn.commit(); await conn.close(); await state.clear(); await m.answer("Текст изменён. Изменение применяется сразу.",reply_markup=admin_kb())

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
