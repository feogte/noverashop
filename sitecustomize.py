import os
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


def _install_edit_text_fix(dispatcher):
    if getattr(dispatcher, "_noverashop_edit_text_fix", False):
        return

    try:
        import __main__ as app
        EditText = app.EditText
        texts = app.TEXTS
        subscription_gate = app.subscription_gate
        is_admin = app.is_admin
    except Exception:
        return

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

    dispatcher._noverashop_edit_text_fix = True


async def _patched_start_polling(self, *args, **kwargs):
    _install_edit_text_fix(self)
    return await _original_start_polling(self, *args, **kwargs)


Dispatcher.start_polling = _patched_start_polling
