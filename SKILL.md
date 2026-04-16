---
name: youtube-learning-skill
description: |
  Monitor YouTube subscriptions and generate Chinese learning notes with AI knowledge cards, synced to Feishu docs.

  **Triggers:**
  - User provides a YouTube URL and asks to "learn from this video" or "generate notes"
  - User says "/youtube-learning-skill" followed by a video URL
  - User asks for a Chinese summary or knowledge cards from a YouTube video

  **Components:**
  - RSS Monitor (cron): checks for new videos, sends Feishu notification cards
  - Callback Server (daemon): receives Feishu card callbacks, orchestrates the learning pipeline
  - YouTube OAuth (one-time): authorizes YouTube account for subscription sync
license: MIT
compatibility: |
  Requires Python 3.10+, yt-dlp, lark-cli (npm), google-genai SDK, Gemini API key,
  Feishu app with bot capability, and optionally YouTube OAuth credentials.
metadata:
  author: DuoDuo25
  version: "1.0"
---

# YouTube Learning Skill

Generate Chinese learning notes and visual knowledge cards from YouTube videos, automatically synced to Feishu documents.

## Architecture

Three runtime components:

1. **RSS Monitor** (cron job, hourly): syncs YouTube subscriptions → checks for new videos via YouTube Data API v3 → sends Feishu interactive card notifications
2. **Callback Server** (long-running daemon): receives Feishu card callbacks via WebSocket → orchestrates the learning pipeline
3. **YouTube OAuth** (one-time setup): authorizes Google account to fetch subscription list

## Setup (First Time)

Before using this skill, the user must complete setup:

```bash
# 1. Install dependencies
pip install -r <skill-path>/requirements.txt

# 2. Configure environment
cp <skill-path>/.env.example <skill-path>/.env
# Fill in: GEMINI_API_KEY, FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_CHAT_ID, FEISHU_FOLDER_TOKEN

# 3. Configure lark-cli
npm install -g lark-cli
lark-cli pair  # Follow prompts to bind Feishu app

# 4. Customize learning profile
cp <skill-path>/references/user_profile.example.md <skill-path>/references/user_profile.md
# Edit user_profile.md with user's background, goals, and preferences

# 5. (Optional) YouTube OAuth for subscription sync
python <skill-path>/scripts/youtube_oauth.py setup
python <skill-path>/scripts/youtube_oauth.py sync
```

## Usage Mode 1: Single Video (Manual)

When the user provides a YouTube URL and wants to learn from it:

### Step 1: Generate Learning Notes

```bash
python <skill-path>/scripts/gemini_notes.py --url "<youtube_url>"
```

This uses Gemini in streaming mode to analyze the video and produce:
- Chinese learning notes (markdown)
- Knowledge card prompts (JSON) for image generation

Output: `notes.md` and `card_prompts.json` in `<skill-path>/data/outputs/<video_id>/`

### Step 2: Generate Knowledge Cards

```bash
python <skill-path>/scripts/gemini_cards.py \
  --prompts "<skill-path>/data/outputs/<video_id>/card_prompts.json" \
  --output "<skill-path>/data/outputs/<video_id>"
```

Generates 2K resolution knowledge card images in parallel (ThreadPoolExecutor), with automatic retry on timeout.

### Step 3: Sync to Feishu

```bash
python <skill-path>/scripts/feishu_sync.py \
  --markdown "<skill-path>/data/outputs/<video_id>/notes.md" \
  --title "<video_title>" \
  --url "<youtube_url>"
```

Creates a Feishu document, inserts knowledge card images, and sends a notification.

## Usage Mode 2: Automated Pipeline (Recommended)

Start the callback server and RSS monitor for fully automated learning:

```bash
# Start callback server (keep running)
python <skill-path>/scripts/callback_server.py

# Set up cron job for RSS monitoring (every hour)
# crontab -e, add:
# 17 * * * * cd <skill-path> && python3 scripts/rss_monitor.py check --hours 2 >> data/rss.log 2>&1
```

Flow: new video detected → Feishu notification card → user clicks "Start Learning" → callback server runs full pipeline automatically (~5 min).

## Testing Commands

```bash
# Test Feishu notification card
python <skill-path>/scripts/rss_monitor.py test <youtube_url>

# Test notes generation only
python <skill-path>/scripts/gemini_notes.py --url <youtube_url>

# Test card generation from existing prompts
python <skill-path>/scripts/gemini_cards.py --prompts <card_prompts.json> --output ./test_output

# Manage YouTube subscriptions
python <skill-path>/scripts/youtube_oauth.py list
python <skill-path>/scripts/youtube_oauth.py add <channel_url> --name "Channel Name"
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/youtube_oauth.py` | YouTube OAuth authorization + subscription management |
| `scripts/rss_monitor.py` | Video monitoring via YouTube Data API + Feishu notification cards |
| `scripts/callback_server.py` | Feishu WebSocket callback handler + learning pipeline orchestration |
| `scripts/gemini_notes.py` | Gemini streaming video analysis → Chinese notes + card prompts (two-step) |
| `scripts/gemini_cards.py` | Gemini image generation → 2K knowledge cards (parallel + retry) |
| `scripts/feishu_sync.py` | Feishu document creation + image insertion via lark-cli |

## References

| File | Content |
|------|---------|
| `references/user_profile.example.md` | Template for user background (copy to `user_profile.md` and customize) |
| `references/output_format.md` | Learning notes structure and writing style requirements |

## Key Technical Details

- **Streaming mode**: Video analysis uses `generate_content_stream` to avoid HTTP timeout on long videos (1h+)
- **Two-step Gemini calls**: Step 1 analyzes video → notes; Step 2 takes notes text → card prompts (no re-watching)
- **Parallel image generation**: All knowledge cards generated concurrently via ThreadPoolExecutor
- **Auto retry**: Each card image retries up to 2 times on timeout
- **HTTP timeout**: Gemini client configured with 10-minute timeout (`HttpOptions(timeout=600000)`)

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `FEISHU_APP_ID` | Yes | Feishu app ID |
| `FEISHU_APP_SECRET` | Yes | Feishu app secret |
| `FEISHU_CHAT_ID` | Yes | Target Feishu group chat ID |
| `FEISHU_FOLDER_TOKEN` | Yes | Feishu folder for storing documents |
| `YOUTUBE_CLIENT_ID` | Optional | Google OAuth client ID (for subscription sync) |
| `YOUTUBE_CLIENT_SECRET` | Optional | Google OAuth client secret |
