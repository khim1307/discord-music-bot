import discord
from discord import app_commands
from discord.ext import commands
import logging

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="kick", description="Kicks a member from the server.")
    @app_commands.describe(member="The member to kick.", reason="The reason for kicking.")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        # ... (Keep the kick logic from the previous combined file) ...
        if member == interaction.user: await interaction.response.send_message("You cannot kick yourself!", ephemeral=True); return
        if member == self.bot.user: await interaction.response.send_message("I cannot kick myself!", ephemeral=True); return
        if interaction.guild.me.top_role <= member.top_role: await interaction.response.send_message("My role isn't high enough.", ephemeral=True); return
        try:
            await member.kick(reason=f"Kicked by {interaction.user.name}: {reason}")
            await interaction.response.send_message(f"👢 Kicked {member.mention} for: {reason}")
            logging.info(f"User {interaction.user} kicked {member} G{interaction.guild_id} R: {reason}")
        except discord.Forbidden: await interaction.response.send_message("❌ I lack kick permissions.", ephemeral=True)
        except Exception as e: await interaction.response.send_message(f"❌ Kick failed: {e}", ephemeral=True); logging.error(f"Kick failed {member}: {e}")


    @app_commands.command(name="ban", description="Bans a member from the server.")
    @app_commands.describe(member="The member to ban.", reason="The reason for banning.")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        # ... (Keep the ban logic from the previous combined file) ...
        if member == interaction.user: await interaction.response.send_message("You cannot ban yourself!", ephemeral=True); return
        if member == self.bot.user: await interaction.response.send_message("I cannot ban myself!", ephemeral=True); return
        if interaction.guild.me.top_role <= member.top_role: await interaction.response.send_message("My role isn't high enough.", ephemeral=True); return
        try:
            await member.ban(reason=f"Banned by {interaction.user.name}: {reason}", delete_message_days=0)
            await interaction.response.send_message(f"🔨 Banned {member.mention} for: {reason}")
            logging.info(f"User {interaction.user} banned {member} G{interaction.guild_id} R: {reason}")
        except discord.Forbidden: await interaction.response.send_message("❌ I lack ban permissions.", ephemeral=True)
        except Exception as e: await interaction.response.send_message(f"❌ Ban failed: {e}", ephemeral=True); logging.error(f"Ban failed {member}: {e}")


    @app_commands.command(name="clear", description="Clears a specified number of messages.")
    @app_commands.describe(amount="Number of messages to delete (max 100).")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear_slash(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        # ... (Keep the clear logic from the previous combined file) ...
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"🗑️ Deleted {len(deleted)} message(s).", ephemeral=True)
            logging.info(f"User {interaction.user} cleared {len(deleted)} in C{interaction.channel.id} G{interaction.guild_id}")
        except discord.Forbidden: await interaction.followup.send("❌ I lack 'Manage Messages' permission.", ephemeral=True)
        except Exception as e: await interaction.followup.send(f"❌ Clear failed: {e}", ephemeral=True); logging.error(f"Clear failed C{interaction.channel.id}: {e}")


    # Optional: Add a global Cog error handler if desired
    # async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
    #     # Handle errors specific to this cog's commands
    #     await interaction.response.send_message(f"Admin command error: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
    logging.info("AdminCog loaded.")
