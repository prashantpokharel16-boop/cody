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

    PRIMARY KEY (giveaway_id, user_id)
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

    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_giveaways_guild
ON giveaways(guild_id);

CREATE INDEX IF NOT EXISTS idx_giveaways_status
ON giveaways(status);

CREATE INDEX IF NOT EXISTS idx_giveaway_entries
ON giveaway_entries(giveaway_id);
"""


# ============================================================
# DATABASE HELPERS
# ============================================================

async def db_execute(bot, query, params=(), commit=False):
    """
    SQLite helper with retry handling for temporary locks.
    """

    for attempt in range(8):
        try:
            cursor = await bot.database.connection.execute(
                query,
                params,
            )

            if commit:
                await bot.database.connection.commit()

            return cursor

        except Exception as error:

            if "database is locked" not in str(error).lower():
                raise

            if attempt >= 7:
                raise

            await asyncio.sleep(
                0.25 * (attempt + 1)
            )

    raise RuntimeError("Database operation failed.")


async def setup_giveaway_database(bot):

    # SQLite performance / locking settings.
    try:
        await bot.database.connection.execute(
            "PRAGMA busy_timeout = 10000"
        )

        await bot.database.connection.execute(
            "PRAGMA journal_mode = WAL"
        )

        await bot.database.connection.execute(
            "PRAGMA synchronous = NORMAL"
        )

    except Exception as error:
        print(
            f"Giveaway SQLite setup warning: {error}"
        )

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

    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,

    "d": 86400,
    "day": 86400,
    "days": 86400,

    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}


def parse_duration(value: str) -> Optional[int]:

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

    total = 0

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
        604800,
    )

    days, remainder = divmod(
        remainder,
        86400,
    )

    hours, remainder = divmod(
        remainder,
        3600,
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

def load_json(value, fallback):

    try:
        result = json.loads(value)

        if isinstance(
            result,
            type(fallback),
        ):
            return result

    except Exception:
        pass

    return fallback


# ============================================================
# GIVEAWAY LOOKUPS
# ============================================================

async def get_giveaway(
    bot,
    giveaway_id: int,
):

    cursor = await db_execute(
        bot,
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

    cursor = await db_execute(
        bot,
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
# MESSAGE COUNT
# ============================================================

async def increment_message_count(
    bot,
    guild_id,
    user_id,
):

    await db_execute(
        bot,
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
        commit=True,
    )


async def get_message_count(
    bot,
    guild_id,
    user_id,
):

    cursor = await db_execute(
        bot,
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

    return int(row[0]) if row else 0


# ============================================================
# MODALS
# ============================================================

class DurationModal(
    discord.ui.Modal,
    title="Set Giveaway Duration",
):

    duration = discord.ui.TextInput(
        label="Duration",
        placeholder="30m / 2hrs / 1d 5h 30m",
        max_length=100,
        required=True,
    )

    def __init__(
        self,
        bot,
        giveaway_id,
    ):

        super().__init__()

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def on_submit(
        self,
        interaction,
    ):

        seconds = parse_duration(
            self.duration.value
        )

        if seconds is None:

            await interaction.response.send_message(
                "❌ Invalid duration.\n\n"
                "Examples:\n"
                "`30s`\n"
                "`10m`\n"
                "`2HRS`\n"
                "`1d 5h 30m`\n\n"
                "Capital and lowercase both work.",
                ephemeral=True,
            )

            return

        await db_execute(
            self.bot,
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
            commit=True,
        )

        await interaction.response.send_message(
            f"⏱️ Duration set to "
            f"**{format_duration(seconds)}**.",
            ephemeral=True,
        )


class WinnersModal(
    discord.ui.Modal,
    title="Set Giveaway Winners",
):

    winners = discord.ui.TextInput(
        label="Number of Winners",
        placeholder="Example: 3",
        max_length=3,
        required=True,
    )

    def __init__(
        self,
        bot,
        giveaway_id,
    ):

        super().__init__()

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def on_submit(
        self,
        interaction,
    ):

        try:
            amount = int(
                self.winners.value
            )
        except ValueError:
            amount = 0

        if not 1 <= amount <= 100:

            await interaction.response.send_message(
                "❌ Winners must be between 1 and 100.",
                ephemeral=True,
            )

            return

        await db_execute(
            self.bot,
            """
            UPDATE giveaways
            SET winners = ?
            WHERE id = ?
              AND status = 'configuring'
            """,
            (
                amount,
                self.giveaway_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"🏆 Winners set to **{amount}**.",
            ephemeral=True,
        )


class PrizeModal(
    discord.ui.Modal,
    title="Set Giveaway Prize",
):

    prize = discord.ui.TextInput(
        label="Prize",
        placeholder="Minecraft Premium Rank",
        max_length=256,
        required=True,
    )

    def __init__(
        self,
        bot,
        giveaway_id,
    ):

        super().__init__()

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def on_submit(
        self,
        interaction,
    ):

        await db_execute(
            self.bot,
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
            commit=True,
        )

        await interaction.response.send_message(
            f"🎁 Prize set to "
            f"**{self.prize.value.strip()}**.",
            ephemeral=True,
        )


class RequiredMessagesModal(
    discord.ui.Modal,
    title="Required Messages",
):

    messages = discord.ui.TextInput(
        label="Minimum Messages",
        placeholder="0 = no requirement",
        max_length=10,
        required=True,
    )

    def __init__(
        self,
        bot,
        giveaway_id,
    ):

        super().__init__()

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def on_submit(
        self,
        interaction,
    ):

        try:
            amount = int(
                self.messages.value
            )
        except ValueError:
            amount = -1

        if amount < 0:

            await interaction.response.send_message(
                "❌ Enter a valid number.",
                ephemeral=True,
            )

            return

        await db_execute(
            self.bot,
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
            commit=True,
        )

        await interaction.response.send_message(
            f"💬 Required messages: **{amount}**.",
            ephemeral=True,
        )


class ExtraEntriesModal(
    discord.ui.Modal,
    title="Set Extra Entries",
):

    amount = discord.ui.TextInput(
        label="Extra Entries",
        placeholder="Example: 3",
        max_length=5,
        required=True,
    )

    def __init__(
        self,
        bot,
        giveaway_id,
        role_id,
        role_name,
    ):

        super().__init__()

        self.bot = bot
        self.giveaway_id = giveaway_id
        self.role_id = role_id
        self.role_name = role_name

    async def on_submit(
        self,
        interaction,
    ):

        try:
            amount = int(
                self.amount.value
            )
        except ValueError:
            amount = -1

        if not 0 <= amount <= 1000:

            await interaction.response.send_message(
                "❌ Enter a number from 0 to 1000.",
                ephemeral=True,
            )

            return

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

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

        await db_execute(
            self.bot,
            """
            UPDATE giveaways
            SET extra_entries = ?
            WHERE id = ?
            """,
            (
                json.dumps(extras),
                self.giveaway_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"✨ **{self.role_name}** receives "
            f"**+{amount} entries**.",
            ephemeral=True,
        )


# ============================================================
# HOST SELECT
# ============================================================

class HostSelect(
    discord.ui.UserSelect
):

    def __init__(
        self,
        bot,
        giveaway_id,
    ):

        super().__init__(
            placeholder="Select giveaway host...",
            min_values=1,
            max_values=1,
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def callback(
        self,
        interaction,
    ):

        user = self.values[0]

        await db_execute(
            self.bot,
            """
            UPDATE giveaways
            SET host_id = ?
            WHERE id = ?
            """,
            (
                user.id,
                self.giveaway_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"👤 Host set to {user.mention}.",
            ephemeral=True,
        )


class HostView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id,
    ):

        super().__init__(
            timeout=120
        )

        self.add_item(
            HostSelect(
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
        giveaway_id,
    ):

        super().__init__(
            placeholder="Select required role...",
            min_values=1,
            max_values=1,
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def callback(
        self,
        interaction,
    ):

        role = self.values[0]

        await db_execute(
            self.bot,
            """
            UPDATE giveaways
            SET required_role_id = ?
            WHERE id = ?
            """,
            (
                role.id,
                self.giveaway_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"🎭 Required role: {role.mention}",
            ephemeral=True,
        )


class RequiredRoleView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id,
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
        giveaway_id,
    ):

        super().__init__(
            placeholder="Select banned roles...",
            min_values=1,
            max_values=25,
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def callback(
        self,
        interaction,
    ):

        roles = [
            role.id
            for role in self.values
        ]

        await db_execute(
            self.bot,
            """
            UPDATE giveaways
            SET banned_role_ids = ?
            WHERE id = ?
            """,
            (
                json.dumps(roles),
                self.giveaway_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"🚫 Added **{len(roles)}** banned role(s).",
            ephemeral=True,
        )


class BannedRoleView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        giveaway_id,
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
# EXTRA ENTRY ROLE
# ============================================================

class ExtraEntryRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        bot,
        giveaway_id,
    ):

        super().__init__(
            placeholder="Select role for extra entries...",
            min_values=1,
            max_values=1,
        )

        self.bot = bot
        self.giveaway_id = giveaway_id

    async def callback(
        self,
        interaction,
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
        giveaway_id,
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
    interaction,
    bot,
    giveaway_id,
    creator_id,
):

    await interaction.response.send_message(
        "🖼️ Upload the giveaway thumbnail in this "
        "channel within **60 seconds**.",
        ephemeral=True,
    )

    def check(message):

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
            "❌ Please upload an image.",
            ephemeral=True,
        )

        return

    if attachment.size > 8 * 1024 * 1024:

        await interaction.followup.send(
            "❌ Maximum thumbnail size is 8 MB.",
            ephemeral=True,
        )

        return

    try:

        data = await attachment.read()

    except Exception:

        await interaction.followup.send(
            "❌ Couldn't read that image.",
            ephemeral=True,
        )

        return

    await db_execute(
        bot,
        """
        UPDATE giveaways
        SET thumbnail_data = ?,
            thumbnail_filename = ?
        WHERE id = ?
        """,
        (
            data,
            attachment.filename,
            giveaway_id,
        ),
        commit=True,
    )

    try:
        await message.delete()
    except Exception:
        pass

    await interaction.followup.send(
        "🖼️ Thumbnail updated.",
        ephemeral=True,
    )


# ============================================================
# PARTICIPANTS
# ============================================================

async def get_participants(
    bot,
    giveaway,
    guild,
):

    extras = load_json(
        giveaway[12],
        {},
    )

    cursor = await db_execute(
        bot,
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

        member = guild.get_member(
            row[0]
        )

        if not member:
            continue

        extra = 0
        extra_roles = []

        for role_id, amount in extras.items():

            try:

                role = guild.get_role(
                    int(role_id)
                )

                amount = int(
                    amount
                )

            except Exception:

                continue

            if (
                role
                and role in member.roles
            ):

                extra += amount

                extra_roles.append(
                    (
                        role,
                        amount,
                    )
                )

        participants.append(
            {
                "member": member,
                "extra": extra,
                "total": 1 + extra,
                "roles": extra_roles,
            }
        )

    return participants


def build_participant_pages(
    participants,
    giveaway,
):

    if not participants:

        return [
            discord.Embed(
                title="👥 Giveaway Participants",
                description="No participants yet.",
            )
        ]

    pages = []

    per_page = 10

    for start in range(
        0,
        len(participants),
        per_page,
    ):

        chunk = participants[
            start:start + per_page
        ]

        page_number = (
            start // per_page
        ) + 1

        total_pages = (
            (len(participants) + per_page - 1)
            // per_page
        )

        embed = discord.Embed(
            title="👥 Giveaway Participants",
            description=(
                f"🎁 **Prize:** {giveaway[6]}\n"
                f"👥 **Participants:** "
                f"{len(participants)}\n"
                f"🎟️ **Total Entries:** "
                f"{sum(p['total'] for p in participants)}"
            ),
        )

        lines = []

        for number, participant in enumerate(
            chunk,
            start=start + 1,
        ):

            member = participant["member"]

            if participant["roles"]:

                extra_text = ", ".join(
                    f"{role.mention} +{amount}"
                    for role, amount
                    in participant["roles"]
                )

            else:

                extra_text = "None"

            lines.append(
                f"**{number}. {member.display_name}**\n"
                f"└ 🎟️ Entries: **{participant['total']}**\n"
                f"└ ✨ Extra: {extra_text}"
            )

        embed.add_field(
            name="Participants",
            value="\n\n".join(lines)[:1024],
            inline=False,
        )

        embed.set_footer(
            text=f"Page {page_number}/{total_pages}"
        )

        pages.append(embed)

    return pages


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

    def refresh_buttons(self):

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

        self.refresh_buttons()

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

        self.refresh_buttons()

        await interaction.response.edit_message(
            embed=self.pages[self.page],
            view=self,
        )


# ============================================================
# WINNER SELECTION
# ============================================================

def calculate_weight(
    member,
    extras,
):

    weight = 1

    role_ids = {
        role.id
        for role in member.roles
    }

    for role_id, amount in extras.items():

        try:

            if int(role_id) in role_ids:
                weight += int(amount)

        except Exception:
            pass

    return weight


async def get_eligible_entries(
    bot,
    giveaway,
    guild,
):

    extras = load_json(
        giveaway[12],
        {},
    )

    banned_roles = load_json(
        giveaway[10],
        [],
    )

    required_role_id = giveaway[9]
    required_messages = giveaway[11]

    cursor = await db_execute(
        bot,
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

            if (
                role
                and role not in member.roles
            ):
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

            count = await get_message_count(
                bot,
                guild.id,
                member.id,
            )

            if count < required_messages:
                continue

        weight = calculate_weight(
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

        cursor = await db_execute(
            bot,
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

    winner_count = min(
        giveaway[8],
        len(eligible),
    )

    pool = list(
        eligible
    )

    winners = []

    for _ in range(
        winner_count
    ):

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

        winners.append(
            member
        )

    reroll_number = 0

    if reroll:

        cursor = await db_execute(
            bot,
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

    for member in winners:

        await db_execute(
            bot,
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
            commit=True,
        )

    return winners


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

            end = datetime.fromisoformat(
                giveaway[16]
            )

            end_text = (
                f"<t:{int(end.timestamp())}:R>"
            )

        except Exception:

            end_text = "Unknown"

    elif giveaway[15] == "ended":

        end_text = "🎉 Ended"

    else:

        end_text = "Not started"

    cursor = await db_execute(
        bot,
        """
        SELECT COUNT(*)
        FROM giveaway_entries
        WHERE giveaway_id = ?
        """,
        (giveaway[0],),
    )

    row = await cursor.fetchone()

    participants = (
        row[0]
        if row
        else 0
    )

    embed = discord.Embed(
        title="🎉 GIVEAWAY",
        description=(
            f"## 🎁 {giveaway[6]}\n\n"
            f"🏆 **Winners:** {giveaway[8]}\n"
            f"👤 **Host:** {host_text}\n"
            f"⏰ **Ends:** {end_text}\n"
            f"👥 **Participants:** {participants}\n\n"
            "Click **🎉 Enter Giveaway** to enter!"
        ),
    )

    if giveaway[9]:

        role = guild.get_role(
            giveaway[9]
        )

        if role:

            embed.add_field(
                name="🎭 Required Role",
                value=role.mention,
                inline=True,
            )

    banned = load_json(
        giveaway[10],
        [],
    )

    if banned:

        names = []

        for role_id in banned:

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

    embed.set_footer(
        text=(
            "Good luck! 🍀"
            if giveaway[15] == "active"
            else "Giveaway ended • Reroll available"
        )
    )

    return embed


# ============================================================
# PARTICIPANTS BUTTON
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

        await interaction.response.send_message(
            embed=pages[0],
            view=ParticipantView(pages),
            ephemeral=True,
        )


# ============================================================
# ENTER BUTTON
# ============================================================

class EnterGiveawayButton(
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

        member = interaction.user

        if not isinstance(
            member,
            discord.Member,
        ):

            member = interaction.guild.get_member(
                interaction.user.id
            )

        if not member:
            return

        # Required role.
        if giveaway[9]:

            role = interaction.guild.get_role(
                giveaway[9]
            )

            if (
                role
                and role not in member.roles
            ):

                await interaction.response.send_message(
                    f"❌ You need {role.mention} "
                    "to enter.",
                    ephemeral=True,
                )

                return

        # Banned roles.
        banned = load_json(
            giveaway[10],
            [],
        )

        member_roles = {
            role.id
            for role in member.roles
        }

        if any(
            role_id in member_roles
            for role_id in banned
        ):

            await interaction.response.send_message(
                "🚫 You have a banned role.",
                ephemeral=True,
            )

            return

        # Required messages.
        if giveaway[11] > 0:

            count = await get_message_count(
                bot,
                interaction.guild.id,
                member.id,
            )

            if count < giveaway[11]:

                await interaction.response.send_message(
                    f"💬 You need **{giveaway[11]} "
                    f"messages** to enter.\n"
                    f"Your messages: **{count}**",
                    ephemeral=True,
                )

                return

        # Already entered.
        cursor = await db_execute(
            bot,
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
                "⚠️ You already entered this giveaway.",
                ephemeral=True,
            )

            return

        await db_execute(
            bot,
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
            commit=True,
        )

        await interaction.response.send_message(
            "🎉 **You're entered!** Good luck! 🍀",
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
            EnterGiveawayButton()
        )

        self.add_item(
            ParticipantsButton()
        )


# ============================================================
# REROLL
# ============================================================

class RerollButton(
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
            f"🔄 **Rerolled winner(s):** {mentions}\n"
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
            RerollButton()
        )

        self.add_item(
            ParticipantsButton()
        )


# ============================================================
# UPDATE MESSAGE
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

    view = (
        GiveawayEntryView()
        if giveaway[15] == "active"
        else GiveawayRerollView()
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
                    "attachment://"
                    + giveaway[14]
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
        pass


# ============================================================
# CONFIG EMBED
# ============================================================

def build_config_embed(
    guild,
    giveaway,
):

    host = guild.get_member(
        giveaway[5]
    )

    host_text = (
        host.mention
        if host
        else f"<@{giveaway[5]}>"
    )

    required = "None"

    if giveaway[9]:

        role = guild.get_role(
            giveaway[9]
        )

        if role:
            required = role.mention

    banned = load_json(
        giveaway[10],
        [],
    )

    banned_text = "None"

    if banned:

        roles = []

        for role_id in banned:

            role = guild.get_role(
                role_id
            )

            if role:
                roles.append(
                    role.mention
                )

        if roles:
            banned_text = " ".join(
                roles
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
            "Configure your giveaway below.\n\n"
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
        value=required,
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
        name="⏱️ Time Format",
        value=(
            "`30s` • `10m` • `2hr` • `2hrs`\n"
            "`1d` • `1w` • `1d 5h 30m`\n"
            "Capital/lowercase both work."
        ),
        inline=False,
    )

    return embed


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

        return True

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
            view=HostView(
                self.bot,
                self.giveaway_id,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Required Role",
        emoji="🎭",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def required_button(
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
            "✨ Select a role.",
            view=ExtraEntryRoleView(
                self.bot,
                self.giveaway_id,
            ),
            ephemeral=True,
        )

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
            embed=embed,
            ephemeral=True,
        )

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
                "❌ **Setup incomplete:**\n\n"
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

        await db_execute(
            self.bot,
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
            commit=True,
        )

        giveaway = await get_giveaway(
            self.bot,
            self.giveaway_id,
        )

        embed = await build_giveaway_embed(
            self.bot,
            giveaway,
            interaction.guild,
        )

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
                    "attachment://"
                    + giveaway[14]
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

        await db_execute(
            self.bot,
            """
            UPDATE giveaways
            SET message_id = ?
            WHERE id = ?
            """,
            (
                message.id,
                self.giveaway_id,
            ),
            commit=True,
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🎉 Giveaway Started!",
                description=(
                    f"🎁 **Prize:** {giveaway[6]}\n"
                    f"🏆 **Winners:** {giveaway[8]}\n"
                    f"⏱️ **Duration:** "
                    f"{format_duration(giveaway[7])}\n\n"
                    f"[Jump to Giveaway]"
                    f"({message.jump_url})"
                ),
            ),
            view=None,
        )

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

        await interaction.response.edit_message(
            embed=build_config_embed(
                interaction.guild,
                giveaway,
            ),
            view=self,
        )


# ============================================================
# COG
# ============================================================

class Giveaways(
    commands.Cog
):

    giveaway_group = app_commands.Group(
        name="giveaway",
        description="Manage giveaways.",
    )

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
        message,
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
    # CREATE
    # ========================================================

    @giveaway_group.command(
        name="create",
        description="Create a giveaway.",
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def create(
        self,
        interaction,
    ):

        await db_execute(
            self.bot,
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
                "Not configured",
            ),
            commit=True,
        )

        cursor = await db_execute(
            self.bot,
            "SELECT last_insert_rowid()"
        )

        row = await cursor.fetchone()

        giveaway_id = row[0]

        giveaway = await get_giveaway(
            self.bot,
            giveaway_id,
        )

        await interaction.response.send_message(
            embed=build_config_embed(
                interaction.guild,
                giveaway,
            ),
            view=GiveawayConfigView(
                self.bot,
                giveaway_id,
                interaction.user.id,
            ),
        )

    # ========================================================
    # PARTICIPANTS
    # ========================================================

    @giveaway_group.command(
        name="participants",
        description="View giveaway participants.",
    )
    @app_commands.describe(
        message_id="Giveaway message ID."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def participants(
        self,
        interaction,
        message_id: str,
    ):

        try:
            message_id = int(message_id)
        except ValueError:

            await interaction.response.send_message(
                "❌ Invalid message ID.",
                ephemeral=True,
            )

            return

        giveaway = await get_giveaway_by_message(
            self.bot,
            message_id,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        people = await get_participants(
            self.bot,
            giveaway,
            interaction.guild,
        )

        pages = build_participant_pages(
            people,
            giveaway,
        )

        await interaction.response.send_message(
            embed=pages[0],
            view=ParticipantView(pages),
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
    async def end(
        self,
        interaction,
        message_id: str,
    ):

        try:
            message_id = int(message_id)
        except ValueError:

            await interaction.response.send_message(
                "❌ Invalid message ID.",
                ephemeral=True,
            )

            return

        giveaway = await get_giveaway_by_message(
            self.bot,
            message_id,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        if giveaway[15] != "active":

            await interaction.response.send_message(
                "❌ Giveaway is not active.",
                ephemeral=True,
            )

            return

        await self.finish_giveaway(
            giveaway[0]
        )

        await interaction.response.send_message(
            "🛑 Giveaway ended.",
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
    async def reroll(
        self,
        interaction,
        message_id: str,
    ):

        try:
            message_id = int(message_id)
        except ValueError:

            await interaction.response.send_message(
                "❌ Invalid message ID.",
                ephemeral=True,
            )

            return

        giveaway = await get_giveaway_by_message(
            self.bot,
            message_id,
        )

        if not giveaway:

            await interaction.response.send_message(
                "❌ Giveaway not found.",
                ephemeral=True,
            )

            return

        if giveaway[15] != "ended":

            await interaction.response.send_message(
                "❌ Giveaway must be ended first.",
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
                "❌ No eligible participants remain.",
                ephemeral=True,
            )

            return

        mentions = " ".join(
            member.mention
            for member in winners
        )

        await interaction.response.send_message(
            f"🔄 **New winner(s):** {mentions}\n"
            f"🎁 **Prize:** {giveaway[6]}"
        )

    # ========================================================
    # AUTOMATIC TIMER
    # ========================================================

    @tasks.loop(seconds=5)
    async def finish_giveaways(
        self,
    ):

        try:

            cursor = await db_execute(
                self.bot,
                """
                SELECT id
                FROM giveaways
                WHERE status = 'active'
                  AND end_time IS NOT NULL
                """
            )

            rows = await cursor.fetchall()

            now = datetime.now(
                timezone.utc
            )

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

                    await self.finish_giveaway(
                        giveaway[0]
                    )

        except Exception as error:

            print(
                f"Giveaway timer error: {error}"
            )

    @finish_giveaways.before_loop
    async def before_finish_giveaways(
        self,
    ):

        await self.bot.wait_until_ready()

    # ========================================================
    # FINISH GIVEAWAY
    # ========================================================

    async def finish_giveaway(
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

        await db_execute(
            self.bot,
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
            commit=True,
        )

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

            result = (
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

            result = (
                "🎉 **GIVEAWAY ENDED!**\n\n"
                "No eligible winners were found."
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
                        "attachment://"
                        + giveaway[14]
                    )
                )

                await message.edit(
                    embed=embed,
                    view=GiveawayRerollView(),
                    attachments=[file],
                )

            else:

                await message.edit(
                    embed=embed,
                    view=GiveawayRerollView(),
                )

            await channel.send(
                result
            )

        except Exception as error:

            print(
                f"Giveaway finish error: {error}"
            )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    @create.error
    @participants.error
    @end.error
    @reroll.error
    async def command_error(
        self,
        interaction,
        error,
    ):

        print(
            f"Giveaway command error: {error}"
        )

        if isinstance(
            error,
            app_commands.errors.MissingPermissions,
        ):

            message = (
                "🔒 You need **Administrator** permission "
                "to manage giveaways."
            )

        else:

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

    bot.add_view(
        GiveawayEntryView()
    )

    bot.add_view(
        GiveawayRerollView()
    )

    print(
        "Giveaway system loaded."
    )
