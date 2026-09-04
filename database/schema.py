SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    welcome_channel_id INTEGER,
    goodbye_channel_id INTEGER,
    log_channel_id INTEGER,
    autorole_id INTEGER,
    xp_enabled INTEGER NOT NULL DEFAULT 1,
    economy_enabled INTEGER NOT NULL DEFAULT 1,
    automod_enabled INTEGER NOT NULL DEFAULT 0,
    ticket_category_id INTEGER,
    ticket_staff_role_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_warnings_guild_user
ON warnings(guild_id, user_id);

CREATE TABLE IF NOT EXISTS user_xp (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS economy_users (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    wallet INTEGER NOT NULL DEFAULT 0,
    bank INTEGER NOT NULL DEFAULT 0,
    last_daily INTEGER,
    last_work INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS economy_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    transaction_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tickets_guild_user
ON tickets(guild_id, user_id);

CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended INTEGER NOT NULL DEFAULT 0
);
"""


async def initialize_schema(database) -> None:
    """Create all database tables."""

    if database.connection is None:
        raise RuntimeError("Database is not connected.")

    await database.connection.executescript(SCHEMA)
    await database.connection.commit()
