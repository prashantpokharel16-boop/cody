import io
import math

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter


DEFAULT_MESSAGE = (
    "Welcome {user} to {server}! 🎉\n"
    "You are member #{member_count}."
)

# Animated card settings
CARD_WIDTH = 800
CARD_HEIGHT = 450
FRAME_COUNT = 12
FRAME_DURATION = 90  # milliseconds


def get_font(size: int, bold: bool = False):
    """Load a good system font, with a safe fallback."""

    possible_fonts = []

    if bold:
        possible_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        possible_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]

    for path in possible_fonts:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    return ImageFont.load_default()


def centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font,
    fill,
):
    """Draw centered text."""

    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]

    x = (CARD_WIDTH - width) // 2

    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
    )


def circular_avatar(avatar: Image.Image, size: int):
    """Create a circular avatar."""

    avatar = avatar.convert("RGBA")
    avatar.thumbnail((size, size), Image.Resampling.LANCZOS)

    canvas = Image.new(
        "RGBA",
        (size, size),
        (0, 0, 0, 0),
    )

    x = (size - avatar.width) // 2
    y = (size - avatar.height) // 2

    canvas.alpha_composite(
        avatar,
        (x, y),
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

    canvas.putalpha(mask)

    return canvas


async def download_avatar(member: discord.Member):
    """Download the joining member's Discord avatar."""

    try:
        url = member.display_avatar.replace(
            format="png",
            size=256,
        ).url

        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(url) as response:

                if response.status != 200:
                    return None

                data = await response.read()

        return Image.open(
            io.BytesIO(data)
        ).convert("RGBA")

    except Exception:
        return None


def create_animated_welcome_gif(
    member: discord.Member,
    avatar: Image.Image | None,
    custom_message: str,
):
    """Generate an animated Discord welcome GIF."""

    frames = []

    # Member information
    display_name = member.display_name
    server_name = member.guild.name
    member_count = member.guild.member_count or 0

    # Replace variables in custom message.
    formatted_message = (
        custom_message
        .replace("{user}", member.mention)
        .replace("{username}", member.name)
        .replace("{server}", server_name)
        .replace("{member_count}", str(member_count))
    )

    # Keep generated card readable.
    message_lines = formatted_message.splitlines()

    if len(message_lines) > 2:
        message_lines = message_lines[:2]

    # Prepare avatar.
    if avatar is None:
        avatar = Image.new(
            "RGBA",
            (256, 256),
            (70, 70, 80, 255),
        )

        avatar_draw = ImageDraw.Draw(avatar)

        avatar_draw.ellipse(
            (20, 20, 236, 236),
            fill=(100, 100, 115, 255),
        )

        avatar_draw.text(
            (128, 128),
            "?",
            anchor="mm",
            font=get_font(90, True),
            fill=(255, 255, 255, 255),
        )

    avatar = circular_avatar(
        avatar,
        180,
    )

    title_font = get_font(42, True)
    name_font = get_font(48, True)
    server_font = get_font(30, True)
    member_font = get_font(20, True)
    message_font = get_font(18)

    for frame_number in range(FRAME_COUNT):

        frame = Image.new(
            "RGBA",
            (CARD_WIDTH, CARD_HEIGHT),
            (13, 15, 25, 255),
        )

        draw = ImageDraw.Draw(frame)

        # -------------------------------------------------
        # Animated background
        # -------------------------------------------------

        # Soft blue/purple gradient.
        for y in range(CARD_HEIGHT):

            ratio = y / CARD_HEIGHT

            r = int(12 + ratio * 18)
            g = int(16 + ratio * 8)
            b = int(35 + ratio * 45)

            draw.line(
                [(0, y), (CARD_WIDTH, y)],
                fill=(r, g, b, 255),
            )

        # Moving glowing particles.
        glow_layer = Image.new(
            "RGBA",
            (CARD_WIDTH, CARD_HEIGHT),
            (0, 0, 0, 0),
        )

        glow_draw = ImageDraw.Draw(
            glow_layer
        )

        for particle in range(30):

            angle = (
                frame_number * 0.18
                + particle * 1.7
            )

            radius = 100 + (
                particle * 23
            ) % 300

            center_x = CARD_WIDTH // 2
            center_y = CARD_HEIGHT // 2

            x = int(
                center_x
                + math.cos(angle) * radius
            )

            y = int(
                center_y
                + math.sin(angle) * radius * 0.55
            )

            if (
                0 <= x < CARD_WIDTH
                and 0 <= y < CARD_HEIGHT
            ):
                size = 2 + (particle % 3)

                glow_draw.ellipse(
                    (
                        x - size,
                        y - size,
                        x + size,
                        y + size,
                    ),
                    fill=(100, 180, 255, 170),
                )

        glow_layer = glow_layer.filter(
            ImageFilter.GaussianBlur(1.2)
        )

        frame = Image.alpha_composite(
            frame,
            glow_layer,
        )

        draw = ImageDraw.Draw(frame)

        # -------------------------------------------------
        # Outer card border
        # -------------------------------------------------

        pulse = int(
            20
            + 25
            * (
                1
                + math.sin(
                    frame_number * 0.55
                )
            )
            / 2
        )

        border_color = (
            90,
            170,
            255,
            255,
        )

        draw.rounded_rectangle(
            (
                12,
                12,
                CARD_WIDTH - 12,
                CARD_HEIGHT - 12,
            ),
            radius=28,
            outline=border_color,
            width=3,
        )

        # -------------------------------------------------
        # Member number badge
        # -------------------------------------------------

        badge_text = f"MEMBER #{member_count}"

        badge_box = draw.textbbox(
            (0, 0),
            badge_text,
            font=member_font,
        )

        badge_width = (
            badge_box[2] - badge_box[0] + 40
        )

        badge_x = (
            CARD_WIDTH - badge_width
        ) // 2

        draw.rounded_rectangle(
            (
                badge_x,
                30,
                badge_x + badge_width,
                65,
            ),
            radius=18,
            fill=(25, 35, 65, 230),
            outline=(100, 190, 255, 220),
            width=2,
        )

        centered_text(
            draw,
            badge_text,
            37,
            member_font,
            (220, 240, 255, 255),
        )

        # -------------------------------------------------
        # Avatar glow
        # -------------------------------------------------

        avatar_x = (
            CARD_WIDTH - avatar.width
        ) // 2

        avatar_y = 82

        glow_size = (
            195 + int(
                6
                * math.sin(
                    frame_number * 0.7
                )
            )
        )

        avatar_glow = Image.new(
            "RGBA",
            (CARD_WIDTH, CARD_HEIGHT),
            (0, 0, 0, 0),
        )

        glow_draw = ImageDraw.Draw(
            avatar_glow
        )

        gx = CARD_WIDTH // 2
        gy = avatar_y + 90

        glow_draw.ellipse(
            (
                gx - glow_size // 2,
                gy - glow_size // 2,
                gx + glow_size // 2,
                gy + glow_size // 2,
            ),
            fill=(60, 150, 255, pulse),
        )

        avatar_glow = avatar_glow.filter(
            ImageFilter.GaussianBlur(18)
        )

        frame = Image.alpha_composite(
            frame,
            avatar_glow,
        )

        # Animated ring.
        draw = ImageDraw.Draw(frame)

        ring_start = (
            frame_number * 15
        ) % 360

        for offset in range(0, 360, 45):

            angle = math.radians(
                ring_start + offset
            )

            ring_radius = 101

            x1 = int(
                gx
                + math.cos(angle)
                * ring_radius
            )

            y1 = int(
                gy
                + math.sin(angle)
                * ring_radius
            )

            x2 = int(
                gx
                + math.cos(angle + 0.18)
                * ring_radius
            )

            y2 = int(
                gy
                + math.sin(angle + 0.18)
                * ring_radius
            )

            draw.line(
                (x1, y1, x2, y2),
                fill=(120, 210, 255, 255),
                width=4,
            )

        frame.alpha_composite(
            avatar,
            (
                avatar_x,
                avatar_y,
            ),
        )

        # -------------------------------------------------
        # Welcome text
        # -------------------------------------------------

        centered_text(
            draw,
            "WELCOME",
            285,
            title_font,
            (235, 240, 255, 255),
        )

        # Keep extremely long names from destroying layout.
        safe_name = display_name

        if len(safe_name) > 20:
            safe_name = safe_name[:17] + "..."

        centered_text(
            draw,
            safe_name,
            330,
            name_font,
            (90, 190, 255, 255),
        )

        centered_text(
            draw,
            f"to {server_name}",
            385,
            server_font,
            (235, 235, 245, 255),
        )

        # -------------------------------------------------
        # Small custom message
        # -------------------------------------------------

        if message_lines:

            small_text = message_lines[0]

            if len(small_text) > 70:
                small_text = (
                    small_text[:67]
                    + "..."
                )

            centered_text(
                draw,
                small_text,
                420,
                message_font,
                (185, 195, 215, 255),
            )

        frames.append(
            frame.convert("RGB")
        )

    # Save animated GIF to memory.
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


class WelcomeConfigView(discord.ui.View):

    def __init__(
        self,
        cog,
        creator_id: int,
        panel_message_id: int | None = None,
    ):
        super().__init__(timeout=None)

        self.cog = cog
        self.creator_id = creator_id
        self.panel_message_id = panel_message_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:

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
            "👇 Select the channel for welcome messages.",
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

        row = await self.cog.bot.database.fetchone(
            """
            SELECT welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (interaction.guild.id,),
        )

        current_message = (
            row["welcome_message"]
            if row and row["welcome_message"]
            else DEFAULT_MESSAGE
        )

        await interaction.response.send_modal(
            WelcomeMessageModal(
                self,
                current_message,
            )
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

        row = await self.cog.bot.database.fetchone(
            """
            SELECT
                welcome_channel_id,
                welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (interaction.guild.id,),
        )

        if not row or not row["welcome_channel_id"]:

            await interaction.response.send_message(
                "❌ Configure a welcome channel first.",
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
            or DEFAULT_MESSAGE
        )

        await interaction.response.defer(
            ephemeral=True
        )

        avatar = await download_avatar(
            interaction.user
        )

        gif = create_animated_welcome_gif(
            interaction.user,
            avatar,
            message,
        )

        await channel.send(
            content=f"🧪 Welcome preview for {interaction.user.mention}",
            file=discord.File(
                gif,
                filename="welcome.gif",
            ),
        )

        await interaction.followup.send(
            "✅ Animated welcome preview sent!",
            ephemeral=True,
        )


class WelcomeChannelView(discord.ui.View):

    def __init__(
        self,
        parent_view: WelcomeConfigView,
    ):
        super().__init__(timeout=60)

        self.add_item(
            WelcomeChannelSelect(parent_view)
        )


class WelcomeChannelSelect(
    discord.ui.ChannelSelect
):

    def __init__(
        self,
        parent_view: WelcomeConfigView,
    ):
        super().__init__(
            placeholder="Select welcome channel...",
            channel_types=[
                discord.ChannelType.text
            ],
            min_values=1,
            max_values=1,
        )

        self.parent_view = parent_view

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        channel = self.values[0]

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings
            (guild_id, welcome_channel_id)
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


class WelcomeRoleView(discord.ui.View):

    def __init__(
        self,
        parent_view: WelcomeConfigView,
    ):
        super().__init__(timeout=60)

        self.add_item(
            WelcomeRoleSelect(parent_view)
        )


class WelcomeRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        parent_view: WelcomeConfigView,
    ):
        super().__init__(
            placeholder="Select automatic role...",
            min_values=1,
            max_values=1,
        )

        self.parent_view = parent_view

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        role = self.values[0]

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings
            (guild_id, autorole_id)
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


class WelcomeMessageModal(
    discord.ui.Modal
):

    def __init__(
        self,
        parent_view: WelcomeConfigView,
        current_message: str,
    ):
        super().__init__(
            title="Welcome Message"
        )

        self.parent_view = parent_view

        self.message_input = discord.ui.TextInput(
            label="Welcome message",
            style=discord.TextStyle.paragraph,
            placeholder="Welcome {user} to {server}!",
            default=current_message,
            required=True,
            max_length=2000,
        )

        self.add_item(
            self.message_input
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        await self.parent_view.cog.bot.database.execute(
            """
            INSERT INTO guild_settings
            (guild_id, welcome_message)
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
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def welcome_config(
        self,
        interaction: discord.Interaction,
    ):

        await self.bot.database.execute(
            """
            INSERT OR IGNORE INTO guild_settings
            (guild_id)
            VALUES (?)
            """,
            (interaction.guild.id,),
        )

        message = await interaction.channel.send(
            embed=await self.create_panel_embed(
                interaction.guild
            ),
        )

        view = WelcomeConfigView(
            self,
            interaction.user.id,
            message.id,
        )

        await message.edit(
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
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def enable_welcome(
        self,
        interaction: discord.Interaction,
    ):

        row = await self.bot.database.fetchone(
            """
            SELECT
                welcome_channel_id,
                welcome_message
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (interaction.guild.id,),
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
            (interaction.guild.id,),
        )

        await interaction.response.send_message(
            "🟢 Automatic welcome messages are now **enabled**!",
            ephemeral=True,
        )

    @disable_group.command(
        name="welcome",
        description="Disable automatic welcome messages.",
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def disable_welcome(
        self,
        interaction: discord.Interaction,
    ):

        await self.bot.database.execute(
            """
            UPDATE guild_settings
            SET welcome_enabled = 0
            WHERE guild_id = ?
            """,
            (interaction.guild.id,),
        )

        await interaction.response.send_message(
            "🔴 Automatic welcome messages are now **disabled**.\n"
            "Your configuration has been preserved.",
            ephemeral=True,
        )

    async def create_panel_embed(
        self,
        guild: discord.Guild,
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
            (guild.id,),
        )

        enabled = bool(
            row and row["welcome_enabled"]
        )

        channel_id = (
            row["welcome_channel_id"]
            if row
            else None
        )

        message = (
            row["welcome_message"]
            if row
            else None
        )

        role_id = (
            row["autorole_id"]
            if row
            else None
        )

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
            title="👋 WELCOME CONFIGURATION",
            description=(
                "Configure your automatic animated "
                "welcome system below."
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

    async def update_panel_message(
        self,
        guild: discord.Guild,
        message_id: int,
    ):

        row = await self.bot.database.fetchone(
            """
            SELECT
                channel_id,
                creator_id
            FROM welcome_panels
            WHERE message_id = ?
            """,
            (message_id,),
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
            (member.guild.id,),
        )

        if not row or not row["welcome_enabled"]:
            return

        message = (
            row["welcome_message"]
            or DEFAULT_MESSAGE
        )

        # Create and send animated welcome card.
        if row["welcome_channel_id"]:

            channel = member.guild.get_channel(
                row["welcome_channel_id"]
            )

            if channel:

                try:

                    avatar = await download_avatar(
                        member
                    )

                    gif = create_animated_welcome_gif(
                        member,
                        avatar,
                        message,
                    )

                    await channel.send(
                        content=member.mention,
                        file=discord.File(
                            gif,
                            filename="welcome.gif",
                        ),
                    )

                except Exception:
                    # Do not crash the whole bot
                    # because of a welcome image error.
                    pass

        # Automatic role.
        if row["autorole_id"]:

            role = member.guild.get_role(
                row["autorole_id"]
            )

            if role:

                try:

                    await member.add_roles(
                        role,
                        reason="Automatic welcome role",
                    )

                except discord.Forbidden:
                    pass

                except discord.HTTPException:
                    pass

    async def restore_panels(self):

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


async def setup(bot):

    cog = Welcome(bot)

    await bot.add_cog(cog)

    await cog.restore_panels()
