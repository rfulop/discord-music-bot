"""Run the standalone Discord music bot."""

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv


LOGGER = logging.getLogger(__name__)


class MusicBot(commands.Bot):
    async def setup_hook(self):
        await self.load_extension("cogs.music")
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            LOGGER.info("Synced %s commands to guild %s", len(synced), guild_id)
        else:
            synced = await self.tree.sync()
            LOGGER.info("Synced %s global commands", len(synced))

    async def on_ready(self):
        LOGGER.info("Logged in as %s", self.user)


def main():
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required; see .env.example")

    logging.basicConfig(level=logging.INFO)
    intents = discord.Intents.default()
    bot = MusicBot(command_prefix=commands.when_mentioned, intents=intents)
    bot.run(token)


if __name__ == "__main__":
    main()
