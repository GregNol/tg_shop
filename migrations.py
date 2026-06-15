"""Lightweight, idempotent schema migration runner.

On startup the bot compares the schema version recorded in the database with
`SCHEMA_VERSION` below and applies any pending migrations automatically, inside
transactions, recording each applied step in the `schema_migrations` table.

To add a migration: write an `async def _mN_<name>(conn)` step (idempotent — use
`IF NOT EXISTS` etc.), append `(N, "<name>", _mN_<name>)` to `MIGRATIONS`, and
bump `SCHEMA_VERSION` to N.
"""

import logging

# Human-readable bot version (stored in settings as `bot_version`).
APP_VERSION = "2.0.0"  # 3x-ui -> Remnawave migration

# Latest schema migration number; must equal the highest version in MIGRATIONS.
SCHEMA_VERSION = 1

logger = logging.getLogger(__name__)


async def _ensure_meta(conn):
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


async def _current_version(conn) -> int:
    val = await conn.fetchval("SELECT MAX(version) FROM schema_migrations")
    return int(val) if val is not None else 0


async def _set_bot_version(conn):
    await conn.execute(
        """
        INSERT INTO settings (key, value) VALUES ('bot_version', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        APP_VERSION,
    )


# --- Migration steps (each must be idempotent) ---

async def _m1_remnawave(conn):
    """3x-ui -> Remnawave: add subscription_url, make legacy inbound_id optional."""
    await conn.execute(
        "ALTER TABLE vpn_subscription_clients ADD COLUMN IF NOT EXISTS subscription_url TEXT"
    )
    await conn.execute(
        "ALTER TABLE vpn_subscription_clients ALTER COLUMN inbound_id SET DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE vpn_subscription_clients ALTER COLUMN inbound_id DROP NOT NULL"
    )


# Ordered list of (version, name, coroutine) migrations.
MIGRATIONS = [
    (1, "remnawave_subscription_url", _m1_remnawave),
]


async def run_migrations(conn):
    """Apply all pending migrations. Safe to call on every startup."""
    await _ensure_meta(conn)
    current = await _current_version(conn)

    if current >= SCHEMA_VERSION:
        logger.info("DB schema up-to-date (version=%s, app=%s)", current, APP_VERSION)
        await _set_bot_version(conn)
        return

    logger.info(
        "Migrating DB schema: %s -> %s (app=%s)", current, SCHEMA_VERSION, APP_VERSION
    )
    for version, name, fn in MIGRATIONS:
        if version > current:
            async with conn.transaction():
                logger.info("Applying migration %s: %s", version, name)
                await fn(conn)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2) "
                    "ON CONFLICT (version) DO NOTHING",
                    version,
                    name,
                )
    await _set_bot_version(conn)
    logger.info("DB schema migrated to version %s", SCHEMA_VERSION)
