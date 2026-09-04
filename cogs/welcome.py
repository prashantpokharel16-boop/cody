import discord
from discord import app_commands
from discord.ext import commands


DEFAULT_MESSAGE = (
    "Welcome {user} to **{server}**! 🎉\n"
    "You are member #{member_count}."
)


class WelcomeConfigView(discord.ui.View):
    def __init__(self, cog, creator_id: int, panel_message_id: int | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.creator_id = creator_id
        self.panel_message_id = panel_message_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ You must have **Administrator** permission to use this panel.",
                ephemeral=True,
            )
            return False

        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "🔒 This configuration panel belongs to another administrator. "
                "Only the administrator who created this panel can edit it.",
                ephemeral=True,
            )
            return False

        return True

    async def refresh_panel(self, interaction: discord.Interaction):
        if self.panel_message_id is None:
            return

        await self.cog.update_panel_message(
            interaction.guild,
            self.panel_message_id,
        )

    @discord.ui.button(
        label="📢 Channel",
        style=discord.ButtonStyle.primary,
        custom_id="welcome_channel_button",
        row=0,
    )
    async def channel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_message(
            "👇 Select the channel where welcome messages should be sent.",
            view=WelcomeChannelView(self),
            ephemeral=True,
        )

    @discord.ui.button(
        label="✏️ Message",
        style=discord.ButtonStyle.secondary,
        custom_id="welcome_message_button",
        row=0,
    )
    async def message_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild_id = interaction.guild.id

        row = await self.cog.bot.database.fetchone(
            """
            SELECT welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        )

        current_message = (
            row["welcome_message"]
            if row and row["welcome_message"]
            else DEFAULT_MESSAGE
        )

        await interaction.response.send_modal(
            WelcomeMessageModal(self, current_message)
        )

    @discord.ui.button(
        label="🎭 Auto Role",
        style=discord.ButtonStyle.secondary,
        custom_id="welcome_role_button",
        row=0,
    )
    async def role_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_message(
            "👇 Select the role to automatically give new members.",
            view=WelcomeRoleView(self),
            ephemeral=True,
        )

    @discord.ui.button(
        label="🧪 Test",
        style=discord.ButtonStyle.success,
        custom_id="welcome_test_button",
        row=0,
    )
    async def test_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild_id = interaction.guild.id

        row = await self.cog.bot.database.fetchone(
            """
            SELECT welcome_channel_id, welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        )

        if not row or not row["welcome_channel_id"]:
            await interaction.response.send_message(
                "❌ Please configure a welcome channel first.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(
            row["welcome_channel_id"]
        )

        if not channel:
            await interaction.response.send_message(
                "❌ The configured welcome channel no longer exists.",
                ephemeral=True,
            )
            return

        message = (
            row["welcome_message"]
            if row["welcome_message"]
            else DEFAULT_MESSAGE
        )

        preview = self.cog.format_message(
            message,
            interaction.guild,
            interaction.user,
        )

        await interaction.response.send_message(
            "🧪 Test welcome message sent.",
            ephemeral=True,
        )

        await channel.send(preview)


class WelcomeChannelView(discord.ui.View):
    def __init__(self, parent_view: WelcomeConfigView):
        super().__init__(timeout=60)
        self.parent_view = parent_view

        self.add_item(
            WelcomeChannelSelect(parent_view)
        )


class WelcomeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view: WelcomeConfigView):
        super().__init__(
            placeholder="Select welcome channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        guild_id = interaction.guild.id

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings (guild_id, welcome_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET welcome_channel_id = excluded.welcome_channel_id
            """,
            (guild_id, channel.id),
        )

        await interaction.response.send_message(
            f"✅ Welcome channel set to {channel.mention}.",
            ephemeral=True,
        )

        await self.parent_view.cog.update_panel_message(
            interaction.guild,
            self.parent_view.panel_message_id,
        )


class WelcomeRoleView(discord.ui.View):
    def __init__(self, parent_view: WelcomeConfigView):
        super().__init__(timeout=60)
        self.add_item(
            WelcomeRoleSelect(parent_view)
        )


class WelcomeRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent_view: WelcomeConfigView):
        super().__init__(
            placeholder="Select automatic role...",
            min_values=1,
            max_values=1,
        )

        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]

        guild_id = interaction.guild.id

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings (guild_id, autorole_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET autorole_id = excluded.autorole_id
            """,
            (guild_id, role.id),
        )

        await interaction.response.send_message(
            f"✅ Auto role set to {role.mention}.",
            ephemeral=True,
        )

        await self.parent_view.cog.update_panel_message(
            interaction.guild,
            self.parent_view.panel_message_id,
        )


class WelcomeMessageModal(discord.ui.Modal):
    def __init__(
        self,
        parent_view: WelcomeConfigView,
        current_message: str,
    ):
        super().__init__(title="Welcome Message")

        self.parent_view = parent_view

        self.message_input = discord.ui.TextInput(
            label="Welcome message",
            style=discord.TextStyle.paragraph,
            placeholder="Welcome {user} to {server}!",
            default=current_message,
            required=True,
            max_length=2000,
        )

        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        message = self.message_input.value

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings (guild_id, welcome_message)
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET welcome_message = excluded.welcome_message
            """,
            (guild_id, message),
        )

        await interaction.response.send_message(
            "✅ Welcome message saved!",
            ephemeral=True,
        )

        await self.parent_view.cog.update_panel_message(
            interaction.guild,
            self.parent_view.panel_message_id,
        )


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    welcome_group = app_commands.Group(
        name="welcome",
        description="Configure the welcome system.",
    )

    enable_group = app_commands.Group(
        name="enable",
        description="Enable server features.",
    )

    disable_group = app_commands.Group(
        name="disable",
        description="Disable server features.",
    )

    @welcome_group.command(
        name="config",
        description="Open the welcome configuration panel.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_config(
        self,
        interaction: discord.Interaction,
    ):
        guild_id = interaction.guild.id

        await self.bot.database.execute(
            """
            INSERT OR IGNORE INTO guild_settings (guild_id)
            VALUES (?)
            """,
            (guild_id,),
        )

        message = await interaction.channel.send(
            embed=await self.create_panel_embed(interaction.guild),
        )

        view = WelcomeConfigView(
            self,
            interaction.user.id,
            message.id,
        )

        await message.edit(view=view)

        await self.bot.database.execute(
            """
            INSERT OR REPLACE INTO welcome_panels
            (guild_id, channel_id, message_id, creator_id)
            VALUES (?, ?, ?, ?)
            """,
            (
                guild_id,
                interaction.channel.id,
                message.id,
                interaction.user.id,
            ),
        )

        await interaction.response.send_message(
            "✅ Welcome configuration panel created.",
            ephemeral=True,
        )

    @enable_group.command(
        name="welcome",
        description="Enable automatic welcome messages.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def enable_welcome(
        self,
        interaction: discord.Interaction,
    ):
        guild_id = interaction.guild.id

        row = await self.bot.database.fetchone(
            """
            SELECT welcome_channel_id, welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        )

        if not row or not row["welcome_channel_id"] or not row["welcome_message"]:
            await interaction.response.send_message(
                "❌ Welcome is not configured yet.\n"
                "Run `/welcome config` first.",
                ephemeral=True,
            )
            return

        await self.bot.database.execute(
            """
            UPDATE guild_settings
            SET welcome_enabled = 1
            WHERE guild_id = ?
            """,
            (guild_id,),
        )

        await interaction.response.send_message(
            "✅ Automatic welcome messages are now **enabled**!",
            ephemeral=True,
        )

    @disable_group.command(
        name="welcome",
        description="Disable automatic welcome messages.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_welcome(
        self,
        interaction: discord.Interaction,
    ):
        guild_id = interaction.guild.id

        await self.bot.database.execute(
            """
            UPDATE guild_settings
            SET welcome_enabled = 0
            WHERE guild_id = ?
            """,
            (guild_id,),
        )

        await interaction.response.send_message(
            "🔴 Automatic welcome messages are now **disabled**.\n"
            "Your configuration has been preserved.",
            ephemeral=True,
        )

    async def create_panel_embed(self, guild: discord.Guild):
        row = await self.bot.database.fetchone(
            """
            SELECT
                welcome_enabled,
                welcome_channel_id,
                welcome_message,
                autorole_id
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild.id,),
        )

        enabled = bool(row and row["welcome_enabled"])
        channel_id = row["welcome_channel_id"] if row else None
        message = row["welcome_message"] if row else None
        role_id = row["autorole_id"] if row else None

        channel = guild.get_channel(channel_id) if channel_id else None
        role = guild.get_role(role_id) if role_id else None

        embed = discord.Embed(
            title="👋 WELCOME CONFIGURATION",
            description="Configure your automatic welcome system below.",
            color=discord.Color.green() if enabled else discord.Color.red(),
        )

        embed.add_field(
            name="Status",
            value="🟢 Enabled" if enabled else "🔴 Disabled",
            inline=False,
        )

        embed.add_field(
            name="📢 Channel",
            value=channel.mention if channel else "Not configured",
            inline=False,
        )

        embed.add_field(
            name="✏️ Message",
            value=(
                message[:1024]
                if message
                else "Not configured"
            ),
            inline=False,
        )

        embed.add_field(
            name="🎭 Auto Role",
            value=role.mention if role else "None",
            inline=False,
        )

        embed.set_footer(
            text="Only the administrator who created this panel can edit it."
        )

        return embed

    async def update_panel_message(
        self,
        guild: discord.Guild,
        message_id: int,
    ):
        row = await self.bot.database.fetchone(
            """
            SELECT channel_id, creator_id
            FROM welcome_panels
            WHERE message_id = ?
            """,
            (message_id,),
        )

        if not row:
            return

        channel = guild.get_channel(row["channel_id"])

        if not channel:
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return

        view = WelcomeConfigView(
            self,
            row["creator_id"],
            message_id,
        )

        await message.edit(
            embed=await self.create_panel_embed(guild),
            view=view,
        )

    def format_message(
        self,
        message: str,
        guild: discord.Guild,
        member: discord.Member,
    ) -> str:
        return (
            message
            .replace("{user}", member.mention)
            .replace("{username}", member.name)
            .replace("{server}", guild.name)
            .replace("{member_count}", str(guild.member_count))
        )

    @commands.Cog.listener()
    async def on_member_join(
        self,
        member: discord.Member,
    ):
        guild_id = member.guild.id

        row = await self.bot.database.fetchone(
            """
            SELECT
                welcome_enabled,
                welcome_channel_id,
                welcome_message,
                autorole_id
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        )

        if not row or not row["welcome_enabled"]:
            return

        channel_id = row["welcome_channel_id"]

        if channel_id:
            channel = member.guild.get_channel(channel_id)

            if channel:
                message = (
                    row["welcome_message"]
                    or DEFAULT_MESSAGE
                )

                formatted = self.format_message(
                    message,
                    member.guild,
                    member,
                )

                try:
                    await channel.send(formatted)
                except discord.Forbidden:
                    pass

        role_id = row["autorole_id"]

        if role_id:
            role = member.guild.get_role(role_id)

            if role:
                try:
                    await member.add_roles(
                        role,
                        reason="Automatic welcome role",
                    )
                except discord.Forbidden:
                    pass

    async def restore_panels(self):
        rows = await self.bot.database.fetchall(
            """
            SELECT guild_id, channel_id, message_id, creator_id
            FROM welcome_panels
            """
        )

        for row in rows:
            view = WelcomeConfigView(
                self,
                row["creator_id"],
                row["message_id"],
            )

            self.bot.add_view(
                view,
                message_id=row["message_id"],
            )


async def setup(bot):
    cog = Welcome(bot)

    await bot.add_cog(cog)

    await cog.restore_panels()
