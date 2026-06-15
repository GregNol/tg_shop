import asyncio
from datetime import datetime, timedelta
import logging
import os

import asyncpg
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import load_config
from services.repository import Repository
from services.remnawave import remnawave_from_config
from services.fragment_sender import FragmentSender

logging.basicConfig(level=logging.INFO)

# thresholds in seconds
THRESHOLDS = {
    '3d': 3 * 24 * 3600,
    '2d': 2 * 24 * 3600,
    '1d': 1 * 24 * 3600,
    '12h': 12 * 3600,
}
# window of checking (seconds) — script should run at least twice per window
WINDOW = 30 * 60  # 30 minutes

NOTIFY_TEXTS = {
    '3d': 'Ваша подписка ВПН истекает через 3 дня. Продлите, чтобы не потерять доступ.',
    '2d': 'Ваша подписка ВПН истекает через 2 дня. Продлите, чтобы не потерять доступ.',
    '1d': 'Ваша подписка ВПН истекает через 1 день. Продлите, чтобы не потерять доступ.',
    '12h': 'Ваша подписка ВПН истекает через 12 часов. Продлите, чтобы не потерять доступ.'
}

AUTO_RENEW_DAYS = 30
EXPIRY_REMINDER_KEYS = ('3d', '2d', '1d', '12h')


def _is_premium_tariff(tariff_name: str | None) -> bool:
    normalized = (tariff_name or '').lower()
    return 'прем' in normalized or 'premium' in normalized


def _renewal_price_for_tariff(tariff_name: str | None, standard_price: float, premium_price: float) -> tuple[float, str]:
    if _is_premium_tariff(tariff_name):
        return premium_price, 'Premium+'
    return standard_price, 'Стандартный'


async def _clear_subscription_notifications(pool, subscription_id: int):
    await pool.execute(
        "DELETE FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when IN ('3d', '2d', '1d', '12h')",
        subscription_id,
    )


def _low_gb_notification_key(client_uuid: str) -> str:
    return f"low_gb:{client_uuid}"

async def run():
    config = load_config()
    database_url = config.database_url
    bot_token = config.bot.bot_token

    pool = await asyncpg.create_pool(database_url)
    repo = Repository(pool)
    bot = Bot(token=bot_token)
    fragment_sender = FragmentSender(config, bot)

    standard_price = float((await repo.get_setting('vpn_standard_price') or '100'))
    premium_price = float((await repo.get_setting('vpn_premium_price') or '400'))

    now = datetime.utcnow()
    # look for subscriptions that expire within next 3 days
    max_window = max(THRESHOLDS.values()) + WINDOW
    rows = await pool.fetch("SELECT id, user_id, expires_at, tariff_name FROM vpn_subscriptions WHERE expires_at IS NOT NULL AND expires_at > $1 AND expires_at <= $2", now, now + timedelta(seconds=max_window))

    for row in rows:
        sub_id = row['id']
        user_id = row['user_id']
        expires_at = row['expires_at']
        if not expires_at:
            continue
        remaining = (expires_at - now).total_seconds()
        for key, threshold in THRESHOLDS.items():
            if threshold - WINDOW <= remaining <= threshold + WINDOW:
                # check notification already sent
                exists = await pool.fetchval("SELECT 1 FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, key)
                if exists:
                    continue

                renewal_price, tariff_label = _renewal_price_for_tariff(row['tariff_name'], standard_price, premium_price)
                current_expiry = expires_at
                new_expiry = current_expiry + timedelta(days=AUTO_RENEW_DAYS)
                new_expiry_ms = int(new_expiry.timestamp() * 1000)

                clients = []
                try:
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            locked_sub = await conn.fetchrow(
                                "SELECT id, user_id, expires_at, tariff_name FROM vpn_subscriptions WHERE id = $1 FOR UPDATE",
                                sub_id,
                            )
                            if not locked_sub:
                                continue

                            locked_expiry = locked_sub['expires_at']
                            if not locked_expiry or locked_expiry <= now:
                                continue

                            if locked_expiry != expires_at:
                                continue

                            current_expiry = locked_expiry
                            old_expiry_ms = int(current_expiry.timestamp() * 1000)
                            renewal_key = f"autorenew:{old_expiry_ms}"

                            renewal_exists = await conn.fetchval(
                                "SELECT 1 FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2",
                                sub_id,
                                renewal_key,
                            )
                            if renewal_exists:
                                continue

                            user_row = await conn.fetchrow("SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE", user_id)
                            user_balance_val = float(user_row['balance']) if user_row and user_row['balance'] is not None else 0.0
                            if user_balance_val < renewal_price:
                                raise RuntimeError("insufficient_funds")

                            clients = await conn.fetch(
                                "SELECT client_uuid, email, inbound_id, panel, total_gb FROM vpn_subscription_clients WHERE subscription_id = $1 ORDER BY created_at ASC",
                                sub_id,
                            )

                            await conn.execute(
                                "INSERT INTO vpn_expiry_notifications (subscription_id, notify_when) VALUES ($1, $2)",
                                sub_id,
                                renewal_key,
                            )
                            await conn.execute("UPDATE users SET balance = balance - $1 WHERE telegram_id = $2", renewal_price, user_id)
                            await conn.execute("UPDATE vpn_subscriptions SET expires_at = $1 WHERE id = $2", new_expiry, sub_id)

                    updated_clients = []
                    try:
                        remna = remnawave_from_config(config)
                        try:
                            for client in clients:
                                success = await remna.update_user(
                                    client['client_uuid'],
                                    expire_at=new_expiry,
                                )
                                if not success:
                                    raise RuntimeError(f"remnawave_update_failed:{client['client_uuid']}")
                                updated_clients.append(client)
                        finally:
                            await remna.close()
                    except Exception as e:
                        logging.exception(f"Remnawave renewal update failed for subscription {sub_id}: {e}")
                        remna = remnawave_from_config(config)
                        try:
                            for client in updated_clients:
                                try:
                                    await remna.update_user(
                                        client['client_uuid'],
                                        expire_at=current_expiry,
                                    )
                                except Exception:
                                    logging.exception("Failed to rollback Remnawave renewal for client %s", client['client_uuid'])
                        finally:
                            await remna.close()

                        async with pool.acquire() as conn:
                            async with conn.transaction():
                                await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2", renewal_price, user_id)
                                await conn.execute("UPDATE vpn_subscriptions SET expires_at = $1 WHERE id = $2", current_expiry, sub_id)
                                await conn.execute("DELETE FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, renewal_key)

                        await fragment_sender._notify_admins(
                            f"❌ Автопродление VPN не применено для user={user_id}, sub={sub_id}, tariff={tariff_label}: {e}"
                        )
                        continue

                    await _clear_subscription_notifications(pool, sub_id)

                    try:
                        await repo.add_purchase_to_history(
                            user_id,
                            'vpn_autorenew',
                            f'Autorenew {tariff_label} sub={sub_id} to {new_expiry.strftime("%Y-%m-%d %H:%M")}',
                            AUTO_RENEW_DAYS,
                            renewal_price,
                            0.0,
                        )
                    except Exception:
                        logging.exception("Failed to write autorenew history for user %s", user_id)

                    try:
                        await bot.send_message(
                            user_id,
                            f"✅ Автопродление подписки выполнено. Тариф: {tariff_label}. Новая дата окончания: {new_expiry.strftime('%Y-%m-%d %H:%M')}. Списано {renewal_price:.2f}₽."
                        )
                    except Exception:
                        logging.exception(f"Failed to notify user {user_id} about auto-renew success")

                    try:
                        await fragment_sender._notify_admins(
                            f"🔁 Автопродление VPN для @{user_id}: тариф {tariff_label}, списано {renewal_price:.2f}₽, до {new_expiry.strftime('%Y-%m-%d %H:%M')}"
                        )
                    except Exception:
                        logging.exception("Failed to notify admins about auto-renew")

                    logging.info("Auto-renew: user %s subscription %s tariff=%s charged %.2f", user_id, sub_id, tariff_label, renewal_price)
                    continue
                except RuntimeError as re:
                    if str(re) == 'insufficient_funds':
                        pass
                    else:
                        logging.exception(f"Auto-renew runtime error for user {user_id}, sub {sub_id}: {re}")
                except Exception as e:
                    logging.exception(f"Auto-renew transaction failed for user {user_id}, subscription {sub_id}: {e}")

                # send message with extend button when appropriate
                text = NOTIFY_TEXTS.get(key, f"Ваша подписка ВПН истекает через {key}.")
                # choose callback: for standard tariff, offer direct extend; otherwise open vpn menu
                cb = "vpn_menu"
                tariff_name = (row['tariff_name'] if row['tariff_name'] else '').lower()
                if 'стандарт' in tariff_name:
                    cb = 'buy_vpn_plan_standard_1'
                elif 'прем' in tariff_name or 'premium' in tariff_name:
                    cb = 'buy_vpn_plan_premium_1'
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Продлить", callback_data=cb), InlineKeyboardButton(text="🏠 Меню ВПН", callback_data='vpn_menu')]])
                try:
                    await bot.send_message(user_id, f"⏳ <b>Напоминание о подписке</b>\n\n{text}", reply_markup=kb)
                    await pool.execute("INSERT INTO vpn_expiry_notifications (subscription_id, notify_when) VALUES ($1, $2)", sub_id, key)
                    logging.info(f"Notified user {user_id} for subscription {sub_id} ({key})")
                except Exception as e:
                    logging.exception(f"Failed to notify user {user_id} for subscription {sub_id}: {e}")

    # Notify about low traffic (<10GB) per client; total_gb=0 means unlimited
    low_rows = await pool.fetch("""
        SELECT c.subscription_id, s.user_id, c.client_uuid, c.total_gb, c.panel, c.email, c.inbound_id
        FROM vpn_subscription_clients c
        JOIN vpn_subscriptions s ON s.id = c.subscription_id
        WHERE c.total_gb > 0 AND c.total_gb < 10 AND c.is_active = 1 AND (s.expires_at IS NULL OR s.expires_at > $1)
    """, now)

    # load auto-topup settings
    auto_enabled = (await repo.get_setting('vpn_auto_topup_enabled') or '0').strip() == '1'
    auto_gb = int((await repo.get_setting('vpn_auto_topup_gb') or '1'))
    auto_price = float((await repo.get_setting('vpn_auto_topup_price_per_gb') or '3'))
    for r in low_rows:
        sub_id = r['subscription_id']
        user_id = r['user_id']
        client_uuid = r['client_uuid']
        total_gb = r['total_gb']
        low_gb_key = _low_gb_notification_key(client_uuid)
        exists = await pool.fetchval("SELECT 1 FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, low_gb_key)
        if exists:
            continue
        # try auto-topup if enabled and user has funds
        if auto_enabled:
            cost = round(auto_price * auto_gb, 2)
            # Perform DB-side checks and updates inside a transaction with row-level locking
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        # re-check low_gb notification to avoid races
                        exists = await conn.fetchval("SELECT 1 FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, low_gb_key)
                        if exists:
                            raise RuntimeError("already_notified")

                        # lock user row and client row
                        user_row = await conn.fetchrow("SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE", user_id)
                        client_row = await conn.fetchrow("SELECT total_gb, inbound_id, email, panel FROM vpn_subscription_clients WHERE client_uuid = $1 FOR UPDATE", client_uuid)

                        user_balance_val = float(user_row['balance']) if user_row and user_row['balance'] is not None else 0.0
                        if user_balance_val < cost:
                            raise RuntimeError("insufficient_funds")

                        # deduct balance and increment client GB, mark notification (all in tx)
                        await conn.execute("UPDATE users SET balance = balance - $1 WHERE telegram_id = $2", cost, user_id)
                        await conn.execute("UPDATE vpn_subscription_clients SET total_gb = total_gb + $1 WHERE client_uuid = $2", auto_gb, client_uuid)
                        await conn.execute("INSERT INTO vpn_expiry_notifications (subscription_id, notify_when) VALUES ($1, $2)", sub_id, low_gb_key)

                        # capture values for external update after commit
                        inbound_id = client_row['inbound_id'] if client_row else None
                        email = client_row['email'] if client_row else None
                        panel = client_row['panel'] if client_row else 'primary'
                        prev_total = client_row['total_gb'] if client_row and client_row['total_gb'] is not None else 0

                # transaction committed successfully; now update external panel and notify
                new_total = (prev_total or 0) + auto_gb
                try:
                    remna = remnawave_from_config(config)
                    try:
                        await remna.update_user(client_uuid, total_gb=new_total)
                    finally:
                        await remna.close()
                except Exception as e:
                    logging.exception(f"Remnawave update failed after auto-topup for {client_uuid}: {e}")
                    # attempt to rollback DB changes: refund user, decrement GB, remove notification
                    try:
                        async with pool.acquire() as conn:
                            async with conn.transaction():
                                await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2", cost, user_id)
                                await conn.execute("UPDATE vpn_subscription_clients SET total_gb = GREATEST(total_gb - $1, 0) WHERE client_uuid = $2", auto_gb, client_uuid)
                                await conn.execute("DELETE FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, low_gb_key)
                    except Exception:
                        logging.exception("Failed to rollback DB after XUI failure for auto-topup")
                    # notify admins about failure
                    await fragment_sender._notify_admins(f"❌ Автодокупка не применена (XUI error) для user={user_id}, sub={sub_id}, client={client_uuid}: {e}")
                    continue

                # record purchase in history
                try:
                    await repo.add_purchase_to_history(user_id, 'vpn_autotopup', f'Autotopup {auto_gb}GB client={client_uuid}', auto_gb, cost, 0.0)
                except Exception:
                    logging.exception(f"Failed to write autopurchase history for user {user_id}")

                # notify user and admins
                try:
                    await bot.send_message(user_id, f"✅ Автодокупка: {auto_gb} ГБ успешно куплены, списано {cost:.2f}₽. Текущий лимит: {new_total} ГБ")
                except Exception:
                    logging.exception(f"Failed to notify user {user_id} about auto-topup success")
                try:
                    await fragment_sender._notify_admins(f"🔄 Автодокупка {auto_gb}ГБ для @{user_id} (subscription={sub_id}) — списано {cost:.2f}₽")
                except Exception:
                    logging.exception("Failed to notify admins about auto-topup")
                logging.info(f"Auto-topup: user {user_id} subscription {sub_id} +{auto_gb}GB charged {cost}")
                continue
            except RuntimeError as re:
                # expected control flow: insufficient funds or already notified
                if str(re) == 'insufficient_funds':
                    # fallback to manual notify
                    pass
                elif str(re) == 'already_notified':
                    # another worker handled it
                    continue
                else:
                    logging.exception(f"Auto-topup runtime error for user {user_id}, sub {sub_id}: {re}")
            except Exception as e:
                logging.exception(f"Auto-topup transaction failed for user {user_id}, subscription {sub_id}: {e}")

        # fallback: send manual reminder with purchase buttons
        text = (f"⚠️ <b>Низкий остаток трафика</b>\n\n"
                f"У вас осталось <b>{total_gb} ГБ</b> трафика на VPN.\n"
                f"Можно докупить 1 ГБ за {auto_price:.0f}₽ или пакетом.")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Купить 1ГБ — {int(auto_price)}₽", callback_data=f"buy_vpn_gb|{client_uuid}|1"), InlineKeyboardButton(text=f"Купить 5ГБ — {int(auto_price*5)}₽", callback_data=f"buy_vpn_gb|{client_uuid}|5")],
            [InlineKeyboardButton(text="Напомнить позже", callback_data="vpn_menu")]
        ])
        try:
            await bot.send_message(user_id, text, reply_markup=kb)
            await pool.execute("INSERT INTO vpn_expiry_notifications (subscription_id, notify_when) VALUES ($1, $2)", sub_id, low_gb_key)
            logging.info(f"Notified user {user_id} about low GB for subscription {sub_id}")
        except Exception as e:
            logging.exception(f"Failed to notify user {user_id} about low GB for subscription {sub_id}: {e}")

    await bot.session.close()
    await pool.close()

if __name__ == '__main__':
    asyncio.run(run())
