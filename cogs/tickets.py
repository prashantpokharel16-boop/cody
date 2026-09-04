import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# DATABASE SCHEMA
# ============================================================

TICKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticket_configs (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    panel_channel_id INTEGER,
    panel_message_id INTEGER,
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


# ============================================================
# DATABASE SETUP / MIGRATION
# ============================================================

async def setup_ticket_database(bot):
    await bot.database.connection.executescript(TICKET_SCHEMA)

    # Safely migrate older ticket_configs tables.
    cursor = await bot.database.connection.execute(
        "PRAGMA table_info(ticket_configs)"
    )

    columns = await cursor.fetchall()

    existing_columns = {
        column[1]
        for column in columns
    }

    if "panel_message_id" not in existing_columns:
        await bot.database.connection.execute(
            """
            ALTER TABLE ticket_configs
            ADD COLUMN panel_message_id INTEGER
            """
        )

    await bot.database.connection.commit()


# ============================================================
# HELPERS
# ============================================================

def clean_name(value: str) -> str:
    value = value.lower().strip()

    value = re.sub(
        r"[^a-z0-9_-]+",
        "-",
        value,
    )

    value = re.sub(
        r"-+",
        "-",
        value,
    )

    value = value.strip("-_")

    if not value:
        value = "ticket"

    return value[:45]


def get_button_style(color: str) -> discord.ButtonStyle:
    colors = {
        "blue": discord.ButtonStyle.primary,
        "gray": discord.ButtonStyle.secondary,
        "grey": discord.ButtonStyle.secondary,
        "green": discord.ButtonStyle.success,
        "red": discord.ButtonStyle.danger,
    }

    return colors.get(
        color.lower(),
        discord.ButtonStyle.primary,
    )


def valid_emoji(emoji: Optional[str]) -> Optional[str]:
    if not emoji:
        return None

    emoji = emoji.strip()

    if not emoji:
        return None

    return emoji[:20]


async def get_config(bot, guild_id: int):
    cursor = await bot.database.connection.execute(
        """
        SELECT
            guild_id,
            enabled,
            panel_channel_id,
            panel_message_id,
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


async def ensure_config(bot, guild_id: int):
    config = await get_config(
        bot,
        guild_id,
    )

    if config is None:
        await bot.database.connection.execute(
            """
            INSERT INTO ticket_configs (
                guild_id
            )
            VALUES (?)
            """,
            (guild_id,),
        )

        await bot.database.connection.commit()

        config = await get_config(
            bot,
            guild_id,
        )

    return config


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


async def get_open_ticket(
    bot,
    guild_id: int,
    user_id: int,
):
    cursor = await bot.database.connection.execute(
        """
        SELECT
            channel_id
        FROM ticket_channels
        WHERE guild_id = ?
          AND user_id = ?
          AND closed = 0
        """,
        (
            guild_id,
            user_id,
        ),
    )

    return await cursor.fetchone()


# ============================================================
# TICKET CLOSE BUTTON
# ============================================================

class CloseTicketButton(discord.ui.Button):

    def __init__(self):
        super().__init__(
            label="Close Ticket",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id="cody_ticket_close",
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        bot = interaction.client

        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not isinstance(
            interaction.channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "❌ This isn't a ticket channel.",
                ephemeral=True,
            )
            return

        channel = interaction.channel

        cursor = await bot.database.connection.execute(
            """
            SELECT
                user_id
            FROM ticket_channels
            WHERE channel_id = ?
              AND closed = 0
            """,
            (channel.id,),
        )

        ticket = await cursor.fetchone()

        if ticket is None:
            await interaction.response.send_message(
                "❌ This isn't an active ticket.",
                ephemeral=True,
            )
            return

        owner_id = ticket[0]

        config = await get_config(
            bot,
            interaction.guild.id,
        )

        staff_role_id = (
            config[5]
            if config
            else None
        )

        is_owner = (
            interaction.user.id == owner_id
        )

        is_staff = False

        if (
            staff_role_id
            and isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            is_staff = any(
                role.id == staff_role_id
                for role in interaction.user.roles
            )

        is_admin = (
            isinstance(
                interaction.user,
                discord.Member,
            )
            and interaction.user.guild_permissions.administrator
        )

        if not (
            is_owner
            or is_staff
            or is_admin
        ):
            await interaction.response.send_message(
                "🔒 You don't have permission to close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🔒 Closing ticket...",
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
            )
        except Exception:
            pass

        try:
            await channel.set_permissions(
                interaction.guild.default_role,
                view_channel=False,
            )

            owner = interaction.guild.get_member(
                owner_id
            )

            if owner:
                await channel.set_permissions(
                    owner,
                    view_channel=False,
                    send_messages=False,
                )

        except Exception:
            pass

        await channel.send(
            f"🔒 **Ticket Closed**\n\n"
            f"Closed by {interaction.user.mention}."
        )


class CloseTicketView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None,
        )

        self.add_item(
            CloseTicketButton()
        )


# ============================================================
# TICKET CREATE BUTTON
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
            emoji=valid_emoji(emoji),
            style=get_button_style(color),
            custom_id=f"cody_ticket_create:{button_id}",
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        bot = interaction.client

        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        # ----------------------------------------------------
        # Check system
        # ----------------------------------------------------

        config = await get_config(
            bot,
            guild.id,
        )

        if not config or not config[1]:
            await interaction.response.send_message(
                "🔴 The ticket system is currently disabled.",
                ephemeral=True,
            )
            return

        category_id = config[4]
        staff_role_id = config[5]

        if not category_id:
            await interaction.response.send_message(
                "❌ The ticket category hasn't been configured.",
                ephemeral=True,
            )
            return

        category = guild.get_channel(
            category_id
        )

        if not isinstance(
            category,
            discord.CategoryChannel,
        ):
            await interaction.response.send_message(
                "❌ The configured ticket category no longer exists.",
                ephemeral=True,
            )
            return

        # ----------------------------------------------------
        # Get button
        # ----------------------------------------------------

        ticket_button = await get_button(
            bot,
            self.ticket_button_id,
        )

        if ticket_button is None:
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
        ) = ticket_button

        # ----------------------------------------------------
        # Check existing ticket
        # ----------------------------------------------------

        existing = await get_open_ticket(
            bot,
            guild.id,
            interaction.user.id,
        )

        if existing:
            existing_channel = guild.get_channel(
                existing[0]
            )

            if existing_channel:
                await interaction.response.send_message(
                    "⚠️ You already have an open ticket:\n"
                    f"{existing_channel.mention}",
                    ephemeral=True,
                )
                return

            # Channel disappeared, clean database.
            await bot.database.connection.execute(
                """
                UPDATE ticket_channels
                SET closed = 1
                WHERE channel_id = ?
                """,
                (existing[0],),
            )

            await bot.database.connection.commit()

        # ----------------------------------------------------
        # Bot permissions
        # ----------------------------------------------------

        me = guild.me

        if me is None:
            await interaction.response.send_message(
                "❌ I couldn't find my server member.",
                ephemeral=True,
            )
            return

        if not me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "❌ I need **Manage Channels** permission "
                "to create tickets.",
                ephemeral=True,
            )
            return

        # ----------------------------------------------------
        # Channel name
        # ----------------------------------------------------

        prefix_name = clean_name(
            prefix or button_name
        )

        username = clean_name(
            getattr(
                interaction.user,
                "display_name",
                interaction.user.name,
            )
        )

        channel_name = (
            f"{prefix_name}-{username}"
        )[:100]

        # ----------------------------------------------------
        # Permissions
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

        staff_role = None

        if staff_role_id:
            staff_role = guild.get_role(
                staff_role_id
            )

            if staff_role:
                overwrites[staff_role] = (
                    discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                    )
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
                    f"Ticket owner: "
                    f"{interaction.user} | "
                    f"Ticket type: {button_name}"
                ),
                reason=(
                    f"Ticket created using "
                    f"{button_name}"
                ),
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to create "
                "ticket channels.",
                ephemeral=True,
            )
            return

        except discord.HTTPException as error:
            await interaction.response.send_message(
                f"❌ Discord failed to create the ticket:\n"
                f"`{error}`",
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

        message = (
            opening_message
            or "Welcome {user}!"
        )

        replacements = {
            "{user}": interaction.user.mention,
            "{username}": interaction.user.display_name,
            "{server}": guild.name,
            "{member_count}": str(
                guild.member_count
            ),
        }

        for variable, value in replacements.items():
            message = message.replace(
                variable,
                value,
            )

        # ----------------------------------------------------
        # Embed
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

        embed.add_field(
            name="📁 Category",
            value=category.name,
            inline=True,
        )

        embed.set_thumbnail(
            url=interaction.user.display_avatar.url
        )

        embed.set_footer(
            text=f"Ticket ID: {channel.id}"
        )

        content = interaction.user.mention

        if staff_role:
            content += f" {staff_role.mention}"

        await channel.send(
            content=content,
            embed=embed,
            view=CloseTicketView(),
        )

        await interaction.response.send_message(
            f"🎫 Ticket created: {channel.mention}",
            ephemeral=True,
        )


# ============================================================
# TICKET PANEL
# ============================================================

class TicketPanelView(discord.ui.View):

    def __init__(self, buttons):
        super().__init__(
            timeout=None
        )

        for ticket_button in buttons[:25]:

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

            self.add_item(
                TicketCreateButton(
                    button_id=button_id,
                    name=name,
                    emoji=emoji,
                    color=color,
                )
            )


# ============================================================
# PANEL MESSAGE MODAL
# ============================================================

class TicketPanelMessageModal(
    discord.ui.Modal
):

    def __init__(
        self,
        bot,
        guild_id: int,
        current_title: str,
        current_message: str,
    ):
        super().__init__(
            title="Edit Ticket Panel"
        )

        self.bot = bot
        self.guild_id = guild_id

        self.title_input = discord.ui.TextInput(
            label="Panel Title",
            placeholder="🎫 Create a Ticket",
            default=current_title,
            max_length=256,
            required=True,
        )

        self.message_input = discord.ui.TextInput(
            label="Panel Message",
            placeholder="Select a department below...",
            default=current_message,
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )

        self.add_item(
            self.title_input
        )

        self.add_item(
            self.message_input
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
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
# PANEL CHANNEL SELECT
# ============================================================

class TicketPanelChannelSelect(
    discord.ui.ChannelSelect
):

    def __init__(self):
        super().__init__(
            placeholder="Select panel channel...",
            channel_types=[
                discord.ChannelType.text
            ],
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
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
            f"📢 Panel channel set to {channel.mention}.",
            ephemeral=True,
        )


class TicketPanelChannelView(
    discord.ui.View
):

    def __init__(self):
        super().__init__(
            timeout=120
        )

        self.add_item(
            TicketPanelChannelSelect()
        )


# ============================================================
# CATEGORY SELECT
# ============================================================

class TicketCategorySelect(
    discord.ui.ChannelSelect
):

    def __init__(self):
        super().__init__(
            placeholder="Select ticket category...",
            channel_types=[
                discord.ChannelType.category
            ],
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
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
            f"📁 Ticket category set to "
            f"**{category.name}**.",
            ephemeral=True,
        )


class TicketCategoryView(
    discord.ui.View
):

    def __init__(self):
        super().__init__(
            timeout=120
        )

        self.add_item(
            TicketCategorySelect()
        )


# ============================================================
# STAFF ROLE SELECT
# ============================================================

class TicketStaffRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(self):
        super().__init__(
            placeholder="Select ticket staff role...",
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
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
            f"🛡️ Ticket staff role set to "
            f"{role.mention}.",
            ephemeral=True,
        )


class TicketStaffRoleView(
    discord.ui.View
):

    def __init__(self):
        super().__init__(
            timeout=120
        )

        self.add_item(
            TicketStaffRoleSelect()
        )


# ============================================================
# ADD BUTTON MODAL
# ============================================================

class AddTicketButtonModal(
    discord.ui.Modal
):

    def __init__(
        self,
        bot,
        guild_id: int,
    ):
        super().__init__(
            title="Add Ticket Button"
        )

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
            max_length=45,
            required=True,
        )

        self.message_input = discord.ui.TextInput(
            label="Opening Message",
            placeholder=(
                "Welcome {user}! "
                "Please explain your issue."
            ),
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )

        self.add_item(
            self.name_input
        )

        self.add_item(
            self.emoji_input
        )

        self.add_item(
            self.prefix_input
        )

        self.add_item(
            self.message_input
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        cursor = await self.bot.database.connection.execute(
            """
            SELECT COALESCE(MAX(position), -1)
            FROM ticket_buttons
            WHERE guild_id = ?
            """,
            (self.guild_id,),
        )

        row = await cursor.fetchone()

        position = (
            (row[0] if row else -1)
            + 1
        )

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
                self.name_input.value.strip(),
                valid_emoji(
                    self.emoji_input.value
                ),
                "blue",
                clean_name(
                    self.prefix_input.value
                ),
                self.message_input.value,
                position,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            f"✅ Button **{self.name_input.value}** added.",
            ephemeral=True,
        )


# ============================================================
# EDIT BUTTON MODAL
# ============================================================

class EditTicketButtonModal(
    discord.ui.Modal
):

    def __init__(
        self,
        bot,
        button,
    ):
        super().__init__(
            title="Edit Ticket Button"
        )

        self.bot = bot
        self.button_id = button[0]

        self.name_input = discord.ui.TextInput(
            label="Button Name",
            default=button[2],
            max_length=80,
            required=True,
        )

        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            default=button[3] or "",
            max_length=20,
            required=False,
        )

        self.prefix_input = discord.ui.TextInput(
            label="Channel Prefix",
            default=button[5],
            max_length=45,
            required=True,
        )

        self.message_input = discord.ui.TextInput(
            label="Opening Message",
            default=button[6],
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )

        self.add_item(
            self.name_input
        )

        self.add_item(
            self.emoji_input
        )

        self.add_item(
            self.prefix_input
        )

        self.add_item(
            self.message_input
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        await self.bot.database.connection.execute(
            """
            UPDATE ticket_buttons
            SET name = ?,
                emoji = ?,
                prefix = ?,
                opening_message = ?
            WHERE id = ?
            """,
            (
                self.name_input.value.strip(),
                valid_emoji(
                    self.emoji_input.value
                ),
                clean_name(
                    self.prefix_input.value
                ),
                self.message_input.value,
                self.button_id,
            ),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            "✅ Ticket button updated.",
            ephemeral=True,
        )


# ============================================================
# BUTTON EDIT SELECT
# ============================================================

class EditTicketButtonSelect(
    discord.ui.Select
):

    def __init__(
        self,
        buttons,
    ):
        options = []

        for button in buttons[:25]:

            options.append(
                discord.SelectOption(
                    label=button[2][:100],
                    value=str(button[0]),
                    emoji=valid_emoji(
                        button[3]
                    ),
                )
            )

        super().__init__(
            placeholder="Select a button to edit...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        bot = interaction.client

        button_id = int(
            self.values[0]
        )

        button = await get_button(
            bot,
            button_id,
        )

        if button is None:
            await interaction.response.send_message(
                "❌ Button not found.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            EditTicketButtonModal(
                bot,
                button,
            )
        )


class EditTicketButtonView(
    discord.ui.View
):

    def __init__(
        self,
        buttons,
    ):
        super().__init__(
            timeout=120
        )

        self.add_item(
            EditTicketButtonSelect(
                buttons
            )
        )


# ============================================================
# BUTTON REMOVE SELECT
# ============================================================

class RemoveTicketButtonSelect(
    discord.ui.Select
):

    def __init__(
        self,
        buttons,
    ):
        options = []

        for button in buttons[:25]:

            options.append(
                discord.SelectOption(
                    label=button[2][:100],
                    value=str(button[0]),
                    emoji=valid_emoji(
                        button[3]
                    ),
                )
            )

        super().__init__(
            placeholder="Select a button to remove...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        bot = interaction.client

        button_id = int(
            self.values[0]
        )

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


class RemoveTicketButtonView(
    discord.ui.View
):

    def __init__(
        self,
        buttons,
    ):
        super().__init__(
            timeout=120
        )

        self.add_item(
            RemoveTicketButtonSelect(
                buttons
            )
        )


# ============================================================
# BUTTON MANAGEMENT
# ============================================================

class TicketButtonsManagementView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        guild_id: int,
        creator_id: int,
    ):
        super().__init__(
            timeout=300
        )

        self.bot = bot
        self.guild_id = guild_id
        self.creator_id = creator_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
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

        return True

    @discord.ui.button(
        label="Add Button",
        emoji="➕",
        style=discord.ButtonStyle.success,
        row=0,
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
        label="Edit Button",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def edit_button(
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
                "❌ No ticket buttons exist.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✏️ Select the button you want to edit.",
            view=EditTicketButtonView(
                buttons
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Remove Button",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=0,
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
                "❌ No ticket buttons exist.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🗑️ Select the button you want to remove.",
            view=RemoveTicketButtonView(
                buttons
            ),
            ephemeral=True,
        )


# ============================================================
# SEND / UPDATE PANEL
# ============================================================

async def send_ticket_panel(
    bot,
    guild: discord.Guild,
    channel: discord.TextChannel,
):
    config = await get_config(
        bot,
        guild.id,
    )

    if not config:
        return None

    buttons = await get_buttons(
        bot,
        guild.id,
    )

    if not buttons:
        return None

    embed = discord.Embed(
        title=config[6],
        description=config[7],
    )

    embed.set_footer(
        text=f"{guild.name} • Ticket System"
    )

    message = await channel.send(
        embed=embed,
        view=TicketPanelView(buttons),
    )

    await bot.database.connection.execute(
        """
        UPDATE ticket_configs
        SET panel_message_id = ?
        WHERE guild_id = ?
        """,
        (
            message.id,
            guild.id,
        ),
    )

    await bot.database.connection.commit()

    return message


# ============================================================
# CONFIGURATION PANEL
# ============================================================

class TicketConfigView(
    discord.ui.View
):

    def __init__(
        self,
        bot,
        guild_id: int,
        creator_id: int,
    ):
        super().__init__(
            timeout=900
        )

        self.bot = bot
        self.guild_id = guild_id
        self.creator_id = creator_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
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
                "❌ Administrator permission required.",
                ephemeral=True,
            )
            return False

        return True

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

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
            "📢 Select the channel where the ticket "
            "panel should appear.",
            view=TicketPanelChannelView(),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # CATEGORY
    # --------------------------------------------------------

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
            "📁 Select the category where ticket "
            "channels should be created.",
            view=TicketCategoryView(),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # STAFF ROLE
    # --------------------------------------------------------

    @discord.ui.button(
        label="Staff Role",
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
            "🛡️ Select the role that should have "
            "access to tickets.",
            view=TicketStaffRoleView(),
            ephemeral=True,
        )

    # --------------------------------------------------------
    # MESSAGES
    # --------------------------------------------------------

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

        await interaction.response.send_modal(
            TicketPanelMessageModal(
                self.bot,
                self.guild_id,
                config[6],
                config[7],
            )
        )

    # --------------------------------------------------------
    # BUTTONS
    # --------------------------------------------------------

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
        )

        if buttons:
            lines = []

            for index, ticket_button in enumerate(
                buttons,
                start=1,
            ):
                name = ticket_button[2]
                emoji = ticket_button[3]
                prefix = ticket_button[5]

                lines.append(
                    f"**{index}.** "
                    f"{emoji or '🎫'} "
                    f"**{name}**\n"
                    f"└ Channel: "
                    f"`{prefix}-username`"
                )

            embed.description = (
                "\n\n".join(lines)
            )

        else:
            embed.description = (
                "❌ No ticket buttons configured."
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

    # --------------------------------------------------------
    # TEST
    # --------------------------------------------------------

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

        if not config:
            await interaction.response.send_message(
                "❌ Configuration not found.",
                ephemeral=True,
            )
            return

        if not config[2]:
            await interaction.response.send_message(
                "❌ Configure the panel channel first.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(
            config[2]
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "❌ The configured panel channel "
                "doesn't exist anymore.",
                ephemeral=True,
            )
            return

        result = await send_ticket_panel(
            self.bot,
            interaction.guild,
            channel,
        )

        if result is None:
            await interaction.response.send_message(
                "❌ Add at least one ticket button first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🧪 Test panel sent to {channel.mention}.",
            ephemeral=True,
        )

    # --------------------------------------------------------
    # ENABLE
    # --------------------------------------------------------

    @discord.ui.button(
        label="Enable Ticket",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def enable_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        config = await get_config(
            self.bot,
            self.guild_id,
        )

        buttons = await get_buttons(
            self.bot,
            self.guild_id,
        )

        missing = []

        if not config:
            missing.append("Configuration")

        else:
            if not config[2]:
                missing.append("📢 Panel Channel")

            if not config[4]:
                missing.append("📁 Ticket Category")

            if not config[5]:
                missing.append("🛡️ Staff Role")

        if not buttons:
            missing.append("🔘 Ticket Button")

        if missing:
            await interaction.response.send_message(
                "❌ **Setup isn't complete.**\n\n"
                + "\n".join(
                    f"• {item}"
                    for item in missing
                ),
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(
            config[2]
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "❌ The panel channel no longer exists.",
                ephemeral=True,
            )
            return

        # Enable.
        await self.bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET enabled = 1
            WHERE guild_id = ?
            """,
            (self.guild_id,),
        )

        await self.bot.database.connection.commit()

        # Send panel automatically.
        panel = await send_ticket_panel(
            self.bot,
            interaction.guild,
            channel,
        )

        if panel:
            await interaction.response.send_message(
                "🟢 **Ticket system enabled!**\n\n"
                f"Ticket panel posted in {channel.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "🟢 **Ticket system enabled!**",
                ephemeral=True,
            )

    # --------------------------------------------------------
    # DISABLE
    # --------------------------------------------------------

    @discord.ui.button(
        label="Disable Ticket",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        row=2,
    )
    async def disable_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.bot.database.connection.execute(
            """
            UPDATE ticket_configs
            SET enabled = 0
            WHERE guild_id = ?
            """,
            (self.guild_id,),
        )

        await self.bot.database.connection.commit()

        await interaction.response.send_message(
            "🔴 **Ticket system disabled.**\n\n"
            "Your configuration has been preserved.",
            ephemeral=True,
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
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        config = await get_config(
            self.bot,
            self.guild_id,
        )

        buttons = await get_buttons(
            self.bot,
            self.guild_id,
        )

        embed = build_config_embed(
            interaction.guild,
            config,
            buttons,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )


# ============================================================
# BUILD CONFIG EMBED
# ============================================================

def build_config_embed(
    guild: discord.Guild,
    config,
    buttons,
):
    if not config:
        return discord.Embed(
            title="🎫 Ticket Configuration",
            description="Configuration not found.",
        )

    enabled = bool(config[1])

    panel_channel = (
        guild.get_channel(config[2])
        if config[2]
        else None
    )

    category = (
        guild.get_channel(config[4])
        if config[4]
        else None
    )

    staff_role = (
        guild.get_role(config[5])
        if config[5]
        else None
    )

    embed = discord.Embed(
        title="🎫 Ticket Configuration",
        description=(
            "Configure your complete ticket system "
            "using the controls below."
        ),
    )

    embed.add_field(
        name="📊 Status",
        value=(
            "🟢 **Enabled**"
            if enabled
            else "🔴 **Disabled**"
        ),
        inline=False,
    )

    embed.add_field(
        name="📢 Panel Channel",
        value=(
            panel_channel.mention
            if isinstance(
                panel_channel,
                discord.TextChannel,
            )
            else "❌ Not configured"
        ),
        inline=True,
    )

    embed.add_field(
        name="📁 Ticket Category",
        value=(
            category.name
            if isinstance(
                category,
                discord.CategoryChannel,
            )
            else "❌ Not configured"
        ),
        inline=True,
    )

    embed.add_field(
        name="🛡️ Staff Role",
        value=(
            staff_role.mention
            if staff_role
            else "❌ Not configured"
        ),
        inline=True,
    )

    embed.add_field(
        name="📝 Panel Title",
        value=config[6][:1024],
        inline=False,
    )

    embed.add_field(
        name="💬 Panel Message",
        value=config[7][:1024],
        inline=False,
    )

    if buttons:
        lines = []

        for index, button in enumerate(
            buttons[:25],
            start=1,
        ):
            lines.append(
                f"**{index}.** "
                f"{button[3] or '🎫'} "
                f"**{button[2]}** "
                f"→ `{button[5]}-username`"
            )

        embed.add_field(
            name=f"🔘 Ticket Buttons ({len(buttons)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )

    else:
        embed.add_field(
            name="🔘 Ticket Buttons",
            value="❌ No buttons configured.",
            inline=False,
        )

    embed.add_field(
        name="📌 Variables",
        value=(
            "`{user}` • `{username}` • `{server}` • "
            "`{member_count}`"
        ),
        inline=False,
    )

    embed.set_footer(
        text=f"{guild.name} • Ticket Configuration"
    )

    return embed


# ============================================================
# TICKET COG
# ============================================================

class Tickets(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    ticket_group = app_commands.Group(
        name="ticket",
        description="Configure the ticket system.",
    )

    # --------------------------------------------------------
    # /ticket config
    # --------------------------------------------------------

    @ticket_group.command(
        name="config",
        description="Open the ticket configuration panel.",
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def ticket_config(
        self,
        interaction: discord.Interaction,
    ):
        if not interaction.guild:
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

        embed = build_config_embed(
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
    # ERROR HANDLER
    # --------------------------------------------------------

    @ticket_config.error
    async def ticket_config_error(
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
                "to configure tickets."
            )

        else:
            print(
                f"Ticket configuration error: {error}"
            )

            message = (
                "❌ Something went wrong while opening "
                "the ticket configuration."
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

    # Create / migrate ticket database.
    await setup_ticket_database(bot)

    # Add cog.
    await bot.add_cog(
        Tickets(bot)
    )

    # Restore close-ticket button.
    bot.add_view(
        CloseTicketView()
    )

    # Restore existing ticket creation buttons.
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
            bot.add_view(
                TicketPanelView(
                    [button]
                )
            )

        print(
            f"Restored {len(buttons)} ticket button(s)."
        )

    except Exception as error:
        print(
            f"Could not restore ticket buttons: {error}"
        )
