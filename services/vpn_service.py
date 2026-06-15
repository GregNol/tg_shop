"""Shared VPN provisioning used by purchase handlers and the auto-resume flow.

`provision_vpn` performs the panel (Remnawave) + DB side of issuing/extending a
subscription. It does NOT touch the user balance or send messages — callers own
payment, refunds and UX so transactional control stays in one place.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from services.remnawave import remnawave_from_config, squads_for_tariff, make_username

logger = logging.getLogger(__name__)


def tariff_name_for(tariff_key: str) -> str:
    return 'Premium+' if tariff_key == 'premium' else 'Стандартный'


async def provision_vpn(
    repo,
    config,
    user_id: int,
    tariff_key: str = 'standard',
    days: int = 30,
    total_gb: int = 0,
    set_total_gb: Optional[int] = None,
) -> dict:
    """Create a new subscription or extend the user's existing one of this tariff.

    `total_gb` is the traffic limit applied on creation. `set_total_gb`, when given,
    also resets the limit on extend (e.g. lifting a trial's cap on a paid purchase).

    Returns a dict: {ok, extended, client_uuid, subscription_url, new_expiry,
    tariff_name, error}.
    """
    premium = tariff_key == 'premium'
    tariff_name = tariff_name_for(tariff_key)

    if premium and not config.remnawave.squads_premium:
        return {'ok': False, 'error': 'premium_not_configured'}

    subs = await repo.get_user_vpn_subscriptions(user_id)
    active_sub = next((s for s in subs if s['tariff_name'] == tariff_name), None)

    remna = remnawave_from_config(config)
    try:
        if active_sub:
            current_expiry = active_sub['expires_at']
            if current_expiry and current_expiry > datetime.utcnow():
                new_expiry = current_expiry + timedelta(days=days)
            else:
                new_expiry = datetime.utcnow() + timedelta(days=days)

            update_kwargs = {'expire_at': new_expiry}
            if set_total_gb is not None:
                update_kwargs['total_gb'] = set_total_gb
            updated = await remna.update_user(active_sub['client_uuid'], **update_kwargs)
            if not updated:
                return {'ok': False, 'error': 'panel'}
            sub_url = updated.get('subscriptionUrl') or active_sub.get('subscription_url')
            if updated.get('subscriptionUrl'):
                await repo.set_subscription_url(active_sub['client_uuid'], updated['subscriptionUrl'])
            await repo.extend_vpn_subscription(active_sub['client_uuid'], new_expires_at=new_expiry)
            if set_total_gb is not None:
                await repo.set_client_total_gb(active_sub['client_uuid'], set_total_gb)
            return {
                'ok': True, 'extended': True,
                'client_uuid': active_sub['client_uuid'],
                'subscription_url': sub_url,
                'new_expiry': new_expiry,
                'tariff_name': tariff_name,
            }

        new_expiry = datetime.utcnow() + timedelta(days=days)
        username = make_username(user_id)
        device_limit = int(await repo.get_setting('vpn_device_limit_default') or 3)
        user_obj = await remna.create_user(
            username=username,
            expire_at=new_expiry,
            squad_uuids=squads_for_tariff(config, premium=premium),
            total_gb=total_gb,
            telegram_id=user_id,
            hwid_device_limit=device_limit,
        )
        if not user_obj:
            return {'ok': False, 'error': 'panel'}

        client_id = user_obj['uuid']
        sub_url = user_obj.get('subscriptionUrl')
        try:
            await repo.create_vpn_subscription(
                user_id=user_id,
                client_uuid=client_id,
                email=username,
                inbound_id=0,
                target_tariff_name=tariff_name,
                total_gb=total_gb,
                expires_at=new_expiry,
                subscription_url=sub_url,
            )
        except Exception:
            logger.exception("provision_vpn DB write failed: user_id=%s uuid=%s", user_id, client_id)
            return {'ok': False, 'error': 'db'}

        return {
            'ok': True, 'extended': False,
            'client_uuid': client_id,
            'subscription_url': sub_url,
            'new_expiry': new_expiry,
            'tariff_name': tariff_name,
        }
    finally:
        await remna.close()
