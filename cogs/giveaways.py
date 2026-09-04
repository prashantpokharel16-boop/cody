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


CREATE TABLE IF NOT EXISTS giveaway_message_counts (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (
        guild_id,
        user_id
    )
);


CREATE INDEX IF NOT EXISTS idx_giveaways_guild
ON giveaways(guild_id);


CREATE INDEX IF NOT EXISTS idx_giveaways_status
ON giveaways(status);


CREATE INDEX IF NOT EXISTS idx_giveaway_entries
ON giveaway_entries(giveaway_id);


CREATE INDEX IF NOT EXISTS idx_giveaway_message_counts
ON giveaway_message_counts(guild_id, user_id);
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
# DURATION
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
    Accepted examples:

    30s
    30S
    30 sec
    30 seconds

    10m
    10M
    10 min
    10 mins
    10 minutes

    2h
    2hr
    2hrs
    2 hour
    2 hours

    1d
    1 day
    1 days

    1w
    1 week

    1d 5h 30m
    2hrs 20mins

    Capitalization is ignored.
    """

    if not value:
        return None

    value = value.lower().strip()

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

    for amount, unit in matches:

        unit = unit.lower()

        if unit not in UNIT_SECONDS:
            return None

        total += (
            float(amount)
            * UNIT_SECONDS[unit]
        )

    cleaned = pattern.sub(
        "",
        value,
    )

    cleaned = cleaned.replace(
        " ",
        "",
    )

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
# JSON
# ============================================================

def load_json(
    value,
    fallback,
):

    try:

        result = json.loads(
            value
        )

        if isinstance(
            result,
            type(fallback),
        ):
            return result

    except Exception:
        pass

    return fallback


# ============================================================
# GIVEAWAY DATABASE HELPERS
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
# MESSAGE COUNTER
# ============================================================

async def increment_message_count(
    bot,
    guild_id: int,
    user_id: int,
):

    await bot.database.connection.execute(
        """
        INSERT INTO giveaway_message_counts (
            guild_id,
            user_id,
            message_count
        )
        VALUES (?, ?, 1)

        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET
            message_count =
                message_count + 1
        """,
        (
            guild_id,
            user_id,
        ),
    )

    await bot.database.connection.commit()


async def get_member_message_count(
    bot,
    guild_id: int,
    user_id: int,
) -> int:

    cursor = await bot.database.connection.execute(
        """
        SELECT message_count
        FROM giveaway_message_counts
        WHERE guild_id = ?
          AND user_id = ?
        """,
        (
            guild_id,
            user_id,
        ),
    )

    row = await cursor.fetchone()

    if not row:
        return 0

    return int(row[0])


# ============================================================
# DURATION MODAL
# ============================================================

class DurationModal(
    discord.ui.Modal
):

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
                "30m / 2hrs / 1d 5h 30m"
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


# ============================================================
# WINNERS MODAL
# ============================================================

class WinnersModal(
    discord.ui.Modal
):

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


# ============================================================
# PRIZE MODAL
# ============================================================

class PrizeModal(
    discord.ui.Modal
):

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
            placeholder="Minecraft Premium Rank",
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
            f"🎁 Prize set to "
            f"**{self.prize.value.strip()}**.",
            ephemeral=True,
        )


# ============================================================
# REQUIRED MESSAGES MODAL
# ============================================================

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


# ============================================================
# HOST SELECT
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
# REQUIRED ROLE
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
# BANNED ROLES
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
# EXTRA ENTRIES
# ============================================================

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

        extras = load_json(
            giveaway[12],
            {},
        )

        if amount == 0:

            extras.pop(
                str(self.role_id),
                None,
            )

        else:

            extras[
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
                json.dumps(extras),
                self.giveaway_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"✨ **{amount}** extra entries configured.",
            ephemeral=True,
        )


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
# THUMBNAIL
# ============================================================

async def request_thumbnail(
    interaction,
    bot,
    giveaway_id,
    creator_id,
):

    await interaction.response.send_message(
        "🖼️ **Upload the giveaway thumbnail now.**\n\n"
        "Send an image attachment in this channel "
        "within **60 seconds**.",
        ephemeral=True,
    )

    def check(
        message: discord.Message
    ):

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

        await interaction.followup.send(
            "⌛ Thumbnail upload timed out.",
            ephemeral=True,
        )

        return

    attachment = message.attachments[0]

    if not (
        attachment.content_type
        and attachment.content_type.startswith(
            "image/"
        )
    ):

        await interaction.followup.send(
            "❌ Please upload an image file.",
            ephemeral=True,
        )

        return

    if attachment.size > 8 * 1024 * 1024:

        await interaction.followup.send(
            "❌ Thumbnail must be 8 MB or smaller.",
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
        "🖼️ Thumbnail updated successfully.",
        ephemeral=True,
    )


# ============================================================
# PARTICIPANT INFORMATION
# ============================================================

async def get_participants(
    bot,
    giveaway,
    guild: discord.Guild,
):

    extras = load_json(
        giveaway[12],
        {},
    )

    cursor = await bot.database.connection.execute(
        """
        SELECT user_id
        FROM giveaway_entries
        WHERE giveaway_id = ?
        ORDER BY entered_at ASC
        """,
        (giveaway[0],),
    )

    rows = await cursor.fetchall()

    participants = []

    for row in rows:

        user_id = row[0]

        member = guild.get_member(
            user_id
        )

        if not member:
            continue

        extra = 0
        extra_roles = []

        for role_id, amount in extras.items():

            try:

                role_id_int = int(
                    role_id
                )

                amount_int = int(
                    amount
                )

            except Exception:

                continue

            role = guild.get_role(
                role_id_int
            )

            if (
                role
                and role in member.roles
            ):

                extra += amount_int

                extra_roles.append(
                    (
                        role,
                        amount_int,
                    )
                )

        total_entries = 1 + extra

        participants.append(
            {
                "member": member,
                "base": 1,
                "extra": extra,
                "total": total_entries,
                "roles": extra_roles,
            }
        )

    return participants


# ============================================================
# PARTICIPANT PAGES
# ============================================================

class ParticipantView(
    discord.ui.View
):

    def __init__(
        self,
        pages,
    ):

        super().__init__(
            timeout=300
        )

        self.pages = pages
        self.page = 0

        self.previous.disabled = True

        if len(pages) <= 1:
            self.next.disabled = True

    def update_buttons(self):

        self.previous.disabled = (
            self.page <= 0
        )

        self.next.disabled = (
            self.page >= len(self.pages) - 1
        )

    @discord.ui.button(
        label="Previous",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
    )
    async def previous(
        self,
        interaction,
        button,
    ):

        if self.page > 0:
            self.page -= 1

        self.update_buttons()

        await interaction.response.edit_message(
            embed=self.pages[self.page],
            view=self,
        )

    @discord.ui.button(
        label="Next",
        emoji="▶️",
        style=discord.ButtonStyle.secondary,
    )
    async def next(
        self,
        interaction,
        button,
    ):

        if self.page < len(self.pages) - 1:
            self.page += 1

        self.update_buttons()

        await interaction.response.edit_message(
            embed=self.pages[self.page],
            view=self,
        )


def build_participant_pages(
    participants,
    giveaway,
):

    if not participants:

        embed = discord.Embed(
            title="👥 Giveaway Participants",
            description="No participants yet.",
        )

        return [embed]

    pages = []

    per_page = 10

    chunks = [
        participants[
            index:index + per_page
        ]
        for index in range(
            0,
            len(participants),
            per_page,
        )
    ]

    for page_number, chunk in enumerate(
        chunks,
        start=1,
    ):

        embed = discord.Embed(
            title="👥 Giveaway Participants",
            description=(
                f"🎁 **Prize:** {giveaway[6]}\n"
                f"👥 **Participants:** "
                f"{len(participants)}\n"
                f"🎟️ **Total Entries:** "
                f"{sum(x['total'] for x in participants)}"
            ),
        )

        start_number = (
            (page_number - 1)
            * per_page
            + 1
        )

        lines = []

        for index, participant in enumerate(
            chunk,
            start=start_number,
        ):

            member = participant["member"]

            role_text = "None"

            if participant["roles"]:

                role_text = ", ".join(
                    f"{role.mention} +{amount}"
                    for role, amount
                    in participant["roles"]
                )

            lines.append(
                f"**{index}. {member.display_name}**\n"
                f"└ 🎟️ Entries: **{participant['total']}**\n"
                f"└ ✨ Extra: {role_text}"
            )

        embed.add_field(
            name="Participants",
            value="\n\n".join(lines)[:1024],
            inline=False,
        )

        embed.set_footer(
            text=(
                f"Page {page_number}/{len(chunks)}"
            ),
        )

        pages.append(embed)

    return pages


# ============================================================
# PARTICIPANT BUTTON
# ============================================================

class ParticipantsButton(
    discord.ui.Button
):

    def __init__(self):

        super().__init__(
            label="Participants",
            emoji="👥",
            style=discord.ButtonStyle.secondary,
            custom_id="cody_giveaway_participants",
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

        is_admin = (
            isinstance(
                interaction.user,
                discord.Member,
            )
            and interaction.user.guild_permissions.administrator
        )

        is_creator = (
            interaction.user.id
            == giveaway[4]
        )

        is_host = (
            interaction.user.id
            == giveaway[5]
        )

        if not (
            is_admin
            or is_creator
            or is_host
        ):

            await interaction.response.send_message(
                "🔒 Only the giveaway creator, host, "
                "or an administrator can view participants.",
                ephemeral=True,
            )

            return

        participants = await get_participants(
            bot,
            giveaway,
            interaction.guild,
        )

        pages = build_participant_pages(
            participants,
            giveaway,
        )

        view = ParticipantView(
            pages
        )

        await interaction.response.send_message(
            embed=pages[0],
            view=view,
            ephemeral=True,
        )


# ============================================================
# ACTIVE GIVEAWAY VIEW
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
        interaction,
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

        if giveaway[15] != "active":

            await interaction.response.send_message(
                "❌ This giveaway is no longer active.",
                ephemeral=True,
            )

            return

        guild = interaction.guild

        if not guild:

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
                "❌ Couldn't find your member information.",
                ephemeral=True,
            )

            return

        # ----------------------------------------------------
        # Required role
        # ----------------------------------------------------

        required_role_id = giveaway[9]

        if required_role_id:

            role = guild.get_role(
                required_role_id
            )

            if (
                role
                and role not in member.roles
            ):

                await interaction.response.send_message(
                    f"❌ You need {role.mention} "
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

        if any(
            role_id in member_role_ids
            for role_id in banned_roles
        ):

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

            count = await get_member_message_count(
                bot,
                guild.id,
                member.id,
            )

            if count < required_messages:

                await interaction.response.send_message(
                    "💬 You need at least "
                    f"**{required_messages} messages** "
                    "to enter.\n\n"
                    f"Your messages: **{count}**",
                    ephemeral=True,
                )

                return

        # ----------------------------------------------------
        # Already entered
        # ----------------------------------------------------

        cursor = await bot.database.connection.execute(
            """
            SELECT 1
            FROM giveaway_entries
            WHERE giveaway_id = ?
              AND user_id = ?
            """,
            (
                giveaway[0],
                member.id,
            ),
        )

        if await cursor.fetchone():

            await interaction.response.send_message(
                "⚠️ You have already entered.",
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
                giveaway[0],
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
            giveaway[0],
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

        self.add_item(
            ParticipantsButton()
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
        interaction,
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

        if giveaway[15] != "ended":

            await interaction.response.send_message(
                "❌ This giveaway hasn't ended yet.",
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
                "❌ No eligible participant is available "
                "for another winner.",
                ephemeral=True,
            )

            return

        mentions = " ".join(
            member.mention
            for member in winners
        )

        await interaction.response.send_message(
            f"🔄 **New winner(s):** {mentions}\n\n"
            f"🎁 Prize: **{giveaway[6]}**"
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

        self.add_item(
            ParticipantsButton()
        )


# ============================================================
# ENTRY WEIGHT
# ============================================================

def calculate_entry_weight(
    member: discord.Member,
    extras: dict,
):

    weight = 1

    member_roles = {
        role.id
        for role in member.roles
    }

    for role_id, bonus in extras.items():

        try:

            role_id = int(
                role_id
            )

            bonus = int(
                bonus
            )

        except Exception:

            continue

        if role_id in member_roles:

            weight += max(
                0,
                bonus,
            )

    return weight


# ============================================================
# ELIGIBLE PARTICIPANTS
# ============================================================

async def get_eligible_entries(
    bot,
    giveaway,
    guild,
):

    extras = load_json(
        giveaway[12],
        {},
    )

    required_role_id = giveaway[9]

    banned_roles = load_json(
        giveaway[10],
        [],
    )

    required_messages = giveaway[11]

    cursor = await bot.database.connection.execute(
        """
        SELECT user_id
        FROM giveaway_entries
        WHERE giveaway_id = ?
        """,
        (giveaway[0],),
    )

    rows = await cursor.fetchall()

    eligible = []

    for row in rows:

        member = guild.get_member(
            row[0]
        )

        if not member:
            continue

        if required_role_id:

            role = guild.get_role(
                required_role_id
            )

            if role and role not in member.roles:
                continue

        member_role_ids = {
            role.id
            for role in member.roles
        }

        if any(
            role_id in member_role_ids
            for role_id in banned_roles
        ):
            continue

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
            extras,
        )

        eligible.append(
            (
                member,
                weight,
            )
        )

    return eligible


# ============================================================
# CHOOSE WINNERS
# ============================================================

async def choose_winners(
    bot,
    giveaway,
    guild,
    reroll=False,
):

    eligible = await get_eligible_entries(
        bot,
        giveaway,
        guild,
    )

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

    if not eligible:
        return []

    count = min(
        giveaway[8],
        len(eligible),
    )

    selected = []

    pool = list(
        eligible
    )

    for _ in range(count):

        if not pool:
            break

        total_weight = sum(
            weight
            for _, weight in pool
        )

        value = random.uniform(
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

            if value <= current:

                selected_index = index
                break

        member, _ = pool.pop(
            selected_index
        )

        selected.append(
            member
        )

    reroll_number = 0

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
            row[0] + 1
        )

    for member in selected:

        await bot.database.connection.execute(
            """
            INSERT INTO giveaway_winners (
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
    guild,
):

    host = guild.get_member(
        giveaway[5]
    )

    host_text = (
        host.mention
        if host
        else f"<@{giveaway[5]}>"
    )

    if giveaway[15] == "active":

        try:

            end_time = datetime.fromisoformat(
                giveaway[16]
            )

            timestamp = int(
                end_time.timestamp()
            )

            end_text = (
                f"<t:{timestamp}:R>"
            )

        except Exception:

            end_text = "Unknown"

    elif giveaway[15] == "ended":

        end_text = "🎉 Ended"

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
            f"👥 **Participants:** {entries}\n\n"
            "Click **🎉 Enter Giveaway** to participate!"
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

        roles = []

        for role_id in banned_roles:

            role = guild.get_role(
                role_id
            )

            if role:
                roles.append(
                    role.mention
                )

        if roles:

            embed.add_field(
                name="🚫 Banned Roles",
                value=" ".join(roles),
                inline=False,
            )

    if giveaway[11] > 0:

        embed.add_field(
            name="💬 Required Messages",
            value=str(
                giveaway[11]
            ),
            inline=True,
        )

    extras = load_json(
        giveaway[12],
        {},
    )

    if extras:

        lines = []

        for role_id, amount in extras.items():

            role = guild.get_role(
                int(role_id)
            )

            if role:

                lines.append(
                    f"{role.mention} +{amount}"
                )

        if lines:

            embed.add_field(
                name="✨ Extra Entries",
                value="\n".join(lines),
                inline=False,
            )

    if giveaway[15] == "active":

        embed.set_footer(
            text="Good luck! 🍀"
        )

    else:

        embed.set_footer(
            text="Giveaway ended • Reroll available"
        )

    return embed


# ============================================================
# UPDATE GIVEAWAY MESSAGE
# ============================================================

async def update_giveaway_message(
    bot,
    giveaway_id,
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

        return

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
                url=(
                    f"attachment://"
                    f"{giveaway[14]}"
                )
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
# CONFIG VIEW
# ============================================================

class GiveawayConfigView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id,
        creator_id,
    ):

        super().__init__(
            timeout=1800
        )

        self.bot = bot
        self.giveaway_id = giveaway_id
        self.creator_id = creator_id

    async def interaction_check(
        self,
        interaction,
    ):

        if interaction.user.id != self.creator_id:

            await interaction.response.send_message(
                "🔒 Only the administrator who created "
                "this giveaway can edit it.",
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
            return False

        if giveaway[15] != "configuring":

            await interaction.response.send_message(
                "❌ This giveaway has already started.",
                ephemeral=True,
            )

            return False

        return True

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    @discord.ui.button(
        label="Time",
        emoji="⏱️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def time_button(
        self,
        interaction,
        button,
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
        interaction,
        button,
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
        interaction,
        button,
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
        interaction,
        button,
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
        row=1,
    )
    async def required_role_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "🎭 Select the required role.",
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
    async def banned_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "🚫 Select banned roles.",
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
        interaction,
        button,
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
    async def extra_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "✨ Select the role that receives extra entries.",
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
        row=2,
    )
    async def thumbnail_button(
        self,
        interaction,
        button,
    ):

        await request_thumbnail(
            interaction,
            self.bot,
            self.giveaway_id,
            self.creator_id,
        )

    # --------------------------------------------------------
    # PREVIEW
    # --------------------------------------------------------

    @discord.ui.button(
        label="Preview",
        emoji="👁️",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def preview_button(
        self,
        interaction,
        button,
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
        row=3,
    )
    async def start_button(
        self,
        interaction,
        button,
    ):

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        if not giveaway:
            return

        missing = []

        if giveaway[7] <= 0:
            missing.append("⏱️ Duration")

        if giveaway[8] <= 0:
            missing.append("🏆 Winners")

        if (
            not giveaway[6]
            or giveaway[6]
            == "Not configured"
        ):
            missing.append("🎁 Prize")

        if missing:

            await interaction.response.send_message(
                "❌ **Setup incomplete.**\n\n"
                + "\n".join(
                    f"• {item}"
                    for item in missing
                ),
                ephemeral=True,
            )

            return

        end_time = (
            datetime.now(
                timezone.utc
            )
            + timedelta(
                seconds=giveaway[7]
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

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        embed = await build_giveaway_embed(
            self.bot,
            giveaway,
            interaction.guild,
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

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🎉 Giveaway Started!",
                description=(
                    f"🎁 **Prize:** {giveaway[6]}\n"
                    f"🏆 **Winners:** {giveaway[8]}\n"
                    f"⏱️ **Duration:** "
                    f"{format_duration(giveaway[7])}\n\n"
                    f"[Jump to Giveaway]({message.jump_url})"
                ),
            ),
            view=None,
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
        interaction,
        button,
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
    guild,
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

    extras = load_json(
        giveaway[12],
        {},
    )

    extra_text = "None"

    if extras:

        lines = []

        for role_id, amount in extras.items():

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

    status = {
        "configuring": "⚙️ Not Started",
        "active": "🟢 Active",
        "ended": "🔴 Ended",
    }.get(
        giveaway[15],
        "Unknown",
    )

    embed = discord.Embed(
        title="🎉 Giveaway Configuration",
        description=(
            "Configure your giveaway before starting it."
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
        value=(
            "✅ Configured"
            if giveaway[13]
            else "None"
        ),
        inline=True,
    )

    embed.add_field(
        name="⏱️ Duration Examples",
        value=(
            "`30s` • `10m` • `2hrs` • `1d` • `1w`\n"
            "`1d 5h 30m` • `2hrs 20mins`\n"
            "Capital or lowercase both work."
        ),
        inline=False,
    )

    embed.set_footer(
        text="Only the creator can edit this configuration."
    )

    return embed


# ============================================================
# GIVEAWAY COG
# ============================================================

class Giveaways(
    commands.Cog
):

    def __init__(
        self,
        bot,
    ):

        self.bot = bot

        self.finish_giveaways.start()

    def cog_unload(
        self,
    ):

        self.finish_giveaways.cancel()

    # ========================================================
    # MESSAGE COUNTER
    # ========================================================

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ):

        if message.author.bot:
            return

        if not message.guild:
            return

        try:

            await increment_message_count(
                self.bot,
                message.guild.id,
                message.author.id,
            )

        except Exception as error:

            print(
                f"Giveaway message counter error: {error}"
            )

    # ========================================================
    # GROUP
    # ========================================================

    giveaway_group = app_commands.Group(
        name="giveaway",
        description="Manage giveaways.",
    )

    # ========================================================
    # CREATE
    # ========================================================

    @giveaway_group.command(
        name="create",
        description="Create a giveaway.",
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def giveaway_create(
        self,
        interaction,
    ):

        if not interaction.guild:
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
            VALUES (?, ?, ?, ?, ?, 0, 1, 'configuring')
            """,
            (
                interaction.guild.id,
                interaction.channel.id,
                interaction.user.id,
                interaction.user.id,
            ),
        )

        await self.bot.database.connection.commit()

        cursor = await self.bot.database.connection.execute(
            "SELECT last_insert_rowid()"
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

        await interaction.response.send_message(
            embed=embed,
            view=GiveawayConfigView(
                self.bot,
                giveaway_id,
                interaction.user.id,
            ),
        )

    # ========================================================
    # PARTICIPANTS COMMAND
    # ========================================================

    @giveaway_group.command(
        name="participants",
        description="View giveaway participants and entries.",
    )
    @app_commands.describe(
        message_id="Giveaway message ID."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def giveaway_participants(
        self,
        interaction,
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

        participants = await get_participants(
            self.bot,
            giveaway,
            interaction.guild,
        )

        pages = build_participant_pages(
            participants,
            giveaway,
        )

        view = ParticipantView(
            pages
        )

        await interaction.response.send_message(
            embed=pages[0],
            view=view,
            ephemeral=True,
        )

    # ========================================================
    # END
    # ========================================================

    @giveaway_group.command(
        name="end",
        description="End an active giveaway.",
    )
    @app_commands.describe(
        message_id="Giveaway message ID."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def giveaway_end(
        self,
        interaction,
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
    # REROLL
    # ========================================================

    @giveaway_group.command(
        name="reroll",
        description="Reroll an ended giveaway.",
    )
    @app_commands.describe(
        message_id="Giveaway message ID."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def giveaway_reroll(
        self,
        interaction,
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
                "❌ The giveaway must be ended first.",
                ephemeral=True,
            )

            return

        winners = await choose_winners(
            self.bot,
            giveaway,
            interaction.guild,
            reroll=True,
        )

        if not winners:

            await interaction.response.send_message(
                "❌ No eligible participants remain "
                "for a reroll.",
                ephemeral=True,
            )

            return

        mentions = " ".join(
            member.mention
            for member in winners
        )

        await interaction.response.send_message(
            f"🔄 **New winner(s):** {mentions}\n\n"
            f"🎁 Prize: **{giveaway[6]}**"
        )

    # ========================================================
    # AUTOMATIC END LOOP
    # ========================================================

    @tasks.loop(seconds=5)
    async def finish_giveaways(
        self,
    ):

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
    async def before_finish_giveaways(
        self,
    ):

        await self.bot.wait_until_ready()

    # ========================================================
    # END GIVEAWAY INTERNAL
    # ========================================================

    async def end_giveaway(
        self,
        giveaway_id,
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

            mentions = " ".join(
                member.mention
                for member in winners
            )

            embed.add_field(
                name="🏆 Winner(s)",
                value=mentions,
                inline=False,
            )

            result_message = (
                "🎉 **GIVEAWAY ENDED!**\n\n"
                f"Congratulations {mentions}!\n"
                f"You won **{giveaway[6]}**!"
            )

        else:

            embed.add_field(
                name="🏆 Winner(s)",
                value="No eligible winners.",
                inline=False,
            )

            result_message = (
                "🎉 **GIVEAWAY ENDED!**\n\n"
                "There were no eligible winners."
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
                result_message
            )

        except Exception as error:

            print(
                f"Could not update giveaway: {error}"
            )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    @giveaway_create.error
    @giveaway_participants.error
    @giveaway_end.error
    @giveaway_reroll.error
    async def giveaway_error(
        self,
        interaction,
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
                "the giveaway."
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

async def setup(
    bot,
):

    await setup_giveaway_database(
        bot
    )

    await bot.add_cog(
        Giveaways(bot)
    )

    # Persistent active giveaway controls.
    bot.add_view(
        GiveawayEntryView()
    )

    # Persistent ended giveaway controls.
    bot.add_view(
        GiveawayRerollView()
    )

    print(
        "Giveaway system loaded."
    )
