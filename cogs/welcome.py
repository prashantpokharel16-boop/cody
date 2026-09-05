import asyncio
import sqlite3
import time

import discord
from discord.ext import commands
from discord import app_commands

class Welcome(commands.Cog):
    VERSION = "WELCOME-TEXT-FIRST-V2"
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
                CREATE TABLE IF NOT EXISTS welcome_settings (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER,
                    message TEXT NOT NULL DEFAULT 'Welcome {user} to {server}! 🎉',
                    auto_role_id INTEGER,
                    panel_owner_id INTEGER,
                    panel_message_id INTEGER
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS welcome_events (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

            conn.commit()

        finally:
            conn.close()

    def get_settings(self, guild_id):
        conn = self.get_connection()

        try:
            row = conn.execute(
                """
                SELECT
                    enabled,
                    channel_id,
                    message,
                    auto_role_id,
                    panel_owner_id,
                    panel_message_id
                FROM welcome_settings
                WHERE guild_id = ?
                """,
                (guild_id,)
            ).fetchone()

            return row

        finally:
            conn.close()

    def create_settings(self, guild_id):
        if self.get_settings(guild_id):
            return

        conn = self.get_connection()

        try:
            conn.execute(
                """
                INSERT INTO welcome_settings
                (
                    guild_id,
                    enabled,
                    channel_id,
                    message,
                    auto_role_id,
                    panel_owner_id,
                    panel_message_id
                )
                VALUES (?, 0, NULL, ?, NULL, NULL, NULL)
                """,
                (
                    guild_id,
                    "Welcome {user} to {server}! 🎉"
                )
            )

            conn.commit()

        finally:
            conn.close()

    def update_settings(
        self,
        guild_id,
        *,
        enabled=None,
        channel_id=None,
        message=None,
        auto_role_id=None,
        panel_owner_id=None,
        panel_message_id=None
    ):
        self.create_settings(guild_id)

        current = self.get_settings(guild_id)

        old_enabled = current[0]
        old_channel = current[1]
        old_message = current[2]
        old_role = current[3]
        old_owner = current[4]
        old_panel = current[5]

        if enabled is None:
            enabled = old_enabled

        if channel_id is None:
            channel_id = old_channel

        if message is None:
            message = old_message

        if auto_role_id is None:
            auto_role_id = old_role

        if panel_owner_id is None:
            panel_owner_id = old_owner

        if panel_message_id is None:
            panel_message_id = old_panel

        conn = self.get_connection()

        try:
            conn.execute(
                """
                UPDATE welcome_settings
                SET
                    enabled = ?,
                    channel_id = ?,
                    message = ?,
                    auto_role_id = ?,
                    panel_owner_id = ?,
                    panel_message_id = ?
                WHERE guild_id = ?
                """,
                (
                    enabled,
                    channel_id,
                    message,
                    auto_role_id,
                    panel_owner_id,
                    panel_message_id,
                    guild_id
                )
            )

            conn.commit()

        finally:
            conn.close()

    # =========================================================
    # VARIABLES
    # =========================================================

    def format_message(self, message, member):
        guild = member.guild

        return (
            message
            .replace("{user}", member.mention)
            .replace("{username}", member.display_name)
            .replace("{server}", guild.name)
            .replace("{member_count}", str(guild.member_count))
        )

    # =========================================================
    # FONTS
    # =========================================================

    # =========================================================
    # SEND WELCOME
    # =========================================================

    async def send_welcome(
        self,
        member
    ):
        """Send the configured welcome text message."""

        # -----------------------------------------------------
        # Prevent multiple Welcome cog instances in the same
        # process from sending the same event at the same time.
        # -----------------------------------------------------

        lock = getattr(
            self.bot,
            "_welcome_send_lock",
            None
        )

        if lock is None:
            lock = asyncio.Lock()
            setattr(
                self.bot,
                "_welcome_send_lock",
                lock
            )

        async with lock:

            guild = member.guild

            settings = self.get_settings(
                guild.id
            )

            if not settings:
                return

            enabled = settings[0]
            channel_id = settings[1]
            welcome_message = settings[2]
            auto_role_id = settings[3]

            if not enabled or not channel_id:
                return

            channel = guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel,
                discord.TextChannel
            ):
                return

            # -------------------------------------------------
            # AUTO ROLE
            # -------------------------------------------------

            if auto_role_id:
                role = guild.get_role(
                    auto_role_id
                )

                if role:
                    try:
                        if (
                            guild.me
                            and role < guild.me.top_role
                        ):
                            await member.add_roles(
                                role,
                                reason="Welcome Auto Role"
                            )

                    except (
                        discord.Forbidden,
                        discord.HTTPException
                    ):
                        pass

            # -------------------------------------------------
            # MESSAGE 1: CONFIGURED TEXT
            # -------------------------------------------------

            formatted_message = self.format_message(
                welcome_message,
                member
            )

            if not formatted_message.strip():
                formatted_message = (
                    f"Welcome {member.display_name} "
                    f"to {guild.name}! 🎉"
                )

            try:
                await channel.send(
                    content=formatted_message,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=True,
                        everyone=False
                    )
                )

                print(
                    f"[WELCOME] Text sent for "
                    f"{member} in {guild.name}"
                )

            except Exception as error:
                print(
                    f"[WELCOME] Text send failed for "
                    f"{member}: {error}"
                )

    # =========================================================
    # CLAIM JOIN EVENT
    # =========================================================

    def claim_join_event(
        self,
        guild_id,
        user_id
    ):
        """
        Atomically allow only one welcome event per member within
        15 seconds. This also protects against two bot processes
        using the same SQLite database.
        """

        now = time.time()
        cutoff = now - 15

        conn = self.get_connection()

        try:
            conn.execute(
                "BEGIN IMMEDIATE"
            )

            row = conn.execute(
                """
                SELECT created_at
                FROM welcome_events
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (
                    guild_id,
                    user_id
                )
            ).fetchone()

            if row and row[0] >= cutoff:
                conn.rollback()
                return False

            conn.execute(
                """
                INSERT INTO welcome_events (
                    guild_id,
                    user_id,
                    created_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET created_at = excluded.created_at
                """,
                (
                    guild_id,
                    user_id,
                    now
                )
            )

            conn.commit()
            return True

        except sqlite3.Error as error:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass

            print(
                f"[WELCOME] Join event guard failed: {error}"
            )

            # Do not block a real welcome if the guard itself fails.
            return True

        finally:
            conn.close()

    # =========================================================
    # PANEL PERMISSION
    # =========================================================

    def owns_panel(
        self,
        interaction
    ):
        settings = self.get_settings(
            interaction.guild.id
        )

        if not settings:
            return False

        owner_id = settings[4]

        if not owner_id:
            return False

        return owner_id == interaction.user.id

    async def panel_permission(
        self,
        interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True
            )
            return False

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ You need **Administrator** permission.",
                ephemeral=True
            )
            return False

        if not self.owns_panel(
            interaction
        ):
            await interaction.response.send_message(
                "🔒 This configuration panel belongs to "
                "another administrator. Only the administrator "
                "who created this panel can edit it.",
                ephemeral=True
            )
            return False

        return True

    # =========================================================
    # CHANNEL SELECT
    # =========================================================

    class WelcomeChannelSelect(
        discord.ui.ChannelSelect
    ):
        def __init__(self, cog):
            self.cog = cog

            super().__init__(
                placeholder="📢 Choose welcome channel...",
                channel_types=[
                    discord.ChannelType.text
                ],
                min_values=1,
                max_values=1
            )

        async def callback(
            self,
            interaction
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            channel = self.values[0]

            self.cog.update_settings(
                interaction.guild.id,
                channel_id=channel.id
            )

            await interaction.response.send_message(
                f"✅ Welcome channel set to "
                f"{channel.mention}.",
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
    # ROLE SELECT
    # =========================================================

    class WelcomeRoleSelect(
        discord.ui.RoleSelect
    ):
        def __init__(self, cog):
            self.cog = cog

            super().__init__(
                placeholder="🎭 Choose automatic role...",
                min_values=1,
                max_values=1
            )

        async def callback(
            self,
            interaction
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            role = self.values[0]

            if role >= interaction.guild.me.top_role:
                await interaction.response.send_message(
                    "❌ I cannot assign that role because "
                    "it is equal to or higher than my highest role.",
                    ephemeral=True
                )
                return

            self.cog.update_settings(
                interaction.guild.id,
                auto_role_id=role.id
            )

            await interaction.response.send_message(
                f"✅ Automatic role set to {role.mention}.",
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
    # MESSAGE MODAL
    # =========================================================

    class WelcomeMessageModal(
        discord.ui.Modal,
        title="Welcome Message"
    ):
        message_input = discord.ui.TextInput(
            label="Welcome message",
            placeholder=(
                "Welcome {user} to {server}! 🎉"
            ),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )

        def __init__(self, cog):
            super().__init__()

            self.cog = cog

            settings = cog.get_settings(
                self.guild_id
            ) if hasattr(self, "guild_id") else None

            if settings:
                self.message_input.default = settings[2]

        async def on_submit(
            self,
            interaction
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            self.cog.update_settings(
                interaction.guild.id,
                message=str(
                    self.message_input.value
                )
            )

            await interaction.response.send_message(
                "✅ Welcome message updated.",
                ephemeral=True
            )

    # =========================================================
    # CONFIG VIEW
    # =========================================================

    class WelcomeConfigView(
        discord.ui.View
    ):
        def __init__(self, cog):
            super().__init__(
                timeout=None
            )

            self.cog = cog

            # Channel selector.
            self.add_item(
                cog.WelcomeChannelSelect(cog)
            )

            # Role selector.
            self.add_item(
                cog.WelcomeRoleSelect(cog)
            )

        # -----------------------------------------------------
        # MESSAGE
        # -----------------------------------------------------

        @discord.ui.button(
            label="Message",
            emoji="✏️",
            style=discord.ButtonStyle.primary,
            custom_id="welcome_message"
        )
        async def message_button(
            self,
            interaction,
            button
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            modal = self.cog.WelcomeMessageModal(
                self.cog
            )

            settings = self.cog.get_settings(
                interaction.guild.id
            )

            if settings:
                modal.message_input.default = (
                    settings[2]
                )

            await interaction.response.send_modal(
                modal
            )

        # -----------------------------------------------------
        # REMOVE AUTO ROLE
        # -----------------------------------------------------

        @discord.ui.button(
            label="Remove Role",
            emoji="🗑️",
            style=discord.ButtonStyle.secondary,
            custom_id="welcome_remove_role"
        )
        async def remove_role(
            self,
            interaction,
            button
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            self.cog.update_settings(
                interaction.guild.id,
                auto_role_id=0
            )

            await interaction.response.send_message(
                "✅ Automatic welcome role removed.",
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
        # ENABLE
        # -----------------------------------------------------

        @discord.ui.button(
            label="Enable",
            emoji="🟢",
            style=discord.ButtonStyle.success,
            custom_id="welcome_enable"
        )
        async def enable(
            self,
            interaction,
            button
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            settings = self.cog.get_settings(
                interaction.guild.id
            )

            if not settings or not settings[1]:
                await interaction.response.send_message(
                    "❌ Please select a welcome channel first.",
                    ephemeral=True
                )
                return

            self.cog.update_settings(
                interaction.guild.id,
                enabled=1
            )

            await interaction.response.send_message(
                "🟢 **Welcome system enabled!**\n\n"
                "New members will receive exactly:\n"
                "🖼️ One animated welcome banner\n"
                "💬 One separate welcome message",
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
            custom_id="welcome_disable"
        )
        async def disable(
            self,
            interaction,
            button
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            self.cog.update_settings(
                interaction.guild.id,
                enabled=0
            )

            await interaction.response.send_message(
                "🔴 **Welcome system disabled.**\n\n"
                "Your channel, message and role settings "
                "have been preserved.",
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
            custom_id="welcome_test"
        )
        async def test(
            self,
            interaction,
            button
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
                return

            settings = self.cog.get_settings(
                interaction.guild.id
            )

            if not settings or not settings[1]:
                await interaction.response.send_message(
                    "❌ Please select a welcome channel first.",
                    ephemeral=True
                )
                return

            channel = interaction.guild.get_channel(
                settings[1]
            )

            if not channel:
                await interaction.response.send_message(
                    "❌ The configured welcome channel "
                    "no longer exists.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                "🧪 Sending the welcome test...",
                ephemeral=True
            )

            # Test uses the administrator who clicked Test
            # as the member shown in the welcome message.
            await self.cog.send_welcome(
                interaction.user
            )

        # -----------------------------------------------------
        # REFRESH
        # -----------------------------------------------------

        @discord.ui.button(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="welcome_refresh"
        )
        async def refresh(
            self,
            interaction,
            button
        ):
            if not await self.cog.panel_permission(
                interaction
            ):
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

        if not settings:
            self.create_settings(
                guild.id
            )
            settings = self.get_settings(
                guild.id
            )

        enabled = settings[0]
        channel_id = settings[1]
        message = settings[2]
        role_id = settings[3]

        channel = (
            guild.get_channel(channel_id)
            if channel_id
            else None
        )

        role = (
            guild.get_role(role_id)
            if role_id
            else None
        )

        embed = discord.Embed(
            title="👋 Welcome Configuration",
            description=(
                "Configure your server's Welcome system.\n\n"
                "Every new member receives **one welcome message**."
            )
        )

        embed.add_field(
            name="📢 Welcome Channel",
            value=(
                channel.mention
                if channel
                else "❌ Not configured"
            ),
            inline=False
        )

        embed.add_field(
            name="💬 Welcome Message",
            value=(
                message[:1000]
                if message
                else "❌ Not configured"
            ),
            inline=False
        )

        embed.add_field(
            name="🎭 Auto Role",
            value=(
                role.mention
                if role
                else "❌ None"
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
            name="🔤 Variables",
            value=(
                "`{user}` — Member mention\n"
                "`{username}` — Display name\n"
                "`{server}` — Server name\n"
                "`{member_count}` — Member count"
            ),
            inline=False
        )

        embed.set_footer(
            text=f"Welcome System • {guild.name}"
        )

        return embed

    # =========================================================
    # /WELCOME CONFIG
    # =========================================================

    welcome = app_commands.Group(
        name="welcome",
        description="Configure the Welcome system."
    )

    @welcome.command(
        name="config",
        description="Open the Welcome configuration panel."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def config(
        self,
        interaction: discord.Interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return

        self.create_settings(
            interaction.guild.id
        )

        # The administrator who creates this panel
        # becomes the owner of this specific panel.
        self.update_settings(
            interaction.guild.id,
            panel_owner_id=interaction.user.id
        )

        embed = self.make_embed(
            interaction.guild
        )

        view = self.WelcomeConfigView(
            self
        )

        await interaction.response.send_message(
            embed=embed,
            view=view
        )

        # Store the panel's message ID.
        try:
            panel_message = (
                await interaction.original_response()
            )

            self.update_settings(
                interaction.guild.id,
                panel_message_id=panel_message.id
            )

        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass

    @config.error
    async def config_error(
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
                "to configure Welcome."
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
    # /ENABLE WELCOME
    # =========================================================

    enable_group = app_commands.Group(
        name="enable",
        description="Enable server features."
    )

    @enable_group.command(
        name="welcome",
        description="Enable the Welcome system."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def enable_welcome(
        self,
        interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True
            )
            return

        self.create_settings(
            interaction.guild.id
        )

        settings = self.get_settings(
            interaction.guild.id
        )

        if not settings[1]:
            await interaction.response.send_message(
                "❌ Welcome is not configured yet.\n"
                "Run `/welcome config` first.",
                ephemeral=True
            )
            return

        self.update_settings(
            interaction.guild.id,
            enabled=1
        )

        await interaction.response.send_message(
            "🟢 **Welcome system enabled!**",
            ephemeral=True
        )

    # =========================================================
    # /DISABLE WELCOME
    # =========================================================

    disable_group = app_commands.Group(
        name="disable",
        description="Disable server features."
    )

    @disable_group.command(
        name="welcome",
        description="Disable the Welcome system."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def disable_welcome(
        self,
        interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True
            )
            return

        self.create_settings(
            interaction.guild.id
        )

        self.update_settings(
            interaction.guild.id,
            enabled=0
        )

        await interaction.response.send_message(
            "🔴 **Welcome system disabled.**\n"
            "Your existing settings have been preserved.",
            ephemeral=True
        )

    # =========================================================
    # MEMBER JOIN
    # =========================================================

    @commands.Cog.listener()
    async def on_member_join(
        self,
        member: discord.Member
    ):
        # Claim the join before any asynchronous work.
        # If another Welcome instance/process already handled this
        # same join event, this returns immediately.
        if not self.claim_join_event(
            member.guild.id,
            member.id
        ):
            print(
                f"[WELCOME] Duplicate join ignored for "
                f"{member} ({member.id})"
            )
            return

        await self.send_welcome(
            member
        )

# =============================================================
# COG SETUP
# =============================================================

async def setup(bot):
    # Prevent the Welcome cog from being registered twice in one process.
    if bot.get_cog("Welcome") is not None:
        print("[WELCOME] Already loaded; skipping duplicate cog registration.")
        return

    print(f"[WELCOME] Loading {Welcome.VERSION}")
    await bot.add_cog(
        Welcome(bot)
    )
