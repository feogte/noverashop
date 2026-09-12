import asyncio
import os
import time
from pathlib import Path

import aiosqlite
from aiogram import Dispatcher
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

    # Referral rounds: after every 20 verified referrals the counter starts
    # a new round. Old referrals remain in the database for admin history.
    async def setup_referral_rounds():
        conn = await app.db()
        cols = await conn.execute_fetchall("PRAGMA table_info(referral_claims)")
        names = {row[1] for row in cols}
        if "claim_join_id" not in names:
            await conn.execute("ALTER TABLE referral_claims ADD COLUMN claim_join_id INTEGER")

        claims = await conn.execute_fetchall(
            "SELECT id, referral_id, claim_join_id FROM referral_claims"
        )
        for claim in claims:
            if claim[2] is not None:
                continue
            row = await conn.execute_fetchone(
                "SELECT id FROM referral_joins WHERE referral_id=? ORDER BY id LIMIT 1 OFFSET ?",
                (claim[1], app.REFERRAL_GOAL - 1),
            )
            checkpoint = row[0] if row else 0
            await conn.execute(
                "UPDATE referral_claims SET claim_join_id=? WHERE id=?",
                (checkpoint, claim[0]),
            )
        await conn.commit()
        await conn.close()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(setup_referral_rounds())
    except RuntimeError:
        pass

    # Defense in depth: a referral can NEVER be finalized unless the invited
    # user is currently subscribed.
    original_finalize = app.finalize_referral

    async def finalize_referral_rounds(user):
        if not await app.is_subscribed(user.id):
            return False

        result = await original_finalize(user)
        if not result:
            return result

        conn = await app.db()
        join = await conn.execute_fetchone(
            "SELECT referral_id,id FROM referral_joins WHERE invited_user_id=?",
            (user.id,),
        )
        if not join:
            await conn.close()
            return result

        referral_id, join_id = join[0], join[1]
        claim = await conn.execute_fetchone(
            "SELECT owner_id,claim_join_id FROM referral_claims WHERE referral_id=?",
            (referral_id,),
        )
        if not claim:
            await conn.close()
            return result

        checkpoint = claim[1] or 0
        count = await conn.execute_fetchone(
            "SELECT COUNT(*) FROM referral_joins WHERE referral_id=? AND id>?",
            (referral_id, checkpoint),
        )
        current_count = count[0]
        if current_count < app.REFERRAL_GOAL:
            await conn.close()
            return result

        nth = await conn.execute_fetchone(
            "SELECT id FROM referral_joins WHERE referral_id=? AND id>? ORDER BY id LIMIT 1 OFFSET ?",
            (referral_id, checkpoint, app.REFERRAL_GOAL - 1),
        )
        new_checkpoint = nth[0] if nth else join_id
        now = app.now_msk().isoformat()
        cur = await conn.execute(
            "UPDATE referral_claims SET claim_join_id=?, status='pending', created_at=? "
            "WHERE referral_id=? AND COALESCE(claim_join_id,0) < ?",
            (new_checkpoint, now, referral_id, new_checkpoint),
        )
        changed = cur.rowcount
        await cur.close()
        owner_id = claim[0]
        await conn.commit()
        await conn.close()

        if changed and checkpoint:
            try:
                await bot_send_round_notice(app, owner_id, referral_id, new_checkpoint)
            except Exception:
                pass
        return result

    async def bot_send_round_notice(app_module, owner_id, referral_id, checkpoint):
        me = await app_module.bot.get_me()
        url = None
        conn = await app_module.db()
        ref = await conn.execute_fetchone(
            "SELECT code FROM referral_links WHERE id=?", (referral_id,)
        )
        await conn.close()
        if ref:
            url = f"https://t.me/{me.username}?start=ref_{ref[0]}"
        await app_module.bot.send_message(
            owner_id,
            "🎉 <b>Поздравляем! Вы снова достигли 20 рефералов.</b>\n\nОжидайте модерации, после чего с вами свяжутся."
        )
        await app_module.bot.send_message(
            app_module.ADMIN_ID,
            f"🔔 <b>Новая заявка (реферальная)</b>\n\n🔗 Реферальная ссылка: <code>{url or 'нет'}</code>\n🆔 ID владельца: <code>{owner_id}</code>\n👥 Новый раунд: {app_module.REFERRAL_GOAL}/{app_module.REFERRAL_GOAL}"
        )

    if not getattr(app, "_noverashop_referral_rounds", False):
        app.finalize_referral = finalize_referral_rounds
        app._noverashop_referral_rounds = True

    # Maintenance mode for the referral button.
    async def referral_info_maintenance(m):
        await m.answer(
            "Раздел на тех работах\n\n"
            "Новости — @noverashop",
            reply_markup=app.home_kb(m.from_user.id),
        )

    app.referral_info = referral_info_maintenance

    # The button is processed inside main.py's generic `router`. Replace that
    # handler's callback itself so the maintenance response is guaranteed to
    # run before the old referral_info branch can execute.
    for handler in dispatcher.message.handlers:
        callback = getattr(handler, "callback", None)
        if getattr(callback, "__name__", "") == "router":
            original_router = callback

            async def router_maintenance(m, state, _original_router=original_router):
                if m.text == texts["referral"]:
                    if not await subscription_gate(m):
                        return
                    await referral_info_maintenance(m)
                    return
                await _original_router(m, state)

            handler.callback = router_maintenance
            break

    async def admin_referrals(m):
        if not is_admin(m.from_user.id):
            return
        conn = await app.db()
        rows = await conn.execute_fetchall(
            "SELECT r.*,u.username,u.first_name," 
            "(SELECT COUNT(*) FROM referral_joins j WHERE j.referral_id=r.id "
            "AND j.id>COALESCE((SELECT claim_join_id FROM referral_claims c WHERE c.referral_id=r.id),0)) c "
            "FROM referral_links r LEFT JOIN users u ON u.id=r.owner_id ORDER BY r.id DESC"
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
            "SELECT r.*,u.username,u.first_name," 
            "(SELECT COUNT(*) FROM referral_joins j WHERE j.referral_id=r.id "
            "AND j.id>COALESCE((SELECT claim_join_id FROM referral_claims c WHERE c.referral_id=r.id),0)) c "
            "FROM referral_links r LEFT JOIN users u ON u.id=r.owner_id WHERE r.id=?",
            (rid,),
        )
        invited = await conn.execute_fetchall(
            "SELECT j.invited_user_id,j.verified_at,u.username,u.first_name FROM referral_joins j "
            "LEFT JOIN users u ON u.id=j.invited_user_id WHERE j.referral_id=? ORDER BY j.id",
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
            lines.append(
                f"{i}. @{u['username']} — ID <code>{u['invited_user_id']}</code>"
                if u['username'] else f"{i}. ID <code>{u['invited_user_id']}</code>"
            )
        users_text = "\n".join(lines) if lines else "пока никто"
        text_msg = (
            f"🔗 <b>Реферальная ссылка</b>\n<code>{url}</code>\n\n"
            f"👤 Владелец: {owner}\n🆔 ID: <code>{r['owner_id']}</code>\n"
            f"👥 Рефералов в текущем раунде: <b>{r['c']}/{app.REFERRAL_GOAL}</b>\n\n"
            f"<b>Пришедшие пользователи (вся история):</b>\n{users_text}"
        )
        await call.message.edit_text(
            text_msg,
            reply_markup=app.InlineKeyboardMarkup(
                inline_keyboard=[[app.inline_back("admin:referrals")]]
            ),
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

    dispatcher.message.register(change_button, StateFilter(None), __import__("aiogram").F.text == texts["change_button"])
    dispatcher.message.register(change_message, StateFilter(None), __import__("aiogram").F.text == texts["change_message"])

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
