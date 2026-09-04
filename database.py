from pathlib import Path
from typing import Optional

import aiosqlite


class Database:
    """Async SQLite database manager."""

    def __init__(self, path: str):
        self.path = path
        self.connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open the database connection."""

        Path(self.path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.connection = await aiosqlite.connect(self.path)

        self.connection.row_factory = aiosqlite.Row

        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA foreign_keys=ON;")

        await self.connection.commit()

    async def execute(
        self,
        query: str,
        parameters: tuple = (),
    ):
        """Execute a query."""

        if self.connection is None:
            raise RuntimeError("Database is not connected.")

        cursor = await self.connection.execute(
            query,
            parameters,
        )

        await self.connection.commit()

        return cursor

    async def fetchone(
        self,
        query: str,
        parameters: tuple = (),
    ):
        """Fetch one row."""

        if self.connection is None:
            raise RuntimeError("Database is not connected.")

        cursor = await self.connection.execute(
            query,
            parameters,
        )

        return await cursor.fetchone()

    async def fetchall(
        self,
        query: str,
        parameters: tuple = (),
    ):
        """Fetch all rows."""

        if self.connection is None:
            raise RuntimeError("Database is not connected.")

        cursor = await self.connection.execute(
            query,
            parameters,
        )

        return await cursor.fetchall()

    async def close(self) -> None:
        """Close the database connection."""

        if self.connection is not None:
            await self.connection.close()
            self.connection = None
