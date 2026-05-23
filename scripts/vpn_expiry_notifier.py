import asyncio
from datetime import datetime, timedelta
import logging
import os

import asyncpg
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import load_config
from services.repository import Repository
from services.xui import xui_from_config
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

async def run():
    config = load_config()
    database_url = config.database_url
    bot_token = config.bot.bot_token

    pool = await asyncpg.create_pool(database_url)
    repo = Repository(pool)
    bot = Bot(token=bot_token)

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

    # Notify about low traffic (<10GB) per client
    low_rows = await pool.fetch("""
        SELECT c.subscription_id, s.user_id, c.client_uuid, c.total_gb, c.panel, c.email, c.inbound_id
        FROM vpn_subscription_clients c
        JOIN vpn_subscriptions s ON s.id = c.subscription_id
        WHERE c.total_gb < 10 AND c.is_active = 1 AND (s.expires_at IS NULL OR s.expires_at > $1)
    """, now)

    # load auto-topup settings
    auto_enabled = (await repo.get_setting('vpn_auto_topup_enabled') or '0').strip() == '1'
    auto_gb = int((await repo.get_setting('vpn_auto_topup_gb') or '1'))
    auto_price = float((await repo.get_setting('vpn_auto_topup_price_per_gb') or '3'))
    fragment_sender = FragmentSender(config, bot)

    for r in low_rows:
        sub_id = r['subscription_id']
        user_id = r['user_id']
        client_uuid = r['client_uuid']
        total_gb = r['total_gb']
        exists = await pool.fetchval("SELECT 1 FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, 'low_gb')
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
                        exists = await conn.fetchval("SELECT 1 FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, 'low_gb')
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
                        await conn.execute("INSERT INTO vpn_expiry_notifications (subscription_id, notify_when) VALUES ($1, $2)", sub_id, 'low_gb')

                        # capture values for external update after commit
                        inbound_id = client_row['inbound_id'] if client_row else None
                        email = client_row['email'] if client_row else None
                        panel = client_row['panel'] if client_row else 'primary'
                        prev_total = client_row['total_gb'] if client_row and client_row['total_gb'] is not None else 0

                # transaction committed successfully; now update external panel and notify
                new_total = (prev_total or 0) + auto_gb
                try:
                    xui = xui_from_config(config, secondary=(panel == 'secondary'))
                    await xui.login()
                    await xui.update_client(inbound_id=inbound_id, client_uuid=client_uuid, email=email, enable=True, total_gb=new_total)
                    await xui.close()
                except Exception as e:
                    logging.exception(f"XUI update failed after auto-topup for {client_uuid}: {e}")
                    # attempt to rollback DB changes: refund user, decrement GB, remove notification
                    try:
                        async with pool.acquire() as conn:
                            async with conn.transaction():
                                await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2", cost, user_id)
                                await conn.execute("UPDATE vpn_subscription_clients SET total_gb = GREATEST(total_gb - $1, 0) WHERE client_uuid = $2", auto_gb, client_uuid)
                                await conn.execute("DELETE FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", sub_id, 'low_gb')
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
            await pool.execute("INSERT INTO vpn_expiry_notifications (subscription_id, notify_when) VALUES ($1, $2)", sub_id, 'low_gb')
            logging.info(f"Notified user {user_id} about low GB for subscription {sub_id}")
        except Exception as e:
            logging.exception(f"Failed to notify user {user_id} about low GB for subscription {sub_id}: {e}")

    await bot.session.close()
    await pool.close()

if __name__ == '__main__':
    asyncio.run(run())
