import re
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# DATABASE
# ============================================================

TICKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticket_configs (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    panel_channel_id INTEGER,
    category_id INTEGER,
    staff_role_id INTEGER,
    panel_title TEXT NOT NULL DEFAULT '🎫 Create a Ticket',
    panel_message TEXT NOT NULL DEFAULT 'Select a department below to create a ticket.'
);

CREATE TABLE IF NOT EXISTS ticket_buttons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    emoji TEXT,
    color TEXT NOT NULL DEFAULT 'blue',
    prefix TEXT NOT NULL,
    opening_message TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ticket_channels (
    channel_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    button_id INTEGER NOT NULL,
    button_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ticket_buttons_guild
ON ticket_buttons(guild_id);

CREATE INDEX IF NOT EXISTS idx_ticket_channels_guild
ON ticket_channels(guild_id);
"""


async def setup_ticket_database(bot):
    """Create ticket tables."""
    await bot.database.connection.executescript(TICKET_SCHEMA)
    await bot.database.connection.commit()


# ============================================================
# HELPERS
# ============================================================

def clean_name(value: str) -> str:
    """
    Convert a name into something safe for a Discord channel name.
    """
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    value = value.strip("-_")

    if not value:
        value = "ticket"

    return value[:40]


def color_from_name(name: str) -> discord.ButtonStyle:
    colors = {
        "blue": discord.ButtonStyle.primary,
        "gray": discord.ButtonStyle.secondary,
        "grey": discord.ButtonStyle.secondary,
        "green": discord.ButtonStyle.success,
        "red": discord.ButtonStyle.danger,
    }

    return colors.get(name.lower(), discord.ButtonStyle.primary)


def color_choices():
    return [
        app_commands.Choice(name="Blue", value="blue"),
        app_commands.Choice(name="Gray", value="gray"),
        app_commands.Choice(name="Green", value="green"),
        app_commands.Choice(name="Red", value="red"),
    ]


async def get_config(bot, guild_id: int):
    cursor = await bot.database.connection.execute(
        """
        SELECT
            guild_id,
            enabled,
            panel_channel_id,
            category_id,
            staff_role_id,
            panel_title,
            panel_message
        FROM ticket_configs
        WHERE guild_id = ?
        """,
        (guild_id,),
    )

    return await cursor.fetchone()


async def get_buttons(bot, guild_id: int):
    cursor = await bot.database.connection.execute(
        """
        SELECT
            id,
            guild_id,
            name,
            emoji,
            color,
            prefix,
            opening_message,
            position
        FROM ticket_buttons
        WHERE guild_id = ?
        ORDER BY position ASC, id ASC
        """,
        (guild_id,),
    )

    return await cursor.fetchall()


async def get_button(bot, button_id: int):
    cursor = await bot.database.connection.execute(
        """
        SELECT
            id,
            guild_id,
            name,
            emoji,
            color,
            prefix,
            opening_message,
            position
        FROM ticket_buttons
        WHERE id = ?
        """,
        (button_id,),
    )

    return await cursor.fetchone()


async def ensure_config(bot, guild_id: int):
    config = await get_config(bot, guild_id)

    if config is None:
        await bot.database.connection.execute(
            """
            INSERT INTO ticket_configs (
                guild_id,
                enabled
            )
            VALUES (?, 0)
            """,
            (guild_id,),
        )

        await bot.database.connection.commit()

        config = await get_config(bot, guild_id)

    return config


# ============================================================
# BUTTON FOR CLOSING TICKETS
# ============================================================

class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Close Ticket",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id="ticket_close",
        )

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ This isn't a ticket channel.",
                ephemeral=True,
            )
            return

        bot = interaction.client

        cursor = await bot.database.connection.execute(
            """
            SELECT guild_id, user_id, button_id, closed
            FROM ticket_channels
            WHERE channel_id = ?
            """,
            (channel.id,),
        )

        ticket = await cursor.fetchone()

        if ticket is None:
            await interaction.response.send_message(
                "❌ This channel isn't registered as a ticket.",
                ephemeral=True,
            )
            return

        guild_id, owner_id, button_id, closed = ticket

        guild = interaction.guild

        if guild is None:
            return

        config = await get_config(bot, guild.id)

        staff_role_id = config[4] if config else None

        is_owner = interaction.user.id == owner_id

        is_staff = (
            staff_role_id is not None
            and isinstance(interaction.user, discord.Member)
            and any(role.id == staff_role_id for role in interaction.user.roles)
        )

        is_admin = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_channels
        )

        if not (is_owner or is_staff or is_admin):
            await interaction.response.send_message(
                "🔒 You don't have permission to close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🔒 Closing this ticket...",
        )

        await bot.database.connection.execute(
            """
            UPDATE ticket_channels
            SET closed = 1
            WHERE channel_id = ?
            """,
            (channel.id,),
        )

        await bot.database.connection.commit()

        try:
            await channel.edit(
                name=f"closed-{channel.name}"[:100],
                overwrites={
                    target: overwrite
                    for target, overwrite in channel.overwrites.items()
                },
            )

            if isinstance(interaction.user, discord.Member):
                await channel.set_permissions(
                    interaction.user,
                    send_messages=False,
                )

        except Exception:
            pass

        await channel.send(
            "🔒 **Ticket Closed**\n"
            "This ticket has been closed by "
            f"{interaction.user.mention}."
        )


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton())


# ============================================================
# DYNAMIC TICKET BUTTON
# ============================================================

class TicketCreateButton(discord.ui.Button):
    def __init__(
        self,
        button_id: int,
        name: str,
        emoji: Optional[str],
        color: str,
    ):
        self.ticket_button_id = button_id

        super().__init__(
            label=name[:80],
            emoji=emoji if emoji else None,
            style=color_from_name(color),
            custom_id=f"ticket_create:{button_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "❌ This can only be used inside a server.",
                ephemeral=True,
            )
            return

        config = await get_config(bot, guild.id)

        if config is None or not config[1]:
            await interaction.response.send_message(
                "❌ The ticket system is currently disabled.",
                ephemeral=True,
            )
            return

        category_id = config[3]
        staff_role_id = config[4]

        if not category_id:
            await interaction.response.send_message(
                "❌ The ticket category hasn't been configured.",
                ephemeral=True,
            )
            return

        category = guild.get_channel(category_id)

        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "❌ The configured ticket category no longer exists.",
                ephemeral=True,
            )
            return

        button = await get_button(bot, self.ticket_button_id)

        if button is None:
            await interaction.response.send_message(
                "❌ This ticket button no longer exists.",
                ephemeral=True,
            )
            return

        (
            button_id,
            guild_id,
            button_name,
            emoji,
            color,
            prefix,
            opening_message,
            position,
        ) = button

        # ----------------------------------------------------
        # Check if user already has an open ticket of this type
        # ----------------------------------------------------

        cursor = await bot.database.connection.execute(
            """
            SELECT channel_id
            FROM ticket_channels
            WHERE guild_id = ?
              AND user_id = ?
              AND closed = 0
            """,
            (guild.id, interaction.user.id),
        )

        existing = await cursor.fetchall()

        for row in existing:
            existing_channel = guild.get_channel(row[0])

            if existing_channel:
                await interaction.response.send_message(
                    "⚠️ You already have an open ticket:\n"
                    f"{existing_channel.mention}",
                    ephemeral=True,
                )
                return

        # ----------------------------------------------------
        # Check bot permissions
        # ----------------------------------------------------

        me = guild.me

        if me is None:
            await interaction.response.send_message(
                "❌ I couldn't find my member information.",
                ephemeral=True,
            )
            return

        if not me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "❌ I need the **Manage Channels** permission to create tickets.",
                ephemeral=True,
            )
            return

        # ----------------------------------------------------
        # Build channel name
        # ----------------------------------------------------

        safe_prefix = clean_name(prefix or button_name)

        username = clean_name(
            getattr(interaction.user, "display_name", interaction.user.name)
        )

        channel_name = f"{safe_prefix}-{username}"[:100]

        # ----------------------------------------------------
        # Permission overwrites
        # ----------------------------------------------------

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),

            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),

            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }

        # Add configured staff role
        staff_role = None

        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)

            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        # ----------------------------------------------------
        # Create channel
        # ----------------------------------------------------

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=(
                    f"Ticket created by {interaction.user} | "
                    f"Type: {button_name}"
                ),
                reason=f"Ticket created using {button_name}",
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to create ticket channels.",
                ephemeral=True,
            )
            return

        except discord.HTTPException as error:
            await interaction.response.send_message(
                f"❌ Discord failed to create the ticket: `{error}`",
                ephemeral=True,
            )
            return

        # ----------------------------------------------------
        # Save ticket
        # ----------------------------------------------------

        await bot.database.connection.execute(
            """
            INSERT INTO ticket_channels (
                channel_id,
                guild_id,
                user_id,
                button_id,
                button_name,
                closed
            )
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                channel.id,
                guild.id,
                interaction.user.id,
                button_id,
                button_name,
            ),
        )

        await bot.database.connection.commit()

        # ----------------------------------------------------
        # Replace variables
        # ----------------------------------------------------

        message = opening_message or "Welcome {user}!"

        message = message.replace(
            "{user}",
            interaction.user.mention,
        )

        message = message.replace(
            "{username}",
            interaction.user.display_name,
        )

        message = message.replace(
            "{server}",
            guild.name,
        )

        message = message.replace(
            "{member_count}",
            str(guild.member_count),
        )

        # ----------------------------------------------------
        # Ticket embed
        # ----------------------------------------------------

        embed = discord.Embed(
            title=f"🎫 {button_name} Ticket",
            description=message,
        )

        embed.add_field(
            name="👤 Ticket Owner",
            value=interaction.user.mention,
            inline=True,
        )

        embed.add_field(
            name="🔘 Ticket Type",
            value=button_name,
            inline=True,
        )

        embed.set_footer(
            text=f"Ticket ID: {channel.id}"
        )

        try:
            embed.set_thumbnail(
                url=interaction.user.display_avatar.url
            )
        except Exception:
            pass

        content = interaction.user.mention

        if staff_role:
            content += f" {staff_role.mention}"

        await channel.send(
            content=content,
            embed=embed,
            view=CloseTicketView(),
        )

        await interaction.response.send_message(
            f"🎫 Your ticket has been created: {channel.mention}",
            ephemeral=True,
        )


# ============================================================
# TICKET PANEL
# ============================================================

class TicketPanelView(discord.ui.View):
    def __init__(self, buttons):
        super().__init__(timeout=None)

        for button in buttons:
            (
                button_id,
                guild_id,
                name,
                emoji,
                color,
                prefix,
                opening_message,
                position,
            ) = button

            self.add_item(
                TicketCreateButton(
                    button_id=button_id,
                    name=name,
                    emoji=emoji,
                    color=color,
                )
            )


# ============================================================
# PANEL SETTINGS MODAL
# ============================================================

class TicketPanelMessageModal(discord.ui.Modal):
    def __init__(self, bot, guild_id: int):
        super().__init__(title="Edit Ticket Panel")

        self.bot = bot
        self.guild_id = guild_id

        config = None

        self.title_input = discord.ui.TextInput(
            label="Panel Title",
            placeholder="🎫 Create a Ticket",
            max_length=256,
            required=True,
        )

        self.message_input = discord.ui.TextInput(
            label="Panel Message",
            placeholder="Select a department below...",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )

        self.add_item(self.title_input)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET panel_title = ?,
                panel_message = ?
            WHERE guild_id = ?
            """,
            (
                self.title_input.value,
                self.message_input.value,
                self.guild_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            "✅ Ticket panel title and message updated.",
            ephemeral=True,
        )


# ============================================================
# CHANNEL SELECT
# ============================================================

class TicketPanelChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select the ticket panel channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client

        channel = self.values[0]

        await bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET panel_channel_id = ?
            WHERE guild_id = ?
            """,
            (
                channel.id,
                interaction.guild.id,
            ),
        )

        await bot.database.connection.commit()

        await interaction.response.send_message(
            f"📢 Ticket panel channel set to {channel.mention}.",
            ephemeral=True,
        )


class TicketPanelChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(TicketPanelChannelSelect())


# ============================================================
# CATEGORY SELECT
# ============================================================

class TicketCategorySelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select the ticket category...",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client

        category = self.values[0]

        await bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET category_id = ?
            WHERE guild_id = ?
            """,
            (
                category.id,
                interaction.guild.id,
            ),
        )

        await bot.database.connection.commit()

        await interaction.response.send_message(
            f"📁 Ticket category set to **{category.name}**.",
            ephemeral=True,
        )


class TicketCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(TicketCategorySelect())


# ============================================================
# ROLE SELECT
# ============================================================

class TicketStaffRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select the ticket staff role...",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client

        role = self.values[0]

        await bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET staff_role_id = ?
            WHERE guild_id = ?
            """,
            (
                role.id,
                interaction.guild.id,
            ),
        )

        await bot.database.connection.commit()

        await interaction.response.send_message(
            f"🛡️ Ticket staff role set to {role.mention}.",
            ephemeral=True,
        )


class TicketStaffRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(TicketStaffRoleSelect())


# ============================================================
# ADD BUTTON MODAL
# ============================================================

class AddTicketButtonModal(discord.ui.Modal):
    def __init__(self, bot, guild_id: int):
        super().__init__(title="Add Ticket Button")

        self.bot = bot
        self.guild_id = guild_id

        self.name_input = discord.ui.TextInput(
            label="Button Name",
            placeholder="Purchase",
            max_length=80,
            required=True,
        )

        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            placeholder="💰",
            max_length=20,
            required=False,
        )

        self.prefix_input = discord.ui.TextInput(
            label="Channel Prefix",
            placeholder="purchase",
            max_length=40,
            required=True,
        )

        self.message_input = discord.ui.TextInput(
            label="Opening Message",
            placeholder="Welcome {user}! Please explain your issue.",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )

        self.add_item(self.name_input)
        self.add_item(self.emoji_input)
        self.add_item(self.prefix_input)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        cursor = await self.bot.database.connection.execute(
            """
            SELECT COALESCE(MAX(position), -1)
            FROM ticket_buttons
            WHERE guild_id = ?
            """,
            (self.guild_id,),
        )

        row = await cursor.fetchone()

        position = (row[0] if row else -1) + 1

        await self.bot.database.connection.execute(
            """
            INSERT INTO ticket_buttons (
                guild_id,
                name,
                emoji,
                color,
                prefix,
                opening_message,
                position
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.guild_id,
                self.name_input.value,
                self.emoji_input.value or None,
                "blue",
                clean_name(self.prefix_input.value),
                self.message_input.value,
                position,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"✅ Ticket button **{self.name_input.value}** added.",
            ephemeral=True,
        )


# ============================================================
# BUTTON MANAGEMENT
# ============================================================

class RemoveTicketButtonSelect(discord.ui.Select):
    def __init__(self, buttons):
        options = []

        for button in buttons[:25]:
            (
                button_id,
                guild_id,
                name,
                emoji,
                color,
                prefix,
                opening_message,
                position,
            ) = button

            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(button_id),
                    emoji=emoji if emoji else None,
                )
            )

        super().__init__(
            placeholder="Select a button to remove...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client

        button_id = int(self.values[0])

        await bot.database.connection.execute(
            """
            DELETE FROM ticket_buttons
            WHERE id = ?
              AND guild_id = ?
            """,
            (
                button_id,
                interaction.guild.id,
            ),
        )

        await bot.database.connection.commit()

        await interaction.response.send_message(
            "🗑️ Ticket button removed.",
            ephemeral=True,
        )


class RemoveTicketButtonView(discord.ui.View):
    def __init__(self, buttons):
        super().__init__(timeout=120)

        if buttons:
            self.add_item(
                RemoveTicketButtonSelect(buttons)
            )


# ============================================================
# TEST PANEL
# ============================================================

async def send_ticket_panel(bot, guild: discord.Guild, channel: discord.TextChannel):
    config = await get_config(bot, guild.id)

    if config is None:
        return False

    buttons = await get_buttons(bot, guild.id)

    if not buttons:
        return False

    title = config[5]
    message = config[6]

    embed = discord.Embed(
        title=title,
        description=message,
    )

    embed.set_footer(
        text=f"{guild.name} • Ticket System"
    )

    await channel.send(
        embed=embed,
        view=TicketPanelView(buttons),
    )

    return True


# ============================================================
# CONFIGURATION VIEW
# ============================================================

class TicketConfigView(discord.ui.View):
    def __init__(self, bot, guild_id: int, creator_id: int):
        super().__init__(timeout=600)

        self.bot = bot
        self.guild_id = guild_id
        self.creator_id = creator_id

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "🔒 This configuration panel belongs to another administrator. "
                "Only the administrator who created this panel can edit it.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            return False

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Administrator permission required.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Channel",
        emoji="📢",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def channel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_message(
            "📢 Select the channel where the ticket panel should be posted.",
            view=TicketPanelChannelView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Category",
        emoji="📁",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def category_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_message(
            "📁 Select the category where ticket channels should be created.",
            view=TicketCategoryView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Staff",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def staff_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_message(
            "🛡️ Select the role that should have access to tickets.",
            view=TicketStaffRoleView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Messages",
        emoji="📝",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def messages_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        modal = TicketPanelMessageModal(
            self.bot,
            self.guild_id,
        )

        config = await get_config(
            self.bot,
            self.guild_id,
        )

        if config:
            modal.title_input.default = config[5]
            modal.message_input.default = config[6]

        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Buttons",
        emoji="🔘",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def buttons_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        buttons = await get_buttons(
            self.bot,
            self.guild_id,
        )

        embed = discord.Embed(
            title="🔘 Ticket Buttons",
            description=(
                "Manage the buttons that users can use "
                "to create tickets."
            ),
        )

        if buttons:
            lines = []

            for index, ticket_button in enumerate(buttons, start=1):
                (
                    button_id,
                    guild_id,
                    name,
                    emoji,
                    color,
                    prefix,
                    opening_message,
                    position,
                ) = ticket_button

                display_emoji = emoji or "🎫"

                lines.append(
                    f"**{index}.** {display_emoji} **{name}**\n"
                    f"└ Channel: `{prefix}-username`"
                )

            embed.description = "\n\n".join(lines)
        else:
            embed.description = (
                "No ticket buttons have been created yet."
            )

        await interaction.response.send_message(
            embed=embed,
            view=TicketButtonsManagementView(
                self.bot,
                self.guild_id,
                self.creator_id,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Test",
        emoji="🧪",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def test_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        config = await get_config(
            self.bot,
            self.guild_id,
        )

        if not config or not config[2]:
            await interaction.response.send_message(
                "❌ Configure the panel channel first.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(config[2])

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ The configured panel channel no longer exists.",
                ephemeral=True,
            )
            return

        success = await send_ticket_panel(
            self.bot,
            interaction.guild,
            channel,
        )

        if not success:
            await interaction.response.send_message(
                "❌ Add at least one ticket button before testing.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🧪 Test ticket panel sent to {channel.mention}.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Save",
        emoji="💾",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def save_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        config = await get_config(
            self.bot,
            self.guild_id,
        )

        if not config:
            await interaction.response.send_message(
                "❌ Configuration not found.",
                ephemeral=True,
            )
            return

        missing = []

        if not config[2]:
            missing.append("📢 Panel Channel")

        if not config[3]:
            missing.append("📁 Ticket Category")

        if not config[4]:
            missing.append("🛡️ Staff Role")

        buttons = await get_buttons(
            self.bot,
            self.guild_id,
        )

        if not buttons:
            missing.append("🔘 At least one Ticket Button")

        if missing:
            await interaction.response.send_message(
                "❌ **Configuration incomplete.**\n\n"
                + "\n".join(f"• {item}" for item in missing),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✅ Ticket configuration is complete!\n\n"
            "Use `/enable ticket` to activate the ticket system.",
            ephemeral=True,
        )


# ============================================================
# BUTTON MANAGEMENT VIEW
# ============================================================

class TicketButtonsManagementView(discord.ui.View):
    def __init__(self, bot, guild_id: int, creator_id: int):
        super().__init__(timeout=300)

        self.bot = bot
        self.guild_id = guild_id
        self.creator_id = creator_id

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "🔒 This configuration panel belongs to another administrator. "
                "Only the administrator who created this panel can edit it.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Add Button",
        emoji="➕",
        style=discord.ButtonStyle.success,
    )
    async def add_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            AddTicketButtonModal(
                self.bot,
                self.guild_id,
            )
        )

    @discord.ui.button(
        label="Remove Button",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
    )
    async def remove_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        buttons = await get_buttons(
            self.bot,
            self.guild_id,
        )

        if not buttons:
            await interaction.response.send_message(
                "❌ There are no buttons to remove.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🗑️ Select the button you want to remove.",
            view=RemoveTicketButtonView(buttons),
            ephemeral=True,
        )


# ============================================================
# TICKET COG
# ============================================================

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --------------------------------------------------------
    # /ticket config
    # --------------------------------------------------------

    ticket_group = app_commands.Group(
        name="ticket",
        description="Configure the ticket system.",
    )

    @ticket_group.command(
        name="config",
        description="Open the ticket configuration panel.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_config(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        await ensure_config(
            self.bot,
            interaction.guild.id,
        )

        config = await get_config(
            self.bot,
            interaction.guild.id,
        )

        buttons = await get_buttons(
            self.bot,
            interaction.guild.id,
        )

        embed = self.build_config_embed(
            interaction.guild,
            config,
            buttons,
        )

        view = TicketConfigView(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
        )

        await interaction.response.send_message(
            embed=embed,
            view=view,
        )

    # --------------------------------------------------------
    # /enable ticket
    # --------------------------------------------------------

    enable_group = app_commands.Group(
        name="enable",
        description="Enable a Cody system.",
    )

    @enable_group.command(
        name="ticket",
        description="Enable the ticket system.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def enable_ticket(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            return

        config = await get_config(
            self.bot,
            interaction.guild.id,
        )

        if not config:
            await ensure_config(
                self.bot,
                interaction.guild.id,
            )

            await interaction.response.send_message(
                "❌ Configure the ticket system first with `/ticket config`.",
                ephemeral=True,
            )
            return

        buttons = await get_buttons(
            self.bot,
            interaction.guild.id,
        )

        missing = []

        if not config[2]:
            missing.append("📢 Panel Channel")

        if not config[3]:
            missing.append("📁 Ticket Category")

        if not config[4]:
            missing.append("🛡️ Staff Role")

        if not buttons:
            missing.append("🔘 Ticket Button")

        if missing:
            await interaction.response.send_message(
                "❌ **You can't enable tickets yet.**\n\n"
                "Missing:\n"
                + "\n".join(f"• {item}" for item in missing)
                + "\n\nRun `/ticket config` to finish the setup.",
                ephemeral=True,
            )
            return

        await self.bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET enabled = 1
            WHERE guild_id = ?
            """,
            (interaction.guild.id,),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            "🎫 **Ticket system enabled!**\n\n"
            "Your configured ticket panel is now active.",
        )

    # --------------------------------------------------------
    # /disable ticket
    # --------------------------------------------------------

    disable_group = app_commands.Group(
        name="disable",
        description="Disable a Cody system.",
    )

    @disable_group.command(
        name="ticket",
        description="Disable the ticket system.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_ticket(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            return

        await ensure_config(
            self.bot,
            interaction.guild.id,
        )

        await self.bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET enabled = 0
            WHERE guild_id = ?
            """,
            (interaction.guild.id,),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            "🔴 **Ticket system disabled.**\n\n"
            "Your configuration has been preserved.",
        )

    # --------------------------------------------------------
    # CONFIG EMBED
    # --------------------------------------------------------

    def build_config_embed(
        self,
        guild: discord.Guild,
        config,
        buttons,
    ):
        enabled = bool(config[1])

        channel = (
            guild.get_channel(config[2])
            if config[2]
            else None
        )

        category = (
            guild.get_channel(config[3])
            if config[3]
            else None
        )

        role = (
            guild.get_role(config[4])
            if config[4]
            else None
        )

        embed = discord.Embed(
            title="🎫 Ticket Configuration",
            description=(
                "Configure every part of your ticket system "
                "using the buttons below."
            ),
        )

        embed.add_field(
            name="📊 Status",
            value=(
                "🟢 Enabled"
                if enabled
                else "🔴 Disabled"
            ),
            inline=False,
        )

        embed.add_field(
            name="📢 Panel Channel",
            value=(
                channel.mention
                if isinstance(channel, discord.TextChannel)
                else "❌ Not configured"
            ),
            inline=True,
        )

        embed.add_field(
            name="📁 Ticket Category",
            value=(
                category.name
                if isinstance(category, discord.CategoryChannel)
                else "❌ Not configured"
            ),
            inline=True,
        )

        embed.add_field(
            name="🛡️ Staff Role",
            value=(
                role.mention
                if role
                else "❌ Not configured"
            ),
            inline=True,
        )

        embed.add_field(
            name="📝 Panel Title",
            value=config[5][:1024],
            inline=False,
        )

        embed.add_field(
            name="💬 Panel Message",
            value=config[6][:1024],
            inline=False,
        )

        if buttons:
            button_text = []

            for index, ticket_button in enumerate(
                buttons,
                start=1,
            ):
                (
                    button_id,
                    guild_id,
                    name,
                    emoji,
                    color,
                    prefix,
                    opening_message,
                    position,
                ) = ticket_button

                button_text.append(
                    f"{index}. {emoji or '🎫'} **{name}** "
                    f"→ `{prefix}-username`"
                )

            embed.add_field(
                name=f"🔘 Ticket Buttons ({len(buttons)})",
                value="\n".join(button_text)[:1024],
                inline=False,
            )
        else:
            embed.add_field(
                name="🔘 Ticket Buttons",
                value="❌ No buttons configured.",
                inline=False,
            )

        embed.set_footer(
            text=(
                f"Configuration created by "
                f"{guild.name}"
            )
        )

        return embed

    # --------------------------------------------------------
    # ERROR HANDLER
    # --------------------------------------------------------

    @ticket_config.error
    @enable_ticket.error
    @disable_ticket.error
    async def ticket_command_error(
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
                "to use this command."
            )
        else:
            message = (
                "❌ An error occurred while processing "
                "the ticket command."
            )

            print(
                f"Ticket command error: {error}"
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
    await setup_ticket_database(bot)

    await bot.add_cog(Tickets(bot))

    # Register persistent close-ticket view.
    bot.add_view(CloseTicketView())

    # Register existing ticket buttons.
    try:
        cursor = await bot.database.connection.execute(
            """
            SELECT
                id,
                guild_id,
                name,
                emoji,
                color,
                prefix,
                opening_message,
                position
            FROM ticket_buttons
            ORDER BY id ASC
            """
        )

        buttons = await cursor.fetchall()

        for button in buttons:
            (
                button_id,
                guild_id,
                name,
                emoji,
                color,
                prefix,
                opening_message,
                position,
            ) = button

            bot.add_view(
                TicketPanelView([button]),
            )

    except Exception as error:
        print(
            f"Could not restore ticket buttons: {error}"
        )
