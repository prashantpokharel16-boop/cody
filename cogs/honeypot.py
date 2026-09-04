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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS honeypot_settings (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                punishment TEXT NOT NULL DEFAULT 'ban',
                enabled INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

    def get_settings(self, guild_id):
        conn = self.get_connection()

        try:
            row = conn.execute(
                """
                SELECT channel_id, punishment, enabled
                FROM honeypot_settings
                WHERE guild_id = ?
                """,
                (guild_id,)
            ).fetchone()

            return row

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

        if not row or not row[0]:
            return

        log_channel = guild.get_channel(row[0])

        if not log_channel:
            return

        punishment_names = {
            "ban": "🔨 Ban",
            "mute": "🔇 Mute",
            "kick": "👢 Kick"
        }

        embed = discord.Embed(
            title="🍯 Honeypot Triggered",
            description=(
                f"**Member:** {member.mention}\n"
                f"**User ID:** `{member.id}`\n"
                f"**Channel:** {channel.mention}\n"
                f"**Action:** "
                f"{punishment_names.get(punishment, punishment)}"
            ),
            timestamp=discord.utils.utcnow()
        )

        embed.set_footer(
            text="Honeypot Security"
        )

        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
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
                    description="Ban anyone who triggers the honeypot."
                ),
                discord.SelectOption(
                    label="Mute",
                    value="mute",
                    emoji="🔇",
                    description="Timeout anyone who triggers the honeypot."
                ),
                discord.SelectOption(
                    label="Kick",
                    value="kick",
                    emoji="👢",
                    description="Kick anyone who triggers the honeypot."
                )
            ]

            super().__init__(
                placeholder="⚔️ Choose punishment...",
                options=options
            )

        async def callback(
            self,
            interaction: discord.Interaction
        ):
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

            names = {
                "ban": "🔨 Ban",
                "mute": "🔇 Mute",
                "kick": "👢 Kick"
            }

            await interaction.response.send_message(
                f"✅ Honeypot punishment set to "
                f"**{names[punishment]}**.",
                ephemeral=True
            )

    # =========================================================
    # CONFIG VIEW
    # =========================================================

    class HoneypotView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)

            self.cog = cog

            self.add_item(
                cog.PunishmentSelect(cog)
            )

        @discord.ui.ChannelSelect(
            placeholder="🍯 Choose honeypot channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1
        )
        async def channel_select(
            self,
            interaction: discord.Interaction,
            select: discord.ui.ChannelSelect
        ):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            channel = select.values[0]

            self.cog.save_settings(
                interaction.guild.id,
                channel_id=channel.id
            )

            await interaction.response.send_message(
                f"✅ Honeypot channel set to "
                f"{channel.mention}.",
                ephemeral=True
            )

        @discord.ui.button(
            label="Enable",
            emoji="🟢",
            style=discord.ButtonStyle.success
        )
        async def enable(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            settings = self.cog.get_settings(
                interaction.guild.id
            )

            if not settings or not settings[0]:
                await interaction.response.send_message(
                    "❌ Please select a honeypot channel first.",
                    ephemeral=True
                )
                return

            self.cog.save_settings(
                interaction.guild.id,
                enabled=1
            )

            await interaction.response.send_message(
                "🟢 **Honeypot enabled!**\n\n"
                "Anyone who sends a message in the "
                "configured honeypot channel will "
                "receive the selected punishment.",
                ephemeral=True
            )

            try:
                await interaction.message.edit(
                    embed=self.cog.make_embed(
                        interaction.guild
                    ),
                    view=self
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        @discord.ui.button(
            label="Disable",
            emoji="🔴",
            style=discord.ButtonStyle.danger
        )
        async def disable(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
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
            except (discord.Forbidden, discord.HTTPException):
                pass

        @discord.ui.button(
            label="Test",
            emoji="🧪",
            style=discord.ButtonStyle.primary
        )
        async def test(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permission.",
                    ephemeral=True
                )
                return

            settings = self.cog.get_settings(
                interaction.guild.id
            )

            if not settings or not settings[0]:
                await interaction.response.send_message(
                    "❌ Configure a honeypot channel first.",
                    ephemeral=True
                )
                return

            punishment = settings[1]

            names = {
                "ban": "🔨 Ban",
                "mute": "🔇 Mute",
                "kick": "👢 Kick"
            }

            await interaction.response.send_message(
                "🧪 **Honeypot Test**\n\n"
                f"Channel: <#{settings[0]}>\n"
                f"Punishment: **{names.get(punishment)}**\n\n"
                "The configuration is working correctly.\n"
                "No punishment was applied to you.",
                ephemeral=True
            )

        @discord.ui.button(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary
        )
        async def refresh(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
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
    # EMBED
    # =========================================================

    def make_embed(self, guild):
        settings = self.get_settings(guild.id)

        if settings:
            channel_id, punishment, enabled = settings
        else:
            channel_id = None
            punishment = "ban"
            enabled = 0

        channel = (
            guild.get_channel(channel_id)
            if channel_id
            else None
        )

        punishment_names = {
            "ban": "🔨 Ban",
            "mute": "🔇 Mute",
            "kick": "👢 Kick"
        }

        embed = discord.Embed(
            title="🍯 Honeypot Configuration",
            description=(
                "Configure your server's Honeypot security system.\n\n"
                "⚠️ **Warning:** Anyone who sends a message "
                "in the selected Honeypot channel will "
                "automatically receive the selected punishment."
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
            value=punishment_names.get(
                punishment,
                "🔨 Ban"
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
            name="How it works",
            value=(
                "Member sends a message → "
                "message is deleted → "
                "selected punishment is applied."
            ),
            inline=False
        )

        embed.set_footer(
            text=f"Honeypot • {guild.name}"
        )

        return embed

    # =========================================================
    # SLASH COMMAND
    # =========================================================

    honeypot = app_commands.Group(
        name="honeypot",
        description="Configure the server Honeypot system."
    )

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

        await interaction.response.send_message(
            embed=embed,
            view=self.HoneypotView(self)
        )

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
        if not message.guild:
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

        # Honeypot disabled.
        if not enabled:
            return

        # No channel configured.
        if not channel_id:
            return

        # Message isn't in Honeypot channel.
        if message.channel.id != channel_id:
            return

        member = message.author

        # Delete triggering message.
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

        try:
            if punishment == "ban":
                await message.guild.ban(
                    member,
                    reason="Honeypot triggered"
                )

            elif punishment == "kick":
                await message.guild.kick(
                    member,
                    reason="Honeypot triggered"
                )

            elif punishment == "mute":
                # Discord's maximum timeout is 28 days.
                until = (
                    discord.utils.utcnow()
                    + datetime.timedelta(days=28)
                )

                await member.timeout(
                    until,
                    reason="Honeypot triggered"
                )

        except discord.Forbidden:
            # Bot doesn't have enough permissions /
            # role hierarchy prevents the action.
            pass

        except discord.HTTPException:
            pass

        # Send moderation log.
        await self.send_log(
            message.guild,
            member,
            message.channel,
            punishment
        )


async def setup(bot):
    await bot.add_cog(
        Honeypot(bot)
    )
