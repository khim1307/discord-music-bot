# cogs/music_cog.py

import discord
from discord import app_commands
from discord import ui
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import logging
import time
from collections import deque
import os

# --- Constants ---
FFMPEG_BASE_OPTIONS = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
FFMPEG_NORMAL_OPTIONS = f'{FFMPEG_BASE_OPTIONS} -vn'
FFMPEG_BASS_BOOST_OPTIONS = f'{FFMPEG_BASE_OPTIONS} -af "bass=g=15,dynaudnorm=f=150:g=15" -vn'
FFMPEG_8D_OPTIONS = f'{FFMPEG_BASE_OPTIONS} -af "apulsator=hz=0.08" -vn'
INACTIVITY_TIMEOUT = 120 # Seconds

# --- YTDL Options ---
YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'cookiefile': '/home/ubuntu/discord-music-bot/cookies.txt',
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)


# --- YTDLSource Class (Keep as is) ---
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('webpage_url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        if not self.thumbnail and data.get('thumbnails'):
            thumbnails = sorted(data['thumbnails'], key=lambda t: t.get('width', 0) * t.get('height', 0), reverse=True)
            if thumbnails: self.thumbnail = thumbnails[0].get('url')
        self.extractor = data.get('extractor_key', 'Unknown').capitalize()
        self.uploader = data.get('uploader') or data.get('channel')
        self.uploader_url = data.get('uploader_url') or data.get('channel_url')
        self.view_count = data.get('view_count')
        self.like_count = data.get('like_count')
        self.upload_date = data.get('upload_date') # Format: YYYYMMDD

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, ffmpeg_options=FFMPEG_NORMAL_OPTIONS):
        loop = loop or asyncio.get_event_loop()
        try: data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        except yt_dlp.utils.DownloadError as e: logging.error(f"YTDL DownloadError URL: {e}"); return None
        if 'entries' in data: data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data); final_ffmpeg_opts = f'{ffmpeg_options}'
        return cls(discord.FFmpegPCMAudio(filename, before_options=FFMPEG_BASE_OPTIONS, options=final_ffmpeg_opts.replace(FFMPEG_BASE_OPTIONS, '').strip()), data=data)

    @classmethod
    async def search(cls, query, *, loop=None, stream=False, ffmpeg_options=FFMPEG_NORMAL_OPTIONS):
        loop = loop or asyncio.get_event_loop()
        try:
            search_query = f"ytsearch1:{query}" # YouTube Search (or scsearch1:)
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=not stream))
        except yt_dlp.utils.DownloadError as e: logging.error(f"YTDL Search DownloadError: {e}"); return None
        except Exception as e: logging.error(f"YTDL Search Error: {e}"); return None
        if not data or not data.get('entries'): logging.warning(f"YTDL No results '{query}'"); return None
        data = data['entries'][0]; filename = data['url'] if stream else ytdl.prepare_filename(data); final_ffmpeg_opts = f'{ffmpeg_options}'
        return cls(discord.FFmpegPCMAudio(filename, before_options=FFMPEG_BASE_OPTIONS, options=final_ffmpeg_opts.replace(FFMPEG_BASE_OPTIONS, '').strip()), data=data)


# --- MusicControlsView Class ---
class MusicControlsView(ui.View):
    def __init__(self, *, timeout=None, music_cog_instance):
        super().__init__(timeout=timeout)
        self.music_cog = music_cog_instance
        self.guild_id = None
        logging.debug(f"Initializing MusicControlsView (Timeout: {timeout})")
        for item in self.children:
             if isinstance(item, ui.Button): logging.debug(f"View Init found child button: {item.custom_id} ({item.label})")
        logging.debug(f"MusicControlsView initialized with {len(self.children)} children.")

    def _get_voice_client(self):
        if not self.guild_id: logging.debug(f"_get_voice_client: No guild_id set."); return None
        vc = self.music_cog.voice_clients.get(self.guild_id)
        logging.debug(f"_get_voice_client G{self.guild_id}: Found VC: {'Yes' if vc else 'No'}")
        return vc

    def _update_buttons(self):
        vc = self._get_voice_client()
        if not vc:
            logging.debug(f"_update_buttons G{self.guild_id}: No VC, disabling playback controls.")
            for item in self.children:
                if isinstance(item, ui.Button):
                    if item.custom_id in ["pause_resume", "skip", "stop"]: item.disabled = True
                    else: item.disabled = False # Keep effect/queue enabled
            return

        is_paused=vc.is_paused(); is_playing=vc.is_playing()
        can_interact_playback = is_playing or is_paused
        logging.debug(f"_update_buttons G{self.guild_id}: playing={is_playing}, paused={is_paused}, can_interact={can_interact_playback}")

        for item in self.children:
            if isinstance(item, ui.Button):
                if item.custom_id == "pause_resume":
                    item.label = "▶️ Resume" if is_paused else "⏸️ Pause"
                    item.style = discord.ButtonStyle.green if is_paused else discord.ButtonStyle.secondary
                    item.disabled = not can_interact_playback
                    logging.debug(f"  Btn {item.custom_id}: Disabled={item.disabled}")
                elif item.custom_id in ["skip", "stop"]:
                     item.disabled = not can_interact_playback
                     logging.debug(f"  Btn {item.custom_id}: Disabled={item.disabled}")
                elif item.custom_id.startswith("effect_") or item.custom_id == "show_queue":
                     item.disabled = False # Keep effect/queue enabled
                     logging.debug(f"  Btn {item.custom_id}: Disabled={item.disabled}")

    async def disable_all(self, interaction: discord.Interaction = None):
        guild_id = self.guild_id
        if not guild_id: return
        for item in self.children:
            if isinstance(item, ui.Button): item.disabled = True
        target_message = None
        if interaction: target_message = interaction.message
        elif guild_id in self.music_cog.now_playing_messages:
            target_message = self.music_cog.now_playing_messages.get(guild_id)
        if target_message:
            try: await target_message.edit(view=self)
            except discord.NotFound: pass
            except Exception as e: logging.error(f"Error disabling music view G{guild_id}: {e}")
        if guild_id in self.music_cog.now_playing_messages:
            del self.music_cog.now_playing_messages[guild_id]

    # --- Button Callbacks ---
    @ui.button(label="⏸️ Pause", style=discord.ButtonStyle.secondary, custom_id="pause_resume", row=0)
    async def pause_resume_button(self, interaction: discord.Interaction, button: ui.Button):
        vc = self._get_voice_client(); action = "Unknown"
        if not vc: await interaction.response.send_message("Not connected.", ephemeral=True); return
        if vc.is_playing(): vc.pause(); action="Paused"
        elif vc.is_paused(): vc.resume(); action="Resumed"
        else: await interaction.response.send_message("Nothing playing/paused.", ephemeral=True); return
        logging.debug(f"{action} via button G{self.guild_id}")
        self._update_buttons(); await interaction.response.edit_message(view=self)

    @ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary, custom_id="skip", row=0)
    async def skip_button(self, interaction: discord.Interaction, button: ui.Button):
        vc = self._get_voice_client(); guild_id = interaction.guild_id;
        if not guild_id: return
        queue = self.music_cog.music_queues.get(guild_id)
        if vc and (vc.is_playing() or vc.is_paused()):
             if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
             vc.stop(); await interaction.followup.send("Skipping...", ephemeral=True)
             logging.debug(f"Skipped via button G{guild_id}")
        elif queue:
             if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
             await self.music_cog._play_next(interaction.channel); await interaction.followup.send("Trying next...", ephemeral=True)
             logging.debug(f"Forcing next via skip G{guild_id}")
        else:
             if not interaction.response.is_done(): await interaction.response.send_message("Nothing to skip.", ephemeral=True)
             else: await interaction.followup.send("Nothing to skip.", ephemeral=True)

    @ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, custom_id="stop", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: ui.Button):
        vc = self._get_voice_client(); guild_id = interaction.guild_id
        if not guild_id: return
        if vc and (vc.is_playing() or vc.is_paused()):
             if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
             if guild_id in self.music_cog.music_queues: self.music_cog.music_queues[guild_id].clear()
             vc.stop(); await self.disable_all(interaction)
             await interaction.channel.send("⏹️ Stopped music and cleared queue.")
             self.music_cog.last_activity[guild_id] = time.time()
             logging.debug(f"Stopped via button G{guild_id}, timer updated.")
        else:
             if not interaction.response.is_done(): await interaction.response.send_message("Not playing.", ephemeral=True)
             else: await interaction.followup.send("Not playing.", ephemeral=True)

    @ui.button(label="#️⃣ Q", style=discord.ButtonStyle.secondary, custom_id="show_queue", row=0)
    async def queue_button(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        if not guild_id: return await interaction.response.send_message("Error: Cannot find server.", ephemeral=True)
        queue = self.music_cog.music_queues.get(guild_id)
        voice_client = self.music_cog.voice_clients.get(guild_id)
        if not queue and (not voice_client or not voice_client.source):
            await interaction.response.send_message("The queue is empty and nothing is playing!", ephemeral=True); return
        embed = discord.Embed(title="Music Queue", color=discord.Color.blue()); current_song = ""
        if voice_client and voice_client.source and hasattr(voice_client.source, 'title'):
            current_song = f"▶️ **{voice_client.source.title}**"
            if hasattr(voice_client.source, 'duration') and voice_client.source.duration:
                 duration = voice_client.source.duration; current_song += f" ({int(duration // 60)}:{int(duration % 60):02d})"
            current_song += "\n\n"
        queue_list = ""
        if queue:
            for i, item in enumerate(list(queue)[:10]): queue_list += f"{i + 1}. {item['query']}\n"
            if len(queue) > 10: queue_list += f"\n...and {len(queue) - 10} more."
        else: queue_list = "Queue is empty."
        embed.description = current_song + queue_list
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="🔊 BB", style=discord.ButtonStyle.primary, custom_id="effect_bassboost", row=1)
    async def bassboost_button(self, interaction: discord.Interaction, button: ui.Button):
        logging.debug(f"BB button clicked G{interaction.guild_id}")
        try: await self.music_cog._apply_effect(interaction, "Bass Boost", FFMPEG_BASS_BOOST_OPTIONS)
        except Exception as e: logging.error(f"Error in BB button cb: {e}")

    @ui.button(label="🎧 8D", style=discord.ButtonStyle.primary, custom_id="effect_8d", row=1)
    async def eightd_button(self, interaction: discord.Interaction, button: ui.Button):
        logging.debug(f"8D button clicked G{interaction.guild_id}")
        try: await self.music_cog._apply_effect(interaction, "8D Audio", FFMPEG_8D_OPTIONS)
        except Exception as e: logging.error(f"Error in 8D button cb: {e}")

    @ui.button(label="⚪ Normal", style=discord.ButtonStyle.secondary, custom_id="effect_normal", row=1)
    async def normal_button(self, interaction: discord.Interaction, button: ui.Button):
        logging.debug(f"Normal button clicked G{interaction.guild_id}")
        try: await self.music_cog._apply_effect(interaction, "Normal", FFMPEG_NORMAL_OPTIONS)
        except Exception as e: logging.error(f"Error in Normal button cb: {e}")


# --- Music Cog Class ---
class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cog owns the state
        self.music_queues = {}
        self.current_effects = {}
        self.voice_clients = {}
        self.now_playing_messages = {}
        self.last_activity = {}
        self.check_inactivity.start()
        logging.info("MusicCog initialized and inactivity check started.")

    def cog_unload(self):
        self.check_inactivity.cancel()
        logging.info("MusicCog unloaded and inactivity check cancelled.")

    # --- Helper Methods (Use self.* for state) ---
    async def _ensure_voice(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id; voice_client = self.voice_clients.get(guild_id)
        if voice_client and voice_client.is_connected(): return voice_client
        user_voice = interaction.user.voice; error_msg = None
        if not user_voice: error_msg = "You need to be in a voice channel."
        elif not user_voice.channel: error_msg = "Could not detect your voice channel."
        if error_msg:
             if interaction.response.is_done(): await interaction.followup.send(error_msg, ephemeral=True)
             else: await interaction.response.send_message(error_msg, ephemeral=True)
             return None
        channel = user_voice.channel
        try:
            voice_client = await channel.connect()
            self.voice_clients[guild_id] = voice_client
            if guild_id not in self.music_queues: self.music_queues[guild_id] = deque()
            if guild_id not in self.current_effects: self.current_effects[guild_id] = FFMPEG_NORMAL_OPTIONS
            if guild_id not in self.last_activity: self.last_activity[guild_id] = 0
            self.last_activity[guild_id] = time.time(); logging.debug(f"Joined VC G{guild_id}, timer started.")
            return voice_client
        except Exception as e:
            logging.error(f"Error joining VC {channel.name} G{guild_id}: {e}"); error_msg = f"Failed join {channel.name}. Error: {e}"
            if interaction.response.is_done(): await interaction.followup.send(error_msg, ephemeral=True)
            else: await interaction.response.send_message(error_msg, ephemeral=True)
            return None

    async def _apply_effect(self, interaction: discord.Interaction, effect_name: str, ffmpeg_options: str):
        guild_id = interaction.guild_id
        if guild_id is None: return
        self.current_effects[guild_id] = ffmpeg_options
        logging.info(f"Effect set: {effect_name} G{guild_id} by {interaction.user.id}")
        await interaction.response.send_message(f"🎧 Effect: **{effect_name}** (applies next).", ephemeral=True)

    # --- _play_next Method (With manual button state fix) ---
    async def _play_next(self, interaction_or_channel):
        is_interaction = isinstance(interaction_or_channel, discord.Interaction)
        if is_interaction: guild_id=interaction_or_channel.guild_id; channel=interaction_or_channel.channel
        elif isinstance(interaction_or_channel, discord.TextChannel): guild_id=interaction_or_channel.guild.id; channel=interaction_or_channel
        else: logging.error(f"Invalid context: {type(interaction_or_channel)}"); return

        # Cleanup previous message
        if guild_id in self.now_playing_messages and self.now_playing_messages[guild_id]:
             old_message = self.now_playing_messages[guild_id]
             try:
                 view = MusicControlsView(music_cog_instance=self); view.guild_id = guild_id
                 for item in view.children: item.disabled = True
                 await old_message.edit(content="*Playback ended/skipped.*", embed=None, view=view)
             except Exception as e: logging.debug(f"Error editing old NP G{guild_id}: {e}")
             finally: self.now_playing_messages[guild_id] = None

        # Play next song
        queue = self.music_queues.get(guild_id)
        if queue:
            queue_entry = queue.popleft()
            query = queue_entry.get('query', 'Unknown'); requester = queue_entry.get('requester')
            ffmpeg_options = self.current_effects.get(guild_id, FFMPEG_NORMAL_OPTIONS)
            voice_client = self.voice_clients.get(guild_id)
            if not voice_client or not voice_client.is_connected(): logging.warning(f"VC disconnected G{guild_id}."); return

            thinking_message = None; player = None
            try:
                if not is_interaction: thinking_message = await channel.send(f"🔄 Searching for `{query}`...")
                logging.debug(f"Fetching player '{query}' G{guild_id}")
                if "http://" in query or "https://" in query: player = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True, ffmpeg_options=ffmpeg_options)
                else: player = await YTDLSource.search(query, loop=self.bot.loop, stream=True, ffmpeg_options=ffmpeg_options)
                logging.debug(f"Player fetch '{query}': {'OK' if player else 'Fail'}")
                if thinking_message: await thinking_message.delete()

                if player is None:
                     err_msg = f"❌ Failed '{query}'. Skipping."
                     if is_interaction and interaction_or_channel.response.is_done(): await interaction_or_channel.followup.send(err_msg, ephemeral=True)
                     else: await channel.send(err_msg)
                     if not queue: self.last_activity[guild_id] = time.time()
                     await self._play_next(channel); return

                # Build Embed & View
                view = MusicControlsView(music_cog_instance=self); view.guild_id = guild_id
                embed = discord.Embed(title="🎶 Now Playing", color=discord.Color.green())
                # ... (Build embed fields) ...
                if player.url: embed.description = f"**[{player.title}]({player.url})**"
                else: embed.description = f"**{player.title}**"
                if player.thumbnail: embed.set_thumbnail(url=player.thumbnail)
                if player.duration: embed.add_field(name="Duration", value=f"{int(player.duration // 60)}:{int(player.duration % 60):02d}", inline=True)
                else: embed.add_field(name="Duration", value="N/A", inline=True)
                if requester: embed.add_field(name="Requested by", value=requester.mention, inline=True)
                else: embed.add_field(name="Requested by", value="Unknown", inline=True)
                queue_len = len(queue)
                embed.add_field(name="Queue", value=f"{queue_len} remaining", inline=True)
                if player.extractor == 'Youtube':
                     if player.uploader: uploader_text = f"[{player.uploader}]({player.uploader_url})" if player.uploader_url else player.uploader; embed.add_field(name="Uploader", value=uploader_text, inline=True)
                     if player.view_count is not None: embed.add_field(name="Views", value=f"{player.view_count:,}", inline=True)
                     if player.upload_date: formatted_date = f"{player.upload_date[:4]}-{player.upload_date[4:6]}-{player.upload_date[6:]}"; embed.add_field(name="Uploaded", value=formatted_date, inline=True)
                footer_text=f"Source: {player.extractor}"; icon_url="";
                if player.extractor == 'Soundcloud': icon_url="https://icons.iconarchive.com/icons/custom-icon-design/pretty-office-7/32/Soundcloud-icon.png"
                elif player.extractor == 'Youtube': icon_url="https://icons.iconarchive.com/icons/social-media-icons/glossy-social-media/32/Youtube-icon.png"
                if icon_url: embed.set_footer(text=footer_text, icon_url=icon_url)
                else: embed.set_footer(text=footer_text)

                # --- Manually Set Initial Button States ---
                logging.debug(f"Manually setting initial btn states G{guild_id}")
                for item in view.children:
                    if isinstance(item, ui.Button):
                        if item.custom_id in ["pause_resume", "skip", "stop", "show_queue"]:
                            item.disabled = False # Start ENABLED
                            if item.custom_id == "pause_resume": # Set initial pause style
                                item.label = "⏸️ Pause"; item.style = discord.ButtonStyle.secondary
                        elif item.custom_id.startswith("effect_"):
                            item.disabled = False # Keep effects enabled
                logging.debug(f"Button states after manual set: {[f'{b.custom_id}({b.disabled})' for b in view.children if isinstance(b, ui.Button)]}")
                # ---

                # Send Message
                logging.debug(f"Attempting send NP G{guild_id}. View type: {type(view)}, Children: {len(view.children)}, Timeout: {view.timeout}")
                now_playing_msg = None
                try:
                    if is_interaction and interaction_or_channel.response.is_done():
                        now_playing_msg = await interaction_or_channel.followup.send(embed=embed, view=view, wait=True)
                    else: now_playing_msg = await channel.send(embed=embed, view=view)

                    if now_playing_msg: logging.debug(f"NP message sent G{guild_id} ID: {now_playing_msg.id}. Components: {len(now_playing_msg.components)} rows.")
                    else: logging.error("Failed get NP message object.")
                    self.now_playing_messages[guild_id] = now_playing_msg

                    # Start playing
                    self.last_activity[guild_id] = 0
                    voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(self._play_next_after_error(channel, e), self.bot.loop))
                    logging.info(f"Started playing '{player.title}' G{guild_id}")

                except Exception as send_e: logging.exception(f"ERROR Sending NP message G{guild_id}")

            except Exception as e:
                 logging.exception(f"Error during playback setup G{guild_id}")
                 err_msg = f"Playback error: {e}"
                 if is_interaction and interaction_or_channel.response.is_done(): await interaction_or_channel.followup.send(err_msg, ephemeral=True)
                 else: await channel.send(err_msg)
                 if not queue: self.last_activity[guild_id] = time.time()
                 await asyncio.sleep(2); await self._play_next(channel)

        else: # Queue empty
            self.last_activity[guild_id] = time.time()
            logging.debug(f"Queue finished G{guild_id}, timer updated.")
            # ... (Cleanup last NP msg) ...
            if guild_id in self.now_playing_messages and self.now_playing_messages[guild_id]:
                 old_message = self.now_playing_messages[guild_id]
                 try:
                      view = MusicControlsView(music_cog_instance=self); view.guild_id = guild_id
                      for item in view.children: item.disabled = True
                      await old_message.edit(content="⏹️ Queue finished.", embed=None, view=view)
                 except Exception as e: logging.debug(f"Error editing final NP msg G{guild_id}: {e}")
                 finally: self.now_playing_messages[guild_id] = None
            else: await channel.send("⏹️ Queue finished!")


    # --- _play_next_after_error Method (Corrected Syntax) ---
    async def _play_next_after_error(self, channel: discord.TextChannel, error):
        """Handles the 'after' callback from voice_client.play"""
        guild_id = channel.guild.id
        # --- SYNTAX FIX APPLIED HERE ---
        if error:
            logging.error(f'Player error G{guild_id}: {error}')
            # Start try block on new line, indented
            try:
                # Indent the statement inside the try block
                await channel.send(f'Player error: {error}. Skipping.')
            except Exception as e:
                logging.error(f"Failed send player error G{guild_id}: {e}")
        # --- END SYNTAX FIX ---

        # Check queue *after* error/finish and update timer if needed
        queue = self.music_queues.get(guild_id) # Use self.
        if not queue:
            self.last_activity[guild_id] = time.time() # Use self.
            logging.debug(f"Queue empty after end/error G{guild_id}, timer updated.")

        # Always try to play the next song or trigger inactivity cleanup
        await self._play_next(channel) # Call cog's method


    # --- Inactivity Check Task (Keep as is) ---
    @tasks.loop(seconds=30)
    async def check_inactivity(self):
        # ... (Implementation unchanged, uses self.* for state) ...
        await self.bot.wait_until_ready(); current_time = time.time()
        guild_ids_to_check = list(self.voice_clients.keys())
        for guild_id in guild_ids_to_check:
            vc = self.voice_clients.get(guild_id); queue = self.music_queues.get(guild_id)
            if vc and vc.is_connected() and not vc.is_playing() and not vc.is_paused() and not queue:
                last_active_time = self.last_activity.get(guild_id, 0)
                if last_active_time == 0: self.last_activity[guild_id] = current_time; logging.debug(f"Inactivity timer started G{guild_id}"); continue
                if current_time - last_active_time > INACTIVITY_TIMEOUT:
                    logging.info(f"Inactivity timeout G{guild_id}. Leaving.")
                    try:
                        guild = self.bot.get_guild(guild_id)
                        if guild:
                             if guild_id in self.now_playing_messages and self.now_playing_messages[guild_id]:
                                 old_message = self.now_playing_messages[guild_id]
                                 try:
                                     view = MusicControlsView(music_cog_instance=self); view.guild_id = guild_id
                                     for item in view.children: item.disabled = True
                                     await old_message.edit(content="Leaving due to inactivity.", embed=None, view=view)
                                 except Exception as e: logging.error(f"Error editing NP on inactivity leave G{guild_id}: {e}")
                                 finally: self.now_playing_messages[guild_id] = None
                             await vc.disconnect()
                             self.voice_clients[guild_id] = None; self.last_activity[guild_id] = 0
                             if guild_id in self.music_queues: self.music_queues[guild_id].clear()
                    except Exception as e: logging.exception(f"Error during auto disconnect G{guild_id}"); self.voice_clients[guild_id] = None; self.last_activity[guild_id] = 0

    # --- Slash Commands (Keep as is, use self.* for state) ---
    @app_commands.command(name="join", description="Tells the bot to join your voice channel.")
    async def join_slash(self, interaction: discord.Interaction):
        # ... (Implementation unchanged) ...
        guild_id = interaction.guild_id; vc = self.voice_clients.get(guild_id); user_voice = interaction.user.voice
        if not user_voice or not user_voice.channel: await interaction.response.send_message("You aren't in a voice channel.", ephemeral=True); return
        channel = user_voice.channel
        if vc and vc.is_connected():
             msg = "";
             if vc.channel != channel: await vc.move_to(channel); msg = f"Moved to {channel.name}."
             else: msg = "Already in your voice channel."
             if not vc.is_playing() and not vc.is_paused() and not self.music_queues.get(guild_id): self.last_activity[guild_id] = time.time()
             await interaction.response.send_message(msg, ephemeral=True)
        else:
             new_vc = await self._ensure_voice(interaction)
             if new_vc: await interaction.response.send_message(f"Joined {channel.name}.", ephemeral=True)

    @app_commands.command(name="play", description="Searches YouTube/plays URL and adds to queue.")
    @app_commands.describe(query="The YouTube search term or a URL (YT/SC).")
    async def play_slash(self, interaction: discord.Interaction, query: str):
        # ... (Implementation unchanged) ...
        guild_id = interaction.guild_id; await interaction.response.defer()
        voice_client = await self._ensure_voice(interaction);
        if not voice_client: return
        if guild_id not in self.music_queues: self.music_queues[guild_id] = deque()
        is_playing_or_paused = voice_client.is_playing() or voice_client.is_paused()
        queue_entry = {'query': query, 'requester': interaction.user}
        self.music_queues[guild_id].append(queue_entry)
        await interaction.followup.send(f"✅ Added: **{query}**")
        if not is_playing_or_paused: await self._play_next(interaction)

    @app_commands.command(name="queue", description="Shows the current music queue.")
    async def queue_slash(self, interaction: discord.Interaction):
        # ... (Implementation unchanged) ...
        guild_id = interaction.guild_id; queue = self.music_queues.get(guild_id)
        voice_client = self.voice_clients.get(guild_id); current_msg = self.now_playing_messages.get(guild_id)
        if not queue and (not voice_client or not voice_client.source): await interaction.response.send_message("Queue empty/inactive.", ephemeral=True); return
        embed = discord.Embed(title="Music Queue", color=discord.Color.blue()); current_song = ""
        if voice_client and voice_client.source and hasattr(voice_client.source, 'title'):
             current_song = f"▶️ **{voice_client.source.title}**";
             if hasattr(voice_client.source, 'duration') and voice_client.source.duration: current_song += f" ({int(voice_client.source.duration // 60)}:{int(voice_client.source.duration % 60):02d})"
             current_song += "\n\n"
        elif current_msg and current_msg.embeds: current_song = f"▶️ {current_msg.embeds[0].description}\n\n" # Approx
        queue_list = "";
        if queue:
             for i, item in enumerate(list(queue)[:10]): queue_list += f"{i + 1}. {item['query']}\n"
             if len(queue) > 10: queue_list += f"\n...and {len(queue) - 10} more."
        else: queue_list = "Queue is empty."
        embed.description = current_song + queue_list
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="leave", description="Disconnects the bot from the voice channel.")
    async def leave_slash(self, interaction: discord.Interaction):
        # ... (Implementation unchanged) ...
        guild_id = interaction.guild_id; voice_client = self.voice_clients.get(guild_id)
        if voice_client and voice_client.is_connected():
             if guild_id in self.now_playing_messages and self.now_playing_messages[guild_id]:
                 old_message = self.now_playing_messages[guild_id]
                 try:
                     view = MusicControlsView(music_cog_instance=self); view.guild_id = guild_id
                     for item in view.children: item.disabled = True
                     await old_message.edit(content="Disconnected.", embed=None, view=view)
                 except Exception as e: logging.error(f"Error editing NP on leave G{guild_id}: {e}")
                 finally: self.now_playing_messages[guild_id] = None
             if guild_id in self.music_queues: self.music_queues[guild_id].clear()
             await voice_client.disconnect()
             self.voice_clients[guild_id] = None; self.last_activity[guild_id] = 0
             logging.debug(f"Left VC via command G{guild_id}, timer reset.")
             await interaction.response.send_message("Disconnected.", ephemeral=True)
        else: await interaction.response.send_message("Not in a voice channel.", ephemeral=True)


# --- Setup Function ---
async def setup(bot: commands.Bot):
    # Optional: Create downloads directory
    if 'downloads/' in YTDL_FORMAT_OPTIONS.get('outtmpl', ''):
        if not os.path.exists('downloads'):
            try: os.makedirs('downloads'); logging.info("Created downloads directory.")
            except OSError as e: logging.error(f"Could not create downloads directory: {e}")

    await bot.add_cog(MusicCog(bot))
    logging.info("MusicCog loaded.")
