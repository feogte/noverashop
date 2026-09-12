import asyncio
import os
import time
from pathlib import Path

import aiosqlite
from aiogram import Dispatcher, F
from aiogram.filters import StateFilter


# Bothost keeps persistent bot data in /app/data.
# Force the working directory to /app so the bot's existing
# relative database path (data/shop.db) always points to that volume.
if Path("/app").is_dir():
    os.chdir("/app")


async def _execute_fetchone(self, sql, parameters=()):
    async with self.execute(sql, parameters) as cursor:
        return await cursor.fetchone()


# aiosqlite provides execute_fetchall(), but not execute_fetchone().
# The bot uses execute_fetchone() in several places, so add a small
# compatibility method automatically when Python starts this project.
if not hasattr(aiosqlite.Connection, "execute_fetchone"):
    aiosqlite.Connection.execute_fetchone = _execute_fetchone


# Fix the admin text editor handler order.
# In main.py the generic StateFilter(None)+F.text router is registered
# before the two "Изменить текст ..." handlers. Therefore the router
# consumes those buttons and the bot appears to do nothing. Register
# the two specific handlers first at polling startup and move them to
# the beginning of the observer so they win over the generic router.
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

    # SQLite hardening for bursts of simultaneous users. Connections can
    # wait briefly instead of immediately failing with "database is locked".
    async def db_with_busy_timeout():
        conn = await original_db()
        await conn.execute("PRAGMA busy_timeout=10000")
        return conn

    if not getattr(app, "_noverashop_db_hardened", False):
        app.db = db_with_busy_timeout
        app._noverashop_db_hardened = True

    # main.py currently updates the Telegram bot short description after
    # every saved user. During a referral spike that creates one extra
    # Telegram API request per user. Keep the counter accurate enough for
    # the admin while coalescing bursts into at most one update per 15 sec.
    original_description_update = app.update_user_count_description
    description_lock = asyncio.Lock()
    description_state = {"last": 0.0, "running": False}

    async def throttled_description_update():
        now = time.monotonic()
        if now - description_state["last"] < 15:
            return
        if description_state["running"]:
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

    async def change_button(message, state):
        if not await subscription_gate(message):
            return
        if not is_admin(message.from_user.id):
            return
        await state.update_data(edit_mode="button")
        await state.set_state(EditText.key)
        await message.answer(
            "Введите ключ кнопки: catalog, reviews, support, admin, stats, stock, broadcast, edit_text, accounts или stars"
        )

    async def change_message(message, state):
        if not await subscription_gate(message):
            return
        if not is_admin(message.from_user.id):
            return
        await state.update_data(edit_mode="message")
        await state.set_state(EditText.key)
        await message.answer(
            "Введите ключ сообщения: welcome, choose_type или другой ключ из списка"
        )

    dispatcher.message.register(
        change_button,
        StateFilter(None),
        F.text == texts["change_button"],
    )
    dispatcher.message.register(
        change_message,
        StateFilter(None),
        F.text == texts["change_message"],
    )

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
