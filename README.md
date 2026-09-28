# Discord Music Bot

A self-hosted Discord music bot written in Python with `discord.py`, `yt-dlp`, and FFmpeg. Search YouTube, choose a result, and play music in a voice channel with slash commands. Each server has its own in-memory queue. No Lavalink server or YouTube API key is required.

This project contains only the music features extracted from Edi Discord Bot. It does not include the original bot's event or role-playing features.

## What it does

- `/play` accepts YouTube search terms or a video URL. Searches show up to five choices; only the person who searched can select one.
- Playback controls include pause, resume, skip, and up to ten queued repeats of the current track.
- `/queue` shows the current track and up to 15 upcoming tracks. `/np` shows the current track.
- The bot refreshes a track's stream URL just before playback and disconnects five minutes after a playback queue becomes empty.

Queues are kept in memory and disappear when the bot restarts. Playback depends on the source remaining accessible to `yt-dlp`; changes at YouTube can temporarily break extraction. The URL path uses `yt-dlp`, so other supported sites may work, but YouTube is the intended and documented source.

## Before you start

You need:

1. **Python 3.10 or newer**. Check with `python --version` (or `python3 --version`).
2. **FFmpeg installed separately and available on `PATH`**. Check with `ffmpeg -version`. Installing the Python requirements does not install the FFmpeg executable. Get it from the [FFmpeg download page](https://ffmpeg.org/download.html) or your OS package manager.
3. **A JavaScript runtime on `PATH` for YouTube**: [Deno](https://docs.deno.com/runtime/getting_started/installation/) 2.3 or newer, or [Node.js](https://nodejs.org/en/download) 22 or newer. Check with `deno --version` or `node --version`. The bot uses Deno when available and otherwise uses Node. The `yt-dlp[default]` requirement installs the companion challenge scripts. See [yt-dlp's current setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS) if YouTube extraction fails.
4. **Your own Discord application and bot token**. This repository does not provide a hosted bot or a shared token.

If you run the bot as a service, verify that the service has the same `PATH` as the terminal where you checked FFmpeg and the JavaScript runtime.

## Set up the Discord application

1. [Create an application in the Discord Developer Portal](https://discord.com/developers/applications). Use its **Bot** page to create or reset a token. Keep the token private; never commit it to Git.
2. On the application's **Installation** page, configure a **Guild Install** with the `bot` and `applications.commands` scopes. Give the bot **View Channels**, **Send Messages**, **Embed Links**, **Connect**, and **Speak** permissions. Copy the install link and add the bot to a server you manage. Channel-specific permission overrides must also allow it to send messages and join/speak in the voice channel.
3. You do not need to enable the privileged **Message Content** intent. The documented interface uses slash commands. The bot is designed for server installation and voice channels, not user-only installation or direct messages.

Discord's [bot setup guide](https://docs.discord.com/developers/quick-start/getting-started) explains the current Developer Portal screens.

## Install and run

From the project directory, create a virtual environment and install the Python dependencies:

**Linux/macOS**

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` in this project directory:

```dotenv
DISCORD_TOKEN=your_new_bot_token
GUILD_ID=
```

`DISCORD_TOKEN` is required. `GUILD_ID` is optional: set it to a server ID while testing so slash commands sync to that server; leave it empty to register commands globally. To find a server ID, enable Developer Mode in Discord, then right-click the server and choose **Copy Server ID**. The bot loads `.env` only from this project directory; environment variables already set by your shell take precedence. It will not load a `.env` from a parent directory. The `.env` file is ignored by Git.

Start the bot:

```sh
python main.py
```

Keep this process running while you use the bot. It should log that commands were synced and then `Logged in as ...`. In Discord, join a voice channel on the installed server and try `/play` with a search term. Choose a result, then use `/queue` and `/leave` to check playback and disconnection. Global commands may take some time to appear after the first sync.

## Commands

| Command | What it does |
| --- | --- |
| `/play <search>` | Search YouTube or queue a URL; joins your voice channel automatically |
| `/join [channel]` | Join your voice channel or a specified channel |
| `/leave` | Stop playback, clear the queue, and disconnect |
| `/pause` | Pause the current track |
| `/resume` | Resume a paused track |
| `/skip [go_to]` | Skip the current track and optionally additional queued tracks |
| `/loop <rep>` | Queue 1–10 repeats of the current track |
| `/queue` | Show the current track and first 15 queued tracks |
| `/np` | Show the current track |

Search selection buttons expire after three minutes. `/play` accepts at most 250 characters of search input.

## Troubleshooting

- **`DISCORD_TOKEN is required` or login fails:** Put a valid token from this bot's Discord application in this project's `.env`. Do not reuse another bot's token.
- **Commands do not appear:** Confirm the bot was installed to the server with `applications.commands`, that the process logged a successful sync, and that `GUILD_ID` matches the server if you set it. Global registration can take longer to appear. Restart the Discord client if needed.
- **The bot cannot join or speak:** Check **Connect** and **Speak** permissions on the specific voice channel, plus **View Channels**. Start `/play` while you are in a voice channel, or use `/join` with a channel.
- **FFmpeg cannot be found:** Run `ffmpeg -version` in the same environment that starts the bot and add its executable directory to `PATH`.
- **YouTube extraction or playback fails:** Check `deno --version` or `node --version` in that environment, update the pinned `yt-dlp[default]` version when appropriate, and consult the [yt-dlp YouTube runtime guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS). An external site can change independently of this project.

## Tests and development

Run the local unit tests after changing the code:

```sh
python -m unittest discover -s tests -v
```

These tests check helpers, source metadata handling, and slash command registration without contacting Discord or YouTube. Passing them does not verify real voice playback; use the `/play` check above for that. GitHub Actions runs the same tests on pushes and pull requests.

## License

GPL-3.0. See [LICENSE](LICENSE). The music code was extracted from Edi Discord Bot. Use audio only where you have the right to access and play it.
