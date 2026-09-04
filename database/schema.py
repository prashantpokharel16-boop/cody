SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,

    -- Welcome system
    welcome_channel_id INTEGER,
    welcome_message TEXT,
    welcome_enabled INTEGER NOT NULL DEFAULT 0,

    -- Other systems
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


-- Stores who created each welcome configuration panel.
-- Only that administrator can edit their panel.
CREATE TABLE IF NOT EXISTS welcome_panels (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER PRIMARY KEY,
    creator_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_welcome_panels_guild
ON welcome_panels(guild_id);


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
    """Create all database tables and safely update older databases."""

    if database.connection is None:
        raise RuntimeError("Database is not connected.")

    # Create all tables.
    await database.connection.executescript(SCHEMA)

    # Check which columns already exist in guild_settings.
    cursor = await database.connection.execute(
        "PRAGMA table_info(guild_settings)"
    )

    columns = await cursor.fetchall()

    existing_columns = {
        column[1]
        for column in columns
    }

    # Add welcome_message if this is an older database.
    if "welcome_message" not in existing_columns:
        await database.connection.execute(
            """
            ALTER TABLE guild_settings
            ADD COLUMN welcome_message TEXT
            """
        )

    # Add welcome_enabled if this is an older database.
    if "welcome_enabled" not in existing_columns:
        await database.connection.execute(
            """
            ALTER TABLE guild_settings
            ADD COLUMN welcome_enabled INTEGER NOT NULL DEFAULT 0
            """
        )

    # Save database changes.
    await database.connection.commit()
