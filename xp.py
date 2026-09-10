import aiosqlite
import math
import os

DB_PATH = os.getenv("XP_DB_PATH", "WaffleHutData.db")

async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS xp (
                user_id TEXT PRIMARY KEY,
                xp INTEGER NOT NULL DEFAULT 0,
                last_xp_time REAL NOT NULL DEFAULT 0
            )
        """)
        await db.commit()

async def get_user_xp(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT xp, last_xp_time FROM xp WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        await cur.close()
        if row:
            return {"xp": row[0], "last_xp_time": row[1]}
        await db.execute("INSERT INTO xp (user_id) VALUES (?)", (user_id,))
        await db.commit()
        return {"xp": 0, "last_xp_time": 0}

async def update_user_xp(user_id: str, xp: int, last_xp_time: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO xp (user_id, xp, last_xp_time)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                xp = excluded.xp,
                last_xp_time = excluded.last_xp_time
            """,
            (user_id, xp, last_xp_time),
        )
        await db.commit()

async def add_xp(user_id: str, delta: int, last_xp_time: float):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT xp FROM xp WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        await cur.close()
        current = row[0] if row else 0
        new_xp = current + delta
        await db.execute(
            """
            INSERT INTO xp (user_id, xp, last_xp_time)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                xp = excluded.xp,
                last_xp_time = excluded.last_xp_time
            """,
            (user_id, new_xp, last_xp_time),
        )
        await db.commit()
        return new_xp

def calculate_level(xp: int) -> int:
    return int(math.sqrt(xp / 100))