import discord
from discord import app_commands
from discord.ext import commands


class Announce(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="announce",
        description="Send an announcement to a specific channel."
    )
    @app_commands.describe(
        channel="The channel where the announcement will be sent.",
        message="The announcement message."
    )
    @app_commands.checks.has_permissions(
        administrator=True
    )
    async def announce(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str
    ):

        # Check bot permissions
        permissions = channel.permissions_for(
            interaction.guild.me
        )

        if not permissions.send_messages:

            await interaction.response.send_message(
                f"❌ I don't have permission to send messages in "
                f"{channel.mention}.",
                ephemeral=True
            )
            return

        try:

            await channel.send(message)

            await interaction.response.send_message(
                f"✅ Announcement sent to {channel.mention}.",
                ephemeral=True
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                f"❌ I don't have permission to send messages in "
                f"{channel.mention}.",
                ephemeral=True
            )

        except Exception as error:

            print(
                f"Announcement error: {error}"
            )

            await interaction.response.send_message(
                "❌ An error occurred while sending the announcement.",
                ephemeral=True
            )

    @announce.error
    async def announce_error(
        self,
        interaction: discord.Interaction,
        error
    ):

        if isinstance(
            error,
            app_commands.errors.MissingPermissions
        ):

            message = (
                "🔒 You need **Administrator** permission "
                "to use `/announce`."
            )

        else:

            print(
                f"Announce command error: {error}"
            )

            message = (
                "❌ An error occurred while processing "
                "the announcement."
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


async def setup(bot):

    await bot.add_cog(
        Announce(bot)
    )

    print(
        "Announcement system loaded."
    )
