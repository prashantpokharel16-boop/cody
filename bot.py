import asyncio
import logging

import discord
from discord.ext import commands

import config
from database import Database
from database import initialize_schema


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("cody")


# ============================================================
# COGS
# ============================================================

COGS = [
    "cogs.moderation",
    "cogs.welcome",
    "cogs.tickets",
    "cogs.giveaways",
    "cogs.announce",
]


# ============================================================
# BOT
# ============================================================

class Cody(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()

        # Required for welcome/member join system
        intents.members = True

        # Required for message-based features
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        self.database = None
        self.old_commands_cleared = False

    # ========================================================
    # SETUP HOOK
    # ========================================================

    async def setup_hook(self):

        logger.info("Starting bot setup...")

        # ----------------------------------------------------
        # DATABASE
        # ----------------------------------------------------

        self.database = Database(config.DATABASE_PATH)

        await self.database.connect()

        await initialize_schema(self.database)

        logger.info("Database initialized.")

        # ----------------------------------------------------
        # LOAD COGS
        # ----------------------------------------------------

        for extension in COGS:

            try:
                await self.load_extension(extension)

                logger.info(
                    "Loaded extension: %s",
                    extension
                )

            except Exception:

                logger.exception(
                    "Failed to load extension: %s",
                    extension
                )

        # ----------------------------------------------------
        # GLOBAL SLASH COMMAND SYNC
        # ----------------------------------------------------

        try:

            await self.tree.sync()

            logger.info(
                "Global slash commands synced."
            )

        except Exception:

            logger.exception(
                "Failed to sync global slash commands."
            )

    # ========================================================
    # READY
    # ========================================================

    async def on_ready(self):

        logger.info(
            "Logged in as %s (%s)",
            self.user,
            self.user.id
        )

        logger.info(
            "Connected to %d guild(s).",
            len(self.guilds)
        )

        for guild in self.guilds:

            logger.info(
                "Guild: %s (%s)",
                guild.name,
                guild.id
            )

        # ----------------------------------------------------
        # REMOVE OLD GUILD-SPECIFIC COMMANDS
        # ----------------------------------------------------
        #
        # Older versions of the bot used GUILD_ID and may have
        # left guild-specific slash commands registered.
        #
        # Those old commands can appear together with the new
        # global commands and cause duplicates.
        #
        # This clears ONLY the old guild-specific commands.
        # Global commands remain untouched.
        # ----------------------------------------------------

        if not self.old_commands_cleared:

            self.old_commands_cleared = True

            for guild in self.guilds:

                try:

                    self.tree.clear_commands(
                        guild=guild
                    )

                    await self.tree.sync(
                        guild=guild
                    )

                    logger.info(
                        "Cleared old guild commands from %s (%s).",
                        guild.name,
                        guild.id
                    )

                except Exception:

                    logger.exception(
                        "Failed to clear old commands from %s (%s).",
                        guild.name,
                        guild.id
                    )


# ============================================================
# MAIN
# ============================================================

async def main():

    # Make sure required configuration exists
    config.validate_config()

    bot = Cody()

    try:

        await bot.start(
            config.DISCORD_TOKEN
        )

    finally:

        if bot.database is not None:

            await bot.database.close()


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )
