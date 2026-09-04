import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# LEVEL PROGRESSION
# ============================================================
#
# Messages required for each new level:
#
# Level 1  = 40 messages
# Level 2  = 50 messages
# Level 3  = 60 messages
# ...
# Level 25 = 280 messages
#
# Total to Level 25:
# 40 + 50 + 60 + ... + 280 = 4000
#
# Higher levels continue getting harder.
#
# Formula:
# messages_for_level = 30 + (level * 10)
#
# ============================================================

BASE_LEVEL_REQUIREMENT = 30
LEVEL_INCREMENT = 10

DEFAULT_XP_PER_MESSAGE = 10


def messages_required_for_level(level: int) -> int:
    """
    Message-equivalent requirement for the specified level.
    """

    if level <= 0:
        return 0

    return (
        BASE_LEVEL_REQUIREMENT
        + (level * LEVEL_INCREMENT)
    )


def total_messages_for_level(level: int) -> int:
    """
    Total message-equivalents needed to reach a level.
    """

    if level <= 0:
        return 0

    total = 0

    for current_level in range(
        1,
        level + 1,
    ):
        total += messages_required_for_level(
            current_level
        )

    return total


def calculate_level(
    total_xp: int,
    xp_per_message: int,
) -> int:
    """
    Convert total XP into a level.

    XP is converted into message-equivalents using
    the configured XP per message.

    Example with 10 XP/message:

    Level 1  = 400 XP
    Level 2  = 900 XP total
    Level 3  = 1500 XP total
    ...
    Level 25 = 40000 XP total
    """

    if xp_per_message <= 0:
        xp_per_message = DEFAULT_XP_PER_MESSAGE

    message_equivalent = (
        total_xp // xp_per_message
    )

    level = 0

    while True:

        next_level = level + 1

        required = total_messages_for_level(
            next_level
        )

        if message_equivalent < required:
            break

        level += 1

    return level


def get_level_progress(
    total_xp: int,
    xp_per_message: int,
):
    """
    Returns:

    level
    current XP within level
    XP needed for next level
    total XP needed for current level
    total XP needed for next level
    """

    if xp_per_message <= 0:
        xp_per_message = DEFAULT_XP_PER_MESSAGE

    level = calculate_level(
        total_xp,
        xp_per_message,
    )

    current_message_total = (
        total_messages_for_level(level)
    )

    next_message_total = (
        total_messages_for_level(level + 1)
    )

    current_level_xp = (
        current_message_total
        * xp_per_message
    )

    next_level_xp = (
        next_message_total
        * xp_per_message
    )

    current_xp = max(
        0,
        total_xp - current_level_xp,
    )

    needed_xp = max(
        xp_per_message,
        next_level_xp - current_level_xp,
    )

    return (
        level,
        current_xp,
        needed_xp,
        current_level_xp,
        next_level_xp,
    )


# ============================================================
# DATABASE
# ============================================================

LEVEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS level_settings (
    guild_id INTEGER PRIMARY KEY,

    enabled INTEGER NOT NULL DEFAULT 1,

    xp_per_message INTEGER NOT NULL DEFAULT 10,

    level_channel_id INTEGER,

    level_message TEXT NOT NULL DEFAULT
        '🎉 Congratulations {user}! You reached level {level}!',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    role_id INTEGER NOT NULL,

    PRIMARY KEY (
        guild_id,
        level
    )
);


CREATE TABLE IF NOT EXISTS user_xp (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,

    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (
        guild_id,
        user_id
    )
);


CREATE INDEX IF NOT EXISTS idx_level_rewards_guild
ON level_rewards(guild_id);


CREATE INDEX IF NOT EXISTS idx_user_xp_guild
ON user_xp(guild_id);
"""


async def db_execute(
    bot,
    query,
    params=(),
    commit=False,
):
    """
    Execute SQLite query with retry handling.
    """

    for attempt in range(8):

        try:

            cursor = await (
                bot.database.connection.execute(
                    query,
                    params,
                )
            )

            if commit:
                await bot.database.connection.commit()

            return cursor

        except Exception as error:

            if "database is locked" not in str(
                error
            ).lower():

                raise

            if attempt >= 7:
                raise

            await asyncio.sleep(
                0.25 * (attempt + 1)
            )

    raise RuntimeError(
        "Database operation failed."
    )


async def setup_level_database(
    bot,
):
    """
    Create level tables.
    """

    await bot.database.connection.execute(
        "PRAGMA busy_timeout = 10000"
    )

    try:

        await bot.database.connection.execute(
            "PRAGMA journal_mode = WAL"
        )

    except Exception:
        pass

    await bot.database.connection.executescript(
        LEVEL_SCHEMA
    )

    await bot.database.connection.commit()


# ============================================================
# SETTINGS
# ============================================================

async def get_settings(
    bot,
    guild_id,
):

    cursor = await db_execute(
        bot,
        """
        SELECT
            guild_id,
            enabled,
            xp_per_message,
            level_channel_id,
            level_message
        FROM level_settings
        WHERE guild_id = ?
        """,
        (guild_id,),
    )

    row = await cursor.fetchone()

    if row:
        return row

    await db_execute(
        bot,
        """
        INSERT OR IGNORE INTO level_settings (
            guild_id,
            enabled,
            xp_per_message
        )
        VALUES (?, 1, ?)
        """,
        (
            guild_id,
            DEFAULT_XP_PER_MESSAGE,
        ),
        commit=True,
    )

    cursor = await db_execute(
        bot,
        """
        SELECT
            guild_id,
            enabled,
            xp_per_message,
            level_channel_id,
            level_message
        FROM level_settings
        WHERE guild_id = ?
        """,
        (guild_id,),
    )

    return await cursor.fetchone()


# ============================================================
# USER XP
# ============================================================

async def get_user_xp(
    bot,
    guild_id,
    user_id,
):

    cursor = await db_execute(
        bot,
        """
        SELECT
            guild_id,
            user_id,
            xp,
            level
        FROM user_xp
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
        return row

    await db_execute(
        bot,
        """
        INSERT OR IGNORE INTO user_xp (
            guild_id,
            user_id,
            xp,
            level
        )
        VALUES (?, ?, 0, 0)
        """,
        (
            guild_id,
            user_id,
        ),
        commit=True,
    )

    return (
        guild_id,
        user_id,
        0,
        0,
    )


# ============================================================
# REWARDS
# ============================================================

async def get_reward(
    bot,
    guild_id,
    level,
):

    cursor = await db_execute(
        bot,
        """
        SELECT role_id
        FROM level_rewards
        WHERE guild_id = ?
          AND level = ?
        """,
        (
            guild_id,
            level,
        ),
    )

    row = await cursor.fetchone()

    if row:
        return row[0]

    return None


async def get_rewards(
    bot,
    guild_id,
):

    cursor = await db_execute(
        bot,
        """
        SELECT
            level,
            role_id
        FROM level_rewards
        WHERE guild_id = ?
        ORDER BY level ASC
        """,
        (guild_id,),
    )

    return await cursor.fetchall()


# ============================================================
# LEVEL-UP MESSAGE VARIABLES
# ============================================================

def format_level_message(
    message,
    member,
    level,
):

    replacements = {
        "{user}": member.mention,
        "{username}": member.display_name,
        "{server}": member.guild.name,
        "{level}": str(level),
        "{member_count}": str(
            member.guild.member_count
        ),
    }

    for key, value in replacements.items():

        message = message.replace(
            key,
            value,
        )

    return message


# ============================================================
# CONFIG EMBED
# ============================================================

async def build_config_embed(
    bot,
    guild,
):

    settings = await get_settings(
        bot,
        guild.id,
    )

    rewards = await get_rewards(
        bot,
        guild.id,
    )

    status = (
        "🟢 Enabled"
        if settings[1]
        else "🔴 Disabled"
    )

    if settings[3]:

        channel = guild.get_channel(
            settings[3]
        )

        if channel:

            channel_text = channel.mention

        else:

            channel_text = "Channel not found"

    else:

        channel_text = "Not configured"

    reward_lines = []

    for level, role_id in rewards:

        role = guild.get_role(
            role_id
        )

        if role:

            reward_lines.append(
                f"**Level {level}** → "
                f"{role.mention}"
            )

        else:

            reward_lines.append(
                f"**Level {level}** → "
                f"Deleted role (`{role_id}`)"
            )

    if reward_lines:

        reward_text = "\n".join(
            reward_lines
        )

    else:

        reward_text = (
            "No level rewards configured."
        )

    embed = discord.Embed(
        title="⭐ Levels Configuration",
        description=(
            "Configure the server's XP and "
            "level system below.\n\n"

            "**📈 Progression**\n"
            "Level 1 → 40 messages\n"
            "Level 2 → 90 total messages\n"
            "Level 5 → 300 total messages\n"
            "Level 10 → 850 total messages\n"
            "Level 15 → 1,500 total messages\n"
            "Level 20 → 2,650 total messages\n"
            "**Level 25 → 4,000 total messages**\n\n"

            "Every new level requires more "
            "messages than the previous one."
        ),
    )

    embed.add_field(
        name="📊 Status",
        value=status,
        inline=True,
    )

    embed.add_field(
        name="⚡ XP Per Message",
        value=f"{settings[2]} XP",
        inline=True,
    )

    embed.add_field(
        name="📢 Level-Up Channel",
        value=channel_text,
        inline=True,
    )

    embed.add_field(
        name="💬 Level-Up Message",
        value=settings[4][:1024],
        inline=False,
    )

    embed.add_field(
        name="🎭 Level Rewards",
        value=reward_text[:1024],
        inline=False,
    )

    embed.add_field(
        name="🔤 Message Variables",
        value=(
            "`{user}` — mentions the user\n"
            "`{username}` — user's name\n"
            "`{server}` — server name\n"
            "`{level}` — new level\n"
            "`{member_count}` — member count"
        ),
        inline=False,
    )

    return embed


# ============================================================
# XP MODAL
# ============================================================

class XPModal(
    discord.ui.Modal,
    title="Set XP Per Message",
):

    xp = discord.ui.TextInput(
        label="XP Per Message",
        placeholder="Example: 10",
        max_length=6,
        required=True,
    )

    def __init__(
        self,
        bot,
        guild_id,
    ):

        super().__init__()

        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(
        self,
        interaction,
    ):

        try:

            amount = int(
                self.xp.value.strip()
            )

        except ValueError:

            amount = 0

        if not 1 <= amount <= 1000:

            await interaction.response.send_message(
                "❌ XP per message must be "
                "between **1 and 1000**.",
                ephemeral=True,
            )

            return

        await db_execute(
            self.bot,
            """
            UPDATE level_settings
            SET xp_per_message = ?
            WHERE guild_id = ?
            """,
            (
                amount,
                self.guild_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"⚡ XP per message is now "
            f"**{amount} XP**.",
            ephemeral=True,
        )


# ============================================================
# LEVEL MESSAGE MODAL
# ============================================================

class LevelMessageModal(
    discord.ui.Modal,
    title="Level-Up Message",
):

    message = discord.ui.TextInput(
        label="Level-Up Message",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "🎉 Congratulations {user}! "
            "You reached level {level}!"
        ),
        max_length=2000,
        required=True,
    )

    def __init__(
        self,
        bot,
        guild_id,
    ):

        super().__init__()

        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(
        self,
        interaction,
    ):

        await db_execute(
            self.bot,
            """
            UPDATE level_settings
            SET level_message = ?
            WHERE guild_id = ?
            """,
            (
                self.message.value,
                self.guild_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            "💬 Level-up message updated.",
            ephemeral=True,
        )


# ============================================================
# LEVEL ROLE SELECT
# ============================================================

class LevelRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        bot,
        guild_id,
    ):

        super().__init__(
            placeholder="Select a server role...",
            min_values=1,
            max_values=1,
        )

        self.bot = bot
        self.guild_id = guild_id

    async def callback(
        self,
        interaction,
    ):

        role = self.values[0]

        await interaction.response.send_message(
            "🎭 You selected "
            f"**{role.name}**.\n\n"
            "Now type the **level number** "
            "where this role should be awarded.",
            ephemeral=True,
        )

        def check(message):

            return (
                message.author.id
                == interaction.user.id
                and message.guild
                and message.guild.id
                == self.guild_id
                and message.channel.id
                == interaction.channel.id
            )

        try:

            message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=check,
            )

        except asyncio.TimeoutError:

            await interaction.followup.send(
                "⌛ Level input timed out.",
                ephemeral=True,
            )

            return

        try:

            level = int(
                message.content.strip()
            )

        except ValueError:

            await interaction.followup.send(
                "❌ Please enter a valid level number.",
                ephemeral=True,
            )

            return

        if not 1 <= level <= 10000:

            await interaction.followup.send(
                "❌ Level must be between "
                "**1 and 10,000**.",
                ephemeral=True,
            )

            return

        # Check bot can give this role.
        bot_member = interaction.guild.me

        if bot_member:

            if role >= bot_member.top_role:

                await interaction.followup.send(
                    "❌ I cannot give this role because "
                    "it is above or equal to my highest role.",
                    ephemeral=True,
                )

                return

        await db_execute(
            self.bot,
            """
            INSERT INTO level_rewards (
                guild_id,
                level,
                role_id
            )
            VALUES (?, ?, ?)

            ON CONFLICT(
                guild_id,
                level
            )
            DO UPDATE SET
                role_id = excluded.role_id
            """,
            (
                self.guild_id,
                level,
                role.id,
            ),
            commit=True,
        )

        try:

            await message.delete()

        except Exception:
            pass

        await interaction.followup.send(
            f"🎭 **{role.name}** will now be "
            f"awarded at **Level {level}**.",
            ephemeral=True,
        )


class LevelRoleView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        guild_id,
    ):

        super().__init__(
            timeout=120
        )

        self.add_item(
            LevelRoleSelect(
                bot,
                guild_id,
            )
        )


# ============================================================
# REMOVE ROLE SELECT
# ============================================================

class RemoveLevelRoleSelect(
    discord.ui.Select
):

    def __init__(
        self,
        bot,
        guild_id,
        rewards,
    ):

        self.bot = bot
        self.guild_id = guild_id

        guild = bot.get_guild(
            guild_id
        )

        options = []

        for level, role_id in rewards:

            role = (
                guild.get_role(role_id)
                if guild
                else None
            )

            role_name = (
                role.name
                if role
                else "Deleted role"
            )

            options.append(
                discord.SelectOption(
                    label=f"Level {level}",
                    description=role_name[:100],
                    value=str(level),
                )
            )

        super().__init__(
            placeholder="Select reward to remove...",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(
        self,
        interaction,
    ):

        level = int(
            self.values[0]
        )

        await db_execute(
            self.bot,
            """
            DELETE FROM level_rewards
            WHERE guild_id = ?
              AND level = ?
            """,
            (
                self.guild_id,
                level,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"🗑️ Removed the reward for "
            f"**Level {level}**.",
            ephemeral=True,
        )


class RemoveLevelRoleView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        guild_id,
        rewards,
    ):

        super().__init__(
            timeout=120
        )

        self.add_item(
            RemoveLevelRoleSelect(
                bot,
                guild_id,
                rewards,
            )
        )


# ============================================================
# LEVEL CHANNEL SELECT
# ============================================================

class LevelChannelSelect(
    discord.ui.ChannelSelect
):

    def __init__(
        self,
        bot,
        guild_id,
    ):

        super().__init__(
            placeholder="Select level-up channel...",
            channel_types=[
                discord.ChannelType.text
            ],
            min_values=1,
            max_values=1,
        )

        self.bot = bot
        self.guild_id = guild_id

    async def callback(
        self,
        interaction,
    ):

        channel = self.values[0]

        await db_execute(
            self.bot,
            """
            UPDATE level_settings
            SET level_channel_id = ?
            WHERE guild_id = ?
            """,
            (
                channel.id,
                self.guild_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            f"📢 Level-up messages will be "
            f"sent to {channel.mention}.",
            ephemeral=True,
        )


class LevelChannelView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        guild_id,
    ):

        super().__init__(
            timeout=120
        )

        self.add_item(
            LevelChannelSelect(
                bot,
                guild_id,
            )
        )


# ============================================================
# CONFIG VIEW
# ============================================================

class LevelsConfigView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        guild_id,
        creator_id,
    ):

        super().__init__(
            timeout=1800
        )

        self.bot = bot
        self.guild_id = guild_id
        self.creator_id = creator_id

    async def interaction_check(
        self,
        interaction,
    ):

        if interaction.user.id != self.creator_id:

            await interaction.response.send_message(
                "🔒 This configuration panel belongs "
                "to another administrator. Only the "
                "administrator who created this panel "
                "can edit it.",
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

    # ========================================================
    # XP
    # ========================================================

    @discord.ui.button(
        label="XP Per Message",
        emoji="⚡",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def xp_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_modal(
            XPModal(
                self.bot,
                self.guild_id,
            )
        )

    # ========================================================
    # CHANNEL
    # ========================================================

    @discord.ui.button(
        label="Level Channel",
        emoji="📢",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def channel_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "📢 Select the channel where "
            "level-ups should be announced.",
            view=LevelChannelView(
                self.bot,
                self.guild_id,
            ),
            ephemeral=True,
        )

    # ========================================================
    # MESSAGE
    # ========================================================

    @discord.ui.button(
        label="Level Message",
        emoji="💬",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def message_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_modal(
            LevelMessageModal(
                self.bot,
                self.guild_id,
            )
        )

    # ========================================================
    # ADD ROLE
    # ========================================================

    @discord.ui.button(
        label="Add Level Role",
        emoji="🎭",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def add_role_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "🎭 Select a server role.",
            view=LevelRoleView(
                self.bot,
                self.guild_id,
            ),
            ephemeral=True,
        )

    # ========================================================
    # REMOVE ROLE
    # ========================================================

    @discord.ui.button(
        label="Remove Role",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def remove_role_button(
        self,
        interaction,
        button,
    ):

        rewards = await get_rewards(
            self.bot,
            self.guild_id,
        )

        if not rewards:

            await interaction.response.send_message(
                "❌ No level rewards configured.",
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            "🗑️ Select the level reward "
            "you want to remove.",
            view=RemoveLevelRoleView(
                self.bot,
                self.guild_id,
                rewards,
            ),
            ephemeral=True,
        )

    # ========================================================
    # ENABLE
    # ========================================================

    @discord.ui.button(
        label="Enable",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def enable_button(
        self,
        interaction,
        button,
    ):

        await db_execute(
            self.bot,
            """
            UPDATE level_settings
            SET enabled = 1
            WHERE guild_id = ?
            """,
            (
                self.guild_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            "🟢 Level system enabled.",
            ephemeral=True,
        )

    # ========================================================
    # DISABLE
    # ========================================================

    @discord.ui.button(
        label="Disable",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        row=2,
    )
    async def disable_button(
        self,
        interaction,
        button,
    ):

        await db_execute(
            self.bot,
            """
            UPDATE level_settings
            SET enabled = 0
            WHERE guild_id = ?
            """,
            (
                self.guild_id,
            ),
            commit=True,
        )

        await interaction.response.send_message(
            "🔴 Level system disabled.",
            ephemeral=True,
        )

    # ========================================================
    # TEST
    # ========================================================

    @discord.ui.button(
        label="Test",
        emoji="🧪",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def test_button(
        self,
        interaction,
        button,
    ):

        settings = await get_settings(
            self.bot,
            self.guild_id,
        )

        preview = format_level_message(
            settings[4],
            interaction.user,
            25,
        )

        await interaction.response.send_message(
            f"🧪 **Level-up preview:**\n\n"
            f"{preview}",
            ephemeral=True,
        )

    # ========================================================
    # REFRESH
    # ========================================================

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

        embed = await build_config_embed(
            self.bot,
            interaction.guild,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )


# ============================================================
# LEVEL COG
# ============================================================

class Levels(
    commands.Cog
):

    levels_group = app_commands.Group(
        name="levels",
        description="Manage the server level system.",
    )

    def __init__(
        self,
        bot,
    ):

        self.bot = bot

        # XP cooldown.
        #
        # A user can receive XP once every
        # 10 seconds to prevent message spam.
        self.message_cooldowns = {}

    # ========================================================
    # /levels config
    # ========================================================

    @levels_group.command(
        name="config",
        description="Configure the server level system.",
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def config(
        self,
        interaction,
    ):

        embed = await build_config_embed(
            self.bot,
            interaction.guild,
        )

        await interaction.response.send_message(
            embed=embed,
            view=LevelsConfigView(
                self.bot,
                interaction.guild.id,
                interaction.user.id,
            ),
        )

    # ========================================================
    # /levels user
    # ========================================================

    @levels_group.command(
        name="user",
        description="View a user's level and XP.",
    )
    @app_commands.describe(
        user="The user whose level you want to view."
    )
    async def user(
        self,
        interaction,
        user: Optional[discord.Member] = None,
    ):

        member = (
            user
            if user
            else interaction.user
        )

        settings = await get_settings(
            self.bot,
            interaction.guild.id,
        )

        xp_per_message = settings[2]

        data = await get_user_xp(
            self.bot,
            interaction.guild.id,
            member.id,
        )

        total_xp = data[2]

        (
            level,
            current_xp,
            needed_xp,
            current_level_xp,
            next_level_xp,
        ) = get_level_progress(
            total_xp,
            xp_per_message,
        )

        # ====================================================
        # SERVER RANK
        # ====================================================

        cursor = await db_execute(
            self.bot,
            """
            SELECT COUNT(*)
            FROM user_xp
            WHERE guild_id = ?
              AND xp > ?
            """,
            (
                interaction.guild.id,
                total_xp,
            ),
        )

        row = await cursor.fetchone()

        rank = (
            (row[0] if row else 0)
            + 1
        )

        # ====================================================
        # PROGRESS BAR
        # ====================================================

        progress_length = 15

        if needed_xp > 0:

            progress = int(
                (
                    current_xp
                    / needed_xp
                )
                * progress_length
            )

            progress = max(
                0,
                min(
                    progress_length,
                    progress,
                ),
            )

        else:

            progress = progress_length

        progress_bar = (
            "🟩" * progress
            + "⬜" * (
                progress_length - progress
            )
        )

        # ====================================================
        # MESSAGE-EQUIVALENT PROGRESS
        # ====================================================

        current_messages = (
            current_level_xp
            // xp_per_message
        )

        next_messages = (
            next_level_xp
            // xp_per_message
        )

        total_message_equivalent = (
            total_xp
            // xp_per_message
        )

        # ====================================================
        # EMBED
        # ====================================================

        embed = discord.Embed(
            title=(
                f"⭐ {member.display_name}'s "
                f"Level"
            ),
            description=(
                f"**Level {level}**\n"
                f"Server Rank: **#{rank}**"
            ),
        )

        embed.set_thumbnail(
            url=member.display_avatar.url
        )

        embed.add_field(
            name="🏆 Level",
            value=f"**{level}**",
            inline=True,
        )

        embed.add_field(
            name="⚡ Total XP",
            value=f"**{total_xp:,} XP**",
            inline=True,
        )

        embed.add_field(
            name="💬 Message Equivalent",
            value=(
                f"**{total_message_equivalent:,}**"
            ),
            inline=True,
        )

        embed.add_field(
            name=(
                f"📈 Progress to Level "
                f"{level + 1}"
            ),
            value=(
                f"{progress_bar}\n"
                f"**{current_xp:,} / "
                f"{needed_xp:,} XP**"
            ),
            inline=False,
        )

        embed.add_field(
            name="📊 Level Curve",
            value=(
                f"Current level starts at "
                f"**{current_messages:,}** "
                f"messages\n"
                f"Next level starts at "
                f"**{next_messages:,}** "
                f"messages"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚡ XP Per Message",
            value=f"**{xp_per_message} XP**",
            inline=True,
        )

        reward_role_id = await get_reward(
            self.bot,
            interaction.guild.id,
            level,
        )

        if reward_role_id:

            role = interaction.guild.get_role(
                reward_role_id
            )

            if role:

                embed.add_field(
                    name="🎭 Level Reward",
                    value=role.mention,
                    inline=True,
                )

        embed.add_field(
            name="👤 User",
            value=member.mention,
            inline=True,
        )

        await interaction.response.send_message(
            embed=embed
        )

    # ========================================================
    # MESSAGE XP
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

            settings = await get_settings(
                self.bot,
                message.guild.id,
            )

            if not settings[1]:
                return

            # =================================================
            # XP COOLDOWN
            # =================================================

            key = (
                message.guild.id,
                message.author.id,
            )

            now = (
                asyncio.get_running_loop()
                .time()
            )

            last = self.message_cooldowns.get(
                key,
                0,
            )

            # One XP reward every 10 seconds.
            if now - last < 10:

                return

            self.message_cooldowns[key] = now

            # =================================================
            # CURRENT DATA
            # =================================================

            old_data = await get_user_xp(
                self.bot,
                message.guild.id,
                message.author.id,
            )

            old_xp = old_data[2]
            old_level = old_data[3]

            # =================================================
            # ADD XP
            # =================================================

            gained_xp = settings[2]

            new_xp = (
                old_xp
                + gained_xp
            )

            new_level = calculate_level(
                new_xp,
                settings[2],
            )

            # =================================================
            # SAVE
            # =================================================

            await db_execute(
                self.bot,
                """
                INSERT INTO user_xp (
                    guild_id,
                    user_id,
                    xp,
                    level
                )
                VALUES (?, ?, ?, ?)

                ON CONFLICT(
                    guild_id,
                    user_id
                )
                DO UPDATE SET
                    xp = excluded.xp,
                    level = excluded.level
                """,
                (
                    message.guild.id,
                    message.author.id,
                    new_xp,
                    new_level,
                ),
                commit=True,
            )

            # =================================================
            # NO LEVEL UP
            # =================================================

            if new_level <= old_level:

                return

            # =================================================
            # ROLE REWARDS
            # =================================================

            for reached_level in range(
                old_level + 1,
                new_level + 1,
            ):

                role_id = await get_reward(
                    self.bot,
                    message.guild.id,
                    reached_level,
                )

                if not role_id:
                    continue

                role = message.guild.get_role(
                    role_id
                )

                if not role:
                    continue

                bot_member = message.guild.me

                if bot_member:

                    if role >= bot_member.top_role:

                        print(
                            "Levels: Cannot give role "
                            f"{role.name} because it is "
                            "above the bot's highest role."
                        )

                        continue

                if role in message.author.roles:
                    continue

                try:

                    await message.author.add_roles(
                        role,
                        reason=(
                            f"Reached Level "
                            f"{reached_level}"
                        ),
                    )

                except discord.Forbidden:

                    print(
                        "Levels: Missing permission "
                        f"to give role {role.name}."
                    )

                except Exception as error:

                    print(
                        f"Levels role error: {error}"
                    )

            # =================================================
            # LEVEL-UP CHANNEL
            # =================================================

            settings = await get_settings(
                self.bot,
                message.guild.id,
            )

            channel_id = settings[3]

            if not channel_id:

                return

            channel = message.guild.get_channel(
                channel_id
            )

            if not channel:

                return

            announcement = format_level_message(
                settings[4],
                message.author,
                new_level,
            )

            try:

                await channel.send(
                    announcement
                )

            except discord.Forbidden:

                print(
                    "Levels: Cannot send level-up "
                    f"message in #{channel.name}."
                )

        except Exception as error:

            print(
                f"Levels XP error: {error}"
            )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    @config.error
    async def config_error(
        self,
        interaction,
        error,
    ):

        if isinstance(
            error,
            app_commands.errors.MissingPermissions,
        ):

            response = (
                "🔒 You need **Administrator** "
                "permission to configure levels."
            )

        else:

            print(
                f"Levels config error: {error}"
            )

            response = (
                "❌ An error occurred while "
                "opening the level configuration."
            )

        if interaction.response.is_done():

            await interaction.followup.send(
                response,
                ephemeral=True,
            )

        else:

            await interaction.response.send_message(
                response,
                ephemeral=True,
            )


# ============================================================
# SETUP
# ============================================================

async def setup(
    bot,
):

    await setup_level_database(
        bot
    )

    await bot.add_cog(
        Levels(bot)
    )

    print(
        "Levels system loaded."
    )
