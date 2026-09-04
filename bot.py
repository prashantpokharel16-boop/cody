import asyncio
import logging

import discord
from discord.ext import commands

import config
from database import initialize_schema
from database import Database


COGS = [
    "cogs.moderation",
    "cogs.automod",
    "cogs.welcome",
    "cogs.logging",
    "cogs.tickets",
    "cogs.roles",
    "cogs.levels",
    "cogs.economy",
    "cogs.fun",
    "cogs.utility",
    "cogs.polls",
    "cogs.configuration",
]


class AllInOneBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()

        intents.members = True
        intents.message_content = True
        intents.presences = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        self.database = Database(
            config.DATABASE_PATH
        )

    async def setup_hook(self):
        """Initialize database and load all Cogs."""

        await self.database.connect()

        await initialize_schema(
            self.database
        )

        for extension in COGS:
            try:
                await self.load_extension(extension)

                logging.info(
                    "Loaded extension: %s",
                    extension,
                )

            except Exception:
                logging.exception(
                    "Failed to load extension: %s",
                    extension,
                )

        guild_id = config.GUILD_ID

        if guild_id.isdigit():
            guild = discord.Object(
                id=int(guild_id)
            )

            self.tree.copy_global_to(
                guild=guild
            )

            await self.tree.sync(
                guild=guild
            )

            logging.info(
                "Slash commands synced to guild %s",
                guild_id,
            )

        else:
            await self.tree.sync()

            logging.info(
                "Global slash commands synced."
            )

    async def on_ready(self):
        logging.info(
            "Logged in as %s (%s)",
            self.user,
            self.user.id if self.user else "unknown",
        )

        logging.info(
            "Connected to %d guild(s).",
            len(self.guilds),
        )

    async def close(self):
        """Gracefully close bot and database."""

        await self.database.close()

        await super().close()


async def main():
    config.validate_config()
    config.ensure_directories()

    logging.basicConfig(
        level=getattr(
            logging,
            config.LOG_LEVEL,
            logging.INFO,
        ),
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
    )

    bot = AllInOneBot()

    try:
        await bot.start(
            config.DISCORD_TOKEN
        )

    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        pass
