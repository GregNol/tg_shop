import asyncpg
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

class Repository:
    def __init__(self, db: asyncpg.Pool):
        self.db = db

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
