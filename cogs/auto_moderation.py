import datetime
import re
import sqlite3
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands


class AutoModeration(commands.Cog):
    INVITE_REGEX = re.compile(
        r"(?:https?://)?(?:www\.)?"
        r"(?:discord\.gg|discord(?:app)?\.com/invite)"
        r"/([A-Za-z0-9-]+)",
        re.IGNORECASE
    )

    # Punishment escalation:
    # 1 = 5 minutes
    # 2 = 20 minutes
    # 3 = 30 minutes
    # 4 = permanent ban
    TIMEOUT_MINUTES = {
        1: 5,
        2: 20,
        3: 30,
    }

    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/bot.db"

        self.setup_database()

    # =========================================================
    # DATABASE
    # =========================================================

    def get_connection(self):
        conn = sqlite3.connect(
            self.db_path,
            timeout=10
        )

        conn.execute(
            "PRAGMA busy_timeout = 10000"
        )

        conn.execute(
            "PRAGMA journal_mode = WAL"
        )

        return conn

    def setup_database(self):
        conn = self.get_connection()

        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_moderation_settings (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_moderation_offenses (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    invite_offenses INTEGER NOT NULL DEFAULT 0,
                    spam_offenses INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

            conn.commit()

        finally:
            conn.close()

    def get_enabled(self, guild_id):
        conn = self.get_connection()

        try:
            row = conn.execute(
                """
                SELECT enabled
                FROM auto_moderation_settings
                WHERE guild_id = ?
                """,
                (guild_id,)
            ).fetchone()

            if not row:
                return False

            return bool(row[0])

        finally:
            conn.close()

    def set_enabled(self, guild_id, enabled):
        conn = self.get_connection()

        try:
            conn.execute(
                """
                INSERT INTO auto_moderation_settings
                (guild_id, enabled)
                VALUES (?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET enabled = excluded.enabled
                """,
                (
                    guild_id,
                    1 if enabled else 0
                )
            )

            conn.commit()

        finally:
            conn.close()

    def get_offenses(self, guild_id, user_id):
        conn = self.get_connection()

        try:
            row = conn.execute(
                """
                SELECT invite_offenses, spam_offenses
                FROM auto_moderation_offenses
                WHERE guild_id = ?
                AND user_id = ?
                """,
                (
                    guild_id,
                    user_id
                )
            ).fetchone()

            if not row:
                return {
                    "invite": 0,
                    "spam": 0
                }

            return {
                "invite": row[0],
                "spam": row[1]
            }

        finally:
            conn.close()

    def add_offense(
        self,
        guild_id,
        user_id,
        offense_type
    ):
        conn = self.get_connection()

        try:
            if offense_type == "invite":
                conn.execute(
                    """
                    INSERT INTO auto_moderation_offenses
                    (
                        guild_id,
                        user_id,
                        invite_offenses,
                        spam_offenses
                    )
                    VALUES (?, ?, 1, 0)
                    ON CONFLICT(guild_id, user_id)
                    DO UPDATE SET
                        invite_offenses =
                            invite_offenses + 1
                    """,
                    (
                        guild_id,
                        user_id
                    )
                )

            elif offense_type == "spam":
                conn.execute(
                    """
                    INSERT INTO auto_moderation_offenses
                    (
                        guild_id,
                        user_id,
                        invite_offenses,
                        spam_offenses
                    )
                    VALUES (?, ?, 0, 1)
                    ON CONFLICT(guild_id, user_id)
                    DO UPDATE SET
                        spam_offenses =
                            spam_offenses + 1
                    """,
                    (
                        guild_id,
                        user_id
                    )
                )

            conn.commit()

        finally:
            conn.close()

        return self.get_offenses(
            guild_id,
            user_id
        )

    # =========================================================
    # ROLE / PERMISSION CHECK
    # =========================================================

    def can_punish(
        self,
        guild,
        member
    ):
        if member.id == guild.owner_id:
            return False

        if member.guild_permissions.administrator:
            return False

        bot_member = guild.me

        if not bot_member:
            return False

        if member.id == bot_member.id:
            return False

        # Member must be below bot's highest role.
        if member.top_role >= bot_member.top_role:
            return False

        return True

    # =========================================================
    # INVITE DETECTION
    # =========================================================

    async def contains_external_invite(
        self,
        message
    ):
        matches = self.INVITE_REGEX.findall(
            message.content
        )

        if not matches:
            return False

        for invite_code in matches:
            try:
                invite = await self.bot.fetch_invite(
                    invite_code,
                    with_counts=False
                )

                # Allow invites belonging to this server.
                if (
                    invite.guild
                    and invite.guild.id == message.guild.id
                ):
                    continue

                # This is an invite to another server.
                return True

            except discord.NotFound:
                # Invalid/deleted invite.
                # It is not a valid external server invite.
                continue

            except (
                discord.HTTPException,
                discord.Forbidden
            ):
                # If Discord cannot resolve the invite,
                # don't punish solely because of that.
                continue

        return False

    # =========================================================
    # WORD SPAM DETECTION
    # =========================================================

    def contains_word_spam(self, content):
        # Remove punctuation and normalize case.
        words = re.findall(
            r"\b[\w'-]+\b",
            content.lower()
        )

        if len(words) < 7:
            return False

        counts = Counter(words)

        for word, count in counts.items():
            if count >= 7:
                # Ignore extremely short single-character
                # repetitions such as "a a a a a a a".
                if len(word) >= 2:
                    return True

        return False

    # =========================================================
    # PUNISHMENT
    # =========================================================

    async def punish(
        self,
        message,
        offense_number,
        offense_type
    ):
        guild = message.guild
        member = message.author

        if not self.can_punish(
            guild,
            member
        ):
            await self.send_log(
                guild,
                member,
                message.channel,
                offense_type,
                offense_number,
                "Could not punish - role hierarchy/admin protection"
            )
            return

        action = ""

        try:
            # -------------------------------------------------
            # 4TH OFFENSE = BAN
            # -------------------------------------------------

            if offense_number >= 4:
                await guild.ban(
                    member,
                    reason=(
                        "AutoMod: "
                        f"{offense_type} offense #{offense_number}"
                    )
                )

                action = "🔨 Permanent Ban"

            # -------------------------------------------------
            # 1ST-3RD = TIMEOUT
            # -------------------------------------------------

            else:
                minutes = self.TIMEOUT_MINUTES[
                    offense_number
                ]

                until = (
                    discord.utils.utcnow()
                    + datetime.timedelta(
                        minutes=minutes
                    )
                )

                await member.timeout(
                    until,
                    reason=(
                        "AutoMod: "
                        f"{offense_type} offense #{offense_number}"
                    )
                )

                action = (
                    f"🔇 {minutes}-minute timeout"
                )

        except discord.Forbidden:
            action = (
                "❌ Failed - Bot lacks permission "
                "or role is too low"
            )

        except discord.HTTPException:
            action = (
                "❌ Failed - Discord API error"
            )

        await self.send_log(
            guild,
            member,
            message.channel,
            offense_type,
            offense_number,
            action
        )

    # =========================================================
    # LOGGING
    # =========================================================

    async def send_log(
        self,
        guild,
        member,
        channel,
        offense_type,
        offense_number,
        action
    ):
        conn = self.get_connection()

        try:
            try:
                row = conn.execute(
                    """
                    SELECT log_channel_id
                    FROM guild_settings
                    WHERE guild_id = ?
                    """,
                    (guild.id,)
                ).fetchone()

            except sqlite3.Error:
                row = None

        finally:
            conn.close()

        if not row or not row[0]:
            return

        log_channel = guild.get_channel(
            row[0]
        )

        if not log_channel:
            return

        offense_names = {
            "invite": "Discord Server Invite",
            "spam": "Repeated Word Spam"
        }

        embed = discord.Embed(
            title="🛡️ AutoMod Action",
            description=(
                f"**Member:** {member.mention}\n"
                f"**User ID:** `{member.id}`\n"
                f"**Channel:** {channel.mention}\n"
                f"**Violation:** "
                f"{offense_names.get(offense_type, offense_type)}\n"
                f"**Offense:** `#{offense_number}`\n"
                f"**Action:** {action}"
            ),
            timestamp=discord.utils.utcnow()
        )

        embed.set_footer(
            text="Automatic Moderation"
        )

        try:
            await log_channel.send(
                embed=embed
            )

        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass

    # =========================================================
    # /MODERATION GROUP
    # =========================================================

    moderation = app_commands.Group(
        name="moderation",
        description="Configure automatic moderation."
    )

    # =========================================================
    # /MODERATION ENABLE
    # =========================================================

    @moderation.command(
        name="enable",
        description="Enable automatic moderation."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def moderation_enable(
        self,
        interaction: discord.Interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return

        self.set_enabled(
            interaction.guild.id,
            True
        )

        await interaction.response.send_message(
            "🟢 **Automatic Moderation Enabled**\n\n"
            "The following protections are now active:\n"
            "• 🚫 Other-server Discord invites\n"
            "• 🔁 Repeated single-word spam\n\n"
            "**Punishment escalation:**\n"
            "1️⃣ 5-minute timeout\n"
            "2️⃣ 20-minute timeout\n"
            "3️⃣ 30-minute timeout\n"
            "4️⃣ 🔨 Permanent ban",
            ephemeral=True
        )

    # =========================================================
    # /MODERATION DISABLE
    # =========================================================

    @moderation.command(
        name="disable",
        description="Disable automatic moderation."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def moderation_disable(
        self,
        interaction: discord.Interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return

        self.set_enabled(
            interaction.guild.id,
            False
        )

        await interaction.response.send_message(
            "🔴 **Automatic Moderation Disabled.**\n\n"
            "Existing offense counts have been preserved.",
            ephemeral=True
        )

    # =========================================================
    # COMMAND ERROR HANDLER
    # =========================================================

    @moderation_enable.error
    async def moderation_enable_error(
        self,
        interaction,
        error
    ):
        await self.handle_command_error(
            interaction,
            error
        )

    @moderation_disable.error
    async def moderation_disable_error(
        self,
        interaction,
        error
    ):
        await self.handle_command_error(
            interaction,
            error
        )

    async def handle_command_error(
        self,
        interaction,
        error
    ):
        if isinstance(
            error,
            app_commands.errors.MissingPermissions
        ):
            message = (
                "❌ You need **Administrator** permission "
                "to change AutoMod settings."
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True
                )

        else:
            raise error

    # =========================================================
    # MESSAGE LISTENER
    # =========================================================

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):
        # Ignore DMs.
        if message.guild is None:
            return

        # Ignore bots.
        if message.author.bot:
            return

        guild = message.guild
        member = message.author

        # AutoMod disabled.
        if not self.get_enabled(guild.id):
            return

        # Protect server owner and administrators.
        if (
            member.id == guild.owner_id
            or member.guild_permissions.administrator
        ):
            return

        # Protect members the bot cannot punish.
        if not self.can_punish(
            guild,
            member
        ):
            return

        violation = None

        # -----------------------------------------------------
        # CHECK OTHER SERVER INVITE
        # -----------------------------------------------------

        if await self.contains_external_invite(
            message
        ):
            violation = "invite"

        # -----------------------------------------------------
        # CHECK REPEATED WORD SPAM
        # -----------------------------------------------------

        elif self.contains_word_spam(
            message.content
        ):
            violation = "spam"

        if violation is None:
            return

        # -----------------------------------------------------
        # DELETE MESSAGE
        # -----------------------------------------------------

        try:
            await message.delete()

        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass

        # -----------------------------------------------------
        # ADD OFFENSE
        # -----------------------------------------------------

        offenses = self.add_offense(
            guild.id,
            member.id,
            violation
        )

        offense_number = offenses[
            violation
        ]

        # -----------------------------------------------------
        # PUNISH
        # -----------------------------------------------------

        await self.punish(
            message,
            offense_number,
            violation
        )


async def setup(bot):
    await bot.add_cog(
        AutoModeration(bot)
    )
