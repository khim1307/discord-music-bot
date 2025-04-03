import discord
from discord.ext import commands
from discord import app_commands # Import app_commands
from discord import ui
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv
from collections import deque
import logging
import json

# --- Basic Setup ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
# message_content might become optional if you ONLY use slash commands
# but keep it for now if you have other message handling or want fallback
intents.message_content = True
intents.voice_states = True
intents.members = True

# REMOVE command_prefix if ONLY using slash commands, or keep for hybrid
# bot = commands.Bot(command_prefix='!', intents=intents) # Old
bot = commands.Bot(command_prefix="!", intents=intents) # Keep prefix for now? Or remove if going full slash

# --- Role Configuration ---
ROLE_CONFIG_FILE = "role_config.json"
role_mappings = {} # {guild_id: {role_id: {'label': 'Button Label', 'style': 'primary/secondary/etc.', 'emoji': 'optional_emoji'}}}

def load_role_config():
    """Loads role mappings from the JSON file."""
    global role_mappings
    try:
        with open(ROLE_CONFIG_FILE, 'r') as f:
            # Convert keys back to integers after loading from JSON
            loaded_config = json.load(f)
            role_mappings = {int(gid): {int(rid): data for rid, data in roles.items()}
                             for gid, roles in loaded_config.items()}
            logging.info("Role configuration loaded successfully.")
    except FileNotFoundError:
        logging.warning(f"{ROLE_CONFIG_FILE} not found. Initializing empty config.")
        role_mappings = {}
    except json.JSONDecodeError:
        logging.error(f"Error decoding {ROLE_CONFIG_FILE}. Initializing empty config.")
        role_mappings = {}
    except Exception as e:
        logging.error(f"Failed to load role config: {e}")
        role_mappings = {} # Fallback to empty

def save_role_config():
    """Saves current role mappings to the JSON file."""
    global role_mappings
    try:
        # Convert keys to strings for JSON compatibility
        config_to_save = {str(gid): {str(rid): data for rid, data in roles.items()}
                          for gid, roles in role_mappings.items()}
        with open(ROLE_CONFIG_FILE, 'w') as f:
            json.dump(config_to_save, f, indent=4)
        logging.info("Role configuration saved successfully.")
    except Exception as e:
        logging.error(f"Failed to save role config: {e}")

# --- Music Variables & Options (Remain Mostly the Same) ---
music_queues = {}
current_effects = {}
voice_clients = {}
now_playing_messages = {}

FFMPEG_BASE_OPTIONS = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
FFMPEG_NORMAL_OPTIONS = f'{FFMPEG_BASE_OPTIONS} -vn'
FFMPEG_BASS_BOOST_OPTIONS = f'{FFMPEG_BASE_OPTIONS} -af "bass=g=15,dynaudnorm=f=150:g=15" -vn'
FFMPEG_8D_OPTIONS = f'{FFMPEG_BASE_OPTIONS} -af "apulsator=hz=0.08" -vn'

YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    # 'cookiefile': '/home/ubuntu/discord-music-bot/cookies.txt', # Still optional
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)


# --- MusicControlsView (No changes needed here, it already uses interactions) ---
class MusicControlsView(ui.View):
    # ... (Keep the existing MusicControlsView code as is) ...
    # It uses interaction callbacks already which is perfect
    def __init__(self, *, timeout=None, bot_instance, guild_id):
        super().__init__(timeout=timeout)
        self.bot = bot_instance
        self.guild_id = guild_id
        self._update_buttons()

    def _get_voice_client(self):
        return voice_clients.get(self.guild_id)

    def _update_buttons(self):
        vc = self._get_voice_client()
        if vc and vc.is_paused():
            for item in self.children:
                if isinstance(item, ui.Button) and item.custom_id == "pause_resume":
                    item.label = "▶️ Resume"
                    item.style = discord.ButtonStyle.green
                    break
        elif vc and vc.is_playing():
             for item in self.children:
                if isinstance(item, ui.Button) and item.custom_id == "pause_resume":
                    item.label = "⏸️ Pause"
                    item.style = discord.ButtonStyle.secondary
                    break

    async def disable_all(self, interaction: discord.Interaction = None):
         for item in self.children:
            if isinstance(item, ui.Button): item.disabled = True

         target_message = None
         if interaction:
             target_message = interaction.message
         elif self.guild_id in now_playing_messages:
              target_message = now_playing_messages.get(self.guild_id)

         if target_message:
             try: await target_message.edit(view=self)
             except discord.NotFound: pass
             except Exception as e: logging.error(f"Error disabling view: {e}")

         if self.guild_id in now_playing_messages:
             del now_playing_messages[self.guild_id] # Clear reference after disabling

    @ui.button(label="⏸️ Pause", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume_button(self, interaction: discord.Interaction, button: ui.Button):
        vc = self._get_voice_client()
        if not vc:
            await interaction.response.send_message("I'm not connected.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            self._update_buttons() # Update internal state first
            await interaction.response.edit_message(view=self) # Then edit the message
        elif vc.is_paused():
            vc.resume()
            self._update_buttons()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("Nothing playing/paused.", ephemeral=True)

    @ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary, custom_id="skip")
    async def skip_button(self, interaction: discord.Interaction, button: ui.Button):
        vc = self._get_voice_client()
        guild_id = interaction.guild_id
        if vc and (vc.is_playing() or vc.is_paused()):
            await interaction.response.defer(ephemeral=True) # Acknowledge interaction quickly
            vc.stop() # This will trigger play_next via the 'after' callback
            await interaction.followup.send("Skipping...", ephemeral=True) # Confirm
        elif guild_id in music_queues and music_queues[guild_id]:
             await interaction.response.defer(ephemeral=True)
             await play_next(interaction.channel) # Pass channel
             await interaction.followup.send("Trying next in queue...", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to skip.", ephemeral=True)

    @ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop_button(self, interaction: discord.Interaction, button: ui.Button):
        vc = self._get_voice_client()
        guild_id = interaction.guild_id
        if vc and (vc.is_playing() or vc.is_paused()):
            if not interaction.response.is_done():
              await interaction.response.defer(ephemeral=True) # Acknowledge silently
            music_queues[guild_id].clear()
            vc.stop()
            # Need to disable buttons on the message this interaction came from
            await self.disable_all(interaction)
            await interaction.followup.send("⏹️ Stopped music and cleared the queue.") # Followup likely needed after edit
        else:
             await interaction.response.send_message("Not playing anything.", ephemeral=True)

    @ui.button(label="🔊 BB", style=discord.ButtonStyle.primary, custom_id="effect_bassboost")
    async def bassboost_button(self, interaction: discord.Interaction, button: ui.Button):
        """Applies Bass Boost effect for the next song."""
        guild_id = interaction.guild_id
        if guild_id is None: return # Should not happen

        current_effects[guild_id] = FFMPEG_BASS_BOOST_OPTIONS
        await interaction.response.send_message("🔊 Bass Boost enabled (applies to next song).", ephemeral=True)

    @ui.button(label="🎧 8D", style=discord.ButtonStyle.primary, custom_id="effect_8d")
    async def eightd_button(self, interaction: discord.Interaction, button: ui.Button):
        """Applies 8D effect for the next song."""
        guild_id = interaction.guild_id
        if guild_id is None: return

        current_effects[guild_id] = FFMPEG_8D_OPTIONS
        await interaction.response.send_message("🎧 8D Audio enabled (applies to next song).", ephemeral=True)

    @ui.button(label="⚪ Normal", style=discord.ButtonStyle.secondary, custom_id="effect_normal")
    async def normal_button(self, interaction: discord.Interaction, button: ui.Button):
        """Resets audio effects to normal for the next song."""
        guild_id = interaction.guild_id
        if guild_id is None: return

        current_effects[guild_id] = FFMPEG_NORMAL_OPTIONS
        await interaction.response.send_message("⚪ Audio effects reset to Normal (applies to next song).", ephemeral=True)
# --- YTDLSource Class (Keep as is) ---
class YTDLSource(discord.PCMVolumeTransformer):
    # ... (Keep the existing YTDLSource code as is) ...
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        if not self.thumbnail and data.get('thumbnails'):
             # Prioritize larger thumbnails if available in the list
             thumbnails = sorted(data['thumbnails'], key=lambda t: t.get('width', 0) * t.get('height', 0), reverse=True)
             if thumbnails: self.thumbnail = thumbnails[0].get('url') # Get URL of the best one

        self.extractor = data.get('extractor_key', 'Unknown').capitalize() # Get source (e.g., 'Youtube', 'Soundcloud')
    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, ffmpeg_options=FFMPEG_NORMAL_OPTIONS):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        except yt_dlp.utils.DownloadError as e:
            logging.error(f"YTDL DownloadError: {e}")
            return None

        if 'entries' in data: data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        final_ffmpeg_opts = f'{ffmpeg_options}'
        return cls(discord.FFmpegPCMAudio(filename, before_options=FFMPEG_BASE_OPTIONS, options=final_ffmpeg_opts.replace(FFMPEG_BASE_OPTIONS, '').strip()), data=data)

    @classmethod
    async def search(cls, query, *, loop=None, stream=False, ffmpeg_options=FFMPEG_NORMAL_OPTIONS):
        loop = loop or asyncio.get_event_loop()
        try:
            search_query = f"scsearch1:{query}" # Defaulting to SoundCloud
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=not stream))

            if 'entries' in data and data['entries']: data = data['entries'][0]
            elif not data: return None
            else: pass # Single result

            filename = data['url'] if stream else ytdl.prepare_filename(data)
            final_ffmpeg_opts = f'{ffmpeg_options}'
            return cls(discord.FFmpegPCMAudio(filename, before_options=FFMPEG_BASE_OPTIONS, options=final_ffmpeg_opts.replace(FFMPEG_BASE_OPTIONS, '').strip()), data=data)

        except yt_dlp.utils.DownloadError as e: logging.error(f"YTDL Search DownloadError: {e}"); return None
        except Exception as e: logging.error(f"Unexpected error during YTDL search: {e}"); return None


# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    # Initialize queues and effects (Keep this initialization)
    for guild in bot.guilds:
        if guild.id not in music_queues: music_queues[guild.id] = deque()
        if guild.id not in current_effects: current_effects[guild.id] = FFMPEG_NORMAL_OPTIONS
        if guild.id not in voice_clients: voice_clients[guild.id] = None
        if guild.id not in now_playing_messages: now_playing_messages[guild.id] = None
    load_role_config() # Load the role mappings
    # --- Command Syncing ---
    try:
        # Sync globally (can take up to an hour to propagate)
        # synced = await bot.tree.sync()
        # print(f"Synced {len(synced)} command(s) globally.")

        # OR Sync to a specific guild for faster testing (replace GUILD_ID)
        # GUILD_ID = 123456789012345678 # Replace with your test server's ID
        # guild_obj = discord.Object(id=GUILD_ID)
        # bot.tree.copy_global_to(guild=guild_obj)
        # synced = await bot.tree.sync(guild=guild_obj)
        # print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}.")

        # Choose one sync method - guild sync is better for development
        # For production, use global sync (and be patient)
        # Using global sync here as an example
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s) globally.")

    except Exception as e:
        print(f"Error syncing commands: {e}")

# --- Global Interaction Handler for Persistent Role Buttons ---
@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Always defer or respond to non-view interactions first if necessary
    # If using only slash commands, bot.process_commands might not be needed
    # await bot.process_commands(interaction) # Use if you still have prefix commands

    # Check if it's a button click (component interaction)
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get('custom_id')

        # Check if it's one of our persistent role buttons
        if custom_id and custom_id.startswith("role_assign_"):
            # --- Role Assignment Logic (Mirrors the old callback) ---
            try:
                role_id = int(custom_id.split('_')[-1])
            except (IndexError, ValueError):
                await interaction.response.send_message("Could not identify the role for this button.", ephemeral=True)
                return

            guild = interaction.guild
            # Ensure interaction is in a guild and member is valid
            if not guild or not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("This interaction can only happen in a server.", ephemeral=True)
                return
            member = interaction.user # Member who clicked

            # Fetch the role object
            role = guild.get_role(role_id)
            if not role:
                await interaction.response.send_message("The role for this button no longer exists.", ephemeral=True)
                return

            # Check bot permissions before deferring
            if not guild.me.guild_permissions.manage_roles:
                await interaction.response.send_message("I don't have the 'Manage Roles' permission.", ephemeral=True)
                return
            if guild.me.top_role <= role:
                await interaction.response.send_message(f"I cannot manage '{role.name}' due to role hierarchy.", ephemeral=True)
                return

            # Defer response - essential for operations that might take time
            await interaction.response.defer(ephemeral=True, thinking=False) # Ephemeral ACK

            try:
                # Toggle the role
                if role in member.roles:
                    await member.remove_roles(role, reason="Self-removed via persistent button")
                    await interaction.followup.send(f"✅ Role '{role.name}' removed.", ephemeral=True)
                    logging.info(f"[Persistent] Removed role {role.id} from {member.id} in guild {guild.id}")
                else:
                    await member.add_roles(role, reason="Self-assigned via persistent button")
                    await interaction.followup.send(f"✅ Role '{role.name}' added!", ephemeral=True)
                    logging.info(f"[Persistent] Added role {role.id} to {member.id} in guild {guild.id}")

            except discord.Forbidden:
                await interaction.followup.send(f"❌ Forbidden: Cannot modify roles for '{role.name}'.", ephemeral=True)
                logging.warning(f"[Persistent] Forbidden error modifying role {role.id} for user {member.id} in guild {guild.id}")
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Error modifying roles: {e}", ephemeral=True)
                logging.error(f"[Persistent] HTTPException modifying role {role.id} for {member.id}: {e}")
            except Exception as e:
                 await interaction.followup.send(f"❌ Unexpected error.", ephemeral=True)
                 logging.error(f"[Persistent] Unexpected error in role handler role {role.id} user {member.id}: {e}")

        # --- Handle other component interactions if needed ---
        # E.g., you could check for music control button custom_ids here
        # if the View object might not always be available, although
        # for music controls, the View is usually tied to the current song
        # and cleaned up, so relying on the View object's callback is often fine.
# --- Music Playback Core Logic (Needs Adjustment) ---
# Modify play_next to handle interaction or channel context better
async def play_next(interaction_or_channel):
    if isinstance(interaction_or_channel, discord.Interaction):
        guild_id = interaction_or_channel.guild_id
        channel = interaction_or_channel.channel # Text channel where interaction happened
        # Need to potentially defer if we are sending messages later
    elif isinstance(interaction_or_channel, discord.TextChannel):
        guild_id = interaction_or_channel.guild.id
        channel = interaction_or_channel
    else:
        logging.error(f"Invalid context type in play_next: {type(interaction_or_channel)}")
        return

    # --- Clean up previous Now Playing message ---
    if guild_id in now_playing_messages and now_playing_messages[guild_id]:
        old_message = now_playing_messages[guild_id]
        try:
            view = MusicControlsView(bot_instance=bot, guild_id=guild_id)
            for item in view.children: item.disabled = True
            await old_message.edit(content="*Playback ended or skipped.*", embed=None, view=view)
        except discord.NotFound: pass
        except Exception as e: logging.error(f"Error editing old Now Playing message: {e}")
        finally: now_playing_messages[guild_id] = None

    # --- Play next song logic ---
    if guild_id in music_queues and music_queues[guild_id]:
        queue_entry = music_queues[guild_id].popleft()
        query = queue_entry.get('query', 'Unknown Query') # Get query
        requester = queue_entry.get('requester') # Get discord.User object
        ffmpeg_options = current_effects.get(guild_id, FFMPEG_NORMAL_OPTIONS)
        voice_client = voice_clients.get(guild_id)

        if not voice_client or not voice_client.is_connected():
            logging.warning(f"VC disconnected for guild {guild_id}.")
            # Check if we have an interaction to respond to
            if isinstance(interaction_or_channel, discord.Interaction):
                 # Avoid sending followup if already responded
                 if not interaction_or_channel.response.is_done():
                      await interaction_or_channel.response.send_message("Lost connection.", ephemeral=True)
            else: # Send to channel if called from 'after' callback
                await channel.send("Lost connection, cannot play next song.")
            return

        thinking_message = None
        try:
            # --- Send "Thinking" ---
            # If called from an interaction, defer might be needed
            if isinstance(interaction_or_channel, discord.Interaction) and not interaction_or_channel.response.is_done():
                 await interaction_or_channel.response.defer(ephemeral=True, thinking=True) # Defer ephemerally? Or public thinking?
                 # Public thinking alternative:
                 # await interaction.response.defer() # Defer publicly
                 # thinking_message = await interaction.followup.send(f"🔄 Searching for `{query}`...")
            else:
                 # If called from 'after', send normally
                 thinking_message = await channel.send(f"🔄 Searching for `{query}`...")

            player = None
            if "http://" in query or "https://" in query:
                player = await YTDLSource.from_url(query, loop=bot.loop, stream=True, ffmpeg_options=ffmpeg_options)
            else:
                player = await YTDLSource.search(query, loop=bot.loop, stream=True, ffmpeg_options=ffmpeg_options)

            if thinking_message: # Delete thinking message if we sent one
                 try: await thinking_message.delete()
                 except discord.NotFound: pass

            if player is None:
                 err_msg = f"❌ Couldn't find or play '{query}'. Skipping."
                 if isinstance(interaction_or_channel, discord.Interaction):
                      # Ensure we send a followup if deferred
                      await interaction_or_channel.followup.send(err_msg, ephemeral=True)
                 else:
                      await channel.send(err_msg)
                 await play_next(channel) # Try next, pass channel
                 return
            # --- Build the New Embed ---
            view = MusicControlsView(bot_instance=bot, guild_id=guild_id)
            embed = discord.Embed(
                title="🎶 Now Playing",
                color=discord.Color.green() # Changed color slightly
            )

            # Clickable Title + URL
            if player.url:
                embed.description = f"**[{player.title}]({player.url})**"
            else:
                embed.description = f"**{player.title}**"

            # Thumbnail
            if player.thumbnail:
                embed.set_thumbnail(url=player.thumbnail)

            # Fields
            if player.duration:
                duration_str = f"{int(player.duration // 60)}:{int(player.duration % 60):02d}"
                embed.add_field(name="Duration", value=duration_str, inline=True)
            else:
                 embed.add_field(name="Duration", value="N/A", inline=True) # Placeholder

            if requester:
                embed.add_field(name="Requested by", value=requester.mention, inline=True)
            else:
                embed.add_field(name="Requested by", value="Unknown", inline=True) # Fallback

            # Queue Info - Get current queue length *after* popping
            queue_len = len(music_queues.get(guild_id, []))
            embed.add_field(name="Queue", value=f"{queue_len} remaining", inline=True)

            # Footer (Source Info)
            footer_text = f"Source: {player.extractor}"
            # Optional: Add icons
            icon_url = ""
            if player.extractor == 'Soundcloud':
                # Find a soundcloud icon URL online if desired
                 icon_url = "https://icons.iconarchive.com/icons/custom-icon-design/pretty-office-7/32/Soundcloud-icon.png" # Example
            elif player.extractor == 'Youtube':
                 # Find a youtube icon URL online if desired
                 icon_url = "https://icons.iconarchive.com/icons/social-media-icons/glossy-social-media/32/Youtube-icon.png" # Example

            if icon_url:
                embed.set_footer(text=footer_text, icon_url=icon_url)
            else:
                embed.set_footer(text=footer_text)

            # --- Send New Now Playing Message with Buttons ---
            view = MusicControlsView(bot_instance=bot, guild_id=guild_id)
            embed = discord.Embed(title="🎶 Now Playing", description=f"**{player.title}**", color=discord.Color.blurple())
            if player.url: embed.url = player.url
            if player.duration: embed.add_field(name="Duration", value=f"{int(player.duration // 60)}:{int(player.duration % 60):02d}")

            # Send message - use followup if interaction was deferred, otherwise send to channel
            now_playing_msg = None
            if isinstance(interaction_or_channel, discord.Interaction) and interaction_or_channel.response.is_done():
                 now_playing_msg = await interaction_or_channel.followup.send(embed=embed, view=view, wait=True) # wait=True gets message obj
            else: # Called from 'after' or initial play response not deferred
                 now_playing_msg = await channel.send(embed=embed, view=view)

            now_playing_messages[guild_id] = now_playing_msg # Store reference

            # Play audio - use channel context for 'after' callback
            voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next_after_error(channel, e), bot.loop))

        except Exception as e:
            logging.error(f"Error playing next song: {e}")
            err_msg = f"An error occurred: {e}"
            # Send error - use followup if deferred
            if isinstance(interaction_or_channel, discord.Interaction) and interaction_or_channel.response.is_done():
                 await interaction_or_channel.followup.send(err_msg, ephemeral=True)
            else:
                 await channel.send(err_msg)
            await asyncio.sleep(2)
            await play_next(channel) # Try next, pass channel

    else: # Queue finished
        # ... (Keep queue finished logic, ensuring it uses 'channel' to send final message) ...
        if guild_id in now_playing_messages and now_playing_messages[guild_id]:
            old_message = now_playing_messages[guild_id]
            try:
                view = MusicControlsView(bot_instance=bot, guild_id=guild_id)
                for item in view.children: item.disabled = True
                await old_message.edit(content="⏹️ Queue finished.", embed=None, view=view)
            except discord.NotFound: pass
            except Exception as e: logging.error(f"Error editing final NP msg: {e}")
            finally: now_playing_messages[guild_id] = None
        else:
            await channel.send("⏹️ Queue finished!")


async def play_next_after_error(channel, error):
    if error:
        logging.error(f'Player error: {error}')
        try:
            await channel.send(f'Player error: {error}. Skipping.')
        except Exception as e:
            logging.error(f"Failed to send player error message: {e}")
    await play_next(channel) # Pass channel context

# --- Slash Commands ---

# Helper function to get or connect voice client
async def ensure_voice(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    voice_client = voice_clients.get(guild_id)

    # Check if already connected
    if voice_client and voice_client.is_connected():
        return voice_client

    # Check if user is in a voice channel
    if not interaction.user.voice:
        await interaction.response.send_message("You need to be in a voice channel first!", ephemeral=True)
        return None

    # Attempt to connect
    channel = interaction.user.voice.channel
    try:
        voice_client = await channel.connect()
        voice_clients[guild_id] = voice_client
        # Initialize state if joining for first time
        if guild_id not in music_queues: music_queues[guild_id] = deque()
        if guild_id not in current_effects: current_effects[guild_id] = FFMPEG_NORMAL_OPTIONS
        return voice_client
    except Exception as e:
        logging.error(f"Error joining voice channel {channel.name}: {e}")
        await interaction.response.send_message(f"Failed to join {channel.name}. Error: {e}", ephemeral=True)
        return None

@bot.tree.command(name="help", description="Displays a list of available commands.")
async def help_slash(interaction: discord.Interaction):
    """Shows available slash commands."""
    try:
        embed = discord.Embed(
            title=f"{bot.user.name} Commands",
            description="Here are the available slash commands:",
            color=discord.Color.blue() # Or your preferred color
        )

        # Get commands registered to the tree (handles global and guild-specific if synced)
        # Sorting commands alphabetically
        registered_commands = sorted(bot.tree.get_commands(), key=lambda cmd: cmd.name)

        if not registered_commands:
             embed.description = "No slash commands seem to be registered currently."
        else:
            for command in registered_commands:
                # Future: Could add specific formatting for command groups if you add them later
                # if isinstance(command, app_commands.Group):
                #    pass # Add group handling
                # else:
                embed.add_field(
                    name=f"/{command.name}",
                    value=command.description or "No description provided.", # Use description
                    inline=False # List each command on a new line
                )

        embed.set_footer(text="Use /<command name> to run a command.")

        # Send the embed as an ephemeral message (only visible to the user who typed /help)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        logging.error(f"Error generating help command: {e}")
        # Avoid failing silently if embed creation fails
        try:
            await interaction.response.send_message("Sorry, couldn't generate the help message right now.", ephemeral=True)
        except discord.InteractionResponded: # Might have already responded if error was late
            await interaction.followup.send("Sorry, couldn't generate the help message right now.", ephemeral=True)

@bot.tree.command(name="join", description="Tells the bot to join your voice channel.")
async def join_slash(interaction: discord.Interaction):
    """Joins the user's voice channel."""
    if not interaction.user.voice:
        await interaction.response.send_message("You are not connected to a voice channel.", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    guild_id = interaction.guild_id
    vc = voice_clients.get(guild_id)

    if vc and vc.is_connected():
        if vc.channel != channel:
            await vc.move_to(channel)
            await interaction.response.send_message(f"Moved to {channel.name}.", ephemeral=True)
        else:
            await interaction.response.send_message("Already in your voice channel.", ephemeral=True)
    else:
        new_vc = await ensure_voice(interaction) # Use helper to connect
        if new_vc:
             # Response is handled within ensure_voice on error
             await interaction.response.send_message(f"Joined {channel.name}.", ephemeral=True)

@bot.tree.command(name="play", description="Searches SoundCloud/plays URL and adds to queue.")
@app_commands.describe(query="The SoundCloud search term or a URL (SC/YT).")
async def play_slash(interaction: discord.Interaction, query: str):
    """Plays or queues music. Searches SoundCloud by default."""
    guild_id = interaction.guild_id

    # Ensure bot is connected to VC
    voice_client = await ensure_voice(interaction)
    if not voice_client:
        # ensure_voice sends the error response
        return

    # Defer the response as searching/playback takes time
    await interaction.response.defer() # Public deferral

    if guild_id not in music_queues: music_queues[guild_id] = deque()

    is_playing_or_paused = voice_client.is_playing() or voice_client.is_paused()
    # Store query and requester info together
    queue_entry = {
        'query': query,
        'requester': interaction.user # Store the discord.User object
    }
    music_queues[guild_id].append(queue_entry)
    # Send confirmation message via followup
    await interaction.followup.send(f"✅ Added to queue: **{query}**")

    # Start playback if not already playing
    if not is_playing_or_paused:
        # Pass the interaction object itself to play_next
        await play_next(interaction)


@bot.tree.command(name="queue", description="Shows the current music queue.")
async def queue_slash(interaction: discord.Interaction):
    """Shows the music queue."""
    # This command is simple enough to reuse most of the old logic
    # Just need to respond via interaction
    guild_id = interaction.guild_id
    queue = music_queues.get(guild_id)
    voice_client = voice_clients.get(guild_id)
    current_msg = now_playing_messages.get(guild_id)

    # ... (Keep the embed creation logic from the old queue command) ...
    if not queue and (not voice_client or not voice_client.source):
         await interaction.response.send_message("The queue is empty and nothing is playing!", ephemeral=True)
         return

    embed = discord.Embed(title="Music Queue", color=discord.Color.blue())
    current_song = ""
    if voice_client and voice_client.source and hasattr(voice_client.source, 'title'):
        current_song = f"▶️ **{voice_client.source.title}**"
        if hasattr(voice_client.source, 'duration') and voice_client.source.duration:
             duration = voice_client.source.duration
             current_song += f" ({int(duration // 60)}:{int(duration % 60):02d})"
        current_song += "\n\n"
    elif current_msg and current_msg.embeds:
         current_song = f"▶️ {current_msg.embeds[0].description}\n\n"

    queue_list = ""
    if queue:
        for i, item in enumerate(list(queue)[:10]):
            queue_list += f"{i + 1}. {item}\n"
        if len(queue) > 10:
            queue_list += f"\n...and {len(queue) - 10} more."
    else:
        queue_list = "Queue is empty."

    embed.description = current_song + queue_list

    await interaction.response.send_message(embed=embed) # Send embed as response

@bot.tree.command(name="leave", description="Disconnects the bot from the voice channel.")
async def leave_slash(interaction: discord.Interaction):
    """Disconnects the bot."""
    guild_id = interaction.guild_id
    voice_client = voice_clients.get(guild_id)

    if voice_client and voice_client.is_connected():
        # Clean up now playing message
        if guild_id in now_playing_messages and now_playing_messages[guild_id]:
            old_message = now_playing_messages[guild_id]
            try:
                view = MusicControlsView(bot_instance=bot, guild_id=guild_id)
                for item in view.children: item.disabled = True
                await old_message.edit(content="Disconnected.", embed=None, view=view)
            except Exception as e: logging.error(f"Error editing NP msg on leave: {e}")
            finally: now_playing_messages[guild_id] = None

        if guild_id in music_queues: music_queues[guild_id].clear()
        await voice_client.disconnect()
        voice_clients[guild_id] = None
        await interaction.response.send_message("Disconnected from the voice channel.", ephemeral=True)
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)

# --- Audio Effects Slash Commands ---
async def apply_effect_slash(interaction: discord.Interaction, effect_name: str, ffmpeg_options: str):
     guild_id = interaction.guild_id
     current_effects[guild_id] = ffmpeg_options
     await interaction.response.send_message(f"🎧 Effect set to: **{effect_name}**. Applies to next song.", ephemeral=True)

@bot.tree.command(name="bassboost", description="Applies bass boost effect to next song.")
async def bassboost_slash(interaction: discord.Interaction):
     await apply_effect_slash(interaction, "Bass Boost", FFMPEG_BASS_BOOST_OPTIONS)

@bot.tree.command(name="8d", description="Applies 8D audio effect to next song.")
async def eightd_slash(interaction: discord.Interaction):
      await apply_effect_slash(interaction, "8D Audio", FFMPEG_8D_OPTIONS)

@bot.tree.command(name="normal", description="Resets audio effects to normal for next song.")
async def normal_slash(interaction: discord.Interaction):
      await apply_effect_slash(interaction, "Normal", FFMPEG_NORMAL_OPTIONS)

# Button View for Role Assignment
class RoleAssignView(ui.View):
    def __init__(self, guild_id: int, timeout=None):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id

        # Load mappings for this specific guild
        guild_map = role_mappings.get(self.guild_id, {})

        # Dynamically create buttons based on config
        for role_id_str, config in guild_map.items():
            role_id = int(role_id_str) # Role ID is stored as string key in JSON/dict

            # Determine button style
            style_str = config.get('style', 'secondary').lower()
            if style_str == 'primary': style = discord.ButtonStyle.primary
            elif style_str == 'success': style = discord.ButtonStyle.success
            elif style_str == 'danger': style = discord.ButtonStyle.danger
            else: style = discord.ButtonStyle.secondary # Default

            # Create and add the button
            button = ui.Button(
                label=config.get('label', f'Role {role_id}'),
                emoji=config.get('emoji'), # Will be None if not set
                style=style,
                custom_id=f"role_assign_{role_id}" # Use role ID in custom_id
            )
            self.add_item(button)

# --- Role Setup Commands ---

# Define allowed button styles for the choice parameter
ButtonStyleChoices = [
    app_commands.Choice(name="Secondary (Default Gray)", value="secondary"),
    app_commands.Choice(name="Primary (Blurple)", value="primary"),
    app_commands.Choice(name="Success (Green)", value="success"),
    app_commands.Choice(name="Danger (Red)", value="danger"),
]

@bot.tree.command(name="setup_role", description="Adds or updates a self-assignable role button.")
@app_commands.describe(
    role="The role members can assign themselves.",
    label="The text displayed on the button.",
    style="The color/style of the button.",
    emoji="An optional emoji for the button (e.g., ✅ or custom :emoji:)."
)
@app_commands.choices(style=ButtonStyleChoices) # Use choices for the style
@app_commands.default_permissions(manage_roles=True) # Require manage_roles for setup
@app_commands.checks.has_permissions(manage_roles=True)
async def setup_role_slash(interaction: discord.Interaction, role: discord.Role, label: str, style: app_commands.Choice[str] = None, emoji: str = None):
    """Sets up or updates a role button."""
    guild_id = interaction.guild_id
    if not guild_id: return # Should not happen in guild command

    # Basic validation
    if len(label) > 80: # Discord button label limit
        await interaction.response.send_message("Button label cannot exceed 80 characters.", ephemeral=True)
        return
    if emoji and len(emoji) > 50: # Basic emoji length check
        await interaction.response.send_message("Emoji seems too long.", ephemeral=True)
        return
    # Check if bot can even assign this role (hierarchy)
    if interaction.guild.me.top_role <= role:
         await interaction.response.send_message(f"❌ I cannot manage the role '{role.name}' due to role hierarchy. My highest role must be above it.", ephemeral=True)
         return

    # Ensure guild_id entry exists
    if guild_id not in role_mappings:
        role_mappings[guild_id] = {}

    # Store configuration
    button_config = {
        'label': label,
        'style': style.value if style else 'secondary', # Use choice value or default
        'emoji': emoji # Store emoji string (or None)
    }
    role_mappings[guild_id][role.id] = button_config # Use role ID as key

    save_role_config() # Save changes to JSON

    await interaction.response.send_message(f"✅ Role button for '{role.name}' configured with label '{label}'. Use `/role_menu` to display it.", ephemeral=True)
    logging.info(f"Role {role.id} configured by {interaction.user.id} in guild {guild_id}")

@bot.tree.command(name="remove_role", description="Removes a role from the self-assignable button menu.")
@app_commands.describe(role="The role button to remove.")
@app_commands.default_permissions(manage_roles=True)
@app_commands.checks.has_permissions(manage_roles=True)
async def remove_role_slash(interaction: discord.Interaction, role: discord.Role):
    """Removes a configured role button."""
    guild_id = interaction.guild_id
    if not guild_id: return

    if guild_id in role_mappings and role.id in role_mappings[guild_id]:
        del role_mappings[guild_id][role.id]
        # Optional: remove guild entry if it becomes empty
        if not role_mappings[guild_id]:
            del role_mappings[guild_id]

        save_role_config() # Save changes
        await interaction.response.send_message(f"🗑️ Role button configuration for '{role.name}' removed.", ephemeral=True)
        logging.info(f"Role {role.id} configuration removed by {interaction.user.id} in guild {guild_id}")
    else:
        await interaction.response.send_message(f"Role '{role.name}' is not currently configured for self-assignment.", ephemeral=True)


@bot.tree.command(name="role_menu", description="Displays the message with self-assignable role buttons.")
@app_commands.default_permissions(manage_roles=True) # Optionally restrict who can post the menu
# @app_commands.checks.has_permissions(manage_roles=True) # Or remove checks to allow anyone
async def role_menu_slash(interaction: discord.Interaction):
    """Posts the role assignment menu."""
    guild_id = interaction.guild_id
    if not guild_id: return

    guild_map = role_mappings.get(guild_id, {})
    if not guild_map:
        await interaction.response.send_message("No self-assignable roles have been configured for this server yet. Use `/setup_role` first.", ephemeral=True)
        return

    # Create the view based on current config
    view = RoleAssignView(guild_id=guild_id, timeout=None)
 # Example: Send to the channel where command was used
    target_channel = interaction.channel
    await target_channel.send(
        content="**Roles tự chọn**\nNhấn vào role muốn nhận, nhấn lại để xóa role", # Example text
        view=view
    )

    # Send the message containing the view
    await interaction.response.send_message(
        content="Click the buttons below to add or remove roles:", # Optional introductory text
        view=view
    )
    logging.info(f"Role menu posted by {interaction.user.id} in guild {guild_id}")

# --- Administrative Slash Commands ---

# Set default permissions for admin commands (users need these perms in the server)
admin_perms = discord.Permissions(kick_members=True, ban_members=True, manage_messages=True)

@bot.tree.command(name="kick", description="Kicks a member from the server.")
@app_commands.describe(member="The member to kick.", reason="The reason for kicking.")
@app_commands.default_permissions(kick_members=True) # Set default Discord perm
@app_commands.checks.has_permissions(kick_members=True) # Double check in code
async def kick_slash(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    """Kicks a member."""
    if member == interaction.user:
        await interaction.response.send_message("You cannot kick yourself!", ephemeral=True)
        return
    if member == bot.user:
        await interaction.response.send_message("I cannot kick myself!", ephemeral=True)
        return
    if interaction.guild.me.top_role <= member.top_role:
         await interaction.response.send_message("My role is not high enough to kick this member.", ephemeral=True)
         return

    try:
        await member.kick(reason=f"Kicked by {interaction.user.name}: {reason}")
        await interaction.response.send_message(f"👢 Kicked {member.mention} for: {reason}")
        logging.info(f"User {interaction.user} kicked {member} for reason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have the permissions to kick.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to kick. Error: {e}", ephemeral=True)
        logging.error(f"HTTPException kicking {member}: {e}")


@bot.tree.command(name="ban", description="Bans a member from the server.")
@app_commands.describe(member="The member to ban.", reason="The reason for banning.")
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
async def ban_slash(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    """Bans a member."""
    # Similar checks as kick...
    if member == interaction.user: await interaction.response.send_message("You cannot ban yourself!", ephemeral=True); return
    if member == bot.user: await interaction.response.send_message("I cannot ban myself!", ephemeral=True); return
    if interaction.guild.me.top_role <= member.top_role: await interaction.response.send_message("My role is not high enough.", ephemeral=True); return

    try:
        await member.ban(reason=f"Banned by {interaction.user.name}: {reason}", delete_message_days=0)
        await interaction.response.send_message(f"🔨 Banned {member.mention} for: {reason}")
        logging.info(f"User {interaction.user} banned {member} for reason: {reason}")
    except discord.Forbidden: await interaction.response.send_message("❌ I lack ban permissions.", ephemeral=True)
    except discord.HTTPException as e: await interaction.response.send_message(f"❌ Failed to ban. Error: {e}", ephemeral=True); logging.error(f"HTTPException banning {member}: {e}")


@bot.tree.command(name="clear", description="Clears a specified number of messages.")
@app_commands.describe(amount="Number of messages to delete (max 100).")
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
async def clear_slash(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]): # Use Range for validation
    """Clears messages."""
    # Defer response as purge takes time and we send a followup
    await interaction.response.defer(ephemeral=True)
    try:
        # Don't add +1, interaction doesn't count as a message to purge
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🗑️ Deleted {len(deleted)} message(s).", ephemeral=True) # Followup after defer
        logging.info(f"User {interaction.user} cleared {len(deleted)} messages in channel {interaction.channel.id}")
    except discord.Forbidden: await interaction.followup.send("❌ I lack 'Manage Messages' permission.", ephemeral=True)
    except discord.HTTPException as e: await interaction.followup.send(f"❌ Failed to clear. Error: {e}", ephemeral=True); logging.error(f"HTTPException clearing messages in {interaction.channel.id}: {e}")


# --- Error Handling for App Commands ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandNotFound):
        # This usually won't happen with synced slash commands
        await interaction.response.send_message("Command not found?", ephemeral=True)
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(f"⛔ You lack permissions: `{' '.join(error.missing_permissions)}`", ephemeral=True)
    elif isinstance(error, app_commands.BotMissingPermissions):
         await interaction.response.send_message(f"❌ I lack permissions: `{' '.join(error.missing_permissions)}`", ephemeral=True)
    elif isinstance(error, app_commands.CheckFailure): # General check failure
         await interaction.response.send_message("⛔ You don't meet the requirements to use this command.", ephemeral=True)
    elif isinstance(error, app_commands.CommandInvokeError):
         original = error.original
         logging.error(f"Error executing app command {interaction.command.name if interaction.command else 'unknown'}: {original}")
         # You can add specific error checking here like in the old handler
         err_msg = f"An error occurred: {original}"
         if len(err_msg) > 1900: err_msg = err_msg[:1900] + "..." # Truncate long errors
         if interaction.response.is_done():
             await interaction.followup.send(err_msg, ephemeral=True)
         else:
             await interaction.response.send_message(err_msg, ephemeral=True)
    else:
        logging.error(f"Unhandled app command error: {error}")
        if interaction.response.is_done():
             await interaction.followup.send(f"An unexpected error occurred.", ephemeral=True)
        else:
             await interaction.response.send_message(f"An unexpected error occurred.", ephemeral=True)


# --- Remove or Comment Out Old Prefix Command Error Handler ---
# @bot.event
# async def on_command_error(ctx, error):
#    # ... (Old handler code) ...


# --- Run the Bot ---
if __name__ == "__main__":
    if TOKEN is None:
        print("ERROR: DISCORD_TOKEN environment variable not set!")
    else:
        try:
            bot.run(TOKEN)
        except discord.LoginFailure: print("ERROR: Invalid Discord Token.")
        except Exception as e: print(f"An error occurred while running the bot: {e}")
