import asyncpg
import logging
from datetime import datetime

async def init_db(database_url: str, support_contact: str = ''):
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                balance REAL DEFAULT 0.0,
                is_admin INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                discount REAL,
                referrer_id BIGINT,
                referral_earned REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                payment_method TEXT,
                amount REAL NOT NULL,
                fee_amount REAL,
                total_amount REAL,
                invoice_id TEXT UNIQUE NOT NULL,
                payload_id TEXT,
                crypto_asset TEXT,
                status TEXT DEFAULT 'pending',
                message_id BIGINT,
                chat_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (telegram_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS purchase_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                purchase_type TEXT NOT NULL,
                item_description TEXT NOT NULL,
                amount INTEGER,
                cost REAL NOT NULL,
                profit REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                promo_type TEXT NOT NULL,
                value REAL NOT NULL,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                expires_at TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                promo_code_id INTEGER NOT NULL,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id) ON DELETE CASCADE
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vpn_subscriptions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                tariff_name TEXT,
                total_gb INTEGER DEFAULT 0,
                expires_at TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vpn_subscription_clients (
                id SERIAL PRIMARY KEY,
                subscription_id INTEGER NOT NULL,
                client_uuid TEXT UNIQUE NOT NULL,
                email TEXT NOT NULL,
                inbound_id INTEGER NOT NULL,
                panel TEXT DEFAULT 'primary', -- 'primary' or 'secondary'
                total_gb INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subscription_id) REFERENCES vpn_subscriptions(id) ON DELETE CASCADE
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vpn_expiry_notifications (
                id SERIAL PRIMARY KEY,
                subscription_id INTEGER NOT NULL,
                notify_when TEXT NOT NULL, -- e.g. '3d','2d','1d','12h'
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (subscription_id, notify_when),
                FOREIGN KEY (subscription_id) REFERENCES vpn_subscriptions(id) ON DELETE CASCADE
            )
        """)
        
        default_settings = {
            'star_price': '1.8',
            'star_price_mode': 'static',
            'star_cost_ton': '0.01',
            'star_cost_ton_mode': 'static',
            'star_cost_ton_quote_username': '',
            'star_cost_ton_quote_qty': '50',
            'star_cost_ton_cache_seconds': '120',
            'star_target_profit_per_100': '15',
            'star_markup_percent': '20',
            'star_min_price': '0',
            'star_max_price': '0',
            'premium_price_0': '799',
            'premium_price_1': '1499',
            'premium_price_2': '2499',
            'maintenance_mode': '0',
            'start_text': '<b>🖐 Добро пожаловать</b>\n\n🚀 У нас моментальная доставка 24/7\n📱 Без KYC и верификаций\n💰 Оплата любым способом',
            'vpn_standard_price': '100',
            'vpn_premium_price': '400',
            'vpn_auto_topup_enabled': '1',
            'vpn_auto_topup_gb': '1',
            'vpn_auto_topup_price_per_gb': '3',
            'purchase_success_text': 'Спасибо за покупку ✅\nЗвёзды придут в течении 5 минут ⭐️',
            'news_channel_id': '',
            'news_channel_link': '',
            'force_subscribe': '0',
            'support_contact': support_contact,
            'fragment_token': '',
            'fragment_token_expires_at': '',
            'fragment_token_last_update': '',
            'lolz_fee': '7.0',
            'cryptobot_fee': '5.0',
            'xrocet_fee': '3.0',
            'rollypay_fee': '12.0',
            'crystalpay_fee': '4.0'
        }
        
        for key, value in default_settings.items():
            await conn.execute("""
                INSERT INTO settings (key, value) 
                VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING
            """, key, value)

        logging.info("База данных инициализирована с новой схемой.")
    finally:
        await conn.close()

async def get_db_connection(database_url: str):
    return await asyncpg.create_pool(database_url)