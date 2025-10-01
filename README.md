# JerseySTEM Volunteer Onboarding Discord Bot

This bot greets new members, collects volunteer info (location, preferred school, availability), assigns a `Volunteer` role, saves responses to `volunteers.json`, and logs details to a `#volunteer-log` channel.

## Features
- Greets new members and DMs onboarding UI
- School preference via dropdown
- Location and availability via modal
- `/onboard` command as fallback to start in-server
- `/finish` command to finalize, assign role, and log
- JSON persistence in `volunteers.json`

## Prerequisites
- Python 3.10+
- A Discord application with a bot token
- Bot invited with the following permissions:
  - Read Messages/View Channels
  - Send Messages
  - Manage Roles (to assign `Volunteer`)
  - Manage Channels (optional: to create `#volunteer-log` if missing)
  - Read Message History

## Setup
1. Install dependencies:
```bash
pip install -U "discord.py>=2.3" python-dotenv
```

2. Configure environment with a .env file (recommended):
```bash
cp .env.example .env
# edit .env and set values
```

Alternatively, export environment variables directly:
```bash
export DISCORD_BOT_TOKEN="your-bot-token"
# Optional but recommended for faster slash command sync
export DISCORD_GUILD_ID="your-guild-id"
# Optional customizations
export VOLUNTEER_ROLE_NAME="Volunteer"
export VOLUNTEER_LOG_CHANNEL="volunteer-log"
# Comma-separated list for dropdown
export SCHOOL_OPTIONS="JerseySTEM - Newark,JerseySTEM - Jersey City,JerseySTEM - Hoboken"
```

3. Run the bot:
```bash
python volunteer_bot.py
```

## Usage
- When a member joins, they receive a DM with:
  - A dropdown to choose school
  - A button to open a modal for location and availability
- If DMs are closed, members can use `/onboard` in the server.
- After providing info, they run `/finish` (in DMs or server) to:
  - Save their data
  - Get the `Volunteer` role
  - Log to `#volunteer-log`

## Notes
- Data is saved to `volunteers.json` in the project directory.
- If the `Volunteer` role or `#volunteer-log` channel do not exist, the bot will try to create them (requires permissions).
- If you change command names or options, re-sync commands may take time globally; set `DISCORD_GUILD_ID` during development for instant sync in that server.