"""One-off cleanup: collapse each user to a SINGLE VPN subscription.

Legacy data (3x-ui era, Premium two-panel) left some users with several
subscription/client rows, which the startup sync recreated as separate Remnawave
users. This keeps the best one per user and removes the rest from both Remnawave
and the DB.

Keeper selection per user: Premium first, then latest expiry, then 'primary'
panel, then most recently created.

SAFE BY DEFAULT: dry-run (reports only). To actually delete, run with --apply
(or env CONSOLIDATE_APPLY=1).

    docker compose run --rm bot python -m scripts.consolidate_subscriptions          # dry-run
    docker compose run --rm bot python -m scripts.consolidate_subscriptions --apply   # delete
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

import asyncpg

from config import load_config
from services.remnawave import remnawave_from_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("consolidate")

APPLY = "--apply" in sys.argv or os.getenv("CONSOLIDATE_APPLY") == "1"


def _is_premium(tariff) -> bool:
    t = (tariff or "").lower()
    return "прем" in t or "premium" in t


def _keeper_key(row):
    return (
        1 if _is_premium(row["tariff_name"]) else 0,
        row["expires_at"] or datetime.min,
        1 if (row["panel"] or "primary") == "primary" else 0,
        row["created_at"] or datetime.min,
    )


async def run():
    config = load_config()
    pool = await asyncpg.create_pool(config.database_url)
    remna = remnawave_from_config(config)

    rows = await pool.fetch(
        """
        SELECT s.user_id, s.id AS subscription_id, s.tariff_name, s.expires_at,
               c.client_uuid, c.email, c.panel, c.created_at
        FROM vpn_subscriptions s
        JOIN vpn_subscription_clients c ON c.subscription_id = s.id
        ORDER BY s.user_id
        """
    )

    by_user = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)

    mode = "APPLY (deleting)" if APPLY else "DRY-RUN (no changes)"
    logger.info("Consolidation mode: %s | users with subs: %s", mode, len(by_user))

    users_touched = 0
    rw_deleted = 0
    rw_failed = 0
    subs_deleted = 0
    clients_deleted = 0

    try:
        for user_id, clients in by_user.items():
            if len(clients) <= 1:
                continue  # already single

            keeper = max(clients, key=_keeper_key)
            keeper_sub = keeper["subscription_id"]
            others = [c for c in clients if c["client_uuid"] != keeper["client_uuid"]]
            users_touched += 1

            logger.info(
                "user=%s keep tariff=%s uuid=%s | removing %s extra",
                user_id, keeper["tariff_name"], keeper["client_uuid"], len(others),
            )

            for c in others:
                logger.info("  - drop uuid=%s tariff=%s panel=%s", c["client_uuid"], c["tariff_name"], c["panel"])
                if APPLY:
                    try:
                        await remna.delete_user(c["client_uuid"])
                        rw_deleted += 1
                    except Exception:
                        rw_failed += 1
                        logger.exception("  failed to delete Remnawave user %s", c["client_uuid"])

            if APPLY:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        # remove other subscriptions (cascade drops their clients)
                        res = await conn.execute(
                            "DELETE FROM vpn_subscriptions WHERE user_id = $1 AND id <> $2",
                            user_id, keeper_sub,
                        )
                        subs_deleted += int(res.split()[-1]) if res.startswith("DELETE") else 0
                        # remove any extra clients still attached to the keeper subscription
                        res2 = await conn.execute(
                            "DELETE FROM vpn_subscription_clients WHERE subscription_id = $1 AND client_uuid <> $2",
                            keeper_sub, keeper["client_uuid"],
                        )
                        clients_deleted += int(res2.split()[-1]) if res2.startswith("DELETE") else 0
    finally:
        await remna.close()
        await pool.close()

    logger.info(
        "Done (%s): users_touched=%s | rw_deleted=%s rw_failed=%s subs_deleted=%s extra_clients_deleted=%s",
        mode, users_touched, rw_deleted, rw_failed, subs_deleted, clients_deleted,
    )
    if not APPLY:
        logger.info("This was a DRY-RUN. Re-run with --apply to perform deletions.")


if __name__ == "__main__":
    asyncio.run(run())
