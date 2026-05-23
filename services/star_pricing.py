from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from config import Config
from services.profit_calculator import ProfitCalculator
from services.repository import Repository


class StarPricingService:
    """Returns the effective star price based on static or dynamic settings."""

    _cached_ton_rate: Optional[float] = None
    _ton_rate_cached_at: float = 0.0
    _ton_rate_cache_ttl_seconds: int = 60
    _cached_star_cost_ton: Optional[float] = None
    _star_cost_cached_at: float = 0.0

    async def get_star_price(self, repo: Repository, config: Optional[Config] = None) -> float:
        mode = (await repo.get_setting("star_price_mode") or "static").strip().lower()

        static_price_raw = await repo.get_setting("star_price")
        static_price = float(static_price_raw) if static_price_raw else 1.8

        if mode != "dynamic":
            return round(static_price, 2)

        # Dynamic mode price: (star TON себестоимость * TON/RUB курс) + markup.
        cost_ton = await self._get_star_cost_ton(repo, config)
        target_profit_per_100_raw = await repo.get_setting("star_target_profit_per_100")
        markup_raw = await repo.get_setting("star_markup_percent")
        min_price_raw = await repo.get_setting("star_min_price")
        max_price_raw = await repo.get_setting("star_max_price")
        rollypay_fee_raw = await repo.get_setting("rollypay_fee")

        target_profit_per_100 = float(target_profit_per_100_raw) if target_profit_per_100_raw else 15.0
        markup_percent = float(markup_raw) if markup_raw else 20.0
        min_price = float(min_price_raw) if min_price_raw else 0.0
        max_price = float(max_price_raw) if max_price_raw else 0.0
        rollypay_fee = float(rollypay_fee_raw) if rollypay_fee_raw else 12.0

        ton_rate = await self._get_ton_rub_rate_cached()
        base_cost_rub_per_star = cost_ton * ton_rate

        if target_profit_per_100 > 0:
            target_profit_per_star = target_profit_per_100 / 100.0
            calculated_price = base_cost_rub_per_star + target_profit_per_star
        else:
            calculated_price = base_cost_rub_per_star * (1 + (markup_percent / 100.0))

        if rollypay_fee > 0:
            fee_multiplier = 1 / (1 - (rollypay_fee / 100.0))
            calculated_price *= fee_multiplier

        if min_price > 0:
            calculated_price = max(calculated_price, min_price)
        if max_price > 0:
            calculated_price = min(calculated_price, max_price)

        # Log details about the computed price
        try:
            logging.info(
                "Star pricing: mode=dynamic, cost_ton=%.6f, ton_rate=%.2f, base_cost_rub_per_star=%.6f, calculated_price=%.4f, min_price=%.2f, max_price=%.2f, target_profit_per_100=%.2f, markup_percent=%.2f, rollypay_fee=%.2f",
                cost_ton, ton_rate, base_cost_rub_per_star, calculated_price, min_price, max_price, target_profit_per_100, markup_percent, rollypay_fee
            )
        except Exception:
            logging.exception("Failed to log star pricing calculation")

        # Safety fallback to avoid invalid numbers from bad settings.
        if calculated_price <= 0:
            logging.info("Star pricing: calculated_price invalid, falling back to static_price=%s", static_price)
            return round(static_price, 2)

        return round(calculated_price, 2)

    async def _get_star_cost_ton(self, repo: Repository, config: Optional[Config]) -> float:
        static_cost_raw = await repo.get_setting("star_cost_ton")
        static_cost = float(static_cost_raw) if static_cost_raw else 0.01

        mode = (await repo.get_setting("star_cost_ton_mode") or "static").strip().lower()
        if mode != "dynamic" or config is None:
            return static_cost

        quote_qty_raw = await repo.get_setting("star_cost_ton_quote_qty")
        quote_qty = int(quote_qty_raw) if quote_qty_raw else 50
        quote_qty = max(50, quote_qty)

        cache_ttl_raw = await repo.get_setting("star_cost_ton_cache_seconds")
        cache_ttl = int(cache_ttl_raw) if cache_ttl_raw else 120

        now = time.time()
        if self._cached_star_cost_ton is not None and (now - self._star_cost_cached_at) < cache_ttl:
            return self._cached_star_cost_ton

        quote_username = (await repo.get_setting("star_cost_ton_quote_username") or "").strip().lstrip("@")
        if not quote_username:
            return static_cost

        quoted_cost = await self._fetch_fragment_star_cost_ton(config, quote_username, quote_qty)
        if quoted_cost and quoted_cost > 0:
            logging.info("Fetched dynamic star cost from Fragment: username=%s, qty=%d, cost_per_star_ton=%.8f", quote_username, quote_qty, quoted_cost)
            self._cached_star_cost_ton = quoted_cost
            self._star_cost_cached_at = now
            return quoted_cost

        return static_cost

    async def _fetch_fragment_star_cost_ton(self, config: Config, username: str, quantity: int) -> Optional[float]:
        url = f"https://fragment.com/api?hash={config.fragment.hash}"
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://fragment.com",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            async with httpx.AsyncClient(cookies=config.fragment.cookies, headers=headers, timeout=20.0) as client:
                step1 = await client.post(
                    url,
                    data={"query": username, "method": "searchStarsRecipient"},
                    headers={**headers, "Referer": "https://fragment.com/stars"},
                )
                step1.raise_for_status()
                data1 = step1.json()
                recipient = data1.get("found", {}).get("recipient")
                if not recipient:
                    return None
                step2 = await client.post(
                    url,
                    data={"recipient": recipient, "quantity": quantity, "method": "initBuyStarsRequest"},
                    headers={**headers, "Referer": f"https://fragment.com/stars/buy?query={username}"},
                )
                step2.raise_for_status()
                data2 = step2.json()
                req_id = data2.get("req_id")
                if not req_id:
                    return None

                step3 = await client.post(
                    url,
                    data={
                        "address": config.fragment.address,
                        "chain": "-239",
                        "walletStateInit": config.fragment.wallets,
                        "publicKey": config.fragment.public_key,
                        "features": ["SendTransaction", {"name": "SendTransaction", "maxMessages": 255}],
                        "maxProtocolVersion": 2,
                        "platform": "iphone",
                        "appName": "Tonkeeper",
                        "appVersion": "5.0.14",
                        "transaction": "1",
                        "id": req_id,
                        "show_sender": "0",
                        "method": "getBuyStarsLink",
                    },
                    headers={**headers, "Referer": f"https://fragment.com/stars/buy?recipient={recipient}&quantity={quantity}"},
                )
                step3.raise_for_status()
                data3 = step3.json()
                tx = data3.get("transaction", {}).get("messages", [{}])[0]
                amount_nano = int(tx.get("amount", 0))
                if amount_nano <= 0:
                    return None

                total_ton = amount_nano / 1_000_000_000
                return total_ton / quantity
        except Exception as exc:
            logging.warning(f"Failed to fetch dynamic star TON cost: {exc}")
            return None

    async def get_star_cost_ton(self, repo: Repository, config: Optional[Config]) -> float:
        """Public wrapper returning per-star cost in TON (fallback to static)."""
        cost = await self._get_star_cost_ton(repo, config)
        return float(cost) if cost else float((await repo.get_setting("star_cost_ton") or 0.01))

    async def _get_ton_rub_rate_cached(self) -> float:
        now = time.time()
        if self._cached_ton_rate is not None and (now - self._ton_rate_cached_at) < self._ton_rate_cache_ttl_seconds:
            return self._cached_ton_rate

        ton_rate = await ProfitCalculator().get_ton_rub_rate()
        if ton_rate > 0:
            self._cached_ton_rate = ton_rate
            self._ton_rate_cached_at = now
        return ton_rate


star_pricing_service = StarPricingService()
