import aiohttp
import logging
import uuid
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class YookassaPayment:
    def __init__(self, shop_id: str, secret_key: str):
        self.shop_id = str(shop_id)
        self.secret_key = secret_key
        self.base_url = "https://api.yookassa.ru/v3"
        self.auth = aiohttp.BasicAuth(login=self.shop_id, password=self.secret_key)
    
    async def create_invoice(self, amount: float, description: str = "Пополнение баланса") -> Optional[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                idempotency_key = str(uuid.uuid4())
                
                headers = {
                    "Idempotence-Key": idempotency_key,
                    "Content-Type": "application/json"
                }
                
                data = {
                    "amount": {
                        "value": f"{float(amount):.2f}",
                        "currency": "RUB"
                    },
                    "capture": True,
                    "confirmation": {
                        "type": "redirect",
                        "return_url": "https://t.me/" # Можно заменить на ссылку вашего бота
                    },
                    "description": description
                }
                
                logger.info(f"Creating YooKassa invoice with data: amount={amount}, description={description}")
                
                async with session.post(
                    f"{self.base_url}/payments",
                    auth=self.auth,
                    headers=headers,
                    json=data
                ) as response:
                    response_text = await response.text()
                    logger.info(f"YooKassa API response status: {response.status}")
                    
                    if response.status == 200:
                        result = await response.json()
                        if "id" in result:
                            logger.info(f"YooKassa invoice created successfully: {result['id']}")
                            return {
                                "success": True,
                                "invoice_id": result["id"],
                                "payment_url": result["confirmation"]["confirmation_url"],
                                "amount": amount
                            }
                        else:
                            logger.error(f"YooKassa API returned unexpected format: {result}")
                            return {
                                "success": False,
                                "error": "Неожиданный ответ от API ЮKassa"
                            }
                    else:
                        logger.error(f"YooKassa invoice creation failed with status {response.status}: {response_text}")
                        return {
                            "success": False,
                            "error": f"Ошибка API: {response.status}"
                        }
                        
        except Exception as e:
            logger.error(f"Error creating YooKassa invoice: {e}")
            return {
                "success": False,
                "error": "Ошибка соединения"
            }
    
    async def check_payment_status(self, payment_id: str) -> Dict[str, Any]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/payments/{payment_id}",
                    auth=self.auth
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        status = result.get("status")
                        
                        return {
                            "success": True,
                            "status": "paid" if status == "succeeded" else "pending",
                            "yookassa_status": status
                        }
                    else:
                        logger.error(f"Failed to check YooKassa payment: {response.status}")
                        return {
                            "success": False,
                            "error": f"API error: {response.status}"
                        }
                        
        except Exception as e:
            logger.error(f"Error checking YooKassa payment: {e}")
            return {
                "success": False,
                "error": "Ошибка соединения"
            }
