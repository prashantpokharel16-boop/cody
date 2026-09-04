import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    str(BASE_DIR / "data" / "bot.db"),
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def validate_config() -> None:
    """Validate required configuration."""

    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. "
            "Create a .env file and add your Discord bot token."
        )


def ensure_directories() -> None:
    """Create directories required by the bot."""

    Path(DATABASE_PATH).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    (BASE_DIR / "logs").mkdir(
        parents=True,
        exist_ok=True,
    )
