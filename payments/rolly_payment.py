import logging
import uuid
import asyncio
from typing import Optional, Dict, Any
from rollypay import RollyPayClient, RollyPayError

logger = logging.getLogger(__name__)

class RollyPayment:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = RollyPayClient(api_key=self.api_key)
    
    async def create_invoice(self, amount: float, description: str = "Пополнение баланса") -> Optional[Dict[str, Any]]:
        try:
            order_id = str(uuid.uuid4())
            
            logger.info(f"Creating RollyPay invoice with data: amount={amount}, description={description}")
            
            # Using asyncio.to_thread to avoid blocking the event loop with synchronous requests
            payment = await asyncio.to_thread(
                self.client.payments.create,
                amount=f"{float(amount):.2f}",
                order_id=order_id,
                # payment_method="sbp",  # default, can be parameterized if needed
                description=description,
                customer_id="bot_user",  # Replace with actual user ID if needed
                redirect_url="https://t.me/"  # You can replace this with your bot's link
            )
            
            logger.info(f"RollyPay invoice created successfully: {payment.get('payment_id')}")
            
            return {
                "success": True,
                "invoice_id": payment["payment_id"],
                "payment_url": payment["pay_url"],
                "amount": amount
            }
                        
        except RollyPayError as e:
            logger.error(f"RollyPay error creating invoice: {e}")
            return {
                "success": False,
                "error": f"Ошибка платежной системы: {e}"
            }
        except Exception as e:
            logger.error(f"Error creating RollyPay invoice: {e}")
            return {
                "success": False,
                "error": "Ошибка соединения"
            }
    
    async def check_payment_status(self, payment_id: str) -> Dict[str, Any]:
        try:
            payment = await asyncio.to_thread(
                self.client.payments.get,
                payment_id
            )
            
            status = payment.get("status")
            
            # "paid" means successful, anything else is typically pending or failed
            return {
                "success": True,
                "status": "paid" if status == "paid" else "pending",
                "rollypay_status": status
            }
                        
        except RollyPayError as e:
            logger.error(f"RollyPay error checking payment: {e}")
            return {
                "success": False,
                "error": f"Ошибка платежной системы API: {e}"
            }
        except Exception as e:
            logger.error(f"Error checking RollyPay payment: {e}")
            return {
                "success": False,
                "error": "Ошибка соединения"
            }
