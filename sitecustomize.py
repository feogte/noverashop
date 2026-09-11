import os
from pathlib import Path

import aiosqlite


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
