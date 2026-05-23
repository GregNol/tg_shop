import httpx
import logging
from typing import Tuple

class ProfitCalculator:
    def __init__(self):
        self.ton_rub_rate = 300.0  
        
    async def get_ton_rub_rate(self) -> float:
        """Получает актуальный курс TON/RUB"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=rub")
                if response.status_code == 200:
                    data = response.json()
                    rate = data.get("the-open-network", {}).get("rub")
                    if rate:
                        self.ton_rub_rate = float(rate)
                        return self.ton_rub_rate
        except Exception as e:
            logging.warning(f"Failed to get TON/RUB rate: {e}")
        
        return self.ton_rub_rate
    
    async def calculate_stars_profit(self, quantity: int, selling_price: float, cost_per_star_ton: float | None = None) -> Tuple[float, float]:

        # Allow caller to provide actual per-star TON cost (dynamic mode), otherwise use default
        if cost_per_star_ton is None:
            cost_per_star_ton = 0.01

        cost_ton = quantity * float(cost_per_star_ton)
        ton_rate = await self.get_ton_rub_rate()
        cost_rub = cost_ton * ton_rate

        profit_rub = selling_price - cost_rub

        # Log calculation details
        try:
            logging.info(
                "Stars profit calc: quantity=%d, cost_per_star_ton=%.8f, ton_rate=%.2f, cost_ton=%.6f, cost_rub=%.2f, selling_price=%.2f, profit_rub=%.2f",
                quantity, float(cost_per_star_ton), ton_rate, cost_ton, cost_rub, selling_price, profit_rub
            )
        except Exception:
            logging.exception("Failed to log profit calculation")

        return cost_ton, profit_rub
    
    async def calculate_premium_profit(self, months: int, selling_price: float) -> Tuple[float, float]:

        premium_costs = {
            3: 9.10,   # 3 месяца ≈ 9.10 TON
            6: 12.14,   # 6 месяцев ≈ 12.14 TON
            12: 22.01  # 12 месяцев ≈ 22.01 TON
        }
        
        cost_ton = premium_costs.get(months, months * 1.43)  
        ton_rate = await self.get_ton_rub_rate()
        cost_rub = cost_ton * ton_rate
        
        profit_rub = selling_price - cost_rub
        
        return cost_ton, profit_rub
    
    def get_profit_margin(self, cost: float, selling_price: float) -> float:
        """Рассчитывает маржу в процентах"""
        if cost <= 0:
            return 0
        return ((selling_price - cost) / cost) * 100
