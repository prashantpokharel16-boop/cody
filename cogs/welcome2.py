import discord
from discord.ext import commands


class Welcome2(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        # Find the same welcome channel used by the original Welcome system
        welcome_cog = self.bot.get_cog("Welcome")

        if not welcome_cog:
            return

        settings = welcome_cog.get_settings(guild.id)

        if not settings:
            return

        channel_id = settings[1]

        if not channel_id:
            return

        channel = guild.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            return

        await channel.send(
            f"Welcome {member.display_name} to {guild.name}!"
        )


async def setup(bot):
    if bot.get_cog("Welcome2") is not None:
        return

    await bot.add_cog(Welcome2(bot))
