import asyncio
import itertools
import logging
import shutil
from urllib.parse import urlparse

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View
from yt_dlp.utils import DownloadError

from exceptions import InvalidVoiceChannel, VoiceConnectionError


LOGGER = logging.getLogger(__name__)


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def defer_interaction(ctx: commands.Context) -> None:
    if ctx.interaction and not ctx.interaction.response.is_done():
        await ctx.interaction.response.defer(thinking=True)


class YTDL:
    js_runtime = "deno" if shutil.which("deno") else "node"
    ytdl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "default_search": "auto",
        "cachedir": False,
        "socket_timeout": 15,
        "retries": 2,
        "extractor_retries": 2,
        "js_runtimes": {js_runtime: {}},
    }
    search_options = {
        **ytdl_options,
        "extract_flat": True,
        "lazy_playlist": False,
    }
    ffmpeg_options = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": "-vn",
    }

    @classmethod
    async def extract(cls, query: str, *, flat: bool = False):
        options = cls.search_options if flat else cls.ytdl_options

        def run_extract():
            # A fresh instance avoids sharing mutable yt-dlp state between threads.
            with yt_dlp.YoutubeDL(options) as downloader:
                return downloader.extract_info(query, download=False)

        try:
            return await asyncio.to_thread(run_extract)
        except DownloadError as exc:
            raise DownloadError(
                "YouTube rejected the request. Please try again shortly."
            ) from exc


class YTDLSource:
    def __init__(self, requester, *, webpage_url=None, title=None, duration=None):
        self.requester = requester
        self.title = title
        self.webpage_url = webpage_url
        self.duration_seconds = duration
        self.url = None

    @classmethod
    def from_data(cls, requester, data):
        return cls(
            requester,
            webpage_url=data.get("webpage_url") or data.get("url"),
            title=data.get("title") or "Unknown title",
            duration=data.get("duration"),
        )

    async def load_metadata(self, query: str):
        data = await YTDL.extract(query)
        if not data:
            raise DownloadError("No playable result was found.")
        if data.get("entries"):
            data = next((entry for entry in data["entries"] if entry), None)
        if not data:
            raise DownloadError("No playable result was found.")
        self.title = data.get("title") or "Unknown title"
        self.webpage_url = data.get("webpage_url") or data.get("original_url") or query
        self.duration_seconds = data.get("duration")
        self.url = data.get("url")
        return self

    async def refresh_stream_url(self):
        if not self.webpage_url:
            raise DownloadError("This track has no usable URL.")
        data = await YTDL.extract(self.webpage_url)
        if not data:
            raise DownloadError("YouTube returned no playable audio stream.")
        if data.get("entries"):
            data = next((entry for entry in data["entries"] if entry), None)
        if not data or not data.get("url"):
            raise DownloadError("YouTube returned no playable audio stream.")
        self.url = data["url"]
        self.title = data.get("title") or self.title
        self.duration_seconds = data.get("duration") or self.duration_seconds
        return self.url

    @property
    def duration(self):
        return MusicPlayer.format_duration(self.duration_seconds)


class YTDLChoiceButton(Button):
    def __init__(self, label: int, view, source: YTDLSource):
        super().__init__(
            label=str(label),
            style=discord.ButtonStyle.primary,
            custom_id=f"ytdl_choice_btn_{label}",
        )
        self.choice_view = view
        self.source = source

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.choice_view.requester.id:
            await interaction.response.send_message(
                "Only the person who searched can choose a track.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        self.choice_view.stop()
        for item in self.choice_view.children:
            item.disabled = True
        await interaction.edit_original_response(view=self.choice_view)
        player = self.choice_view.cog.get_player(self.choice_view.ctx)
        await player.queue.put(self.source)
        embed = self.choice_view.cog.source_embed(
            self.choice_view.ctx, self.source, "Added to queue 📀"
        )
        await interaction.followup.send(embed=embed)


class YTDLChoiceView(View):
    def __init__(self, cog, ctx, sources):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.requester = ctx.author
        self.message = None
        for index, source in enumerate(sources, start=1):
            self.add_item(YTDLChoiceButton(index, self, source))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        LOGGER.exception("YouTube selection failed", exc_info=error)
        message = "Could not add this track to the queue."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class MusicPlayer:
    IDLE_TIMEOUT_SECONDS = 300

    def __init__(self, ctx):
        self.ctx = ctx
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.current = None
        self.task = asyncio.create_task(
            self.player_loop(), name=f"music-player-{ctx.guild.id}"
        )
        self.display_playing = True

    @staticmethod
    def format_duration(duration):
        if duration is None:
            return "unknown duration"
        seconds = max(0, int(duration))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
        return f"{minutes:02d}m {seconds:02d}s"

    async def player_loop(self):
        await self.ctx.bot.wait_until_ready()
        try:
            while not self.ctx.bot.is_closed():
                self.next.clear()
                try:
                    source = await asyncio.wait_for(
                        self.queue.get(), timeout=self.IDLE_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    break
                self.current = source
                voice_client = self.ctx.guild.voice_client
                if not voice_client or not voice_client.is_connected():
                    await self.ctx.cog.send_error_embed(
                        self.ctx, "The voice connection was lost."
                    )
                    break
                try:
                    await source.refresh_stream_url()
                    audio = discord.FFmpegPCMAudio(source.url, **YTDL.ffmpeg_options)
                    transformed = discord.PCMVolumeTransformer(audio, volume=0.5)
                    voice_client.play(
                        transformed,
                        after=lambda error: self.ctx.bot.loop.call_soon_threadsafe(
                            self._track_finished, error
                        ),
                    )
                    if self.display_playing:
                        await self.ctx.cog.send_source_embed(
                            self.ctx, source, "Now playing 🎶"
                        )
                    await self.next.wait()
                except Exception as exc:
                    LOGGER.exception("Could not play %s", source.webpage_url)
                    await self.ctx.cog.send_error_embed(self.ctx, str(exc))
                finally:
                    self.current = None
                    self.queue.task_done()
        except asyncio.CancelledError:
            raise
        finally:
            await self.ctx.cog.cleanup(self.ctx.guild, from_player=True)

    def _track_finished(self, error):
        if error:
            LOGGER.error("FFmpeg playback error: %s", error)
        self.next.set()


class Music(commands.Cog):
    NOT_CONNECTED_MESSAGE = "I am not connected to a voice channel."
    NOT_PLAYING_MESSAGE = "No track is playing."

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cog_unload(self):
        for player in self.players.values():
            player.task.cancel()
        self.players.clear()

    async def cleanup(self, guild, *, force=False, from_player=False):
        player = self.players.get(guild.id)
        if from_player and (
            player is None or player.task is not asyncio.current_task()
        ):
            return
        self.players.pop(guild.id, None)
        if player and not from_player and player.task is not asyncio.current_task():
            player.task.cancel()
        if guild.voice_client:
            await guild.voice_client.disconnect(force=force)

    async def cog_command_error(self, ctx, error):
        original = getattr(error, "original", error)
        if isinstance(original, VoiceConnectionError):
            message = str(original)
        elif isinstance(original, commands.MissingPermissions):
            message = "You do not have permission to use this command."
        elif isinstance(original, commands.NoPrivateMessage):
            message = "This command cannot be used in a direct message."
        elif isinstance(original, DownloadError):
            message = str(original)
        else:
            LOGGER.error("Music command failed", exc_info=original)
            message = "An unexpected error occurred. Please try again shortly."
        try:
            await self.send_error_embed(ctx, message)
        except discord.HTTPException:
            LOGGER.exception("Could not send Discord error message")

    @staticmethod
    def source_description(source):
        return " | ".join(
            [
                f"[{source.title}]({source.webpage_url})",
                f"`{source.duration}`",
                f"`Requested by:` {source.requester.mention}",
            ]
        )

    def source_embed(self, ctx, source, title):
        embed = discord.Embed(
            description=self.source_description(source), color=discord.Color.greyple()
        )
        embed.set_author(icon_url=self.bot.user.display_avatar.url, name=title)
        embed.set_footer(text=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        return embed

    async def send_source_embed(self, ctx, source, title):
        await ctx.send(embed=self.source_embed(ctx, source, title))

    async def send_error_embed(self, ctx, error):
        embed = discord.Embed(description=error, color=discord.Color.dark_red())
        embed.set_author(
            icon_url=self.bot.user.display_avatar.url, name="An error occurred 😟"
        )
        embed.set_footer(text=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    def get_player(self, ctx):
        player = self.players.get(ctx.guild.id)
        if not player or player.task.done():
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player
        return player

    async def get_voice_client(self, ctx):
        voice_client = ctx.voice_client
        if not voice_client or not voice_client.is_connected():
            await self.send_error_embed(ctx, self.NOT_CONNECTED_MESSAGE)
            return None
        return voice_client

    async def list_choices(self, ctx, search):
        data = await YTDL.extract(f"ytsearch5:{search}", flat=True)
        entries = [entry for entry in data.get("entries", []) if entry]
        if not entries:
            raise DownloadError("No YouTube results were found.")
        sources = [YTDLSource.from_data(ctx.author, entry) for entry in entries]
        description = "\n".join(
            f"`{index}.` [{source.title}]({source.webpage_url}) | `{source.duration}`"
            for index, source in enumerate(sources, start=1)
        )
        embed = discord.Embed(description=description, color=discord.Color.greyple())
        embed.set_author(
            icon_url=self.bot.user.display_avatar.url,
            name=f'Results for: "{search}" 🔍',
        )
        view = YTDLChoiceView(self, ctx, sources)
        view.message = await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="play", aliases=["search", "pl"], description="Play or search for a track.")
    @app_commands.describe(search="A YouTube URL or search terms")
    @app_commands.guild_only()
    async def play(self, ctx, *, search: str):
        await defer_interaction(ctx)
        if not search or len(search) > 250:
            return await self.send_error_embed(
                ctx, "The search must be between 1 and 250 characters."
            )
        if is_url(search):
            source = await YTDLSource(ctx.author).load_metadata(search)
            player = self.get_player(ctx)
            await player.queue.put(source)
            await self.send_source_embed(ctx, source, "Added to queue 📀")
        else:
            await self.list_choices(ctx, search)

    @commands.hybrid_command(name="loop", aliases=["lp", "repeat"], description="Repeat the current track.")
    @app_commands.describe(rep="Number of repeats, up to 10")
    @app_commands.guild_only()
    async def loop(self, ctx, rep: int):
        if rep <= 0:
            return await self.send_error_embed(ctx, "The repeat count must be positive.")
        voice_client = ctx.voice_client
        player = self.players.get(ctx.guild.id)
        if (
            not voice_client
            or not voice_client.is_playing()
            or not player
            or not player.current
        ):
            return await self.send_error_embed(ctx, self.NOT_PLAYING_MESSAGE)
        rep = min(rep, 10)
        for _ in range(rep):
            await player.queue.put(
                YTDLSource(
                    ctx.author,
                    webpage_url=player.current.webpage_url,
                    title=player.current.title,
                    duration=player.current.duration_seconds,
                )
            )
        await self.send_source_embed(ctx, player.current, f"Queued {rep} repeat(s) 🔄")

    @commands.hybrid_command(name="pause", aliases=["p"], description="Pause playback.")
    @app_commands.guild_only()
    async def pause(self, ctx):
        voice_client = ctx.voice_client
        if not voice_client or not voice_client.is_playing():
            return await self.send_error_embed(ctx, self.NOT_PLAYING_MESSAGE)
        voice_client.pause()
        await self.send_source_embed(ctx, self.get_player(ctx).current, "Paused ⏸")

    @commands.hybrid_command(name="resume", aliases=["replay", "r"], description="Resume playback.")
    @app_commands.guild_only()
    async def resume(self, ctx):
        voice_client = await self.get_voice_client(ctx)
        if not voice_client or not voice_client.is_paused():
            return
        voice_client.resume()
        await self.send_source_embed(ctx, self.get_player(ctx).current, "Resumed ⏯")

    @commands.hybrid_command(name="queue", aliases=["q", "playlist"], description="Show the queue.")
    @app_commands.guild_only()
    async def queue_info(self, ctx):
        voice_client = await self.get_voice_client(ctx)
        if not voice_client:
            return
        player = self.players.get(ctx.guild.id)
        if not player:
            return await self.send_error_embed(ctx, "The queue is empty.")
        upcoming = list(itertools.islice(player.queue._queue, 0, 15))
        if not player.current and not upcoming:
            return await self.send_error_embed(ctx, "The queue is empty.")
        lines = []
        if player.current:
            lines.extend(["__Now playing__", self.source_description(player.current), ""])
        lines.append("__Up next__")
        lines.extend(
            f"`{index}.` {self.source_description(source)}"
            for index, source in enumerate(upcoming, start=1)
        )
        if not upcoming:
            lines.append("Nothing in the queue.")
        embed = discord.Embed(description="\n".join(lines), color=discord.Color.greyple())
        embed.set_author(
            icon_url=self.bot.user.display_avatar.url,
            name=f"Queue for {ctx.guild.name} 🎼",
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="np", aliases=["song", "current", "playing"], description="Show the current track.")
    @app_commands.guild_only()
    async def now_playing(self, ctx):
        player = self.players.get(ctx.guild.id)
        if not player or not player.current:
            return await self.send_error_embed(ctx, self.NOT_PLAYING_MESSAGE)
        await self.send_source_embed(ctx, player.current, "Now playing 🎶")

    @commands.hybrid_command(name="skip", aliases=["next", "pass", "s"], description="Skip one or more tracks.")
    @app_commands.describe(go_to="Number of tracks to skip")
    @app_commands.guild_only()
    async def skip(self, ctx, go_to: int = 1):
        if go_to <= 0:
            return await self.send_error_embed(
                ctx, "The number of tracks to skip must be positive."
            )
        voice_client = await self.get_voice_client(ctx)
        if not voice_client or not voice_client.is_playing():
            return
        player = self.players.get(ctx.guild.id)
        if not player or not player.current:
            return await self.send_error_embed(ctx, self.NOT_PLAYING_MESSAGE)
        await self.send_source_embed(ctx, player.current, "Skipped ⏭")
        voice_client.stop()
        for _ in range(go_to - 1):
            try:
                player.queue.get_nowait()
                player.queue.task_done()
            except asyncio.QueueEmpty:
                break

    @commands.hybrid_command(name="join", aliases=["connect", "j"], description="Join a voice channel.")
    @app_commands.describe(channel="Channel to join; defaults to your current channel")
    @app_commands.guild_only()
    async def connect(self, ctx, channel: discord.VoiceChannel | None = None):
        await defer_interaction(ctx)
        if channel is None:
            voice_state = getattr(ctx.author, "voice", None)
            channel = voice_state.channel if voice_state else None
        if channel is None:
            raise InvalidVoiceChannel(
                "Join a voice channel or specify one to join."
            )
        voice_client = ctx.voice_client
        try:
            if voice_client and voice_client.is_connected():
                if voice_client.channel.id == channel.id:
                    return await ctx.send(f"I am already in `{channel}`.")
                await voice_client.move_to(channel, timeout=30)
            else:
                await channel.connect(timeout=30, reconnect=True)
        except (asyncio.TimeoutError, discord.ClientException) as exc:
            raise VoiceConnectionError(
                f"Could not connect to `{channel}`."
            ) from exc
        await ctx.send(f"**Joined `{channel}`** 🤟")

    @commands.hybrid_command(name="leave", aliases=["stop", "dc", "bye", "quit"], description="Stop playback and leave voice.")
    @app_commands.guild_only()
    async def leave(self, ctx):
        voice_client = await self.get_voice_client(ctx)
        if not voice_client:
            return
        await self.cleanup(ctx.guild)
        await ctx.send("**Disconnected** 👋")

    @play.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None or not ctx.voice_client.is_connected():
            await self.connect(ctx)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id == self.bot.user.id and before.channel and not after.channel:
            player = self.players.pop(member.guild.id, None)
            if player:
                player.task.cancel()


async def setup(bot):
    await bot.add_cog(Music(bot))
