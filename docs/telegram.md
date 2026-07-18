# Telegram Bot

A personal AI assistant via Telegram with full tjai access, voice dialogue, and location awareness. Designed for hands-free use while driving.

## Features

- **Voice I/O** — speak and hear responses. OpenAI Whisper (STT) + OpenAI TTS (Nova voice, OGG Opus)
- **Voice/text toggle** — mode persists across sessions; Claude adapts response style
- **GPS location** — share live location for location-aware responses (reverse geocoding via OpenStreetMap Nominatim)
- **Web search/fetch** — Claude server tools with user location context
- **Text chat** with Claude Sonnet + all tjai tools (calendar, todos, memories, bookmarks, search)
- **Calendar reminders** — background job checks every 5 minutes, pushes notification 15 minutes before events
- **Persistent history** — survives restarts (stored as tjai entries, tag `tgchat`)
- **Mini App** — Telegram WebApp with tabs for Calendar, Synopsis, All (recent entries), Picks, ReadMe, RSS, Named entries, Contexts, Research, Search. Sticky headers, same triage UX as desktop
- Single-user auth via Telegram user ID

## Setup

1. Create bot via [@BotFather](https://t.me/botfather)
2. Get user ID from [@userinfobot](https://t.me/userinfobot)
3. Add to `/var/www/tjai/.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=<from_botfather>
   TELEGRAM_USER_ID=<your_user_id>
   ANTHROPIC_API_KEY=<key>
   OPENAI_API_KEY=<key>  # Whisper STT and TTS
   ```
4. Bot deps are part of the standard venv (`requirements/tgbot.txt`, included in
   `dev.txt`/`prod.txt`); see [Python Environment](python-environment.md). No
   separate install step.
5. Start: `./deploy/restart_tgbot.sh --sync`

## Usage

- `/start` — Initialize bot
- `/clear`, `/voice`, `/text` — Typed commands
- **Text message** — Chat with AI
- **Voice message** — Transcribe, process, respond with voice
- **Share location** — Enable location-aware responses

### Voice Commands (instant, no LLM)

- "use voice" / "use text" — switch response mode
- "clear history" — start fresh
- "repeat that" — replay last response
- "save that" — save last response to memory
- "get shopping" — retrieve named entry (case-insensitive)
- "add shopping pizza dough" — append to named entry
- "memo buy milk" — save as memory
- "calendar" — today and tomorrow
- "journal tomorrow 2pm doctors" — create calendar event
- "recent" — last 5 entries
- "voice help" — list commands

## Mini App server access

The Mini App is served at `/tjai/m/` by the `miniapp` view, which carries no
`@login_required` so the page can render before authentication. On load the
app POSTs the Telegram WebApp `initData` to `/tjai/api/tg-auth` (`tg_auth`
view), which validates the HMAC signature against the bot token, requires a
recent `auth_date`, checks the Telegram user against `TELEGRAM_USER_ID`, and
logs in the Django superuser. The Mini App then holds a normal Django session,
and the `@login_required` API endpoints it calls authenticate against that
session.

`api_entry_save` (`api/entry/<uuid>/save`), used by the Mini App editor, also
carries no `@login_required` by design.

## Management

```bash
./deploy/restart_tgbot.sh          # Restart
./deploy/restart_tgbot.sh --sync   # Sync code from dev and restart
tj                                  # Shows bot status in CLI
```

`deploy/update_from_dev.sh` also restarts the bot (`tjai-tgbot` service) on
every deploy.

Entries created via Telegram are tagged `fromtg` and `fromai`.
