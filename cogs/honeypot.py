import datetime
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands


class Honeypot(commands.Cog):
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

        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")

        return conn

    def setup_database(self):
        conn = self.get_connection()

        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS honeypot_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    punishment TEXT NOT NULL DEFAULT 'ban',
                    enabled INTEGER NOT NULL DEFAULT 0
                )
            """)

            conn.commit()

        finally:
            conn.close()

    def get_settings(self, guild_id):
        conn = self.get_connection()

        try:
            return conn.execute(
                """
                SELECT channel_id, punishment, enabled
                FROM honeypot_settings
                WHERE guild_id = ?
                """,
                (guild_id,)
            ).fetchone()

        finally:
            conn.close()

    def save_settings(
        self,
        guild_id,
        channel_id=None,
        punishment=None,
        enabled=None
    ):
        current = self.get_settings(guild_id)

        if current:
            old_channel, old_punishment, old_enabled = current

            if channel_id is None:
                channel_id = old_channel

            if punishment is None:
                punishment = old_punishment

            if enabled is None:
                enabled = old_enabled

            conn = self.get_connection()

            try:
                conn.execute(
                    """
                    UPDATE honeypot_settings
                    SET channel_id = ?,
                        punishment = ?,
                        enabled = ?
                    WHERE guild_id = ?
                    """,
                    (
                        channel_id,
                        punishment,
                        enabled,
                        guild_id
                    )
                )

                conn.commit()

            finally:
                conn.close()

        else:
            if punishment is None:
                punishment = "ban"

            if enabled is None:
                enabled = 0

            conn = self.get_connection()

            try:
                conn.execute(
                    """
                    INSERT INTO honeypot_settings
                    (
                        guild_id,
                        channel_id,
                        punishment,
                        enabled
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        channel_id,
                        punishment,
                        enabled
                    )
                )

                conn.commit()

            finally:
                conn.close()

    # =========================================================
    # NAMES
    # =========================================================

    @staticmethod
    def punishment_name(punishment):
        names = {
            "ban": "🔨 Ban",
            "mute": "🔇 Mute",
            "kick": "👢 Kick"
        }

        return names.get(
            punishment,
            "🔨 Ban"
        )

    # =========================================================
    # LOGGING
    # =========================================================

    async def send_log(
        self,
        guild,
        member,
        channel,
        punishment
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

        if not row:
            return

        log_channel_id = row[0]

        if not log_channel_id:
            return

        log_channel = guild.get_channel(
            log_channel_id
        )

        if not log_channel:
            return

        embed = discord.Embed(
            title="🍯 Honeypot Triggered",
            description=(
                f"**Member:** {member.mention}\n"
                f"**User ID:** `{member.id}`\n"
                f"**Channel:** {channel.mention}\n"
                f"**Action:** "
                f"{self.punishment_name(punishment)}"
            ),
            timestamp=discord.utils.utcnow()
        )

        embed.set_footer(
            text="Honeypot Security"
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
    # CHANNEL SELECT
    # =========================================================

    class HoneypotChannelSelect(discord.ui.ChannelSelect):
        def __init__(self, cog):
            self.cog = cog

            super().__init__(
                placeholder="🍯 Choose honeypot channel...",
                channel_types=[
                    discord.ChannelType.text
                ],
                min_values=1,
                max_values=1
            )

        async def callback(
            self,
            interaction: discord.Interaction
        ):
            if not interaction.guild:
                await interaction.response.send_message(
                    "❌ This can only be used in a server.",
                    ephemeral=True
                )
                return

            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            channel = self.values[0]

            self.cog.save_settings(
                interaction.guild.id,
                channel_id=channel.id
            )

            await interaction.response.send_message(
                f"✅ Honeypot channel set to "
                f"{channel.mention}.",
                ephemeral=True
            )

            # Refresh the configuration panel.
            try:
                await interaction.message.edit(
                    embed=self.cog.make_embed(
                        interaction.guild
                    ),
                    view=self.view
                )

            except (
                discord.Forbidden,
                discord.HTTPException
            ):
                pass

    # =========================================================
    # PUNISHMENT SELECT
    # =========================================================

    class PunishmentSelect(discord.ui.Select):
        def __init__(self, cog):
            self.cog = cog

            options = [
                discord.SelectOption(
                    label="Ban",
                    value="ban",
                    emoji="🔨",
                    description=(
                        "Ban anyone who triggers "
                        "the honeypot."
                    )
                ),
                discord.SelectOption(
                    label="Mute",
                    value="mute",
                    emoji="🔇",
                    description=(
                        "Timeout anyone who triggers "
                        "the honeypot."
                    )
                ),
                discord.SelectOption(
                    label="Kick",
                    value="kick",
                    emoji="👢",
                    description=(
                        "Kick anyone who triggers "
                        "the honeypot."
                    )
                )
            ]

            super().__init__(
                placeholder="⚔️ Choose punishment...",
                options=options,
                min_values=1,
                max_values=1
            )

        async def callback(
            self,
            interaction: discord.Interaction
        ):
            if not interaction.guild:
                await interaction.response.send_message(
                    "❌ This can only be used in a server.",
                    ephemeral=True
                )
                return

            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            punishment = self.values[0]

            self.cog.save_settings(
                interaction.guild.id,
                punishment=punishment
            )

            await interaction.response.send_message(
                "✅ Honeypot punishment set to "
                f"**{self.cog.punishment_name(punishment)}**.",
                ephemeral=True
            )

            try:
                await interaction.message.edit(
                    embed=self.cog.make_embed(
                        interaction.guild
                    ),
                    view=self.view
                )

            except (
                discord.Forbidden,
                discord.HTTPException
            ):
                pass

    # =========================================================
    # CONFIG VIEW
    # =========================================================

    class HoneypotView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(
                timeout=None
            )

            self.cog = cog

            # IMPORTANT:
            # ChannelSelect is added as an item.
            # It is NOT used as a decorator.
            self.add_item(
                cog.HoneypotChannelSelect(cog)
            )

            self.add_item(
                cog.PunishmentSelect(cog)
            )

        # -----------------------------------------------------
        # ENABLE
        # -----------------------------------------------------

        @discord.ui.button(
            label="Enable",
            emoji="🟢",
            style=discord.ButtonStyle.success,
            custom_id="honeypot_enable"
        )
        async def enable(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if not interaction.guild:
                await interaction.response.send_message(
                    "❌ This can only be used in a server.",
                    ephemeral=True
                )
                return

            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            settings = self.cog.get_settings(
                interaction.guild.id
            )

            if not settings:
                await interaction.response.send_message(
                    "❌ Please configure the honeypot first.",
                    ephemeral=True
                )
                return

            channel_id = settings[0]

            if not channel_id:
                await interaction.response.send_message(
                    "❌ Please select a honeypot channel first.",
                    ephemeral=True
                )
                return

            channel = interaction.guild.get_channel(
                channel_id
            )

            if not channel:
                await interaction.response.send_message(
                    "❌ The configured honeypot channel "
                    "no longer exists. Please select "
                    "another channel.",
                    ephemeral=True
                )
                return

            self.cog.save_settings(
                interaction.guild.id,
                enabled=1
            )

            await interaction.response.send_message(
                "🟢 **Honeypot enabled!**\n\n"
                f"🍯 Channel: {channel.mention}\n"
                f"⚔️ Punishment: "
                f"**{self.cog.punishment_name(settings[1])}**\n\n"
                "Anyone who sends a message in this "
                "channel will receive the selected punishment.",
                ephemeral=True
            )

            try:
                await interaction.message.edit(
                    embed=self.cog.make_embed(
                        interaction.guild
                    ),
                    view=self
                )

            except (
                discord.Forbidden,
                discord.HTTPException
            ):
                pass

        # -----------------------------------------------------
        # DISABLE
        # -----------------------------------------------------

        @discord.ui.button(
            label="Disable",
            emoji="🔴",
            style=discord.ButtonStyle.danger,
            custom_id="honeypot_disable"
        )
        async def disable(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if not interaction.guild:
                await interaction.response.send_message(
                    "❌ This can only be used in a server.",
                    ephemeral=True
                )
                return

            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            self.cog.save_settings(
                interaction.guild.id,
                enabled=0
            )

            await interaction.response.send_message(
                "🔴 **Honeypot disabled.**",
                ephemeral=True
            )

            try:
                await interaction.message.edit(
                    embed=self.cog.make_embed(
                        interaction.guild
                    ),
                    view=self
                )

            except (
                discord.Forbidden,
                discord.HTTPException
            ):
                pass

        # -----------------------------------------------------
        # TEST
        # -----------------------------------------------------

        @discord.ui.button(
            label="Test",
            emoji="🧪",
            style=discord.ButtonStyle.primary,
            custom_id="honeypot_test"
        )
        async def test(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if not interaction.guild:
                await interaction.response.send_message(
                    "❌ This can only be used in a server.",
                    ephemeral=True
                )
                return

            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            settings = self.cog.get_settings(
                interaction.guild.id
            )

            if not settings:
                await interaction.response.send_message(
                    "❌ Configure the honeypot first.",
                    ephemeral=True
                )
                return

            channel_id, punishment, enabled = settings

            if not channel_id:
                await interaction.response.send_message(
                    "❌ Select a honeypot channel first.",
                    ephemeral=True
                )
                return

            channel = interaction.guild.get_channel(
                channel_id
            )

            status = (
                "🟢 Enabled"
                if enabled
                else "🔴 Disabled"
            )

            await interaction.response.send_message(
                "🧪 **Honeypot Test**\n\n"
                f"🍯 Channel: "
                f"{channel.mention if channel else 'Unknown'}\n"
                f"⚔️ Punishment: "
                f"**{self.cog.punishment_name(punishment)}**\n"
                f"📡 Status: **{status}**\n\n"
                "✅ Configuration is working.\n"
                "No punishment was applied to you.",
                ephemeral=True
            )

        # -----------------------------------------------------
        # REFRESH
        # -----------------------------------------------------

        @discord.ui.button(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="honeypot_refresh"
        )
        async def refresh(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if not interaction.guild:
                await interaction.response.send_message(
                    "❌ This can only be used in a server.",
                    ephemeral=True
                )
                return

            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            await interaction.response.edit_message(
                embed=self.cog.make_embed(
                    interaction.guild
                ),
                view=self
            )

    # =========================================================
    # CONFIG EMBED
    # =========================================================

    def make_embed(self, guild):
        settings = self.get_settings(
            guild.id
        )

        if settings:
            channel_id, punishment, enabled = settings

        else:
            channel_id = None
            punishment = "ban"
            enabled = 0

        channel = None

        if channel_id:
            channel = guild.get_channel(
                channel_id
            )

        embed = discord.Embed(
            title="🍯 Honeypot Configuration",
            description=(
                "Configure your server's Honeypot "
                "security system.\n\n"
                "⚠️ **Warning:** Anyone who sends a "
                "message in the configured Honeypot "
                "channel will automatically receive "
                "the selected punishment."
            )
        )

        embed.add_field(
            name="🍯 Honeypot Channel",
            value=(
                channel.mention
                if channel
                else "❌ Not configured"
            ),
            inline=False
        )

        embed.add_field(
            name="⚔️ Punishment",
            value=self.punishment_name(
                punishment
            ),
            inline=True
        )

        embed.add_field(
            name="📡 Status",
            value=(
                "🟢 Enabled"
                if enabled
                else "🔴 Disabled"
            ),
            inline=True
        )

        embed.add_field(
            name="⚡ How It Works",
            value=(
                "A member sends a message in the "
                "Honeypot channel → the message is "
                "deleted → the selected punishment "
                "is applied automatically."
            ),
            inline=False
        )

        embed.set_footer(
            text=f"Honeypot • {guild.name}"
        )

        return embed

    # =========================================================
    # SLASH COMMAND GROUP
    # =========================================================

    honeypot = app_commands.Group(
        name="honeypot",
        description="Configure the server Honeypot system."
    )

    # =========================================================
    # /HONEYPOT SETUP
    # =========================================================

    @honeypot.command(
        name="setup",
        description="Configure the Honeypot system."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def setup(
        self,
        interaction: discord.Interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return

        if not self.get_settings(
            interaction.guild.id
        ):
            self.save_settings(
                interaction.guild.id
            )

        embed = self.make_embed(
            interaction.guild
        )

        view = self.HoneypotView(
            self
        )

        await interaction.response.send_message(
            embed=embed,
            view=view
        )

    # =========================================================
    # COMMAND ERROR
    # =========================================================

    @setup.error
    async def setup_error(
        self,
        interaction: discord.Interaction,
        error
    ):
        if isinstance(
            error,
            app_commands.errors.MissingPermissions
        ):
            message = (
                "❌ You need **Administrator** permission "
                "to configure the Honeypot."
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
    # HONEYPOT MESSAGE DETECTION
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

        settings = self.get_settings(
            message.guild.id
        )

        if not settings:
            return

        channel_id, punishment, enabled = settings

        # Disabled.
        if not enabled:
            return

        # No channel.
        if not channel_id:
            return

        # Wrong channel.
        if message.channel.id != channel_id:
            return

        member = message.author

        # =====================================================
        # DELETE MESSAGE
        # =====================================================

        try:
            await message.delete()

        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass

        # =====================================================
        # APPLY PUNISHMENT
        # =====================================================

        action_success = False

        try:
            if punishment == "ban":

                await message.guild.ban(
                    member,
                    reason="Honeypot triggered"
                )

                action_success = True

            elif punishment == "kick":

                await message.guild.kick(
                    member,
                    reason="Honeypot triggered"
                )

                action_success = True

            elif punishment == "mute":

                # Discord maximum timeout:
                # 28 days.
                timeout_until = (
                    discord.utils.utcnow()
                    + datetime.timedelta(
                        days=28
                    )
                )

                await member.timeout(
                    timeout_until,
                    reason="Honeypot triggered"
                )

                action_success = True

        except discord.Forbidden:
            action_success = False

        except discord.HTTPException:
            action_success = False

        # =====================================================
        # LOG
        # =====================================================

        await self.send_log(
            message.guild,
            member,
            message.channel,
            punishment
        )


# =============================================================
# SETUP
# =============================================================

async def setup(bot):
    await bot.add_cog(
        Honeypot(bot)
    )
