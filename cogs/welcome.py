import io
import math

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter


DEFAULT_MESSAGE = "Welcome {user} to {server}! 🎉"

CARD_WIDTH = 900
CARD_HEIGHT = 500
FRAME_COUNT = 16
FRAME_DURATION = 80


# ---------------------------------------------------------
# Fonts
# ---------------------------------------------------------

def get_font(size: int, bold: bool = False):
    if bold:
        fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]

    for font_path in fonts:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            continue

    return ImageFont.load_default()


# ---------------------------------------------------------
# Text helpers
# ---------------------------------------------------------

def draw_centered(
    draw,
    text,
    y,
    font,
    fill,
):
    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font,
    )

    width = bbox[2] - bbox[0]

    x = (CARD_WIDTH - width) // 2

    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
    )


# ---------------------------------------------------------
# Avatar
# ---------------------------------------------------------

def make_circular_avatar(
    avatar: Image.Image,
    size: int = 190,
):
    avatar = avatar.convert("RGBA")

    avatar = ImageOps.fit(
        avatar,
        (size, size),
        method=Image.Resampling.LANCZOS,
    )

    mask = Image.new(
        "L",
        (size, size),
        0,
    )

    mask_draw = ImageDraw.Draw(mask)

    mask_draw.ellipse(
        (0, 0, size - 1, size - 1),
        fill=255,
    )

    avatar.putalpha(mask)

    return avatar


# ---------------------------------------------------------
# Download user's Discord avatar
# ---------------------------------------------------------

async def download_avatar(member: discord.Member):
    try:
        avatar_url = member.display_avatar.replace(
            format="png",
            size=512,
        ).url

        timeout = aiohttp.ClientTimeout(
            total=10
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                avatar_url
            ) as response:

                if response.status != 200:
                    return None

                data = await response.read()

        return Image.open(
            io.BytesIO(data)
        ).convert("RGBA")

    except Exception:
        return None


# ---------------------------------------------------------
# Create animated welcome GIF
# ---------------------------------------------------------

def create_welcome_gif(
    member: discord.Member,
    avatar: Image.Image | None,
    custom_message: str,
):
    display_name = member.display_name
    server_name = member.guild.name
    member_count = member.guild.member_count or 0

    # -----------------------------------------------------
    # Format message variables
    # -----------------------------------------------------

    formatted_message = (
        custom_message
        .replace(
            "{user}",
            f"@{display_name}",
        )
        .replace(
            "{username}",
            member.name,
        )
        .replace(
            "{server}",
            server_name,
        )
        .replace(
            "{member_count}",
            str(member_count),
        )
    )

    # -----------------------------------------------------
    # Avatar fallback
    # -----------------------------------------------------

    if avatar is None:
        avatar = Image.new(
            "RGBA",
            (512, 512),
            (55, 60, 80, 255),
        )

        avatar_draw = ImageDraw.Draw(
            avatar
        )

        avatar_draw.ellipse(
            (20, 20, 492, 492),
            fill=(80, 90, 120, 255),
        )

        avatar_draw.text(
            (256, 256),
            "?",
            anchor="mm",
            font=get_font(150, True),
            fill=(255, 255, 255, 255),
        )

    avatar = make_circular_avatar(
        avatar,
        190,
    )

    # -----------------------------------------------------
    # Fonts
    # -----------------------------------------------------

    small_font = get_font(
        22,
        True,
    )

    welcome_font = get_font(
        38,
        True,
    )

    name_font = get_font(
        54,
        True,
    )

    server_font = get_font(
        30,
        True,
    )

    message_font = get_font(
        19,
        False,
    )

    frames = []

    # -----------------------------------------------------
    # Generate frames
    # -----------------------------------------------------

    for frame_number in range(
        FRAME_COUNT
    ):

        frame = Image.new(
            "RGBA",
            (
                CARD_WIDTH,
                CARD_HEIGHT,
            ),
            (10, 13, 25, 255),
        )

        draw = ImageDraw.Draw(
            frame
        )

        # -------------------------------------------------
        # Background gradient
        # -------------------------------------------------

        for y in range(
            CARD_HEIGHT
        ):

            ratio = y / CARD_HEIGHT

            r = int(
                8
                + ratio * 20
            )

            g = int(
                12
                + ratio * 12
            )

            b = int(
                30
                + ratio * 50
            )

            draw.line(
                (
                    0,
                    y,
                    CARD_WIDTH,
                    y,
                ),
                fill=(
                    r,
                    g,
                    b,
                    255,
                ),
            )

        # -------------------------------------------------
        # Moving particles
        # -------------------------------------------------

        particle_layer = Image.new(
            "RGBA",
            (
                CARD_WIDTH,
                CARD_HEIGHT,
            ),
            (0, 0, 0, 0),
        )

        particle_draw = ImageDraw.Draw(
            particle_layer
        )

        for particle in range(35):

            angle = (
                particle * 0.9
                + frame_number * 0.12
            )

            radius = (
                160
                + (particle * 31) % 330
            )

            x = int(
                CARD_WIDTH / 2
                + math.cos(angle)
                * radius
            )

            y = int(
                CARD_HEIGHT / 2
                + math.sin(angle)
                * radius
                * 0.55
            )

            if (
                0 <= x < CARD_WIDTH
                and 0 <= y < CARD_HEIGHT
            ):

                size = (
                    2
                    + particle % 4
                )

                particle_draw.ellipse(
                    (
                        x - size,
                        y - size,
                        x + size,
                        y + size,
                    ),
                    fill=(
                        80,
                        180,
                        255,
                        190,
                    ),
                )

        particle_layer = particle_layer.filter(
            ImageFilter.GaussianBlur(
                1
            )
        )

        frame = Image.alpha_composite(
            frame,
            particle_layer,
        )

        draw = ImageDraw.Draw(
            frame
        )

        # -------------------------------------------------
        # Main glass card
        # -------------------------------------------------

        draw.rounded_rectangle(
            (
                25,
                25,
                CARD_WIDTH - 25,
                CARD_HEIGHT - 25,
            ),
            radius=35,
            fill=(
                12,
                18,
                35,
                220,
            ),
            outline=(
                80,
                170,
                255,
                255,
            ),
            width=3,
        )

        # -------------------------------------------------
        # Animated border glow
        # -------------------------------------------------

        glow_alpha = int(
            90
            + 80
            * (
                1
                + math.sin(
                    frame_number
                    * 0.6
                )
            )
            / 2
        )

        glow_layer = Image.new(
            "RGBA",
            (
                CARD_WIDTH,
                CARD_HEIGHT,
            ),
            (0, 0, 0, 0),
        )

        glow_draw = ImageDraw.Draw(
            glow_layer
        )

        glow_draw.rounded_rectangle(
            (
                20,
                20,
                CARD_WIDTH - 20,
                CARD_HEIGHT - 20,
            ),
            radius=40,
            outline=(
                70,
                160,
                255,
                glow_alpha,
            ),
            width=10,
        )

        glow_layer = glow_layer.filter(
            ImageFilter.GaussianBlur(
                12
            )
        )

        frame = Image.alpha_composite(
            frame,
            glow_layer,
        )

        draw = ImageDraw.Draw(
            frame
        )

        # -------------------------------------------------
        # Member count badge
        # -------------------------------------------------

        badge_text = (
            f"MEMBER #{member_count}"
        )

        badge_bbox = draw.textbbox(
            (0, 0),
            badge_text,
            font=small_font,
        )

        badge_width = (
            badge_bbox[2]
            - badge_bbox[0]
            + 45
        )

        badge_x = (
            CARD_WIDTH
            - badge_width
        ) // 2

        draw.rounded_rectangle(
            (
                badge_x,
                45,
                badge_x
                + badge_width,
                82,
            ),
            radius=20,
            fill=(
                25,
                38,
                70,
                240,
            ),
            outline=(
                100,
                200,
                255,
                255,
            ),
            width=2,
        )

        draw_centered(
            draw,
            badge_text,
            52,
            small_font,
            (
                220,
                240,
                255,
                255,
            ),
        )

        # -------------------------------------------------
        # Avatar glow
        # -------------------------------------------------

        avatar_center_x = (
            CARD_WIDTH // 2
        )

        avatar_center_y = 180

        glow_radius = int(
            105
            + 7
            * math.sin(
                frame_number
                * 0.7
            )
        )

        avatar_glow = Image.new(
            "RGBA",
            (
                CARD_WIDTH,
                CARD_HEIGHT,
            ),
            (0, 0, 0, 0),
        )

        avatar_glow_draw = ImageDraw.Draw(
            avatar_glow
        )

        avatar_glow_draw.ellipse(
            (
                avatar_center_x
                - glow_radius,
                avatar_center_y
                - glow_radius,
                avatar_center_x
                + glow_radius,
                avatar_center_y
                + glow_radius,
            ),
            fill=(
                40,
                150,
                255,
                170,
            ),
        )

        avatar_glow = avatar_glow.filter(
            ImageFilter.GaussianBlur(
                20
            )
        )

        frame = Image.alpha_composite(
            frame,
            avatar_glow,
        )

        # -------------------------------------------------
        # Animated avatar ring
        # -------------------------------------------------

        draw = ImageDraw.Draw(
            frame
        )

        ring_start = (
            frame_number * 18
        )

        for ring_part in range(
            12
        ):

            angle = math.radians(
                ring_start
                + ring_part * 30
            )

            radius = 103

            x1 = int(
                avatar_center_x
                + math.cos(angle)
                * radius
            )

            y1 = int(
                avatar_center_y
                + math.sin(angle)
                * radius
            )

            x2 = int(
                avatar_center_x
                + math.cos(
                    angle + 0.12
                )
                * radius
            )

            y2 = int(
                avatar_center_y
                + math.sin(
                    angle + 0.12
                )
                * radius
            )

            draw.line(
                (
                    x1,
                    y1,
                    x2,
                    y2,
                ),
                fill=(
                    100,
                    210,
                    255,
                    255,
                ),
                width=4,
            )

        # -------------------------------------------------
        # Put user's avatar on card
        # -------------------------------------------------

        avatar_x = (
            avatar_center_x
            - avatar.width // 2
        )

        avatar_y = (
            avatar_center_y
            - avatar.height // 2
        )

        frame.alpha_composite(
            avatar,
            (
                avatar_x,
                avatar_y,
            ),
        )

        # -------------------------------------------------
        # Welcome
        # -------------------------------------------------

        draw = ImageDraw.Draw(
            frame
        )

        draw_centered(
            draw,
            "WELCOME",
            285,
            welcome_font,
            (
                240,
                245,
                255,
                255,
            ),
        )

        # -------------------------------------------------
        # User's OWN Discord name
        # -------------------------------------------------

        safe_name = display_name

        if len(safe_name) > 22:
            safe_name = (
                safe_name[:19]
                + "..."
            )

        draw_centered(
            draw,
            safe_name,
            330,
            name_font,
            (
                90,
                200,
                255,
                255,
            ),
        )

        # -------------------------------------------------
        # Server name
        # -------------------------------------------------

        safe_server = server_name

        if len(safe_server) > 35:
            safe_server = (
                safe_server[:32]
                + "..."
            )

        draw_centered(
            draw,
            f"to {safe_server}",
            400,
            server_font,
            (
                235,
                240,
                255,
                255,
            ),
        )

        # -------------------------------------------------
        # Small message
        # -------------------------------------------------

        message_lines = (
            formatted_message
            .splitlines()
        )

        if message_lines:

            text = message_lines[0]

            if len(text) > 75:
                text = (
                    text[:72]
                    + "..."
                )

            draw_centered(
                draw,
                text,
                450,
                message_font,
                (
                    180,
                    195,
                    220,
                    255,
                ),
            )

        frames.append(
            frame.convert(
                "RGB"
            )
        )

    # -----------------------------------------------------
    # Save GIF in memory
    # -----------------------------------------------------

    output = io.BytesIO()

    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_DURATION,
        loop=0,
        optimize=True,
    )

    output.seek(0)

    return output


# ---------------------------------------------------------
# Configuration Panel
# ---------------------------------------------------------

class WelcomeConfigView(
    discord.ui.View
):

    def __init__(
        self,
        cog,
        creator_id: int,
        panel_message_id: int | None = None,
    ):
        super().__init__(
            timeout=None
        )

        self.cog = cog
        self.creator_id = creator_id
        self.panel_message_id = (
            panel_message_id
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):

        if not interaction.guild:
            return False

        # Must be administrator.
        if not interaction.user.guild_permissions.administrator:

            await interaction.response.send_message(
                "❌ You need **Administrator** permission to use this panel.",
                ephemeral=True,
            )

            return False

        # Must be panel creator.
        if (
            interaction.user.id
            != self.creator_id
        ):

            await interaction.response.send_message(
                "🔒 This configuration panel belongs to another administrator. "
                "Only the administrator who created this panel can edit it.",
                ephemeral=True,
            )

            return False

        return True

    # -----------------------------------------------------
    # Channel
    # -----------------------------------------------------

    @discord.ui.button(
        label="📢 Channel",
        style=discord.ButtonStyle.primary,
        custom_id="welcome_channel_button",
        row=0,
    )
    async def channel_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "👇 Select the welcome channel:",
            view=WelcomeChannelView(
                self
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # Message
    # -----------------------------------------------------

    @discord.ui.button(
        label="✏️ Message",
        style=discord.ButtonStyle.secondary,
        custom_id="welcome_message_button",
        row=0,
    )
    async def message_button(
        self,
        interaction,
        button,
    ):

        row = await self.cog.bot.database.fetchone(
            """
            SELECT welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (
                interaction.guild.id,
            ),
        )

        current_message = (
            row["welcome_message"]
            if row
            and row["welcome_message"]
            else DEFAULT_MESSAGE
        )

        await interaction.response.send_modal(
            WelcomeMessageModal(
                self,
                current_message,
            )
        )

    # -----------------------------------------------------
    # Auto Role
    # -----------------------------------------------------

    @discord.ui.button(
        label="🎭 Auto Role",
        style=discord.ButtonStyle.secondary,
        custom_id="welcome_role_button",
        row=0,
    )
    async def role_button(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "👇 Select the automatic role:",
            view=WelcomeRoleView(
                self
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # Test
    # -----------------------------------------------------

    @discord.ui.button(
        label="🧪 Test",
        style=discord.ButtonStyle.success,
        custom_id="welcome_test_button",
        row=0,
    )
    async def test_button(
        self,
        interaction,
        button,
    ):

        row = await self.cog.bot.database.fetchone(
            """
            SELECT
                welcome_channel_id,
                welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (
                interaction.guild.id,
            ),
        )

        if (
            not row
            or not row["welcome_channel_id"]
        ):

            await interaction.response.send_message(
                "❌ Configure a welcome channel first.",
                ephemeral=True,
            )

            return

        channel = interaction.guild.get_channel(
            row["welcome_channel_id"]
        )

        if channel is None:

            await interaction.response.send_message(
                "❌ The configured channel no longer exists.",
                ephemeral=True,
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        avatar = await download_avatar(
            interaction.user
        )

        message = (
            row["welcome_message"]
            or DEFAULT_MESSAGE
        )

        gif = create_welcome_gif(
            interaction.user,
            avatar,
            message,
        )

        # IMPORTANT:
        # The GIF is embedded inside Discord.
        file = discord.File(
            gif,
            filename="welcome.gif",
        )

        embed = discord.Embed()

        embed.set_image(
            url="attachment://welcome.gif"
        )

        await channel.send(
            embed=embed,
            file=file,
        )

        await interaction.followup.send(
            "✅ Welcome card test sent!",
            ephemeral=True,
        )


# ---------------------------------------------------------
# Channel selector
# ---------------------------------------------------------

class WelcomeChannelView(
    discord.ui.View
):

    def __init__(
        self,
        parent_view,
    ):
        super().__init__(
            timeout=60
        )

        self.add_item(
            WelcomeChannelSelect(
                parent_view
            )
        )


class WelcomeChannelSelect(
    discord.ui.ChannelSelect
):

    def __init__(
        self,
        parent_view,
    ):

        super().__init__(
            placeholder=(
                "Select welcome channel..."
            ),
            channel_types=[
                discord.ChannelType.text
            ],
            min_values=1,
            max_values=1,
        )

        self.parent_view = parent_view

    async def callback(
        self,
        interaction,
    ):

        channel = self.values[0]

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings
            (
                guild_id,
                welcome_channel_id
            )
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                welcome_channel_id =
                excluded.welcome_channel_id
            """,
            (
                interaction.guild.id,
                channel.id,
            ),
        )

        await interaction.response.send_message(
            f"✅ Welcome channel set to {channel.mention}.",
            ephemeral=True,
        )

        await self.parent_view.cog.update_panel_message(
            interaction.guild,
            self.parent_view.panel_message_id,
        )


# ---------------------------------------------------------
# Role selector
# ---------------------------------------------------------

class WelcomeRoleView(
    discord.ui.View
):

    def __init__(
        self,
        parent_view,
    ):
        super().__init__(
            timeout=60
        )

        self.add_item(
            WelcomeRoleSelect(
                parent_view
            )
        )


class WelcomeRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        parent_view,
    ):

        super().__init__(
            placeholder=(
                "Select automatic role..."
            ),
            min_values=1,
            max_values=1,
        )

        self.parent_view = parent_view

    async def callback(
        self,
        interaction,
    ):

        role = self.values[0]

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings
            (
                guild_id,
                autorole_id
            )
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                autorole_id =
                excluded.autorole_id
            """,
            (
                interaction.guild.id,
                role.id,
            ),
        )

        await interaction.response.send_message(
            f"✅ Auto role set to {role.mention}.",
            ephemeral=True,
        )

        await self.parent_view.cog.update_panel_message(
            interaction.guild,
            self.parent_view.panel_message_id,
        )


# ---------------------------------------------------------
# Message modal
# ---------------------------------------------------------

class WelcomeMessageModal(
    discord.ui.Modal
):

    def __init__(
        self,
        parent_view,
        current_message,
    ):

        super().__init__(
            title="Welcome Message"
        )

        self.parent_view = parent_view

        self.message_input = discord.ui.TextInput(
            label="Welcome message",
            style=discord.TextStyle.paragraph,
            placeholder=(
                "Welcome {user} to {server}!"
            ),
            default=current_message,
            required=True,
            max_length=2000,
        )

        self.add_item(
            self.message_input
        )

    async def on_submit(
        self,
        interaction,
    ):

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings
            (
                guild_id,
                welcome_message
            )
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                welcome_message =
                excluded.welcome_message
            """,
            (
                interaction.guild.id,
                self.message_input.value,
            ),
        )

        await interaction.response.send_message(
            "✅ Welcome message saved!",
            ephemeral=True,
        )

        await self.parent_view.cog.update_panel_message(
            interaction.guild,
            self.parent_view.panel_message_id,
        )


# ---------------------------------------------------------
# Welcome Cog
# ---------------------------------------------------------

class Welcome(commands.Cog):

    def __init__(
        self,
        bot,
    ):
        self.bot = bot

    welcome_group = app_commands.Group(
        name="welcome",
        description=(
            "Configure the welcome system."
        ),
    )

    enable_group = app_commands.Group(
        name="enable",
        description=(
            "Enable server features."
        ),
    )

    disable_group = app_commands.Group(
        name="disable",
        description=(
            "Disable server features."
        ),
    )

    # -----------------------------------------------------
    # /welcome config
    # -----------------------------------------------------

    @welcome_group.command(
        name="config",
        description=(
            "Open the welcome configuration panel."
        ),
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def welcome_config(
        self,
        interaction,
    ):

        await self.bot.database.execute(
            """
            INSERT OR IGNORE INTO guild_settings
            (guild_id)
            VALUES (?)
            """,
            (
                interaction.guild.id,
            ),
        )

        await interaction.response.defer(
            ephemeral=True
        )

        panel_message = await interaction.channel.send(
            embed=await self.create_panel_embed(
                interaction.guild
            ),
        )

        view = WelcomeConfigView(
            self,
            interaction.user.id,
            panel_message.id,
        )

        await panel_message.edit(
            view=view
        )

        await self.bot.database.execute(
            """
            INSERT OR REPLACE INTO welcome_panels
            (
                guild_id,
                channel_id,
                message_id,
                creator_id
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                interaction.guild.id,
                interaction.channel.id,
                panel_message.id,
                interaction.user.id,
            ),
        )

        await interaction.followup.send(
            "✅ Welcome configuration panel created.",
            ephemeral=True,
        )

    # -----------------------------------------------------
    # /enable welcome
    # -----------------------------------------------------

    @enable_group.command(
        name="welcome",
        description=(
            "Enable automatic welcome messages."
        ),
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def enable_welcome(
        self,
        interaction,
    ):

        row = await self.bot.database.fetchone(
            """
            SELECT
                welcome_channel_id,
                welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (
                interaction.guild.id,
            ),
        )

        if (
            not row
            or not row["welcome_channel_id"]
            or not row["welcome_message"]
        ):

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
            (
                interaction.guild.id,
            ),
        )

        await interaction.response.send_message(
            "🟢 Automatic welcome messages are now **enabled**!",
            ephemeral=True,
        )

    # -----------------------------------------------------
    # /disable welcome
    # -----------------------------------------------------

    @disable_group.command(
        name="welcome",
        description=(
            "Disable automatic welcome messages."
        ),
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def disable_welcome(
        self,
        interaction,
    ):

        await self.bot.database.execute(
            """
            UPDATE guild_settings
            SET welcome_enabled = 0
            WHERE guild_id = ?
            """,
            (
                interaction.guild.id,
            ),
        )

        await interaction.response.send_message(
            "🔴 Automatic welcome messages are now **disabled**.\n"
            "Your configuration has been preserved.",
            ephemeral=True,
        )

    # -----------------------------------------------------
    # Configuration panel embed
    # -----------------------------------------------------

    async def create_panel_embed(
        self,
        guild,
    ):

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
            (
                guild.id,
            ),
        )

        enabled = bool(
            row
            and row["welcome_enabled"]
        )

        channel = None

        if row and row["welcome_channel_id"]:
            channel = guild.get_channel(
                row["welcome_channel_id"]
            )

        role = None

        if row and row["autorole_id"]:
            role = guild.get_role(
                row["autorole_id"]
            )

        message = (
            row["welcome_message"]
            if row
            else None
        )

        embed = discord.Embed(
            title="👋 WELCOME CONFIGURATION",
            description=(
                "Configure your automatic "
                "welcome card below."
            ),
            color=(
                discord.Color.green()
                if enabled
                else discord.Color.red()
            ),
        )

        embed.add_field(
            name="Status",
            value=(
                "🟢 Enabled"
                if enabled
                else "🔴 Disabled"
            ),
            inline=False,
        )

        embed.add_field(
            name="📢 Channel",
            value=(
                channel.mention
                if channel
                else "Not configured"
            ),
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
            value=(
                role.mention
                if role
                else "None"
            ),
            inline=False,
        )

        embed.set_footer(
            text=(
                "Only the administrator who created "
                "this panel can edit it."
            )
        )

        return embed

    # -----------------------------------------------------
    # Update panel
    # -----------------------------------------------------

    async def update_panel_message(
        self,
        guild,
        message_id,
    ):

        row = await self.bot.database.fetchone(
            """
            SELECT
                channel_id,
                creator_id
            FROM welcome_panels
            WHERE message_id = ?
            """,
            (
                message_id,
            ),
        )

        if not row:
            return

        channel = guild.get_channel(
            row["channel_id"]
        )

        if not channel:
            return

        try:
            message = await channel.fetch_message(
                message_id
            )
        except discord.NotFound:
            return

        view = WelcomeConfigView(
            self,
            row["creator_id"],
            message_id,
        )

        await message.edit(
            embed=await self.create_panel_embed(
                guild
            ),
            view=view,
        )

    # -----------------------------------------------------
    # Automatic welcome
    # -----------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(
        self,
        member: discord.Member,
    ):

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
            (
                member.guild.id,
            ),
        )

        if (
            not row
            or not row["welcome_enabled"]
        ):
            return

        # -------------------------------------------------
        # Welcome card
        # -------------------------------------------------

        channel_id = row["welcome_channel_id"]

        if channel_id:

            channel = member.guild.get_channel(
                channel_id
            )

            if channel:

                try:

                    # IMPORTANT:
                    # This is the person who JUST JOINED.
                    avatar = await download_avatar(
                        member
                    )

                    message = (
                        row["welcome_message"]
                        or DEFAULT_MESSAGE
                    )

                    gif = create_welcome_gif(
                        member,
                        avatar,
                        message,
                    )

                    file = discord.File(
                        gif,
                        filename="welcome.gif",
                    )

                    # Put GIF INSIDE embed.
                    embed = discord.Embed()

                    embed.set_image(
                        url="attachment://welcome.gif"
                    )

                    await channel.send(
                        embed=embed,
                        file=file,
                    )

                except Exception:
                    # Don't crash the bot if image
                    # generation fails.
                    pass

        # -------------------------------------------------
        # Automatic role
        # -------------------------------------------------

        role_id = row["autorole_id"]

        if role_id:

            role = member.guild.get_role(
                role_id
            )

            if role:

                try:

                    await member.add_roles(
                        role,
                        reason=(
                            "Automatic welcome role"
                        ),
                    )

                except (
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    pass

    # -----------------------------------------------------
    # Restore panels after restart
    # -----------------------------------------------------

    async def restore_panels(
        self
    ):

        rows = await self.bot.database.fetchall(
            """
            SELECT
                guild_id,
                channel_id,
                message_id,
                creator_id
            FROM welcome_panels
            """
        )

        for row in rows:

            view = WelcomeConfigView(
                self,
                row["creator_id"],
                row["message_id"],
            )

            try:

                self.bot.add_view(
                    view,
                    message_id=row["message_id"],
                )

            except Exception:
                pass


# ---------------------------------------------------------
# Setup
# ---------------------------------------------------------

async def setup(
    bot,
):

    cog = Welcome(bot)

    await bot.add_cog(cog)

    await cog.restore_panels()
