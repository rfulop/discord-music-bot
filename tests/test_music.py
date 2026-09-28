import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

from cogs.music import MusicPlayer, YTDL, YTDLSource, is_url
from main import MusicBot


class MusicHelpersTest(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(MusicPlayer.format_duration(61), "01m 01s")
        self.assertEqual(MusicPlayer.format_duration(3661), "1h 01m 01s")
        self.assertEqual(MusicPlayer.format_duration(None), "unknown duration")

    def test_url_detection(self):
        self.assertTrue(is_url("https://www.youtube.com/watch?v=abc"))
        self.assertFalse(is_url("tavern ambience music"))
        self.assertFalse(is_url("javascript:alert(1)"))


class YTDLSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_load_metadata_uses_first_valid_entry(self):
        requester = SimpleNamespace(mention="@player")
        source = YTDLSource(requester)
        result = {
            "entries": [
                None,
                {
                    "title": "Tavern",
                    "webpage_url": "https://example.test/video",
                    "duration": 123,
                    "url": "https://stream.test/old",
                },
            ]
        }
        with patch.object(YTDL, "extract", AsyncMock(return_value=result)):
            await source.load_metadata("query")
        self.assertEqual(source.title, "Tavern")
        self.assertEqual(source.duration, "02m 03s")
        self.assertEqual(source.webpage_url, "https://example.test/video")

    async def test_refresh_stream_url_reextracts_at_playback_time(self):
        requester = SimpleNamespace(mention="@player")
        source = YTDLSource(
            requester,
            webpage_url="https://example.test/video",
            title="Old title",
            duration=1,
        )
        result = {
            "title": "Current title",
            "duration": 125,
            "url": "https://stream.test/fresh",
        }
        with patch.object(YTDL, "extract", AsyncMock(return_value=result)) as extract:
            stream_url = await source.refresh_stream_url()
        extract.assert_awaited_once_with("https://example.test/video")
        self.assertEqual(stream_url, "https://stream.test/fresh")
        self.assertEqual(source.title, "Current title")
        self.assertEqual(source.duration, "02m 05s")


class BotSetupTest(unittest.IsolatedAsyncioTestCase):
    async def test_music_cog_registers_all_slash_commands(self):
        bot = MusicBot(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
        )
        try:
            await bot.load_extension("cogs.music")
            self.assertEqual(
                {command.name for command in bot.tree.get_commands()},
                {"join", "leave", "loop", "np", "pause", "play", "queue", "resume", "skip"},
            )
        finally:
            await bot.close()


if __name__ == "__main__":
    unittest.main()
