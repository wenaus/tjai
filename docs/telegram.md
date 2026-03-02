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
- **Mini App** — Telegram WebApp with tabs for Calendar, Picks, ReadMe, Synopsis, RSS, Named entries, Contexts, Search. Sticky headers, same triage UX as desktop
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
4. Install: `pip install -r requirements-tgbot.txt`
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

## Management

```bash
./deploy/restart_tgbot.sh          # Restart
./deploy/restart_tgbot.sh --sync   # Sync code from dev and restart
tj                                  # Shows bot status in CLI
```

Entries created via Telegram are tagged `fromtg` and `fromai`.
