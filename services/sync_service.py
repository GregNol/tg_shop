"""Startup reconciliation between the bot DB and the Remnawave panel.

For every subscription stored in the DB:
  * if the user is missing in Remnawave -> it is (re)created there and the DB
    record is repointed to the new Remnawave uuid / subscription URL;
  * if it exists but the traffic limit or expiry differ -> the MAXIMUM of the two
    is kept (applied to whichever side is lower). Unlimited (0 GB) and "no expiry"
    are treated as the maximum.
"""

import logging
from datetime import datetime

from services.remnawave import (
    RemnawaveAPI,
    RemnawaveAPIError,
    remnawave_from_config,
    squads_for_tariff,
    make_username,
    parse_remnawave_datetime,
    GB,
)

logger = logging.getLogger(__name__)


def _is_premium(tariff_name) -> bool:
    normalized = (tariff_name or '').lower()
    return 'прем' in normalized or 'premium' in normalized


def _resolve_traffic_gb(db_total_gb, rw_traffic_bytes) -> int:
    """Return the target traffic limit in GB (0 = unlimited wins)."""
    db_gb = int(db_total_gb or 0)
    rw_gb = int(round((rw_traffic_bytes or 0) / GB))
    if db_gb == 0 or rw_gb == 0:
        return 0  # unlimited is the maximum
    return max(db_gb, rw_gb)


def _resolve_expiry(db_expiry, rw_expiry):
    """Return the target expiry (latest). None means 'forever' and wins."""
    if db_expiry is None or rw_expiry is None:
        return None
    return max(db_expiry, rw_expiry)


async def _sync_one(remna: RemnawaveAPI, repo, config, row) -> str:
    client_uuid = row['client_uuid']
    premium = _is_premium(row['tariff_name'])
    db_total_gb = row['total_gb']
    db_expiry = row['expires_at']

    try:
        rw_user = await remna.get_user(client_uuid)
    except RemnawaveAPIError as e:
        # Ambiguous failure — do NOT recreate (would risk duplicates). Skip this one.
        logger.warning("Sync: skip client_uuid=%s (panel lookup failed): %s", client_uuid, e)
        return 'error'

    # --- Case 1: confirmed missing (404) in Remnawave -> (re)create ---
    if rw_user is None:
        username = make_username(row['user_id'])
        created = await remna.create_user(
            username=username,
            expire_at=db_expiry,
            squad_uuids=squads_for_tariff(config, premium=premium),
            total_gb=int(db_total_gb or 0),
            telegram_id=row['user_id'],
        )
        if not created:
            logger.error("Sync: failed to recreate Remnawave user for client_uuid=%s", client_uuid)
            return 'error'
        await repo.update_client_identity(
            old_uuid=client_uuid,
            new_uuid=created['uuid'],
            new_email=username,
            subscription_url=created.get('subscriptionUrl'),
        )
        logger.info("Sync: recreated missing Remnawave user %s -> %s", client_uuid, created['uuid'])
        return 'recreated'

    # --- Case 2: exists -> reconcile traffic limit and expiry to the maximum ---
    rw_total_bytes = rw_user.get('trafficLimitBytes') or 0
    rw_expiry = parse_remnawave_datetime(rw_user.get('expireAt'))

    target_gb = _resolve_traffic_gb(db_total_gb, rw_total_bytes)
    target_expiry = _resolve_expiry(db_expiry, rw_expiry)

    db_gb = int(db_total_gb or 0)
    rw_gb = int(round((rw_total_bytes or 0) / GB))

    rw_needs_update = False
    update_kwargs = {}

    # traffic: push to Remnawave if its limit is below target
    if target_gb != rw_gb:
        update_kwargs['total_gb'] = target_gb
        rw_needs_update = True
    # traffic: bump DB if it is below target
    if target_gb != db_gb:
        await repo.set_client_total_gb(client_uuid, target_gb)

    # expiry: only act when both sides have a date and they differ
    if db_expiry is not None and rw_expiry is not None and target_expiry is not None:
        if target_expiry != rw_expiry:
            update_kwargs['expire_at'] = target_expiry
            rw_needs_update = True
        if target_expiry != db_expiry:
            await repo.extend_vpn_subscription(client_uuid, new_expires_at=target_expiry)

    # device limit: keep the maximum of DB vs Remnawave hwidDeviceLimit
    db_devices = int(row['device_limit'] or 3)
    rw_devices = int(rw_user.get('hwidDeviceLimit') or 0)
    target_devices = max(db_devices, rw_devices)
    if target_devices != rw_devices:
        update_kwargs['hwid_device_limit'] = target_devices
        rw_needs_update = True
    if target_devices != db_devices:
        await repo.set_device_limit(client_uuid, target_devices)

    changed = bool(update_kwargs)
    if rw_needs_update:
        updated = await remna.update_user(client_uuid, **update_kwargs)
        if updated and updated.get('subscriptionUrl'):
            await repo.set_subscription_url(client_uuid, updated['subscriptionUrl'])
    elif not row.get('subscription_url') and rw_user.get('subscriptionUrl'):
        # backfill a missing subscription URL from Remnawave
        await repo.set_subscription_url(client_uuid, rw_user['subscriptionUrl'])
        changed = True

    return 'updated' if changed else 'in_sync'


async def sync_subscriptions(repo, config):
    """Reconcile all DB subscriptions with Remnawave. Safe to call at startup."""
    if not config.remnawave.token:
        logger.warning("Sync skipped: REMNAWAVE_TOKEN is not set.")
        return

    rows = await repo.get_all_subscription_clients()
    if not rows:
        logger.info("Sync: no VPN subscriptions in DB, nothing to reconcile.")
        return

    stats = {'recreated': 0, 'updated': 0, 'in_sync': 0, 'error': 0}
    remna = remnawave_from_config(config)
    try:
        for row in rows:
            try:
                result = await _sync_one(remna, repo, config, row)
                stats[result] = stats.get(result, 0) + 1
            except Exception:
                logger.exception("Sync: unhandled error for client_uuid=%s", row.get('client_uuid'))
                stats['error'] += 1
    finally:
        await remna.close()

    logger.info(
        "VPN sync done: %s checked | recreated=%s updated=%s in_sync=%s errors=%s",
        len(rows), stats['recreated'], stats['updated'], stats['in_sync'], stats['error'],
    )
