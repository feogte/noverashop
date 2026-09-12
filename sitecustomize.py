import asyncio
import os
import re
import time
from pathlib import Path

import aiosqlite
from aiogram import Dispatcher, F
from aiogram.filters import StateFilter

if Path("/app").is_dir():
    os.chdir("/app")

async def _execute_fetchone(self, sql, parameters=()):
    async with self.execute(sql, parameters) as cursor:
        return await cursor.fetchone()

if not hasattr(aiosqlite.Connection, "execute_fetchone"):
    aiosqlite.Connection.execute_fetchone = _execute_fetchone

_original_start_polling = Dispatcher.start_polling


def _install_runtime_fixes(dispatcher):
    if getattr(dispatcher, "_noverashop_runtime_fixes", False):
        return

    try:
        import __main__ as app
        EditText = app.EditText
        texts = app.TEXTS
        subscription_gate = app.subscription_gate
        is_admin = app.is_admin
        original_db = app.db
    except Exception:
        return

    async def db_with_busy_timeout():
        conn = await original_db()
        await conn.execute("PRAGMA busy_timeout=10000")
        return conn

    if not getattr(app, "_noverashop_db_hardened", False):
        app.db = db_with_busy_timeout
        app._noverashop_db_hardened = True

    original_description_update = app.update_user_count_description
    description_lock = asyncio.Lock()
    description_state = {"last": 0.0, "running": False}

    async def throttled_description_update():
        now = time.monotonic()
        if now - description_state["last"] < 15 or description_state["running"]:
            return
        async with description_lock:
            now = time.monotonic()
            if now - description_state["last"] < 15:
                return
            description_state["running"] = True
            try:
                await original_description_update()
                description_state["last"] = time.monotonic()
            finally:
                description_state["running"] = False

    if not getattr(app, "_noverashop_description_throttled", False):
        app.update_user_count_description = throttled_description_update
        app._noverashop_description_throttled = True

    async def setup_referral_rounds():
        conn = await app.db()
        cols = await conn.execute_fetchall("PRAGMA table_info(referral_claims)")
        names = {row[1] for row in cols}
        if "claim_join_id" not in names:
            await conn.execute("ALTER TABLE referral_claims ADD COLUMN claim_join_id INTEGER")
        claims = await conn.execute_fetchall("SELECT id, referral_id, claim_join_id FROM referral_claims")
        for claim in claims:
            if claim[2] is not None:
                continue
            row = await conn.execute_fetchone(
                "SELECT id FROM referral_joins WHERE referral_id=? ORDER BY id LIMIT 1 OFFSET ?",
                (claim[1], app.REFERRAL_GOAL - 1),
            )
            checkpoint = row[0] if row else 0
            await conn.execute("UPDATE referral_claims SET claim_join_id=? WHERE id=?", (checkpoint, claim[0]))
        await conn.commit()
        await conn.close()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(setup_referral_rounds())
    except RuntimeError:
        pass

    original_finalize = app.finalize_referral

    async def finalize_referral_rounds(user):
        if not await app.is_subscribed(user.id):
            return False
        result = await original_finalize(user)
        if not result:
            return result
        conn = await app.db()
        join = await conn.execute_fetchone("SELECT referral_id,id FROM referral_joins WHERE invited_user_id=?", (user.id,))
        if not join:
            await conn.close()
            return result
        referral_id, join_id = join[0], join[1]
        claim = await conn.execute_fetchone("SELECT owner_id,claim_join_id FROM referral_claims WHERE referral_id=?", (referral_id,))
        if not claim:
            await conn.close()
            return result
        checkpoint = claim[1] or 0
        count = await conn.execute_fetchone("SELECT COUNT(*) FROM referral_joins WHERE referral_id=? AND id>?", (referral_id, checkpoint))
        if count[0] < app.REFERRAL_GOAL:
            await conn.close()
            return result
        nth = await conn.execute_fetchone(
            "SELECT id FROM referral_joins WHERE referral_id=? AND id>? ORDER BY id LIMIT 1 OFFSET ?",
            (referral_id, checkpoint, app.REFERRAL_GOAL - 1),
        )
        new_checkpoint = nth[0] if nth else join_id
        now = app.now_msk().isoformat()
        cur = await conn.execute(
            "UPDATE referral_claims SET claim_join_id=?, status='pending', created_at=? WHERE referral_id=? AND COALESCE(claim_join_id,0) < ?",
            (new_checkpoint, now, referral_id, new_checkpoint),
        )
        changed = cur.rowcount
        await cur.close()
        owner_id = claim[0]
        await conn.commit()
        await conn.close()
        if changed and checkpoint:
            try:
                await app.bot.send_message(owner_id, "🎉 <b>Поздравляем! Вы снова достигли 20 рефералов.</b>\n\nОжидайте модерации, после чего с вами свяжутся.")
            except Exception:
                pass
        return result

    if not getattr(app, "_noverashop_referral_rounds", False):
        app.finalize_referral = finalize_referral_rounds
        app._noverashop_referral_rounds = True

    async def referral_maintenance(m):
        if not await subscription_gate(m):
            return
        await m.answer(
            "Раздел на тех работах\n\n"
            "Новости — @noverashop",
            reply_markup=app.home_kb(m.from_user.id),
        )

    original_router = getattr(app, "router", None)
    if original_router and not getattr(app, "_noverashop_router_patched", False):
        async def router_maintenance(m, state):
            if m.text == texts["referral"]:
                return await referral_maintenance(m)
            if is_admin(m.from_user.id):
                if m.text == texts["stats"]:
                    return await app.stats(m)
                if m.text == texts["stock"]:
                    return await app.stock(m)
                if m.text == texts["edit_text"]:
                    return await m.answer("Что изменить?", reply_markup=app.edit_kb())
                if m.text == texts["change_button"]:
                    await state.update_data(edit_mode="button")
                    await state.set_state(EditText.key)
                    return await m.answer("Введите ключ кнопки: catalog, reviews, support, admin, stats, stock, broadcast, edit_text, accounts или stars")
                if m.text == texts["change_message"]:
                    await state.update_data(edit_mode="message")
                    await state.set_state(EditText.key)
                    return await m.answer("Введите ключ сообщения: welcome, choose_type или другой ключ из списка")
            return await original_router(m, state)
        app.router = router_maintenance
        for handler in dispatcher.message.handlers:
            if getattr(handler, "callback", None) is original_router:
                handler.callback = router_maintenance
        app._noverashop_router_patched = True

    # Country flags for account products.
    # The first matching calling-code prefix wins; longest prefixes are checked first.
    CALLING_CODE_ISO = {
        "20":"EG","211":"SS","212":"MA","213":"DZ","216":"TN","218":"LY",
        "220":"GM","221":"SN","222":"MR","223":"ML","224":"GN","225":"CI","226":"BF","227":"NE","228":"TG","229":"BJ",
        "230":"MU","231":"LR","232":"SL","233":"GH","234":"NG","235":"TD","236":"CF","237":"CM","238":"CV","239":"ST",
        "240":"GQ","241":"GA","242":"CG","243":"CD","244":"AO","245":"GW","246":"IO","247":"SH","248":"SC","249":"SD",
        "250":"RW","251":"ET","252":"SO","253":"DJ","254":"KE","255":"TZ","256":"UG","257":"BI","258":"MZ","260":"ZM",
        "261":"MG","262":"RE","263":"ZW","264":"NA","265":"MW","266":"LS","267":"BW","268":"SZ","269":"KM","27":"ZA",
        "290":"SH","291":"ER","297":"AW","298":"FO","299":"GL",
        "30":"GR","31":"NL","32":"BE","33":"FR","34":"ES","350":"GI","351":"PT","352":"LU","353":"IE","354":"IS","355":"AL","356":"MT","357":"CY","358":"FI","359":"BG",
        "36":"HU","370":"LT","371":"LV","372":"EE","373":"MD","374":"AM","375":"BY","376":"AD","377":"MC","378":"SM","379":"VA",
        "380":"UA","381":"RS","382":"ME","383":"XK","385":"HR","386":"SI","387":"BA","389":"MK",
        "39":"IT","40":"RO","41":"CH","420":"CZ","421":"SK","423":"LI","43":"AT","44":"GB","45":"DK","46":"SE","47":"NO","48":"PL","49":"DE",
        "500":"FK","501":"BZ","502":"GT","503":"SV","504":"HN","505":"NI","506":"CR","507":"PA","508":"PM","509":"HT","51":"PE","52":"MX","53":"CU","54":"AR","55":"BR","56":"CL","57":"CO","58":"VE",
        "590":"GP","591":"BO","592":"GY","593":"EC","594":"GF","595":"PY","596":"MQ","597":"SR","598":"UY","599":"CW",
        "60":"MY","61":"AU","62":"ID","63":"PH","64":"NZ","65":"SG","66":"TH",
        "670":"TL","672":"AQ","673":"BN","674":"NR","675":"PG","676":"TO","677":"SB","678":"VU","679":"FJ","680":"PW","681":"WF","682":"CK","683":"NU","685":"WS","686":"KI","687":"NC","688":"TV","689":"PF","690":"TK","691":"FM","692":"MH",
        "800":"UN","808":"UN","81":"JP","82":"KR","84":"VN","850":"KP","852":"HK","853":"MO","855":"KH","856":"LA","86":"CN","880":"BD","886":"TW",
        "90":"TR","91":"IN","92":"PK","93":"AF","94":"LK","95":"MM","960":"MV","961":"LB","962":"JO","963":"SY","964":"IQ","965":"KW","966":"SA","967":"YE","968":"OM","970":"PS","971":"AE","972":"IL","973":"BH","974":"QA","975":"BT","976":"MN","977":"NP","98":"IR","992":"TJ","993":"TM","994":"AZ","995":"GE","996":"KG","998":"UZ",
    }

    def _flag_from_iso(iso):
        if not iso or len(iso) != 2 or not iso.isalpha():
            return ""
        return "".join(chr(127397 + ord(ch)) for ch in iso.upper())

    def _account_flag(name):
        # Keep the original product text intact; only prepend a country flag.
        # Supports entries such as "+77", "+77 5 лет", "+1 ...", etc.
        digits_match = re.search(r"\+\s*(\d[\d\s().-]*)", str(name))
        if not digits_match:
            return ""
        digits = re.sub(r"\D", "", digits_match.group(1))
        if not digits:
            return ""

        # +7 is shared by Russia and Kazakhstan. Kazakhstan uses +76/+77;
        # other +7 ranges are Russian. Plain +7 therefore remains Russia.
        if digits.startswith("7"):
            if len(digits) >= 2 and digits[1] in "67":
                return _flag_from_iso("KZ")
            return _flag_from_iso("RU")

        # NANP (+1) is shared by multiple countries. Without a full number,
        # the conventional default is the US flag.
        if digits.startswith("1"):
            return _flag_from_iso("US")

        for length in (4, 3, 2, 1):
            code = digits[:length]
            iso = CALLING_CODE_ISO.get(code)
            if iso and iso != "UN":
                return _flag_from_iso(iso)
        return ""

    def product_label(product, kind=None):
        name = str(product["name"])
        if kind == "account" or str(product.get("kind", "")) == "account":
            flag = _account_flag(name)
            if flag and not name.startswith(flag):
                return f"{flag} {name}"
        return name

    app.product_label = product_label

    async def change_button(message, state):
        if not await subscription_gate(message):
            return
        if not is_admin(message.from_user.id):
            return
        await state.update_data(edit_mode="button")
        await state.set_state(EditText.key)
        await message.answer("Введите ключ кнопки: catalog, reviews, support, admin, stats, stock, broadcast, edit_text, accounts или stars")

    async def change_message(message, state):
        if not await subscription_gate(message):
            return
        if not is_admin(message.from_user.id):
            return
        await state.update_data(edit_mode="message")
        await state.set_state(EditText.key)
        await message.answer("Введите ключ сообщения: welcome, choose_type или другой ключ из списка")

    async def stock_handler(message, state):
        if not await subscription_gate(message):
            return
        if not is_admin(message.from_user.id):
            return
        await app.stock(message)

    async def edit_text_handler(message, state):
        if not await subscription_gate(message):
            return
        if not is_admin(message.from_user.id):
            return
        await message.answer("Что изменить?", reply_markup=app.edit_kb())

    async def stats_handler(message, state):
        if not await subscription_gate(message):
            return
        if not is_admin(message.from_user.id):
            return
        await app.stats(message)

    # Register explicit admin handlers and put them before the generic text router.
    dispatcher.message.register(change_button, StateFilter(None), F.text == texts["change_button"])
    dispatcher.message.register(change_message, StateFilter(None), F.text == texts["change_message"])
    dispatcher.message.register(stock_handler, StateFilter(None), F.text == texts["stock"])
    dispatcher.message.register(edit_text_handler, StateFilter(None), F.text == texts["edit_text"])
    dispatcher.message.register(stats_handler, StateFilter(None), F.text == texts["stats"])

    priority = (stats_handler, edit_text_handler, stock_handler, change_message, change_button, referral_maintenance)
    handlers = dispatcher.message.handlers
    for callback in priority:
        for index, handler in enumerate(handlers):
            if getattr(handler, "callback", None) is callback:
                handlers.insert(0, handlers.pop(index))
                break

    dispatcher._noverashop_runtime_fixes = True


async def _patched_start_polling(self, *args, **kwargs):
    _install_runtime_fixes(self)
    return await _original_start_polling(self, *args, **kwargs)

Dispatcher.start_polling = _patched_start_polling