import discord
from discord import app_commands
from discord.ext import commands


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="ban",
        description="Ban a member from the server."
    )
    @app_commands.describe(
        member="The member to ban.",
        reason="Reason for the ban."
    )
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
    ):
        if member == interaction.user:
            await interaction.response.send_message(
                "❌ You cannot ban yourself.",
                ephemeral=True,
            )
            return

        if member == interaction.guild.owner:
            await interaction.response.send_message(
                "❌ You cannot ban the server owner.",
                ephemeral=True,
            )
            return

        if member.top_role >= interaction.user.top_role:
            await interaction.response.send_message(
                "❌ You cannot ban someone with an equal or higher role.",
                ephemeral=True,
            )
            return

        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "❌ My role is not high enough to ban this member.",
                ephemeral=True,
            )
            return

        try:
            await member.ban(
                reason=f"{reason} | Moderator: {interaction.user}"
            )

            await interaction.response.send_message(
                f"🔨 **{member}** has been banned.\n"
                f"**Reason:** {reason}"
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to ban this member.",
                ephemeral=True,
            )

    @app_commands.command(
        name="kick",
        description="Kick a member from the server."
    )
    @app_commands.describe(
        member="The member to kick.",
        reason="Reason for the kick."
    )
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
    ):
        if member == interaction.user:
            await interaction.response.send_message(
                "❌ You cannot kick yourself.",
                ephemeral=True,
            )
            return

        if member == interaction.guild.owner:
            await interaction.response.send_message(
                "❌ You cannot kick the server owner.",
                ephemeral=True,
            )
            return

        if member.top_role >= interaction.user.top_role:
            await interaction.response.send_message(
                "❌ You cannot kick someone with an equal or higher role.",
                ephemeral=True,
            )
            return

        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "❌ My role is not high enough to kick this member.",
                ephemeral=True,
            )
            return

        try:
            await member.kick(
                reason=f"{reason} | Moderator: {interaction.user}"
            )

            await interaction.response.send_message(
                f"👢 **{member}** has been kicked.\n"
                f"**Reason:** {reason}"
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to kick this member.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
