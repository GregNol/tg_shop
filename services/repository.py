import asyncpg
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

class Repository:
    def __init__(self, db: asyncpg.Pool):
        self.db = db
        self._vpn_subscriptions_has_legacy_client_columns: Optional[bool] = None

    async def _vpn_subscriptions_uses_legacy_columns(self) -> bool:
        """Check whether `vpn_subscriptions` still has legacy client fields.

        Some live databases were created with an older schema where
        `client_uuid/email/inbound_id` live in `vpn_subscriptions` and can be NOT NULL.
        """
        if self._vpn_subscriptions_has_legacy_client_columns is not None:
            return self._vpn_subscriptions_has_legacy_client_columns

        exists = await self.db.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'vpn_subscriptions'
                  AND column_name = 'client_uuid'
            )
            """
        )
        self._vpn_subscriptions_has_legacy_client_columns = bool(exists)
        return self._vpn_subscriptions_has_legacy_client_columns

    # --- User Methods ---
    async def get_or_create_user(self, telegram_id: int, username: str, first_name: str = None, last_name: str = None, referrer_id: int = None) -> asyncpg.Record:
        user = await self.get_user(telegram_id)
        if not user:
            await self.db.execute(
                "INSERT INTO users (telegram_id, username, first_name, last_name, referrer_id) VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                telegram_id, username, first_name, last_name, referrer_id
            )
            user = await self.get_user(telegram_id)
        return user

    async def get_user_by_id_or_username(self, user_input: str) -> Optional[asyncpg.Record]:
        if user_input.isdigit():
            return await self.db.fetchrow("SELECT * FROM users WHERE telegram_id = $1", int(user_input))
        else:
            return await self.db.fetchrow("SELECT * FROM users WHERE username = $1", user_input)
    
    async def get_user(self, user_id: int) -> Optional[asyncpg.Record]:
        return await self.db.fetchrow("SELECT * FROM users WHERE telegram_id = $1", user_id)

    async def get_total_top_up(self, user_id: int) -> float:
        row = await self.db.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) as total FROM payments WHERE user_id = $1 AND status = 'paid'",
            user_id
        )
        return float(row['total']) if row and row['total'] else 0.0

    async def count_user_payments(self, user_id: int) -> int:
        row = await self.db.fetchrow("SELECT COUNT(*) as count FROM payments WHERE user_id = $1", user_id)
        return row['count'] if row else 0

    async def get_user_payments_page(self, user_id: int, page: int, page_size: int) -> list:
        offset = (page - 1) * page_size
        return await self.db.fetch(
            "SELECT * FROM payments WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            user_id, page_size, offset
        )

    async def get_referral_stats(self, user_id: int) -> tuple[int, float]:
        """
        Возвращает количество рефералов (сколько человек пригласил) 
        и общую сумму заработанных на них денег.
        """
        ref_count_row = await self.db.fetchrow("SELECT COUNT(id) as ref_count FROM users WHERE referrer_id = $1", user_id)
        ref_count = ref_count_row['ref_count'] if ref_count_row else 0
        
        earned_row = await self.db.fetchrow("SELECT referral_earned FROM users WHERE telegram_id = $1", user_id)
        earned = earned_row['referral_earned'] if earned_row and earned_row['referral_earned'] else 0.0
        
        return ref_count, earned

    async def update_user_block_status(self, user_id: int, is_blocked: bool):
        await self.db.execute("UPDATE users SET is_blocked = $1 WHERE telegram_id = $2", int(is_blocked), user_id)

    async def update_user_balance(self, user_id: int, amount: float, operation: str = 'add'):
        op_char = '+' if operation == 'add' else '-'
        await self.db.execute(f"UPDATE users SET balance = balance {op_char} $1 WHERE telegram_id = $2", amount, user_id)

    async def update_user_discount(self, user_id: int, discount: Optional[float]):
        await self.db.execute("UPDATE users SET discount = $1 WHERE telegram_id = $2", discount, user_id)
        
    async def get_all_users_for_broadcast(self) -> List[asyncpg.Record]:
        return await self.db.fetch("SELECT telegram_id FROM users WHERE is_blocked = 0")
        
    async def is_user_blocked(self, user_id: int) -> bool:
        row = await self.db.fetchrow("SELECT is_blocked FROM users WHERE telegram_id = $1", user_id)
        return row and row['is_blocked'] == 1

    # --- Purchase History & Stars Methods ---
    async def get_total_stars_bought(self, user_id: int) -> int:
        res = await self.db.fetchval("SELECT COALESCE(SUM(amount), 0) FROM purchase_history WHERE user_id = $1 AND purchase_type = 'stars'", user_id)
        return int(res) if res else 0

    async def add_purchase_to_history(self, user_id: int, p_type: str, desc: str, amount: int, cost: float, profit: float = 0):
        await self.db.execute(
            "INSERT INTO purchase_history (user_id, purchase_type, item_description, amount, cost, profit) VALUES ($1, $2, $3, $4, $5, $6)",
            user_id, p_type, desc, amount, cost, profit
        )

    # --- Payment Methods ---
    async def create_payment(self, user_id: int, payment_method: str, amount: float, fee_amount: float, total_amount: float, invoice_id: str, expires_at: datetime, crypto_asset: str = None, message_id: int = None, chat_id: int = None, payload_id: str = None):
        await self.db.execute(
            "INSERT INTO payments (user_id, payment_method, amount, fee_amount, total_amount, invoice_id, payload_id, crypto_asset, expires_at, message_id, chat_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            user_id, payment_method, amount, fee_amount, total_amount, invoice_id, payload_id, crypto_asset, expires_at, message_id, chat_id
        )

    async def get_pending_payments(self) -> List[Dict]:
        rows = await self.db.fetch("SELECT * FROM payments WHERE status = 'pending' AND expires_at > CURRENT_TIMESTAMP")
        return [dict(row) for row in rows]

    async def update_payment_status(self, invoice_id: str, status: str) -> bool:
        tag = await self.db.execute("UPDATE payments SET status = $1 WHERE invoice_id = $2 AND status != $1", status, invoice_id)
        return tag != "UPDATE 0"

    async def get_user_active_payment(self, user_id: int) -> Optional[Dict]:
        row = await self.db.fetchrow("SELECT * FROM payments WHERE user_id = $1 AND status = 'pending' AND expires_at > CURRENT_TIMESTAMP ORDER BY created_at DESC LIMIT 1", user_id)
        return dict(row) if row else None
        
    async def get_payment_by_invoice_id(self, invoice_id: str) -> Optional[Dict]:
        row = await self.db.fetchrow("SELECT * FROM payments WHERE invoice_id = $1", invoice_id)
        return dict(row) if row else None
        
    async def process_successful_payment(self, invoice_id: str) -> Optional[Dict[str, Any]]:
        async with self.db.transaction():
            payment = await self.db.fetchrow("SELECT * FROM payments WHERE invoice_id = $1 AND status = 'pending'", invoice_id)
            if not payment:
                return None
            await self.db.execute("UPDATE payments SET status = 'paid' WHERE invoice_id = $1", invoice_id)
            await self.db.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2", float(payment["amount"]), payment["user_id"])
        return dict(payment)

    async def process_referral_reward(self, user_id: int, amount: float, percentage: float) -> Optional[tuple[int, float]]:
        user = await self.get_user(user_id)
        if not user or not user['referrer_id']:
            return None
            
        referrer_id = user['referrer_id']
        reward = round(amount * (percentage / 100), 2)
        if reward > 0:
            await self.db.execute(
                "UPDATE users SET balance = balance + $1, referral_earned = referral_earned + $1 WHERE telegram_id = $2",
                reward, referrer_id
            )
            return referrer_id, reward
        return None

    # --- Promo Methods ---
    async def get_promo_by_code(self, code: str) -> Optional[asyncpg.Record]:
        return await self.db.fetchrow("SELECT * FROM promo_codes WHERE code = $1 AND is_active = 1", code)

    async def check_promo_usage_by_user(self, user_id: int, promo_id: int) -> bool:
        res = await self.db.fetchval("SELECT 1 FROM promo_history WHERE user_id = $1 AND promo_code_id = $2", user_id, promo_id)
        return res is not None

    async def activate_promo_for_user(self, user_id: int, promo: asyncpg.Record):
        await self.db.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = $1", promo['id'])
        await self.db.execute("INSERT INTO promo_history (user_id, promo_code_id) VALUES ($1, $2)", user_id, promo['id'])
        if promo['promo_type'] == 'discount':
            await self.update_user_discount(user_id, float(promo['value']))
        else:
            await self.update_user_balance(user_id, float(promo['value']), 'add')

    # --- Settings Methods ---
    async def get_setting(self, key: str) -> Optional[str]:
        return await self.db.fetchval("SELECT value FROM settings WHERE key = $1", key)

    async def get_multiple_settings(self, keys: List[str]) -> Dict[str, str]:
        if not keys:
            return {}
        rows = await self.db.fetch("SELECT key, value FROM settings WHERE key = ANY($1::text[])", keys)
        return {r['key']: r['value'] for r in rows}

    async def update_setting(self, key: str, value: Any):
        await self.db.execute("INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", str(key), str(value))

    # --- Stats Methods ---
    async def get_bot_statistics(self) -> Dict[str, int]:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        month_ago = datetime.utcnow() - timedelta(days=30)
        queries = {
            "total_users": "SELECT COUNT(id) FROM users",
            "month_users": "SELECT COUNT(id) FROM users WHERE created_at >= $1",
            "day_stars": "SELECT COALESCE(SUM(amount), 0) FROM purchase_history WHERE purchase_type = 'stars' AND created_at >= $1",
            "month_stars": "SELECT COALESCE(SUM(amount), 0) FROM purchase_history WHERE purchase_type = 'stars' AND created_at >= $1",
            "total_stars": "SELECT COALESCE(SUM(amount), 0) FROM purchase_history WHERE purchase_type = 'stars'"
        }
        results = {}
        for key, query in queries.items():
            if 'month_users' in key or 'month_stars' in key:
                param = month_ago
                val = await self.db.fetchval(query, param)
            elif 'day_stars' in key:
                param = today_start
                val = await self.db.fetchval(query, param)
            else:
                val = await self.db.fetchval(query)
            results[key] = int(val) if val else 0
        return results

    async def get_profit_statistics(self) -> Dict[str, float]:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        month_ago = datetime.utcnow() - timedelta(days=30)
        queries = {
            "day_profit": "SELECT COALESCE(SUM(profit), 0) FROM purchase_history WHERE created_at >= $1",
            "month_profit": "SELECT COALESCE(SUM(profit), 0) FROM purchase_history WHERE created_at >= $1",
            "total_profit": "SELECT COALESCE(SUM(profit), 0) FROM purchase_history",
            "day_revenue": "SELECT COALESCE(SUM(cost), 0) FROM purchase_history WHERE created_at >= $1",
            "month_revenue": "SELECT COALESCE(SUM(cost), 0) FROM purchase_history WHERE created_at >= $1",
            "total_revenue": "SELECT COALESCE(SUM(cost), 0) FROM purchase_history"
        }
        results = {}
        for key, query in queries.items():
            if 'month_' in key:
                param = month_ago
                val = await self.db.fetchval(query, param)
            elif 'day_' in key:
                param = today_start
                val = await self.db.fetchval(query, param)
            else:
                val = await self.db.fetchval(query)
            results[key] = float(val) if val else 0.0
        return results

    async def get_payments_stats(self, days: int = None) -> dict:
        base_query = "SELECT COUNT(*) as total_payments, COALESCE(SUM(amount), 0) as total_revenue, payment_method, status FROM payments "
        
        if days:
            date_filter = f"WHERE created_at >= NOW() - INTERVAL '{days} days'"
            query = base_query + date_filter + " GROUP BY payment_method, status"
        else:
            query = base_query + " GROUP BY payment_method, status"
        
        rows = await self.db.fetch(query)
        
        stats = {'total_payments': 0, 'total_revenue': 0.0, 'paid_payments': 0, 'paid_revenue': 0.0, 'methods': {}}
        
        for row in rows:
            method, status, payments, revenue = row['payment_method'], row['status'], row['total_payments'], row['total_revenue']
            if method not in stats['methods']:
                stats['methods'][method] = {'total_payments': 0, 'total_revenue': 0.0, 'paid_payments': 0, 'paid_revenue': 0.0}
            
            stats['methods'][method]['total_payments'] += payments
            stats['methods'][method]['total_revenue'] += revenue
            stats['total_payments'] += payments
            stats['total_revenue'] += revenue
            
            if status == 'paid':
                stats['methods'][method]['paid_payments'] += payments
                stats['methods'][method]['paid_revenue'] += revenue
                stats['paid_payments'] += payments
                stats['paid_revenue'] += revenue
        return stats

    # --- VPN Subscriptions Methods ---
    async def create_vpn_subscription(self, user_id: int, client_uuid: str, email: str, inbound_id: int, target_tariff_name: str, total_gb: int, expires_at: Optional[datetime] = None) -> asyncpg.Record:
        """Создать запись о новой VPN подписке пользователя."""
        try:
            async with self.db.transaction():
                # create subscription with robust fallback for mixed/legacy deployments
                sub = None
                use_legacy_insert = await self._vpn_subscriptions_uses_legacy_columns()

                if not use_legacy_insert:
                    try:
                        sub = await self.db.fetchrow(
                            """
                            INSERT INTO vpn_subscriptions (user_id, tariff_name, total_gb, expires_at)
                            VALUES ($1, $2, $3, $4)
                            RETURNING *
                            """,
                            user_id, target_tariff_name, total_gb, expires_at
                        )
                    except asyncpg.NotNullViolationError as e:
                        # Some live DBs still require legacy columns even if detection misses them.
                        if 'client_uuid' in str(e) or 'email' in str(e) or 'inbound_id' in str(e):
                            logging.warning(
                                "create_vpn_subscription switched to legacy insert due to not-null violation: "
                                "user_id=%s inbound_id=%s client_uuid=%s tariff=%s error=%s",
                                user_id,
                                inbound_id,
                                client_uuid,
                                target_tariff_name,
                                e,
                            )
                            use_legacy_insert = True
                            self._vpn_subscriptions_has_legacy_client_columns = True
                        else:
                            raise

                if sub is None and use_legacy_insert:
                    sub = await self.db.fetchrow(
                        """
                        INSERT INTO vpn_subscriptions (user_id, client_uuid, email, inbound_id, tariff_name, total_gb, expires_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (email)
                        DO UPDATE SET
                            user_id = EXCLUDED.user_id,
                            client_uuid = EXCLUDED.client_uuid,
                            inbound_id = EXCLUDED.inbound_id,
                            tariff_name = EXCLUDED.tariff_name,
                            total_gb = EXCLUDED.total_gb,
                            expires_at = EXCLUDED.expires_at,
                            is_active = 1
                        RETURNING *
                        """,
                        user_id, client_uuid, email, inbound_id, target_tariff_name, total_gb, expires_at
                    )

                # create client entry linked to subscription
                client = await self.db.fetchrow(
                    """
                    INSERT INTO vpn_subscription_clients (subscription_id, client_uuid, email, inbound_id, panel, total_gb)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (client_uuid)
                    DO UPDATE SET
                        subscription_id = EXCLUDED.subscription_id,
                        email = EXCLUDED.email,
                        inbound_id = EXCLUDED.inbound_id,
                        panel = EXCLUDED.panel,
                        total_gb = EXCLUDED.total_gb,
                        is_active = 1
                    RETURNING *
                    """,
                    sub['id'], client_uuid, email, inbound_id, 'primary', total_gb
                )
                logging.info(f"Created vpn subscription {sub['id']} and primary client {client_uuid} for user {user_id}")
                # return a combined dict similar to legacy vpn_subscriptions row
                return {
                    'subscription_id': sub['id'],
                    'client_uuid': client['client_uuid'],
                    'email': client['email'],
                    'inbound_id': client['inbound_id'],
                    'tariff_name': sub['tariff_name'],
                    'total_gb': client['total_gb'],
                    'expires_at': sub['expires_at'],
                    'is_active': client['is_active']
                }
        except Exception:
            logging.exception(
                "Failed to create VPN subscription: user_id=%s client_uuid=%s email=%s inbound_id=%s tariff=%s total_gb=%s expires_at=%s",
                user_id,
                client_uuid,
                email,
                inbound_id,
                target_tariff_name,
                total_gb,
                expires_at,
            )
            raise

    async def get_vpn_subscription(self, client_uuid: str) -> Optional[asyncpg.Record]:
        """Получить информацию о конкретной подписке по UUID (client-level)."""
        row = await self.db.fetchrow(
            """
            SELECT s.id as subscription_id, s.user_id, s.tariff_name, s.expires_at, c.client_uuid, c.email, c.inbound_id, c.panel, c.total_gb, c.is_active
            FROM vpn_subscription_clients c
            JOIN vpn_subscriptions s ON s.id = c.subscription_id
            WHERE c.client_uuid = $1
            """,
            client_uuid
        )
        return row

    async def get_user_vpn_subscriptions(self, user_id: int) -> List[asyncpg.Record]:
        """Получить все подписки конкретного пользователя (каждый клиент отдельной строкой)."""
        rows = await self.db.fetch(
            """
            SELECT s.id as subscription_id, s.user_id, s.tariff_name, s.expires_at, c.client_uuid, c.email, c.inbound_id, c.panel, c.total_gb, c.is_active, c.created_at
            FROM vpn_subscriptions s
            JOIN vpn_subscription_clients c ON c.subscription_id = s.id
            WHERE s.user_id = $1
            ORDER BY c.created_at DESC
            """,
            user_id
        )
        return rows

    async def update_vpn_subscription_status(self, client_uuid: str, is_active: bool):
        """Обновить статус активности клиентской записи подписки."""
        status = 1 if is_active else 0
        await self.db.execute("UPDATE vpn_subscription_clients SET is_active = $1 WHERE client_uuid = $2", status, client_uuid)

    async def extend_vpn_subscription(self, client_uuid: str, new_expires_at: Optional[datetime] = None, added_gb: int = 0):
        """Продлить подписку: обновить expires_at у subscription и добавить трафик конкретному клиенту."""
        async with self.db.transaction():
            # find client and subscription
            row = await self.db.fetchrow("SELECT subscription_id FROM vpn_subscription_clients WHERE client_uuid = $1", client_uuid)
            if not row:
                return
            sub_id = row['subscription_id']
            await self.db.execute("UPDATE vpn_subscriptions SET expires_at = COALESCE($1, expires_at) WHERE id = $2", new_expires_at, sub_id)
            if added_gb:
                await self.db.execute("UPDATE vpn_subscription_clients SET total_gb = total_gb + $1 WHERE client_uuid = $2", added_gb, client_uuid)
            await self.db.execute("UPDATE vpn_subscription_clients SET is_active = 1 WHERE client_uuid = $1", client_uuid)

    async def change_vpn_subscription_tariff(self, client_uuid: str, new_tariff_name: str, new_total_gb: Optional[int] = None, new_expires_at: Optional[datetime] = None):
        """Сменить тариф подписки: обновить subscription.tariff_name и при необходимости client.total_gb/expires_at."""
        async with self.db.transaction():
            row = await self.db.fetchrow("SELECT subscription_id FROM vpn_subscription_clients WHERE client_uuid = $1", client_uuid)
            if not row:
                return
            sub_id = row['subscription_id']
            await self.db.execute("UPDATE vpn_subscriptions SET tariff_name = $1 WHERE id = $2", new_tariff_name, sub_id)
            if new_total_gb is not None:
                await self.db.execute("UPDATE vpn_subscription_clients SET total_gb = $1 WHERE client_uuid = $2", new_total_gb, client_uuid)
            if new_expires_at is not None:
                await self.db.execute("UPDATE vpn_subscriptions SET expires_at = $1 WHERE id = $2", new_expires_at, sub_id)

    async def delete_vpn_subscription(self, client_uuid: str):
        """Удалить подписку целиком по UUID клиента."""
        async with self.db.transaction():
            row = await self.db.fetchrow(
                "SELECT subscription_id FROM vpn_subscription_clients WHERE client_uuid = $1",
                client_uuid,
            )
            if not row:
                return
            sub_id = row['subscription_id']
            await self.db.execute("DELETE FROM vpn_subscriptions WHERE id = $1", sub_id)

    async def create_vpn_subscription_client(self, subscription_id: int, client_uuid: str, email: str, inbound_id: int, panel: str = 'secondary', total_gb: int = 0) -> asyncpg.Record:
        """Добавить client запись к существующей подписке."""
        return await self.db.fetchrow(
            """
            INSERT INTO vpn_subscription_clients (subscription_id, client_uuid, email, inbound_id, panel, total_gb)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            subscription_id, client_uuid, email, inbound_id, panel, total_gb
        )


    async def get_subscription_clients(self, subscription_id: int):
        return await self.db.fetch("SELECT * FROM vpn_subscription_clients WHERE subscription_id = $1 ORDER BY created_at DESC", subscription_id)
