import re
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import aiosqlite


# ============================================================
# DATABASE TABLES
# ============================================================

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS moderation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS moderation_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mod_warnings_user
ON moderation_warnings(guild_id, user_id);
"""


# ============================================================
# DURATION PARSER
# ============================================================

def parse_duration(value: str) -> Optional[timedelta]:
    """
    Supports:
    30s
    2m
    1h
    7d
    1w

    Also supports combinations:
    1h30m
    2d12h
    """

    value = value.lower().replace(" ", "").strip()

    if not value:
        return None

    matches = re.findall(r"(\d+)(s|m|h|d|w)", value)

    if not matches:
        return None

    rebuilt = "".join(number + unit for number, unit in matches)

    if rebuilt != value:
        return None

    total_seconds = 0

    for number, unit in matches:

        number = int(number)

        if unit == "s":
            total_seconds += number

        elif unit == "m":
            total_seconds += number * 60

        elif unit == "h":
            total_seconds += number * 60 * 60

        elif unit == "d":
            total_seconds += number * 24 * 60 * 60

        elif unit == "w":
            total_seconds += number * 7 * 24 * 60 * 60

    if total_seconds <= 0:
        return None

    # Discord timeout maximum is 28 days.
    if total_seconds > 28 * 24 * 60 * 60:
        return None

    return timedelta(seconds=total_seconds)


def format_duration(delta: timedelta) -> str:

    seconds = int(delta.total_seconds())

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if seconds:
        parts.append(f"{seconds}s")

    return " ".join(parts) if parts else "0s"


# ============================================================
# MODERATION COG
# ============================================================

class Moderation(commands.Cog):

    def __init__(self, bot: commands.Bot):

        self.bot = bot

        self.db_path = None

    # ========================================================
    # READY
    # ========================================================

    async def cog_load(self):

        import config

        self.db_path = config.DATABASE_PATH

        async with aiosqlite.connect(self.db_path) as db:

            await db.executescript(CREATE_TABLES)

            await db.commit()

    # ========================================================
    # DATABASE
    # ========================================================

    async def get_db(self):

        return await aiosqlite.connect(self.db_path)

    # ========================================================
    # EMBEDS
    # ========================================================

    def success_embed(
        self,
        title: str,
        description: str
    ) -> discord.Embed:

        return discord.Embed(
            title=f"✅ {title}",
            description=description,
            color=discord.Color.green()
        )

    def error_embed(
        self,
        title: str,
        description: str
    ) -> discord.Embed:

        return discord.Embed(
            title=f"❌ {title}",
            description=description,
            color=discord.Color.red()
        )

    # ========================================================
    # PERMISSION / HIERARCHY
    # ========================================================

    async def can_moderate(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ) -> bool:

        guild = interaction.guild

        if guild is None:
            return False

        moderator = interaction.user

        if not isinstance(moderator, discord.Member):
            return False

        # Server owner can moderate everyone except themselves.
        if member.id == guild.owner_id:
            return False

        # Nobody can moderate themselves.
        if member.id == moderator.id:
            return False

        # Server owner bypasses moderator role hierarchy.
        if moderator.id != guild.owner_id:

            if member.top_role >= moderator.top_role:
                return False

        # Bot cannot moderate users above/equal to its highest role.
        me = guild.me

        if me is None:
            return False

        if member.top_role >= me.top_role:
            return False

        return True

    # ========================================================
    # LOG CHANNEL
    # ========================================================

    async def get_log_channel(
        self,
        guild_id: int
    ):

        async with aiosqlite.connect(self.db_path) as db:

            cursor = await db.execute(
                """
                SELECT channel_id
                FROM moderation_logs
                WHERE guild_id = ?
                """,
                (guild_id,)
            )

            row = await cursor.fetchone()

        if row is None:
            return None

        return self.bot.get_channel(row[0])

    async def save_log_channel(
        self,
        guild_id: int,
        channel_id: int
    ):

        async with aiosqlite.connect(self.db_path) as db:

            await db.execute(
                """
                INSERT OR REPLACE INTO moderation_logs
                (guild_id, channel_id)
                VALUES (?, ?)
                """,
                (guild_id, channel_id)
            )

            await db.commit()

    async def remove_log_channel(
        self,
        guild_id: int
    ):

        async with aiosqlite.connect(self.db_path) as db:

            await db.execute(
                """
                DELETE FROM moderation_logs
                WHERE guild_id = ?
                """,
                (guild_id,)
            )

            await db.commit()

    async def send_log(
        self,
        guild: discord.Guild,
        title: str,
        description: str,
        color: discord.Color
    ):

        channel = await self.get_log_channel(guild.id)

        if channel is None:
            return

        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )

        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(embed=embed)

        except discord.Forbidden:
            pass

        except discord.HTTPException:
            pass

    # ========================================================
    # /BAN
    # ========================================================

    @app_commands.command(
        name="ban",
        description="Ban a member from the server."
    )
    @app_commands.describe(
        user="The member to ban.",
        reason="Reason for the ban."
    )
    @app_commands.default_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided."
    ):

        guild = interaction.guild

        if guild is None:
            return

        if not await self.can_moderate(interaction, user):

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Cannot Ban",
                    "You cannot ban this member because of the role hierarchy."
                ),
                ephemeral=True
            )
            return

        try:

            await user.ban(
                reason=f"{reason} | Moderator: {interaction.user}"
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Ban Failed",
                    "I don't have permission to ban this member."
                ),
                ephemeral=True
            )
            return

        except discord.HTTPException:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Ban Failed",
                    "Discord rejected the ban request."
                ),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=self.success_embed(
                "Member Banned",
                f"**User:** {user.mention}\n"
                f"**Reason:** {reason}"
            )
        )

        await self.send_log(
            guild,
            "🔨 Member Banned",
            f"**User:** {user} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            discord.Color.red()
        )

    # ========================================================
    # /KICK
    # ========================================================

    @app_commands.command(
        name="kick",
        description="Kick a member from the server."
    )
    @app_commands.describe(
        user="The member to kick.",
        reason="Reason for the kick."
    )
    @app_commands.default_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided."
    ):

        guild = interaction.guild

        if guild is None:
            return

        if not await self.can_moderate(interaction, user):

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Cannot Kick",
                    "You cannot kick this member because of the role hierarchy."
                ),
                ephemeral=True
            )
            return

        try:

            await user.kick(
                reason=f"{reason} | Moderator: {interaction.user}"
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Kick Failed",
                    "I don't have permission to kick this member. "
                    "Make sure my bot role is above their highest role."
                ),
                ephemeral=True
            )
            return

        except discord.HTTPException:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Kick Failed",
                    "Discord rejected the kick request."
                ),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=self.success_embed(
                "Member Kicked",
                f"**User:** {user.mention}\n"
                f"**Reason:** {reason}"
            )
        )

        await self.send_log(
            guild,
            "👢 Member Kicked",
            f"**User:** {user} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            discord.Color.orange()
        )

    # ========================================================
    # /TIMEOUT
    # ========================================================

    @app_commands.command(
        name="timeout",
        description="Timeout a member."
    )
    @app_commands.describe(
        user="The member to timeout.",
        duration="Example: 2m, 1h, 7d.",
        reason="Reason for the timeout."
    )
    @app_commands.default_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: str,
        reason: str = "No reason provided."
    ):

        guild = interaction.guild

        if guild is None:
            return

        delta = parse_duration(duration)

        if delta is None:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Invalid Duration",
                    "Use something like `30s`, `2m`, `1h`, or `7d`.\n"
                    "Maximum timeout is 28 days."
                ),
                ephemeral=True
            )
            return

        if not await self.can_moderate(interaction, user):

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Cannot Timeout",
                    "You cannot timeout this member because of the role hierarchy."
                ),
                ephemeral=True
            )
            return

        try:

            await user.timeout(
                delta,
                reason=f"{reason} | Moderator: {interaction.user}"
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Timeout Failed",
                    "I don't have permission to timeout this member."
                ),
                ephemeral=True
            )
            return

        except discord.HTTPException:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Timeout Failed",
                    "Discord rejected the timeout request."
                ),
                ephemeral=True
            )
            return

        duration_text = format_duration(delta)

        await interaction.response.send_message(
            embed=self.success_embed(
                "Member Timed Out",
                f"**User:** {user.mention}\n"
                f"**Duration:** {duration_text}\n"
                f"**Reason:** {reason}"
            )
        )

        await self.send_log(
            guild,
            "🔇 Member Timed Out",
            f"**User:** {user} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Duration:** {duration_text}\n"
            f"**Reason:** {reason}",
            discord.Color.orange()
        )

    # ========================================================
    # /UNMUTE
    # ========================================================

    @app_commands.command(
        name="unmute",
        description="Remove a timeout from a member."
    )
    @app_commands.describe(
        user="The member to unmute.",
        reason="Reason for removing the timeout."
    )
    @app_commands.default_permissions(moderate_members=True)
    async def unmute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided."
    ):

        guild = interaction.guild

        if guild is None:
            return

        if user.timed_out_until is None:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Not Muted",
                    f"{user.mention} is not currently timed out."
                ),
                ephemeral=True
            )
            return

        try:

            await user.timeout(
                None,
                reason=f"{reason} | Moderator: {interaction.user}"
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Unmute Failed",
                    "I don't have permission to remove this timeout."
                ),
                ephemeral=True
            )
            return

        except discord.HTTPException:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Unmute Failed",
                    "Discord rejected the request."
                ),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=self.success_embed(
                "Member Unmuted",
                f"**User:** {user.mention}\n"
                f"**Reason:** {reason}"
            )
        )

        await self.send_log(
            guild,
            "🔊 Member Unmuted",
            f"**User:** {user} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            discord.Color.green()
        )

    # ========================================================
    # /WARN
    # ========================================================

    @app_commands.command(
        name="warn",
        description="Warn a member."
    )
    @app_commands.describe(
        user="The member to warn.",
        reason="Reason for the warning."
    )
    @app_commands.default_permissions(moderate_members=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str
    ):

        guild = interaction.guild

        if guild is None:
            return

        if not await self.can_moderate(interaction, user):

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Cannot Warn",
                    "You cannot warn this member because of the role hierarchy."
                ),
                ephemeral=True
            )
            return

        async with aiosqlite.connect(self.db_path) as db:

            cursor = await db.execute(
                """
                INSERT INTO moderation_warnings
                (guild_id, user_id, moderator_id, reason)
                VALUES (?, ?, ?, ?)
                """,
                (
                    guild.id,
                    user.id,
                    interaction.user.id,
                    reason
                )
            )

            warning_id = cursor.lastrowid

            await db.commit()

        await interaction.response.send_message(
            embed=self.success_embed(
                "Member Warned",
                f"**User:** {user.mention}\n"
                f"**Warning ID:** `{warning_id}`\n"
                f"**Reason:** {reason}"
            )
        )

        await self.send_log(
            guild,
            "⚠️ Member Warned",
            f"**User:** {user} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Warning ID:** `{warning_id}`\n"
            f"**Reason:** {reason}",
            discord.Color.yellow()
        )

    # ========================================================
    # WARNING REMOVE VIEW
    # ========================================================

    class WarningRemoveSelect(
        discord.ui.Select
    ):

        def __init__(
            self,
            cog,
            guild_id,
            user_id,
            warnings
        ):

            self.cog = cog
            self.guild_id = guild_id
            self.user_id = user_id

            options = []

            for warning in warnings[:25]:

                warning_id, moderator_id, reason, created_at = warning

                text = reason[:80]

                options.append(
                    discord.SelectOption(
                        label=f"Warning #{warning_id}",
                        description=text,
                        value=str(warning_id)
                    )
                )

            super().__init__(
                placeholder="Select a warning to remove...",
                min_values=1,
                max_values=1,
                options=options
            )

        async def callback(
            self,
            interaction: discord.Interaction
        ):

            warning_id = int(self.values[0])

            if not isinstance(
                interaction.user,
                discord.Member
            ):
                return

            if not (
                interaction.user.guild_permissions.moderate_members
                or interaction.user.guild_permissions.administrator
            ):

                await interaction.response.send_message(
                    "❌ You don't have permission to remove warnings.",
                    ephemeral=True
                )
                return

            async with aiosqlite.connect(
                self.cog.db_path
            ) as db:

                cursor = await db.execute(
                    """
                    SELECT user_id, reason
                    FROM moderation_warnings
                    WHERE id = ?
                    AND guild_id = ?
                    """,
                    (
                        warning_id,
                        self.guild_id
                    )
                )

                row = await cursor.fetchone()

                if row is None:

                    await interaction.response.send_message(
                        "❌ That warning no longer exists.",
                        ephemeral=True
                    )
                    return

                await db.execute(
                    """
                    DELETE FROM moderation_warnings
                    WHERE id = ?
                    AND guild_id = ?
                    """,
                    (
                        warning_id,
                        self.guild_id
                    )
                )

                await db.commit()

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="✅ Warning Removed",
                    description=(
                        f"Warning `{warning_id}` was removed."
                    ),
                    color=discord.Color.green()
                ),
                view=None
            )

            guild = interaction.guild

            if guild:

                await self.cog.send_log(
                    guild,
                    "🗑️ Warning Removed",
                    f"**Warning ID:** `{warning_id}`\n"
                    f"**User ID:** `{self.user_id}`\n"
                    f"**Moderator:** {interaction.user.mention}",
                    discord.Color.green()
                )

    class WarningRemoveView(discord.ui.View):

        def __init__(
            self,
            cog,
            guild_id,
            user_id,
            warnings
        ):

            super().__init__(timeout=120)

            self.add_item(
                cog.WarningRemoveSelect(
                    cog,
                    guild_id,
                    user_id,
                    warnings
                )
            )

    # ========================================================
    # /WARNINGS
    # ========================================================

    @app_commands.command(
        name="warnings",
        description="View a member's warnings."
    )
    @app_commands.describe(
        user="The member whose warnings you want to view."
    )
    @app_commands.default_permissions(moderate_members=True)
    async def warnings(
        self,
        interaction: discord.Interaction,
        user: discord.Member
    ):

        guild = interaction.guild

        if guild is None:
            return

        async with aiosqlite.connect(self.db_path) as db:

            cursor = await db.execute(
                """
                SELECT id, moderator_id, reason, created_at
                FROM moderation_warnings
                WHERE guild_id = ?
                AND user_id = ?
                ORDER BY id DESC
                """,
                (
                    guild.id,
                    user.id
                )
            )

            warnings = await cursor.fetchall()

        if not warnings:

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="📋 Warnings",
                    description=(
                        f"{user.mention} has no warnings."
                    ),
                    color=discord.Color.green()
                ),
                ephemeral=True
            )
            return

        lines = []

        for warning in warnings[:20]:

            warning_id, moderator_id, reason, created_at = warning

            moderator = guild.get_member(moderator_id)

            moderator_text = (
                moderator.mention
                if moderator
                else f"<@{moderator_id}>"
            )

            lines.append(
                f"**#{warning_id}** — {reason}\n"
                f"Moderator: {moderator_text}\n"
                f"Date: `{created_at}`"
            )

        embed = discord.Embed(
            title=f"📋 Warnings — {user}",
            description="\n\n".join(lines),
            color=discord.Color.yellow()
        )

        embed.set_thumbnail(
            url=user.display_avatar.url
        )

        view = self.WarningRemoveView(
            self,
            guild.id,
            user.id,
            warnings
        )

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True
        )

    # ========================================================
    # /UNBAN
    # ========================================================

    @app_commands.command(
        name="unban",
        description="Unban a user using their Discord ID."
    )
    @app_commands.describe(
        user_id="The Discord user ID to unban.",
        reason="Reason for the unban."
    )
    @app_commands.default_permissions(ban_members=True)
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str = "No reason provided."
    ):

        guild = interaction.guild

        if guild is None:
            return

        try:
            user_id_int = int(user_id)

        except ValueError:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Invalid User ID",
                    "Enter a valid Discord user ID."
                ),
                ephemeral=True
            )
            return

        try:

            user = await self.bot.fetch_user(
                user_id_int
            )

            await guild.unban(
                user,
                reason=f"{reason} | Moderator: {interaction.user}"
            )

        except discord.NotFound:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Unban Failed",
                    "That user is not banned or doesn't exist."
                ),
                ephemeral=True
            )
            return

        except discord.Forbidden:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Unban Failed",
                    "I don't have permission to unban members."
                ),
                ephemeral=True
            )
            return

        except discord.HTTPException:

            await interaction.response.send_message(
                embed=self.error_embed(
                    "Unban Failed",
                    "Discord rejected the unban request."
                ),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=self.success_embed(
                "User Unbanned",
                f"**User:** {user}\n"
                f"**ID:** `{user.id}`\n"
                f"**Reason:** {reason}"
            )
        )

        await self.send_log(
            guild,
            "🔓 User Unbanned",
            f"**User:** {user} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            discord.Color.green()
        )

    # ========================================================
    # /USER
    # ========================================================

    @app_commands.command(
        name="user",
        description="Show detailed information about a member."
    )
    @app_commands.describe(
        user="The member to inspect."
    )
    async def user_info(
        self,
        interaction: discord.Interaction,
        user: discord.Member
    ):

        guild = interaction.guild

        if guild is None:
            return

        roles = [
            role.mention
            for role in user.roles
            if role != guild.default_role
        ]

        roles_text = (
            ", ".join(roles)
            if roles
            else "No roles"
        )

        embed = discord.Embed(
            title="👤 User Information",
            color=user.color
            if user.color != discord.Color.default()
            else discord.Color.blurple()
        )

        embed.set_thumbnail(
            url=user.display_avatar.url
        )

        embed.add_field(
            name="Username",
            value=str(user),
            inline=True
        )

        embed.add_field(
            name="Display Name",
            value=user.display_name,
            inline=True
        )

        embed.add_field(
            name="User ID",
            value=f"`{user.id}`",
            inline=True
        )

        embed.add_field(
            name="Account Created",
            value=discord.utils.format_dt(
                user.created_at,
                style="F"
            ),
            inline=False
        )

        embed.add_field(
            name="Joined Server",
            value=(
                discord.utils.format_dt(
                    user.joined_at,
                    style="F"
                )
                if user.joined_at
                else "Unknown"
            ),
            inline=False
        )

        embed.add_field(
            name="Highest Role",
            value=user.top_role.mention,
            inline=True
        )

        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=roles_text[:1024],
            inline=False
        )

        if user.premium_since:

            embed.add_field(
                name="Server Booster",
                value="Yes",
                inline=True
            )

        embed.set_footer(
            text=f"Requested by {interaction.user}"
        )

        await interaction.response.send_message(
            embed=embed
        )

    # ========================================================
    # /LOGS GROUP
    # ========================================================

    logs_group = app_commands.Group(
        name="logs",
        description="Configure moderation logs."
    )

    @logs_group.command(
        name="channel",
        description="Set the moderation log channel."
    )
    @app_commands.describe(
        channel="The channel where moderation logs should be sent."
    )
    @app_commands.default_permissions(administrator=True)
    async def logs_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel
    ):

        guild = interaction.guild

        if guild is None:
            return

        await self.save_log_channel(
            guild.id,
            channel.id
        )

        await interaction.response.send_message(
            embed=self.success_embed(
                "Moderation Logs Enabled",
                f"Moderation logs will now be sent to {channel.mention}."
            ),
            ephemeral=True
        )

    @logs_group.command(
        name="disable",
        description="Disable moderation logs."
    )
    @app_commands.default_permissions(administrator=True)
    async def logs_disable(
        self,
        interaction: discord.Interaction
    ):

        guild = interaction.guild

        if guild is None:
            return

        await self.remove_log_channel(
            guild.id
        )

        await interaction.response.send_message(
            embed=self.success_embed(
                "Moderation Logs Disabled",
                "Moderation logs have been disabled."
            ),
            ephemeral=True
        )


# ============================================================
# SETUP
# ============================================================

async def setup(bot: commands.Bot):

    await bot.add_cog(
        Moderation(bot)
    )
