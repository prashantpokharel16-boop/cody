import asyncio
import io
import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks


# ============================================================
# DATABASE
# ============================================================

GIVEAWAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,

    creator_id INTEGER NOT NULL,
    host_id INTEGER NOT NULL,

    prize TEXT NOT NULL DEFAULT 'Not configured',
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    winners INTEGER NOT NULL DEFAULT 1,

    required_role_id INTEGER,
    banned_role_ids TEXT NOT NULL DEFAULT '[]',

    required_messages INTEGER NOT NULL DEFAULT 0,

    extra_entries TEXT NOT NULL DEFAULT '{}',

    thumbnail_data BLOB,
    thumbnail_filename TEXT,

    status TEXT NOT NULL DEFAULT 'configuring',

    end_time TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP
);


CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,

    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (
        giveaway_id,
        user_id
    )
);


CREATE TABLE IF NOT EXISTS giveaway_winners (
    giveaway_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reroll_number INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (
        giveaway_id,
        user_id,
        reroll_number
    )
);


CREATE INDEX IF NOT EXISTS idx_giveaways_guild
ON giveaways(guild_id);


CREATE INDEX IF NOT EXISTS idx_giveaways_status
ON giveaways(status);


CREATE INDEX IF NOT EXISTS idx_giveaway_entries
ON giveaway_entries(giveaway_id);
"""


# ============================================================
# DATABASE SETUP
# ============================================================

async def setup_giveaway_database(bot):

    await bot.database.connection.executescript(
        GIVEAWAY_SCHEMA
    )

    await bot.database.connection.commit()


# ============================================================
# DURATION PARSER
# ============================================================

UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,

    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,

    "h": 60 * 60,
    "hr": 60 * 60,
    "hrs": 60 * 60,
    "hour": 60 * 60,
    "hours": 60 * 60,

    "d": 60 * 60 * 24,
    "day": 60 * 60 * 24,
    "days": 60 * 60 * 24,

    "w": 60 * 60 * 24 * 7,
    "week": 60 * 60 * 24 * 7,
    "weeks": 60 * 60 * 24 * 7,
}


def parse_duration(value: str) -> Optional[int]:
    """
    Parse durations such as:

    10s
    10S
    10 sec
    10 seconds

    5m
    5 mins
    5 minutes

    2h
    2hr
    2hrs
    2 hours

    2d
    2 days

    1w
    1 week

    1d 5h 30m
    2hrs 20mins
    """

    if not value:
        return None

    value = value.lower().strip()

    # Remove commas.
    value = value.replace(",", " ")

    pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s*"
        r"(weeks?|w|days?|d|"
        r"hours?|hrs?|h|"
        r"minutes?|mins?|min|m|"
        r"seconds?|secs?|sec|s)"
    )

    matches = pattern.findall(value)

    if not matches:
        return None

    total = 0.0
    consumed = 0

    for amount, unit in matches:
        unit = unit.lower()

        if unit not in UNIT_SECONDS:
            return None

        total += (
            float(amount)
            * UNIT_SECONDS[unit]
        )

    # Validate that the input doesn't contain
    # random unsupported text.
    cleaned = pattern.sub("", value)
    cleaned = cleaned.replace(" ", "")

    if cleaned:
        return None

    if total <= 0:
        return None

    return int(total)


def format_duration(seconds: int) -> str:

    if seconds <= 0:
        return "Not configured"

    weeks, remainder = divmod(
        seconds,
        7 * 24 * 60 * 60,
    )

    days, remainder = divmod(
        remainder,
        24 * 60 * 60,
    )

    hours, remainder = divmod(
        remainder,
        60 * 60,
    )

    minutes, seconds = divmod(
        remainder,
        60,
    )

    parts = []

    if weeks:
        parts.append(f"{weeks}w")

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if seconds:
        parts.append(f"{seconds}s")

    return " ".join(parts)


# ============================================================
# JSON HELPERS
# ============================================================

def load_json(value, fallback):

    try:
        result = json.loads(value)

        if isinstance(result, type(fallback)):
            return result

    except Exception:
        pass

    return fallback


# ============================================================
# GET GIVEAWAY
# ============================================================

async def get_giveaway(
    bot,
    giveaway_id: int,
):

    cursor = await bot.database.connection.execute(
        """
        SELECT
            id,
            guild_id,
            channel_id,
            message_id,
            creator_id,
            host_id,
            prize,
            duration_seconds,
            winners,
            required_role_id,
            banned_role_ids,
            required_messages,
            extra_entries,
            thumbnail_data,
            thumbnail_filename,
            status,
            end_time,
            created_at,
            ended_at
        FROM giveaways
        WHERE id = ?
        """,
        (giveaway_id,),
    )

    return await cursor.fetchone()


async def get_giveaway_by_message(
    bot,
    message_id: int,
):

    cursor = await bot.database.connection.execute(
        """
        SELECT
            id,
            guild_id,
            channel_id,
            message_id,
            creator_id,
            host_id,
            prize,
            duration_seconds,
            winners,
            required_role_id,
            banned_role_ids,
            required_messages,
            extra_entries,
            thumbnail_data,
            thumbnail_filename,
            status,
            end_time,
            created_at,
            ended_at
        FROM giveaways
        WHERE message_id = ?
        """,
        (message_id,),
    )

    return await cursor.fetchone()


# ============================================================
# CONFIGURATION MODALS
# ============================================================

class DurationModal(discord.ui.Modal):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            title="Set Giveaway Duration"
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

        self.duration = discord.ui.TextInput(
            label="Duration",
            placeholder=(
                "Examples: 30m, 2hrs, 1d 5h 30m"
            ),
            max_length=100,
            required=True,
        )

        self.add_item(
            self.duration
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        seconds = parse_duration(
            self.duration.value
        )

        if seconds is None:
            await interaction.response.send_message(
                "❌ Invalid duration.\n\n"
                "Examples:\n"
                "`30s`\n"
                "`10 minutes`\n"
                "`2hrs`\n"
                "`1d 5h 30m`",
                ephemeral=True,
            )
            return

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET duration_seconds = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                seconds,
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            "⏱️ Duration set to "
            f"**{format_duration(seconds)}**.",
            ephemeral=True,
        )


class WinnersModal(discord.ui.Modal):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            title="Set Giveaway Winners"
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

        self.winners = discord.ui.TextInput(
            label="Number of Winners",
            placeholder="Example: 3",
            max_length=3,
            required=True,
        )

        self.add_item(
            self.winners
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        try:
            winners = int(
                self.winners.value
            )
        except ValueError:
            winners = 0

        if winners < 1 or winners > 100:
            await interaction.response.send_message(
                "❌ Winners must be between **1 and 100**.",
                ephemeral=True,
            )
            return

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET winners = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                winners,
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"🏆 Number of winners set to **{winners}**.",
            ephemeral=True,
        )


class PrizeModal(discord.ui.Modal):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            title="Set Giveaway Prize"
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

        self.prize = discord.ui.TextInput(
            label="Prize",
            placeholder="Example: Minecraft Premium Rank",
            max_length=256,
            required=True,
        )

        self.add_item(
            self.prize
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET prize = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                self.prize.value.strip(),
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"🎁 Prize set to **{self.prize.value.strip()}**.",
            ephemeral=True,
        )


class RequiredMessagesModal(
    discord.ui.Modal
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            title="Required Messages"
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

        self.messages = discord.ui.TextInput(
            label="Minimum Messages",
            placeholder="0 = no requirement",
            max_length=10,
            required=True,
        )

        self.add_item(
            self.messages
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        try:
            amount = int(
                self.messages.value
            )
        except ValueError:
            amount = -1

        if amount < 0:
            await interaction.response.send_message(
                "❌ Enter a number such as `100`.",
                ephemeral=True,
            )
            return

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET required_messages = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                amount,
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"💬 Required messages set to **{amount}**.",
            ephemeral=True,
        )


class ExtraEntriesModal(
    discord.ui.Modal
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
        role_id: int,
        role_name: str,
    ):
        super().__init__(
            title="Set Extra Entries"
        )

        self.bot = bot
        self.giveaway_id = giveaway_id
        self.role_id = role_id

        self.amount = discord.ui.TextInput(
            label=f"Extra entries for {role_name[:40]}",
            placeholder="Example: 3",
            max_length=5,
            required=True,
        )

        self.add_item(
            self.amount
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        try:
            amount = int(
                self.amount.value
            )
        except ValueError:
            amount = -1

        if amount < 0 or amount > 1000:
            await interaction.response.send_message(
                "❌ Enter a number between 0 and 1000.",
                ephemeral=True,
            )
            return

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        if not giveaway:
            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )
            return

        extra_entries = load_json(
            giveaway[12],
            {},
        )

        if amount == 0:
            extra_entries.pop(
                str(self.role_id),
                None,
            )
        else:
            extra_entries[
                str(self.role_id)
            ] = amount

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET extra_entries = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                json.dumps(extra_entries),
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"✨ **{amount}** extra entries configured "
            f"for **{role_name}**.",
            ephemeral=True,
        )


# ============================================================
# HOST USER SELECT
# ============================================================

class GiveawayHostSelect(
    discord.ui.UserSelect
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        self.bot = bot
        self.giveaway_id = giveaway_id

        super().__init__(
            placeholder="Select giveaway host...",
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        user = self.values[0]

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET host_id = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                user.id,
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"👤 Host set to {user.mention}.",
            ephemeral=True,
        )


class GiveawayHostView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            timeout=120
        )

        self.add_item(
            GiveawayHostSelect(
                bot,
                giveaway_id,
            )
        )


# ============================================================
# REQUIRED ROLE SELECT
# ============================================================

class RequiredRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        self.bot = bot
        self.giveaway_id = giveaway_id

        super().__init__(
            placeholder="Select required role...",
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        role = self.values[0]

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET required_role_id = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                role.id,
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"🎭 Required role set to {role.mention}.",
            ephemeral=True,
        )


class RequiredRoleView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            timeout=120
        )

        self.add_item(
            RequiredRoleSelect(
                bot,
                giveaway_id,
            )
        )


# ============================================================
# BANNED ROLE SELECT
# ============================================================

class BannedRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        self.bot = bot
        self.giveaway_id = giveaway_id

        super().__init__(
            placeholder="Select banned roles...",
            min_values=1,
            max_values=25,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        role_ids = [
            role.id
            for role in self.values
        ]

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET banned_role_ids = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                json.dumps(role_ids),
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"🚫 Added **{len(role_ids)}** banned role(s).",
            ephemeral=True,
        )


class BannedRoleView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            timeout=120
        )

        self.add_item(
            BannedRoleSelect(
                bot,
                giveaway_id,
            )
        )


# ============================================================
# EXTRA ENTRY ROLE SELECT
# ============================================================

class ExtraEntryRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        self.bot = bot
        self.giveaway_id = giveaway_id

        super().__init__(
            placeholder="Select role for extra entries...",
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        role = self.values[0]

        await interaction.response.send_modal(
            ExtraEntriesModal(
                self.bot,
                self.giveaway_id,
                role.id,
                role.name,
            )
        )


class ExtraEntryRoleView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
    ):
        super().__init__(
            timeout=120
        )

        self.add_item(
            ExtraEntryRoleSelect(
                bot,
                giveaway_id,
            )
        )


# ============================================================
# THUMBNAIL UPLOAD
# ============================================================

async def request_thumbnail(
    interaction: discord.Interaction,
    bot,
    giveaway_id: int,
    creator_id: int,
):

    await interaction.response.send_message(
        "🖼️ **Upload your giveaway thumbnail now.**\n\n"
        "Send the image as an attachment in this channel "
        "within **60 seconds**.\n\n"
        "Only the administrator who created this giveaway "
        "can upload it.",
        ephemeral=True,
    )

    def check(message: discord.Message):

        return (
            message.author.id == creator_id
            and message.channel.id
            == interaction.channel.id
            and len(message.attachments) > 0
        )

    try:

        message = await bot.wait_for(
            "message",
            timeout=60,
            check=check,
        )

    except asyncio.TimeoutError:
        try:
            await interaction.followup.send(
                "⌛ Thumbnail upload timed out.",
                ephemeral=True,
            )
        except Exception:
            pass

        return

    attachment = message.attachments[0]

    allowed = (
        attachment.content_type
        and attachment.content_type.startswith(
            "image/"
        )
    )

    if not allowed:

        await interaction.followup.send(
            "❌ Please upload an image file.",
            ephemeral=True,
        )

        return

    if attachment.size > 8 * 1024 * 1024:

        await interaction.followup.send(
            "❌ Thumbnail must be **8 MB or smaller**.",
            ephemeral=True,
        )

        return

    try:

        data = await attachment.read()

    except Exception:

        await interaction.followup.send(
            "❌ I couldn't read that image.",
            ephemeral=True,
        )

        return

    await bot.database.connection.execute(
        """
        UPDATE giveaways
        SET thumbnail_data = ?,
            thumbnail_filename = ?
        WHERE id = ?
          AND status = 'configuring'
        """,
        (
            data,
            attachment.filename,
            giveaway_id,
        ),
    )

    await bot.database.connection.commit()

    try:
        await message.delete()
    except Exception:
        pass

    await interaction.followup.send(
        "🖼️ Giveaway thumbnail updated.",
        ephemeral=True,
    )


# ============================================================
# GIVEAWAY ENTRY VIEW
# ============================================================

class GiveawayEntryButton(
    discord.ui.Button
):

    def __init__(self):

        super().__init__(
            label="Enter Giveaway",
            emoji="🎉",
            style=discord.ButtonStyle.success,
            custom_id="cody_giveaway_enter",
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        bot = interaction.client

        giveaway = await get_giveaway_by_message(
            bot,
            interaction.message.id,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        giveaway_id = giveaway[0]
        guild_id = giveaway[1]
        status = giveaway[15]

        if status != "active":

            await interaction.response.send_message(
                "❌ This giveaway is no longer active.",
                ephemeral=True,
            )

            return

        guild = interaction.guild

        if not guild:

            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member,
        ):

            member = guild.get_member(
                interaction.user.id
            )

        if not member:

            await interaction.response.send_message(
                "❌ I couldn't find your server member information.",
                ephemeral=True,
            )

            return

        # ----------------------------------------------------
        # Required role
        # ----------------------------------------------------

        required_role_id = giveaway[9]

        if required_role_id:

            required_role = guild.get_role(
                required_role_id
            )

            if (
                required_role
                and required_role not in member.roles
            ):

                await interaction.response.send_message(
                    "❌ You need "
                    f"{required_role.mention} "
                    "to enter this giveaway.",
                    ephemeral=True,
                )

                return

        # ----------------------------------------------------
        # Banned roles
        # ----------------------------------------------------

        banned_roles = load_json(
            giveaway[10],
            [],
        )

        member_role_ids = {
            role.id
            for role in member.roles
        }

        banned_match = [
            role_id
            for role_id in banned_roles
            if role_id in member_role_ids
        ]

        if banned_match:

            await interaction.response.send_message(
                "🚫 You have a role that is banned "
                "from this giveaway.",
                ephemeral=True,
            )

            return

        # ----------------------------------------------------
        # Required messages
        # ----------------------------------------------------

        required_messages = giveaway[11]

        if required_messages > 0:

            message_count = await get_member_message_count(
                bot,
                guild.id,
                member.id,
            )

            if message_count < required_messages:

                await interaction.response.send_message(
                    "💬 You need at least "
                    f"**{required_messages} messages** "
                    "to enter this giveaway.\n\n"
                    f"Your messages: **{message_count}**",
                    ephemeral=True,
                )

                return

        # ----------------------------------------------------
        # Check already entered
        # ----------------------------------------------------

        cursor = await bot.database.connection.execute(
            """
            SELECT 1
            FROM giveaway_entries
            WHERE giveaway_id = ?
              AND user_id = ?
            """,
            (
                giveaway_id,
                member.id,
            ),
        )

        existing = await cursor.fetchone()

        if existing:

            await interaction.response.send_message(
                "⚠️ You have already entered this giveaway.",
                ephemeral=True,
            )

            return

        # ----------------------------------------------------
        # Add entry
        # ----------------------------------------------------

        await bot.database.connection.execute(
            """
            INSERT INTO giveaway_entries (
                giveaway_id,
                user_id
            )
            VALUES (?, ?)
            """,
            (
                giveaway_id,
                member.id,
            ),
        )

        await bot.database.connection.commit()

        await interaction.response.send_message(
            "🎉 **You're entered!**\n\n"
            "Good luck! 🍀",
            ephemeral=True,
        )

        await update_giveaway_message(
            bot,
            giveaway_id,
        )


class GiveawayEntryView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

        self.add_item(
            GiveawayEntryButton()
        )


# ============================================================
# REROLL VIEW
# ============================================================

class GiveawayRerollButton(
    discord.ui.Button
):

    def __init__(self):

        super().__init__(
            label="Reroll Giveaway",
            emoji="🔄",
            style=discord.ButtonStyle.primary,
            custom_id="cody_giveaway_reroll",
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        bot = interaction.client

        if not interaction.guild:

            await interaction.response.send_message(
                "❌ Server only.",
                ephemeral=True,
            )

            return

        if not isinstance(
            interaction.user,
            discord.Member,
        ):

            await interaction.response.send_message(
                "❌ Unable to verify permissions.",
                ephemeral=True,
            )

            return

        if not interaction.user.guild_permissions.administrator:

            await interaction.response.send_message(
                "🔒 Administrator permission required.",
                ephemeral=True,
            )

            return

        giveaway = await get_giveaway_by_message(
            bot,
            interaction.message.id,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        winners = await choose_winners(
            bot,
            giveaway,
            interaction.guild,
            reroll=True,
        )

        if not winners:

            await interaction.response.send_message(
                "❌ There aren't enough eligible entrants "
                "for a reroll.",
                ephemeral=True,
            )

            return

        mentions = " ".join(
            member.mention
            for member in winners
        )

        await interaction.response.send_message(
            f"🔄 **New winner(s):** {mentions}"
        )

        await interaction.channel.send(
            f"🎉 Congratulations {mentions}!"
        )


class GiveawayRerollView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

        self.add_item(
            GiveawayRerollButton()
        )


# ============================================================
# MESSAGE COUNTER
# ============================================================

async def get_member_message_count(
    bot,
    guild_id: int,
    user_id: int,
) -> int:

    # If the moderation/database system already has
    # a message counter, use it when available.

    try:

        cursor = await bot.database.connection.execute(
            """
            SELECT message_count
            FROM user_message_stats
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        )

        row = await cursor.fetchone()

        if row:
            return int(row[0])

    except Exception:
        pass

    # Fallback:
    # If no message tracking table exists, this requirement
    # cannot be verified yet.
    return 0


# ============================================================
# CALCULATE ENTRY WEIGHT
# ============================================================

def calculate_entry_weight(
    member: discord.Member,
    extra_entries: dict,
) -> int:

    weight = 1

    member_roles = {
        role.id
        for role in member.roles
    }

    for role_id, bonus in extra_entries.items():

        try:
            role_id = int(role_id)
            bonus = int(bonus)
        except Exception:
            continue

        if role_id in member_roles:
            weight += max(
                0,
                bonus,
            )

    return weight


# ============================================================
# GET ELIGIBLE MEMBERS
# ============================================================

async def get_eligible_entries(
    bot,
    giveaway,
    guild: discord.Guild,
):

    giveaway_id = giveaway[0]

    required_role_id = giveaway[9]

    banned_roles = load_json(
        giveaway[10],
        [],
    )

    required_messages = giveaway[11]

    extra_entries = load_json(
        giveaway[12],
        {},
    )

    cursor = await bot.database.connection.execute(
        """
        SELECT user_id
        FROM giveaway_entries
        WHERE giveaway_id = ?
        """,
        (giveaway_id,),
    )

    rows = await cursor.fetchall()

    eligible = []

    for row in rows:

        user_id = row[0]

        member = guild.get_member(
            user_id
        )

        if not member:
            continue

        # Required role.
        if required_role_id:

            if not any(
                role.id == required_role_id
                for role in member.roles
            ):
                continue

        # Banned role.
        member_role_ids = {
            role.id
            for role in member.roles
        }

        if any(
            role_id in member_role_ids
            for role_id in banned_roles
        ):
            continue

        # Required messages.
        if required_messages > 0:

            count = await get_member_message_count(
                bot,
                guild.id,
                member.id,
            )

            if count < required_messages:
                continue

        weight = calculate_entry_weight(
            member,
            extra_entries,
        )

        eligible.append(
            (
                member,
                weight,
            )
        )

    return eligible


# ============================================================
# WEIGHTED WINNER SELECTION
# ============================================================

async def choose_winners(
    bot,
    giveaway,
    guild: discord.Guild,
    reroll: bool = False,
):

    eligible = await get_eligible_entries(
        bot,
        giveaway,
        guild,
    )

    if not eligible:
        return []

    # Remove previous winners during reroll.
    if reroll:

        cursor = await bot.database.connection.execute(
            """
            SELECT user_id
            FROM giveaway_winners
            WHERE giveaway_id = ?
            """,
            (giveaway[0],),
        )

        previous = {
            row[0]
            for row in await cursor.fetchall()
        }

        eligible = [
            item
            for item in eligible
            if item[0].id not in previous
        ]

    winner_count = giveaway[8]

    if len(eligible) < winner_count:
        winner_count = len(eligible)

    selected = []

    pool = list(eligible)

    for _ in range(winner_count):

        if not pool:
            break

        total_weight = sum(
            weight
            for _, weight in pool
        )

        random_value = random.uniform(
            0,
            total_weight,
        )

        current = 0

        selected_index = 0

        for index, (
            member,
            weight,
        ) in enumerate(pool):

            current += weight

            if random_value <= current:

                selected_index = index
                break

        member, _ = pool.pop(
            selected_index
        )

        selected.append(
            member
        )

    reroll_number = 1

    if reroll:

        cursor = await bot.database.connection.execute(
            """
            SELECT COALESCE(
                MAX(reroll_number),
                0
            )
            FROM giveaway_winners
            WHERE giveaway_id = ?
            """,
            (giveaway[0],),
        )

        row = await cursor.fetchone()

        reroll_number = (
            (row[0] if row else 0)
            + 1
        )

    for member in selected:

        await bot.database.connection.execute(
            """
            INSERT OR REPLACE INTO giveaway_winners (
                giveaway_id,
                user_id,
                reroll_number
            )
            VALUES (?, ?, ?)
            """,
            (
                giveaway[0],
                member.id,
                reroll_number,
            ),
        )

    await bot.database.connection.commit()

    return selected


# ============================================================
# GIVEAWAY EMBED
# ============================================================

async def build_giveaway_embed(
    bot,
    giveaway,
    guild: discord.Guild,
):

    host = guild.get_member(
        giveaway[5]
    )

    host_text = (
        host.mention
        if host
        else f"<@{giveaway[5]}>"
    )

    status = giveaway[15]

    if status == "active":

        end_time = giveaway[16]

        try:

            end_datetime = datetime.fromisoformat(
                end_time
            )

            timestamp = int(
                end_datetime.timestamp()
            )

            end_text = (
                f"<t:{timestamp}:R>\n"
                f"<t:{timestamp}:F>"
            )

        except Exception:

            end_text = "Unknown"

    elif status == "ended":

        end_text = "🎉 Giveaway ended"

    else:

        end_text = "Not started"

    cursor = await bot.database.connection.execute(
        """
        SELECT COUNT(*)
        FROM giveaway_entries
        WHERE giveaway_id = ?
        """,
        (giveaway[0],),
    )

    row = await cursor.fetchone()

    entries = row[0] if row else 0

    embed = discord.Embed(
        title="🎉 GIVEAWAY",
        description=(
            f"## 🎁 {giveaway[6]}\n\n"
            f"🏆 **Winners:** {giveaway[8]}\n"
            f"👤 **Host:** {host_text}\n"
            f"⏰ **Ends:** {end_text}\n\n"
            f"🎟️ **Entries:** {entries}\n"
        ),
    )

    required_role_id = giveaway[9]

    if required_role_id:

        role = guild.get_role(
            required_role_id
        )

        if role:

            embed.add_field(
                name="🎭 Required Role",
                value=role.mention,
                inline=True,
            )

    banned_roles = load_json(
        giveaway[10],
        [],
    )

    if banned_roles:

        names = []

        for role_id in banned_roles:

            role = guild.get_role(
                role_id
            )

            if role:
                names.append(
                    role.mention
                )

        if names:

            embed.add_field(
                name="🚫 Banned Roles",
                value=" ".join(names),
                inline=False,
            )

    required_messages = giveaway[11]

    if required_messages > 0:

        embed.add_field(
            name="💬 Required Messages",
            value=str(
                required_messages
            ),
            inline=True,
        )

    extra_entries = load_json(
        giveaway[12],
        {},
    )

    if extra_entries:

        bonuses = []

        for role_id, bonus in extra_entries.items():

            role = guild.get_role(
                int(role_id)
            )

            if role:

                bonuses.append(
                    f"{role.mention} +{bonus}"
                )

        if bonuses:

            embed.add_field(
                name="✨ Extra Entries",
                value="\n".join(
                    bonuses
                ),
                inline=False,
            )

    if status == "ended":

        embed.set_footer(
            text="Giveaway ended • Use the button below to reroll"
        )

    else:

        embed.set_footer(
            text="Click the button below to enter!"
        )

    return embed


# ============================================================
# UPDATE GIVEAWAY MESSAGE
# ============================================================

async def update_giveaway_message(
    bot,
    giveaway_id: int,
):

    giveaway = await get_giveaway(
        bot,
        giveaway_id,
    )

    if not giveaway:
        return

    guild = bot.get_guild(
        giveaway[1]
    )

    if not guild:
        return

    channel = guild.get_channel(
        giveaway[2]
    )

    if not isinstance(
        channel,
        discord.TextChannel,
    ):
        return

    try:

        message = await channel.fetch_message(
            giveaway[3]
        )

    except Exception:

        return

    embed = await build_giveaway_embed(
        bot,
        giveaway,
        guild,
    )

    if giveaway[15] == "active":

        view = GiveawayEntryView()

    elif giveaway[15] == "ended":

        view = GiveawayRerollView()

    else:

        view = None

    try:

        if (
            giveaway[13]
            and giveaway[14]
        ):

            file = discord.File(
                io.BytesIO(
                    giveaway[13]
                ),
                filename=giveaway[14],
            )

            embed.set_thumbnail(
                url=f"attachment://{giveaway[14]}"
            )

            await message.edit(
                embed=embed,
                view=view,
                attachments=[file],
            )

        else:

            await message.edit(
                embed=embed,
                view=view,
            )

    except Exception:

        try:
            await message.edit(
                embed=embed,
                view=view,
            )
        except Exception:
            pass


# ============================================================
# CONFIGURATION VIEW
# ============================================================

class GiveawayConfigView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id: int,
        creator_id: int,
    ):

        super().__init__(
            timeout=1800
        )

        self.bot = bot
        self.giveaway_id = giveaway_id
        self.creator_id = creator_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):

        if interaction.user.id != self.creator_id:

            await interaction.response.send_message(
                "🔒 This giveaway configuration belongs "
                "to another administrator. Only the "
                "administrator who created it can edit it.",
                ephemeral=True,
            )

            return False

        if not isinstance(
            interaction.user,
            discord.Member,
        ):

            return False

        if not interaction.user.guild_permissions.administrator:

            await interaction.response.send_message(
                "🔒 Administrator permission required.",
                ephemeral=True,
            )

            return False

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway no longer exists.",
                ephemeral=True,
            )

            return False

        if giveaway[15] != "configuring":

            await interaction.response.send_message(
                "❌ This giveaway has already started.",
                ephemeral=True,
            )

            return False

        return True

    # --------------------------------------------------------
    # DURATION
    # --------------------------------------------------------

    @discord.ui.button(
        label="Time",
        emoji="⏱️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def duration_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            DurationModal(
                self.bot,
                self.giveaway_id,
            )
        )

    # --------------------------------------------------------
    # WINNERS
    # --------------------------------------------------------

    @discord.ui.button(
        label="Winners",
        emoji="🏆",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def winners_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            WinnersModal(
                self.bot,
                self.giveaway_id,
            )
        )

    # --------------------------------------------------------
    # PRIZE
    # --------------------------------------------------------

    @discord.ui.button(
        label="Prize",
        emoji="🎁",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def prize_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            PrizeModal(
                self.bot,
                self.giveaway_id,
            )
        )

    # --------------------------------------------------------
    # HOST
    # --------------------------------------------------------

    @discord.ui.button(
        label="Host",
        emoji="👤",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def host_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            "👤 Select the giveaway host.",
            view=GiveawayHostView(
                self.bot,
                self.giveaway_id,
            ),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # REQUIRED ROLE
    # --------------------------------------------------------

    @discord.ui.button(
        label="Required Role",
        emoji="🎭",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def required_role_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            "🎭 Select the role required to enter.",
            view=RequiredRoleView(
                self.bot,
                self.giveaway_id,
            ),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # BANNED ROLES
    # --------------------------------------------------------

    @discord.ui.button(
        label="Banned Roles",
        emoji="🚫",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def banned_roles_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            "🚫 Select the roles that cannot enter.",
            view=BannedRoleView(
                self.bot,
                self.giveaway_id,
            ),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # REQUIRED MESSAGES
    # --------------------------------------------------------

    @discord.ui.button(
        label="Messages",
        emoji="💬",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def messages_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            RequiredMessagesModal(
                self.bot,
                self.giveaway_id,
            )
        )

    # --------------------------------------------------------
    # EXTRA ENTRIES
    # --------------------------------------------------------

    @discord.ui.button(
        label="Extra Entries",
        emoji="✨",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def extra_entries_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            "✨ Select a role to configure extra entries.",
            view=ExtraEntryRoleView(
                self.bot,
                self.giveaway_id,
            ),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # THUMBNAIL
    # --------------------------------------------------------

    @discord.ui.button(
        label="Thumbnail",
        emoji="🖼️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def thumbnail_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await request_thumbnail(
            interaction,
            self.bot,
            self.giveaway_id,
            self.creator_id,
        )

    # --------------------------------------------------------
    # TEST
    # --------------------------------------------------------

    @discord.ui.button(
        label="Preview",
        emoji="👁️",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def preview_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        embed = await build_giveaway_embed(
            self.bot,
            giveaway,
            interaction.guild,
        )

        await interaction.response.send_message(
            "👁️ **Giveaway Preview**",
            embed=embed,
            ephemeral=True,
        )

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    @discord.ui.button(
        label="Start Giveaway",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def start_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        missing = []

        if giveaway[7] <= 0:
            missing.append("⏱️ Duration")

        if giveaway[8] <= 0:
            missing.append("🏆 Winners")

        if (
            not giveaway[6]
            or giveaway[6] == "Not configured"
        ):
            missing.append("🎁 Prize")

        if missing:

            await interaction.response.send_message(
                "❌ **Giveaway setup is incomplete.**\n\n"
                + "\n".join(
                    f"• {item}"
                    for item in missing
                ),
                ephemeral=True,
            )

            return

        duration = giveaway[7]

        end_time = (
            datetime.now(
                timezone.utc
            )
            + timedelta(
                seconds=duration
            )
        ).isoformat()

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET status = 'active',
                end_time = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                end_time,
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        # Send actual giveaway message.
        embed = await build_giveaway_embed(
            self.bot,
            await get_giveaway(
                self.bot,
                self.giveaway_id,
            ),
            interaction.guild,
        )

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        if giveaway[13] and giveaway[14]:

            file = discord.File(
                io.BytesIO(
                    giveaway[13]
                ),
                filename=giveaway[14],
            )

            embed.set_thumbnail(
                url=(
                    f"attachment://"
                    f"{giveaway[14]}"
                )
            )

            message = await interaction.channel.send(
                embed=embed,
                view=GiveawayEntryView(),
                file=file,
            )

        else:

            message = await interaction.channel.send(
                embed=embed,
                view=GiveawayEntryView(),
            )

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET message_id = ?,
                channel_id = ?
            WHERE id = ?
            """,
            (
                message.id,
                interaction.channel.id,
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        # Update configuration message.
        try:

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🎉 Giveaway Started",
                    description=(
                        f"Your giveaway has started!\n\n"
                        f"🎁 **Prize:** {giveaway[6]}\n"
                        f"🏆 **Winners:** {giveaway[8]}\n"
                        f"⏱️ **Duration:** "
                        f"{format_duration(duration)}\n\n"
                        f"🔗 [Jump to Giveaway]"
                        f"({message.jump_url})"
                    ),
                ),
                view=None,
            )

        except Exception:

            if not interaction.response.is_done():

                await interaction.response.send_message(
                    "🎉 Giveaway started!",
                    ephemeral=True,
                )


    # --------------------------------------------------------
    # REFRESH
    # --------------------------------------------------------

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        embed = build_config_embed(
            interaction.guild,
            giveaway,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )


# ============================================================
# CONFIG EMBED
# ============================================================

def build_config_embed(
    guild: discord.Guild,
    giveaway,
):

    if not giveaway:

        return discord.Embed(
            title="🎉 Giveaway Configuration",
            description="Giveaway not found.",
        )

    host = guild.get_member(
        giveaway[5]
    )

    host_text = (
        host.mention
        if host
        else f"<@{giveaway[5]}>"
    )

    required_role = "None"

    if giveaway[9]:

        role = guild.get_role(
            giveaway[9]
        )

        if role:
            required_role = role.mention

    banned_roles = load_json(
        giveaway[10],
        [],
    )

    banned_text = "None"

    if banned_roles:

        names = []

        for role_id in banned_roles:

            role = guild.get_role(
                role_id
            )

            if role:
                names.append(
                    role.mention
                )

        if names:
            banned_text = " ".join(
                names
            )

    extra_entries = load_json(
        giveaway[12],
        {},
    )

    extra_text = "None"

    if extra_entries:

        lines = []

        for role_id, amount in extra_entries.items():

            role = guild.get_role(
                int(role_id)
            )

            if role:

                lines.append(
                    f"{role.mention} +{amount}"
                )

        if lines:
            extra_text = "\n".join(
                lines
            )

    thumbnail = (
        "🖼️ Configured"
        if giveaway[13]
        else "None"
    )

    status_map = {
        "configuring": "⚙️ Not Started",
        "active": "🟢 Active",
        "ended": "🔴 Ended",
    }

    status = status_map.get(
        giveaway[15],
        "Unknown",
    )

    embed = discord.Embed(
        title="🎉 Giveaway Configuration",
        description=(
            "Configure your giveaway below.\n"
            "Only the administrator who created "
            "this configuration can edit it."
        ),
    )

    embed.add_field(
        name="📊 Status",
        value=status,
        inline=False,
    )

    embed.add_field(
        name="⏱️ Duration",
        value=format_duration(
            giveaway[7]
        ),
        inline=True,
    )

    embed.add_field(
        name="🏆 Winners",
        value=str(
            giveaway[8]
        ),
        inline=True,
    )

    embed.add_field(
        name="🎁 Prize",
        value=giveaway[6],
        inline=True,
    )

    embed.add_field(
        name="👤 Host",
        value=host_text,
        inline=True,
    )

    embed.add_field(
        name="🎭 Required Role",
        value=required_role,
        inline=True,
    )

    embed.add_field(
        name="🚫 Banned Roles",
        value=banned_text[:1024],
        inline=True,
    )

    embed.add_field(
        name="💬 Required Messages",
        value=str(
            giveaway[11]
        ),
        inline=True,
    )

    embed.add_field(
        name="✨ Extra Entries",
        value=extra_text[:1024],
        inline=True,
    )

    embed.add_field(
        name="🖼️ Thumbnail",
        value=thumbnail,
        inline=True,
    )

    embed.add_field(
        name="⏱️ Duration Examples",
        value=(
            "`30s` • `10m` • `2hrs` • `1d` • `1w`\n"
            "`1d 5h 30m` • `2hrs 20mins`"
        ),
        inline=False,
    )

    embed.set_footer(
        text="Configure everything before pressing Start Giveaway."
    )

    return embed


# ============================================================
# GIVEAWAY COG
# ============================================================

class Giveaways(commands.Cog):

    def __init__(self, bot):

        self.bot = bot

        self.finish_giveaways.start()

    async def cog_unload(self):

        self.finish_giveaways.cancel()

    # ========================================================
    # /giveaway GROUP
    # ========================================================

    giveaway_group = app_commands.Group(
        name="giveaway",
        description="Manage giveaways.",
    )

    # ========================================================
    # /giveaway create
    # ========================================================

    @giveaway_group.command(
        name="create",
        description="Create a giveaway configuration.",
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def giveaway_create(
        self,
        interaction: discord.Interaction,
    ):

        if not interaction.guild:

            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )

            return

        await self.bot.database.connection.execute(
            """
            INSERT INTO giveaways (
                guild_id,
                channel_id,
                creator_id,
                host_id,
                prize,
                duration_seconds,
                winners,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'configuring')
            """,
            (
                interaction.guild.id,
                interaction.channel.id,
                interaction.user.id,
                interaction.user.id,
                "Not configured",
                0,
                1,
            ),
        )

        await self.bot.database.connection.commit()

        cursor = await self.bot.database.connection.execute(
            """
            SELECT last_insert_rowid()
            """
        )

        row = await cursor.fetchone()

        giveaway_id = row[0]

        giveaway = await get_giveaway(
            self.bot,
            giveaway_id,
        )

        embed = build_config_embed(
            interaction.guild,
            giveaway,
        )

        view = GiveawayConfigView(
            self.bot,
            giveaway_id,
            interaction.user.id,
        )

        await interaction.response.send_message(
            embed=embed,
            view=view,
        )

    # ========================================================
    # /giveaway end
    # ========================================================

    @giveaway_group.command(
        name="end",
        description="End an active giveaway.",
    )
    @app_commands.describe(
        message_id="The giveaway message ID."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def giveaway_end(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ):

        try:

            message_id_int = int(
                message_id
            )

        except ValueError:

            await interaction.response.send_message(
                "❌ Invalid message ID.",
                ephemeral=True,
            )

            return

        giveaway = await get_giveaway_by_message(
            self.bot,
            message_id_int,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        if giveaway[15] != "active":

            await interaction.response.send_message(
                "❌ This giveaway isn't active.",
                ephemeral=True,
            )

            return

        await self.end_giveaway(
            giveaway[0]
        )

        await interaction.response.send_message(
            "🛑 Giveaway ended successfully.",
            ephemeral=True,
        )

    # ========================================================
    # /giveaway reroll
    # ========================================================

    @giveaway_group.command(
        name="reroll",
        description="Reroll the winner of an ended giveaway.",
    )
    @app_commands.describe(
        message_id="The ended giveaway message ID."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def giveaway_reroll(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ):

        try:

            message_id_int = int(
                message_id
            )

        except ValueError:

            await interaction.response.send_message(
                "❌ Invalid message ID.",
                ephemeral=True,
            )

            return

        giveaway = await get_giveaway_by_message(
            self.bot,
            message_id_int,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        if giveaway[15] != "ended":

            await interaction.response.send_message(
                "❌ The giveaway must be ended before rerolling.",
                ephemeral=True,
            )

            return

        guild = interaction.guild

        if not guild:

            return

        winners = await choose_winners(
            self.bot,
            giveaway,
            guild,
            reroll=True,
        )

        if not winners:

            await interaction.response.send_message(
                "❌ No eligible members are available "
                "for another winner.",
                ephemeral=True,
            )

            return

        mentions = " ".join(
            member.mention
            for member in winners
        )

        await interaction.response.send_message(
            f"🔄 **Rerolled winner(s):** {mentions}"
        )

    # ========================================================
    # AUTOMATIC GIVEAWAY FINISHER
    # ========================================================

    @tasks.loop(seconds=5)
    async def finish_giveaways(self):

        try:

            now = datetime.now(
                timezone.utc
            )

            cursor = await self.bot.database.connection.execute(
                """
                SELECT id
                FROM giveaways
                WHERE status = 'active'
                  AND end_time IS NOT NULL
                """
            )

            rows = await cursor.fetchall()

            for row in rows:

                giveaway = await get_giveaway(
                    self.bot,
                    row[0],
                )

                if not giveaway:
                    continue

                try:

                    end_time = datetime.fromisoformat(
                        giveaway[16]
                    )

                except Exception:

                    continue

                if now >= end_time:

                    await self.end_giveaway(
                        giveaway[0]
                    )

        except Exception as error:

            print(
                f"Giveaway loop error: {error}"
            )

    @finish_giveaways.before_loop
    async def before_finish_giveaways(self):

        await self.bot.wait_until_ready()

    # ========================================================
    # END GIVEAWAY INTERNAL
    # ========================================================

    async def end_giveaway(
        self,
        giveaway_id: int,
    ):

        giveaway = await get_giveaway(
            self.bot,
            giveaway_id,
        )

        if not giveaway:
            return

        if giveaway[15] != "active":
            return

        guild = self.bot.get_guild(
            giveaway[1]
        )

        if not guild:
            return

        winners = await choose_winners(
            self.bot,
            giveaway,
            guild,
            reroll=False,
        )

        await self.bot.database.connection.execute(
            """
            UPDATE giveaways
            SET status = 'ended',
                ended_at = ?
            WHERE id = ?
              AND status = 'active'
            """,
            (
                datetime.now(
                    timezone.utc
                ).isoformat(),
                giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        channel = guild.get_channel(
            giveaway[2]
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            return

        try:

            message = await channel.fetch_message(
                giveaway[3]
            )

        except Exception:

            return

        giveaway = await get_giveaway(
            self.bot,
            giveaway_id,
        )

        embed = await build_giveaway_embed(
            self.bot,
            giveaway,
            guild,
        )

        if winners:

            winner_mentions = " ".join(
                member.mention
                for member in winners
            )

            embed.add_field(
                name="🏆 Winner(s)",
                value=winner_mentions,
                inline=False,
            )

            content = (
                "🎉 **GIVEAWAY ENDED!**\n\n"
                f"Congratulations {winner_mentions}!\n"
                f"You won **{giveaway[6]}**!"
            )

        else:

            embed.add_field(
                name="🏆 Winner(s)",
                value="No eligible winners.",
                inline=False,
            )

            content = (
                "🎉 **GIVEAWAY ENDED!**\n\n"
                "Unfortunately, there were no eligible winners."
            )

        try:

            if giveaway[13] and giveaway[14]:

                file = discord.File(
                    io.BytesIO(
                        giveaway[13]
                    ),
                    filename=giveaway[14],
                )

                embed.set_thumbnail(
                    url=(
                        f"attachment://"
                        f"{giveaway[14]}"
                    )
                )

                await message.edit(
                    content=None,
                    embed=embed,
                    view=GiveawayRerollView(),
                    attachments=[file],
                )

            else:

                await message.edit(
                    content=None,
                    embed=embed,
                    view=GiveawayRerollView(),
                )

            await channel.send(
                content
            )

        except Exception as error:

            print(
                f"Could not update ended giveaway: {error}"
            )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    @giveaway_create.error
    @giveaway_end.error
    @giveaway_reroll.error
    async def giveaway_error(
        self,
        interaction: discord.Interaction,
        error,
    ):

        if isinstance(
            error,
            app_commands.errors.MissingPermissions,
        ):

            message = (
                "🔒 You need **Administrator** permission "
                "to manage giveaways."
            )

        else:

            print(
                f"Giveaway command error: {error}"
            )

            message = (
                "❌ An error occurred while processing "
                "the giveaway command."
            )

        if interaction.response.is_done():

            await interaction.followup.send(
                message,
                ephemeral=True,
            )

        else:

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )


# ============================================================
# SETUP
# ============================================================

async def setup(bot):

    await setup_giveaway_database(
        bot
    )

    await bot.add_cog(
        Giveaways(bot)
    )

    # Persistent active giveaway button.
    bot.add_view(
        GiveawayEntryView()
    )

    # Persistent reroll button.
    bot.add_view(
        GiveawayRerollView()
    )

    print(
        "Giveaway system loaded."
    )
