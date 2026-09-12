import asyncio
import os
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

    # TECHNICAL MAINTENANCE: the referral button is deliberately handled
    # here by replacing the actual registered generic router callback.
    # This does not touch referral tables or referral progress.
    async def referral_maintenance(m):
        if not await subscription_gate(m):
            return
        await m.answer(
            "🔧 <b>Раздел на тех работах</b>\n\n"
            "Новости — @noverashop",
            reply_markup=app.home_kb(m.from_user.id),
        )

    # Patch the router function itself. The generic handler's callback is
    # replaced with a wrapper, so the old `referral_info(m)` branch can never
    # execute for the referral button.
    original_router = getattr(app, "router", None)
    if original_router and not getattr(app, "_noverashop_router_patched", False):
        async def router_maintenance(m, state):
            if m.text == texts["referral"]:
                return await referral_maintenance(m)
            return await original_router(m, state)
        app.router = router_maintenance
        for handler in dispatcher.message.handlers:
            if getattr(handler, "callback", None) is original_router:
                handler.callback = router_maintenance
        app._noverashop_router_patched = True

    # Also keep a direct handler as a fallback and place it first.
    if not getattr(app, "_noverashop_referral_handler", False):
        dispatcher.message.register(referral_maintenance, StateFilter(None), F.text == texts["referral"])
        for index, handler in enumerate(dispatcher.message.handlers):
            if getattr(handler, "callback", None) is referral_maintenance:
                dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop(index))
                break
        app._noverashop_referral_handler = True

    async def admin_referrals(m):
        if not is_admin(m.from_user.id):
            return
        conn = await app.db()
        rows = await conn.execute_fetchall(
            "SELECT r.*,u.username,u.first_name,(SELECT COUNT(*) FROM referral_joins j WHERE j.referral_id=r.id AND j.id>COALESCE((SELECT claim_join_id FROM referral_claims c WHERE c.referral_id=r.id),0)) c FROM referral_links r LEFT JOIN users u ON u.id=r.owner_id ORDER BY r.id DESC"
        )
        await conn.close()
        if not rows:
            return await m.answer("Реферальных ссылок пока нет.", reply_markup=app.admin_kb())
        kb = app.InlineKeyboardBuilder()
        for r in rows:
            owner = f"@{r['username']}" if r['username'] else str(r['owner_id'])
            kb.button(text=f"🔗 {owner} — {r['c']}/{app.REFERRAL_GOAL}", callback_data=f"ref:view:{r['id']}")
        kb.button(text="⬅️ Назад", callback_data="back:admin")
        kb.adjust(1)
        await m.answer("Все реферальные ссылки:", reply_markup=kb.as_markup())

    app.admin_referrals = admin_referrals

    async def admin_ref_view(call):
        if not is_admin(call.from_user.id):
            return
        rid = int(call.data.split(":")[2])
        conn = await app.db()
        r = await conn.execute_fetchone(
            "SELECT r.*,u.username,u.first_name,(SELECT COUNT(*) FROM referral_joins j WHERE j.referral_id=r.id AND j.id>COALESCE((SELECT claim_join_id FROM referral_claims c WHERE c.referral_id=r.id),0)) c FROM referral_links r LEFT JOIN users u ON u.id=r.owner_id WHERE r.id=?",
            (rid,),
        )
        invited = await conn.execute_fetchall(
            "SELECT j.invited_user_id,j.verified_at,u.username,u.first_name FROM referral_joins j LEFT JOIN users u ON u.id=j.invited_user_id WHERE j.referral_id=? ORDER BY j.id",
            (rid,),
        )
        await conn.close()
        if not r:
            return await call.answer("Ссылка не найдена", show_alert=True)
        owner = f"@{r['username']}" if r['username'] else str(r['owner_id'])
        me = await app.bot.get_me()
        url = f"https://t.me/{me.username}?start=ref_{r['code']}"
        lines = []
        for i, u in enumerate(invited, 1):
            lines.append(f"{i}. @{u['username']} — ID <code>{u['invited_user_id']}</code>" if u['username'] else f"{i}. ID <code>{u['invited_user_id']}</code>")
        users_text = "\n".join(lines) if lines else "пока никто"
        await call.message.edit_text(
            f"🔗 <b>Реферальная ссылка</b>\n<code>{url}</code>\n\n👤 Владелец: {owner}\n🆔 ID: <code>{r['owner_id']}</code>\n👥 Рефералов в текущем раунде: <b>{r['c']}/{app.REFERRAL_GOAL}</b>\n\n<b>Пришедшие пользователи (вся история):</b>\n{users_text}",
            reply_markup=app.InlineKeyboardMarkup(inline_keyboard=[[app.inline_back("admin:referrals")]]),
        )
        await call.answer()

    app.admin_ref_view = admin_ref_view

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

    dispatcher.message.register(change_button, StateFilter(None), F.text == texts["change_button"])
    dispatcher.message.register(change_message, StateFilter(None), F.text == texts["change_message"])
    handlers = dispatcher.message.handlers
    for callback in (change_message, change_button):
        for index, handler in enumerate(handlers):
            if getattr(handler, "callback", None) is callback:
                handlers.insert(0, handlers.pop(index))
                break

    dispatcher._noverashop_runtime_fixes = True


async def _patched_start_polling(self, *args, **kwargs):
    _install_runtime_fixes(self)
    return await _original_start_polling(self, *args, **kwargs)

Dispatcher.start_polling = _patched_start_polling
