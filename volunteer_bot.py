import asyncio
import json
import logging
import os
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List

import discord
from discord import app_commands


# Configure logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("volunteer_bot")


# Load environment from .env if available
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if load_dotenv and _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH)

# Config via env vars with sensible defaults
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")  # optional, for faster command sync
VOLUNTEER_ROLE_NAME = os.getenv("VOLUNTEER_ROLE_NAME", "Volunteer")
LOG_CHANNEL_NAME = os.getenv("VOLUNTEER_LOG_CHANNEL", "volunteer-log")

# Schools may be configured via env var, else use defaults
SCHOOL_OPTIONS_ENV = os.getenv("SCHOOL_OPTIONS")
DEFAULT_SCHOOLS = [
    "JerseySTEM - Newark",
    "JerseySTEM - Jersey City",
    "JerseySTEM - Hoboken",
    "JerseySTEM - Paterson",
    "JerseySTEM - Elizabeth",
]
SCHOOL_OPTIONS: List[str] = [s.strip() for s in SCHOOL_OPTIONS_ENV.split(",")] if SCHOOL_OPTIONS_ENV else DEFAULT_SCHOOLS


# Data persistence
DATA_PATH = Path(__file__).resolve().parent / "volunteers.json"
_file_lock = asyncio.Lock()


@dataclass
class VolunteerRecord:
    user_id: int
    user_tag: str
    location: str
    school_preference: str
    availability: str
    timestamp_iso: str


class VolunteerStorage:
    def __init__(self, json_path: Path):
        self.json_path = json_path
        self._cache: Dict[str, Dict] = {}

    async def load(self) -> None:
        if not self.json_path.exists():
            self._cache = {}
            return
        try:
            async with _file_lock:
                text = self.json_path.read_text(encoding="utf-8")
            self._cache = json.loads(text or "{}")
        except Exception as exc:
            logger.error("Failed to load JSON: %s", exc)
            self._cache = {}

    async def save_record(self, record: VolunteerRecord) -> None:
        try:
            await self.load()
            self._cache[str(record.user_id)] = asdict(record)
            async with _file_lock:
                self.json_path.write_text(json.dumps(self._cache, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.exception("Error saving volunteer record: %s", exc)


class SessionStore:
    def __init__(self):
        self._sessions: Dict[int, Dict[str, str]] = {}

    def get(self, user_id: int) -> Dict[str, str]:
        return self._sessions.setdefault(user_id, {})

    def set_value(self, user_id: int, key: str, value: str) -> None:
        self.get(user_id)[key] = value

    def is_complete(self, user_id: int) -> bool:
        data = self._sessions.get(user_id, {})
        return bool(data.get("school") and data.get("location") and data.get("availability"))

    def pop(self, user_id: int) -> Dict[str, str]:
        return self._sessions.pop(user_id, {})


class SchoolSelect(discord.ui.Select):
    def __init__(self, session_store: SessionStore):
        options = [discord.SelectOption(label=name, value=name) for name in SCHOOL_OPTIONS]
        super().__init__(placeholder="Choose your preferred school", min_values=1, max_values=1, options=options, custom_id="school_select")
        self.session_store = session_store

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        self.session_store.set_value(interaction.user.id, "school", selected)
        await interaction.response.send_message(f"School set to: {selected}", ephemeral=True)


class DetailsModal(discord.ui.Modal, title="Volunteer Details"):
    def __init__(self, session_store: SessionStore):
        super().__init__(timeout=300)
        self.session_store = session_store
        self.location = discord.ui.TextInput(
            label="Where are you located?",
            style=discord.TextStyle.short,
            required=True,
            placeholder="City/Neighborhood",
            max_length=100,
        )
        self.availability = discord.ui.TextInput(
            label="What times are you available?",
            style=discord.TextStyle.paragraph,
            required=True,
            placeholder="e.g., Weekdays 3-6pm, Sat mornings",
            max_length=500,
        )
        self.add_item(self.location)
        self.add_item(self.availability)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session_store.set_value(interaction.user.id, "location", str(self.location))
        self.session_store.set_value(interaction.user.id, "availability", str(self.availability))
        await interaction.response.send_message("Thanks! Details captured.", ephemeral=True)


class OnboardingView(discord.ui.View):
    def __init__(self, session_store: SessionStore):
        super().__init__(timeout=600)
        self.session_store = session_store
        self.add_item(SchoolSelect(session_store))

    @discord.ui.button(label="Open details", style=discord.ButtonStyle.primary, custom_id="open_modal_btn")
    async def open_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DetailsModal(self.session_store)
        await interaction.response.send_modal(modal)


class VolunteerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.session_store = SessionStore()
        self.storage = VolunteerStorage(DATA_PATH)

    async def setup_hook(self) -> None:
        # Optionally sync to a single guild for faster updates if provided
        if GUILD_ID:
            try:
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Synced commands to guild %s", GUILD_ID)
            except Exception as exc:
                logger.error("Failed to sync to guild %s: %s", GUILD_ID, exc)
        else:
            try:
                await self.tree.sync()
                logger.info("Synced commands globally")
            except Exception as exc:
                logger.error("Failed to sync commands globally: %s", exc)

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user and self.user.id)
        await self.storage.load()

    async def on_member_join(self, member: discord.Member):
        # Greet and kick off onboarding via DM; fall back to guild if DMs closed
        content = (
            f"Welcome to {member.guild.name}, {member.mention}!\n\n"
            "We'd love to learn a bit about you to match you with a volunteer opportunity.\n"
            "Please select your preferred school and share your location and availability.\n"
            "When you're done, run /finish here or in the server."
        )
        view = OnboardingView(self.session_store)
        # Remember the guild for role assignment/logging even if finishing in DMs
        self.session_store.set_value(member.id, "guild_id", str(member.guild.id))
        try:
            await member.send(content, view=view)
        except discord.Forbidden:
            # Post in a channel where bot can speak, preferably system channel
            channel = member.guild.system_channel or next((c for c in member.guild.text_channels if c.permissions_for(member.guild.me).send_messages), None)
            if channel:
                await channel.send(f"Welcome, {member.mention}! I've DMed you to collect onboarding details, but your DMs seem closed. Use /onboard to start here instead.")

    async def finalize_onboarding(self, interaction: discord.Interaction) -> Optional[str]:
        user_id = interaction.user.id
        if not self.session_store.is_complete(user_id):
            return "Please select a school and submit your details first."

        data = self.session_store.pop(user_id)
        record = VolunteerRecord(
            user_id=user_id,
            user_tag=str(interaction.user),
            location=data["location"],
            school_preference=data["school"],
            availability=data["availability"],
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        await self.storage.save_record(record)

        # Determine guild context for role assignment and logging
        guild: Optional[discord.Guild] = interaction.guild
        guild_id_str = data.get("guild_id") or self.session_store.get(user_id).get("guild_id") if self.session_store.get(user_id) else None
        if not guild and guild_id_str:
            try:
                guild = self.get_guild(int(guild_id_str))
            except Exception:
                guild = None

        # Assign role
        try:
            member: Optional[discord.Member] = None
            if isinstance(interaction.user, discord.Member):
                member = interaction.user
            elif guild:
                member = guild.get_member(user_id)
            if member and guild:
                role = await get_or_create_role(guild, VOLUNTEER_ROLE_NAME)
                if role and role not in member.roles:
                    await member.add_roles(role, reason="Completed volunteer onboarding")
        except discord.Forbidden:
            logger.warning("Missing permissions to assign role.")
        except Exception:
            logger.exception("Error assigning role")

        # Log to channel
        try:
            if guild:
                channel = await get_or_create_text_channel(guild, LOG_CHANNEL_NAME)
                if channel:
                    embed = discord.Embed(title="New Volunteer", color=discord.Color.green())
                    embed.add_field(name="User", value=f"{interaction.user.mention} ({record.user_tag})", inline=False)
                    embed.add_field(name="Location", value=record.location, inline=True)
                    embed.add_field(name="Preferred School", value=record.school_preference, inline=True)
                    embed.add_field(name="Availability", value=record.availability, inline=False)
                    embed.set_footer(text=record.timestamp_iso)
                    await channel.send(embed=embed)
        except Exception:
            logger.exception("Error logging to channel")

        return None


async def get_or_create_role(guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
    role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), guild.roles)
    if role:
        return role
    try:
        me = guild.me
        if me and guild.owner_id == me.id:
            # Owner can always create; otherwise requires manage_roles
            pass
        role = await guild.create_role(name=role_name, reason="Volunteer role for onboarding")
        return role
    except discord.Forbidden:
        logger.warning("Insufficient permissions to create role '%s'", role_name)
    except Exception:
        logger.exception("Error creating role '%s'", role_name)
    return None


async def get_or_create_text_channel(guild: discord.Guild, channel_name: str) -> Optional[discord.TextChannel]:
    channel = discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == channel_name.lower(), guild.channels)
    if channel:
        return channel
    try:
        channel = await guild.create_text_channel(channel_name, reason="Volunteer log channel")
        return channel
    except discord.Forbidden:
        logger.warning("Insufficient permissions to create channel '%s'", channel_name)
    except Exception:
        logger.exception("Error creating channel '%s'", channel_name)
    return None


bot = VolunteerBot()


@bot.tree.command(name="onboard", description="Start volunteer onboarding")
async def onboard(interaction: discord.Interaction):
    content = (
        "Let's get you onboarded as a volunteer!\n"
        "- Choose your preferred school\n"
        "- Click 'Open details' and provide your location and availability\n\n"
        "When done, run /finish (here or in DMs)."
    )
    # Remember guild for role assignment/logging
    if interaction.guild:
        bot.session_store.set_value(interaction.user.id, "guild_id", str(interaction.guild.id))
    await interaction.response.send_message(content, view=OnboardingView(bot.session_store), ephemeral=True)


@bot.tree.command(name="finish", description="Finish volunteer onboarding after selecting school and entering details")
async def finish(interaction: discord.Interaction):
    error = await bot.finalize_onboarding(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
    else:
        await interaction.response.send_message("You're all set! The Volunteer role has been assigned (if permitted).", ephemeral=True)


def main():
    if not BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN is not set. Please set the environment variable.")
        return
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()


