import discord
from discord.ext import commands
import os
import asyncio
import logging
import json # For role interactions if handled here
import time # For role interactions if handled here
from dotenv import load_dotenv
# deque is not needed here anymore

# --- Load Environment Variables ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# --- Logging ---
# Keep logging setup as before
logging.basicConfig(level=logging.INFO)
handler = logging.FileHandler(filename='discord_bot.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logging.getLogger().addHandler(handler)

# --- Bot Intents ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

# --- Bot Class ---
class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents) # Prefix optional if only slash

        # --- State Dictionaries REMOVED from Bot instance ---
        # Let Cogs manage their own state

        # --- State needed for interactions handled in main bot ---
        self.role_mappings = {} # Role Cog will populate this
        self.role_cog_instance = None # To access Role Cog methods/state if needed


    async def setup_hook(self):
        """Loads extensions (Cogs) and syncs commands."""
        print("Running setup hook...")
        logging.info("Running setup hook...")
        cogs_to_load = ['cogs.music_cog', 'cogs.role_assign_cog', 'cogs.admin_cog']

        if GOOGLE_API_KEY:
             cogs_to_load.append('cogs.ai_cog')
        else:
             print("WARNING: GOOGLE_API_KEY not set. Skipping AICog loading.")
             logging.warning("GOOGLE_API_KEY not set. Skipping AICog loading.")

        for extension in cogs_to_load:
            try:
                await self.load_extension(extension)
                print(f"Successfully loaded extension: {extension}")
                logging.info(f"Successfully loaded extension: {extension}")
            except Exception as e:
                print(f"ERROR: Failed to load '{extension}': {e}")
                logging.exception(f"Failed to load extension {extension}")

        # Get RoleAssignCog instance AFTER loading and link mappings
        self.role_cog_instance = self.get_cog('RoleAssignCog')
        if self.role_cog_instance and hasattr(self.role_cog_instance, 'role_mappings'):
             self.role_mappings = self.role_cog_instance.role_mappings # Link reference
             logging.info("Linked role_mappings from RoleAssignCog to main bot.")
        else:
             logging.warning("Could not find RoleAssignCog or its role_mappings after load.")


        # Sync commands AFTER loading extensions
        print("Syncing commands...")
        logging.info("Syncing commands...")
        try:
            # --- CHOOSE SYNC METHOD ---
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s) globally.")
            logging.info(f"Synced {len(synced)} command(s) globally.")
            # --- OR ---
            # GUILD_ID = YOUR_TEST_GUILD_ID # Replace
            # guild_obj = discord.Object(id=GUILD_ID)
            # self.tree.copy_global_to(guild=guild_obj)
            # synced = await self.tree.sync(guild=guild_obj)
            # print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}.")
            # logging.info(f"Synced {len(synced)} command(s) to guild {GUILD_ID}.")
            # --- CHOOSE SYNC METHOD ---
        except Exception as e:
            print(f"Error syncing commands: {e}")
            logging.exception("Command sync failed")

    async def on_ready(self):
        """Called when the bot is ready."""
        print(f'Logged in as {self.user.name} ({self.user.id})')
        print(f'discord.py version: {discord.__version__}')
        print('Ready and operational.')
        logging.info(f'Bot logged in as {self.user.name}')
        # Role config loading is now handled by the RoleAssignCog init

    async def on_interaction(self, interaction: discord.Interaction):
        """Global interaction listener, primarily for persistent role buttons."""
        custom_id = interaction.data.get('custom_id', '')

        # --- Persistent Role Button Handling ---
        if custom_id.startswith("role_assign_"):
            logging.debug(f"Handling persistent role button click: {custom_id}")
            # Ensure the cog holding the logic and mappings is loaded
            if not self.role_cog_instance:
                 logging.warning("RoleAssignCog instance not found in on_interaction.")
                 try:
                      if not interaction.response.is_done(): await interaction.response.send_message("Role system unavailable.", ephemeral=True)
                      else: await interaction.followup.send("Role system unavailable.", ephemeral=True)
                 except discord.NotFound: pass # Ignore if interaction already gone
                 return

            # Delegate the actual role handling logic maybe? Or keep it here?
            # Keeping it here for now, but requires self.bot.role_mappings to be linked correctly.
            try:
                role_id = int(custom_id.split('_')[-1])
            except (IndexError, ValueError):
                 if not interaction.response.is_done(): await interaction.response.send_message("Invalid button ID.", ephemeral=True); return
                 else: await interaction.followup.send("Invalid button ID.", ephemeral=True); return

            guild = interaction.guild
            if not guild or not isinstance(interaction.user, discord.Member):
                 if not interaction.response.is_done(): await interaction.response.send_message("Guild/Member context error.", ephemeral=True); return
                 else: await interaction.followup.send("Guild/Member context error.", ephemeral=True); return
            member = interaction.user

            # --- Check if role is configured in the mappings LINKED from the cog ---
            guild_map = self.role_mappings.get(guild.id, {})
            if role_id not in guild_map:
                 if not interaction.response.is_done(): await interaction.response.send_message("Role not configured for this button.", ephemeral=True); return
                 else: await interaction.followup.send("Role not configured for this button.", ephemeral=True); return

            role = guild.get_role(role_id)
            if not role:
                 if not interaction.response.is_done(): await interaction.response.send_message("Role not found on server.", ephemeral=True); return
                 else: await interaction.followup.send("Role not found on server.", ephemeral=True); return

            # Permission Checks (Bot)
            if not guild.me.guild_permissions.manage_roles:
                 if not interaction.response.is_done(): await interaction.response.send_message("I lack 'Manage Roles' permission.", ephemeral=True); return
                 else: await interaction.followup.send("I lack 'Manage Roles' permission.", ephemeral=True); return
            if guild.me.top_role <= role:
                 if not interaction.response.is_done(): await interaction.response.send_message(f"My role isn't high enough for '{role.name}'.", ephemeral=True); return
                 else: await interaction.followup.send(f"My role isn't high enough for '{role.name}'.", ephemeral=True); return

            # Defer before role modification
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

            # --- Toggle Role ---
            try:
                action = ""
                if role in member.roles:
                    await member.remove_roles(role, reason="Self-removed via persistent button")
                    action = "removed"
                else:
                    await member.add_roles(role, reason="Self-assigned via persistent button")
                    action = "added"
                await interaction.followup.send(f"✅ Role '{role.name}' {action}.", ephemeral=True)
                logging.info(f"[Persistent] {action.capitalize()} role {role.id} for {member.id} G{guild.id}")
            except discord.Forbidden:
                 await interaction.followup.send(f"❌ Forbidden: Cannot modify role '{role.name}'.", ephemeral=True)
                 logging.warning(f"[Persistent] Forbidden role {role.id} for {member.id} G{guild.id}")
            except discord.HTTPException as e:
                 await interaction.followup.send(f"❌ Error modifying role: {e}", ephemeral=True)
                 logging.error(f"[Persistent] HTTPException role {role.id} for {member.id}: {e}")
            except Exception as e:
                 await interaction.followup.send(f"❌ Unexpected error.", ephemeral=True)
                 logging.exception(f"[Persistent] Unexpected error role {role.id} user {member.id}")

        # --- Dispatch other interactions (might not be needed) ---
        # If slash commands / other component interactions are handled by cogs,
        # the bot usually dispatches them automatically.
        # You might need this if you have global checks or prefix commands.
        # elif interaction.type == discord.InteractionType.application_command and not custom_id:
        #    pass # Let the command tree handle it

# --- Run the Bot ---
if __name__ == "__main__":
    if TOKEN is None:
        print("ERROR: DISCORD_TOKEN environment variable not set!")
        logging.critical("DISCORD_TOKEN environment variable not set!")
    else:
        bot_instance = MusicBot()
        try:
            # Use asyncio.run with bot.start() which handles the event loop
            asyncio.run(bot_instance.start(TOKEN))
        except discord.LoginFailure:
            print("ERROR: Invalid Discord Token.")
            logging.critical("Invalid Discord Token.")
        except KeyboardInterrupt:
             print("Bot shutdown requested.")
             logging.info("Bot shutdown requested via KeyboardInterrupt.")
        except Exception as e:
             print(f"An critical error occurred during bot execution: {e}")
             logging.exception("Critical error running bot")
