import discord
from discord import app_commands
from discord import ui
from discord.ext import commands
import json
import logging
import os

# --- Constants ---
ROLE_CONFIG_FILE = "role_config.json"

# Define allowed button styles for the choice parameter
ButtonStyleChoices = [
    app_commands.Choice(name="Secondary (Default Gray)", value="secondary"),
    app_commands.Choice(name="Primary (Blurple)", value="primary"),
    app_commands.Choice(name="Success (Green)", value="success"),
    app_commands.Choice(name="Danger (Red)", value="danger"),
]

# --- RoleAssignView Class (Only builds the view, no callbacks here) ---
class RoleAssignView(ui.View):
    def __init__(self, guild_id: int, role_mappings_for_guild: dict, timeout=None):
        super().__init__(timeout=timeout) # Pass timeout=None for persistence

        # Dynamically create buttons based on config for THIS guild
        for role_id_str, config in role_mappings_for_guild.items():
            role_id = int(role_id_str)
            style_str = config.get('style', 'secondary').lower()
            if style_str == 'primary': style = discord.ButtonStyle.primary
            elif style_str == 'success': style = discord.ButtonStyle.success
            elif style_str == 'danger': style = discord.ButtonStyle.danger
            else: style = discord.ButtonStyle.secondary

            button = ui.Button(
                label=config.get('label', f'Role {role_id}'),
                emoji=config.get('emoji'),
                style=style,
                custom_id=f"role_assign_{role_id}" # Persistent ID handled by on_interaction
            )
            self.add_item(button)


# --- Role Assign Cog Class ---
class RoleAssignCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.role_mappings = {} # Cog holds the authoritative mapping
        self._load_role_config() # Load config when cog initializes

    def _load_role_config(self):
        """Loads role mappings from the JSON file."""
        try:
            with open(ROLE_CONFIG_FILE, 'r') as f:
                loaded_config = json.load(f)
                # Convert keys back to integers
                self.role_mappings = {int(gid): {int(rid): data for rid, data in roles.items()}
                                      for gid, roles in loaded_config.items()}
                logging.info("Role configuration loaded successfully by RoleAssignCog.")
                # Update the main bot instance's reference if needed after loading
                self.bot.role_mappings = self.role_mappings
        except FileNotFoundError:
             logging.warning(f"{ROLE_CONFIG_FILE} not found. RoleAssignCog starting empty.")
             self.role_mappings = {}; self.bot.role_mappings = {}
        except Exception as e:
            logging.error(f"Failed to load role config in RoleAssignCog: {e}")
            self.role_mappings = {}; self.bot.role_mappings = {}


    def _save_role_config(self):
        """Saves current role mappings to the JSON file."""
        try:
            # Convert keys to strings for JSON
            config_to_save = {str(gid): {str(rid): data for rid, data in roles.items()}
                              for gid, roles in self.role_mappings.items()}
            with open(ROLE_CONFIG_FILE, 'w') as f:
                json.dump(config_to_save, f, indent=4)
            logging.info("Role configuration saved successfully by RoleAssignCog.")
        except Exception as e:
            logging.error(f"Failed to save role config in RoleAssignCog: {e}")


    # --- Slash Commands for Role Management ---
    @app_commands.command(name="setup_role", description="Adds or updates a self-assignable role button.")
    @app_commands.describe(role="Role", label="Button text", style="Button color", emoji="Optional emoji")
    @app_commands.choices(style=ButtonStyleChoices)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setup_role_slash(self, interaction: discord.Interaction, role: discord.Role, label: str, style: app_commands.Choice[str] = None, emoji: str = None):
        """Sets up or updates a role button."""
        guild_id = interaction.guild_id
        if not guild_id: return

        if len(label) > 80: await interaction.response.send_message("Label too long (max 80).", ephemeral=True); return
        if emoji and len(emoji) > 50: await interaction.response.send_message("Emoji too long.", ephemeral=True); return
        if interaction.guild.me.top_role <= role: await interaction.response.send_message(f"My role isn't high enough to manage '{role.name}'.", ephemeral=True); return

        if guild_id not in self.role_mappings: self.role_mappings[guild_id] = {}
        button_config = {'label': label, 'style': style.value if style else 'secondary', 'emoji': emoji}
        self.role_mappings[guild_id][role.id] = button_config
        self._save_role_config()

        await interaction.response.send_message(f"✅ Role button for '{role.name}' configured. Use `/role_menu`.", ephemeral=True)
        logging.info(f"Role {role.id} configured by {interaction.user.id} G{guild_id}")

    @app_commands.command(name="remove_role", description="Removes a role from the self-assignable button menu.")
    @app_commands.describe(role="The role button to remove.")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def remove_role_slash(self, interaction: discord.Interaction, role: discord.Role):
        """Removes a configured role button."""
        guild_id = interaction.guild_id
        if not guild_id: return

        if guild_id in self.role_mappings and role.id in self.role_mappings[guild_id]:
            del self.role_mappings[guild_id][role.id]
            if not self.role_mappings[guild_id]: del self.role_mappings[guild_id]
            self._save_role_config()
            await interaction.response.send_message(f"🗑️ Config for '{role.name}' removed.", ephemeral=True)
            logging.info(f"Role {role.id} removed by {interaction.user.id} G{guild_id}")
        else:
            await interaction.response.send_message(f"Role '{role.name}' isn't configured.", ephemeral=True)

    @app_commands.command(name="role_menu", description="Displays the message with self-assignable role buttons.")
    @app_commands.default_permissions(manage_roles=True) # Restrict who can post
    async def role_menu_slash(self, interaction: discord.Interaction):
        """Posts the role assignment menu."""
        guild_id = interaction.guild_id
        if not guild_id: return

        guild_map = self.role_mappings.get(guild_id, {})
        if not guild_map:
             await interaction.response.send_message("No roles configured. Use `/setup_role`.", ephemeral=True); return

        # Pass only the relevant guild's mappings and set timeout=None for persistence
        view = RoleAssignView(guild_id=guild_id, role_mappings_for_guild=guild_map, timeout=None)

        target_channel = interaction.channel
        try:
             await target_channel.send(content="**Self-Assignable Roles**\nClick buttons to add/remove:", view=view)
             await interaction.response.send_message(f"Role menu posted in {target_channel.mention}.", ephemeral=True)
             logging.info(f"Role menu posted by {interaction.user.id} G{guild_id}")
        except Exception as e:
             logging.error(f"Failed to post role menu for G{guild_id}: {e}")
             if not interaction.response.is_done():
                  await interaction.response.send_message(f"Failed to post role menu: {e}", ephemeral=True)


# --- Setup Function ---
async def setup(bot: commands.Bot):
    await bot.add_cog(RoleAssignCog(bot))
    logging.info("RoleAssignCog loaded.")
