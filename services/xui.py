import httpx
import uuid
import json
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class XUIServer:
    def __init__(self, host: str, port: int, username: str, password: str, https: bool = True, web_base_path: str = ""):
        protocol = "https" if https else "http"
        self.host_url = f"{protocol}://{host}:2096"
        base = f"{protocol}://{host}:{port}"
        # добавляем web_base_path если он есть, убедившись, что слеши стоят правильно
        if web_base_path:
            web_base_path = web_base_path.strip("/")
            base = f"{base}/{web_base_path}"
        self.base_url = base
        self.username = username
        self.password = password
        self.session = httpx.AsyncClient(verify=False)

    async def close(self):
        """Закрыть сессию."""
        await self.session.aclose()

    async def login(self) -> bool:
        """Авторизация в панели 3x-ui."""
        url = f"{self.base_url}/login"
        data = {"username": self.username, "password": self.password}
        try:
            response = await self.session.post(url, data=data)
            payload = response.json()
            if response.status_code == 200 and payload.get("success"):
                logger.info("XUI login succeeded: url=%s status=%s", url, response.status_code)
                return True
            logger.error(
                "XUI login failed: url=%s status=%s success=%s response=%s",
                url,
                response.status_code,
                payload.get("success"),
                response.text[:500],
            )
        except Exception as e:
            logger.exception("XUI login exception: url=%s error=%s", url, e)
        return False

    async def get_inbounds(self) -> Optional[List[Dict[str, Any]]]:
        """Получить список всех inbound соединений."""
        url = f"{self.base_url}/panel/api/inbounds/list"
        response = await self.session.get(url)
        if response.status_code == 200:
            res_data = response.json()
            if res_data.get("success"):
                return res_data.get("obj")
        return None

    async def add_client(
        self, 
        inbound_id: int, 
        email: str, 
        enable: bool = True, 
        vless: bool = True, 
        limit_ip: int = 0, 
        total_gb: int = 0, 
        expire_time: int = 0,
        flow: str = "xtls-rprx-vision"
    ) -> Optional[str]:
        """Добавить клиента в существующий inbound.
        Возвращает UUID созданного клиента или None в случае ошибки."""
        url = f"{self.base_url}/panel/api/inbounds/addClient"
        
        client_id = str(uuid.uuid4())
        settings = {
            "clients": [
                {
                    "id": client_id,
                    "flow": flow if vless else "",
                    "email": email,
                    "limitIp": limit_ip,
                    "totalGB": total_gb,
                    "expiryTime": expire_time,
                    "enable": enable,
                    "tgId": "",
                    "subId": client_id
                }
            ]
        }
        
        data = {
            "id": inbound_id,
            "settings": json.dumps(settings)
        }

        try:
            response = await self.session.post(url, json=data)
            payload = response.json()
            if response.status_code == 200 and payload.get("success", False):
                logger.info(
                    "XUI add_client succeeded: inbound_id=%s email=%s client_id=%s status=%s",
                    inbound_id,
                    email,
                    client_id,
                    response.status_code,
                )
                return client_id

            logger.error(
                "XUI add_client failed: inbound_id=%s email=%s client_id=%s status=%s success=%s response=%s",
                inbound_id,
                email,
                client_id,
                response.status_code,
                payload.get("success"),
                response.text[:500],
            )
        except Exception:
            logger.exception(
                "XUI add_client exception: inbound_id=%s email=%s client_id=%s",
                inbound_id,
                email,
                client_id,
            )
        return None

    async def get_client_uuid_by_email(self, inbound_id: int, email: str) -> Optional[str]:
        """Find existing client UUID by email inside a specific inbound."""
        try:
            inbounds = await self.get_inbounds()
            if not inbounds:
                logger.warning("XUI get_client_uuid_by_email: inbounds list is empty for inbound_id=%s email=%s", inbound_id, email)
                return None

            target = None
            for inbound in inbounds:
                if int(inbound.get("id", 0)) == int(inbound_id):
                    target = inbound
                    break

            if not target:
                logger.warning("XUI get_client_uuid_by_email: inbound not found inbound_id=%s email=%s", inbound_id, email)
                return None

            settings_raw = target.get("settings")
            settings = settings_raw if isinstance(settings_raw, dict) else json.loads(settings_raw or "{}")
            clients = settings.get("clients") or []

            for client in clients:
                if client.get("email") == email:
                    existing_id = client.get("id")
                    if existing_id:
                        logger.info(
                            "XUI found existing client by email: inbound_id=%s email=%s client_id=%s",
                            inbound_id,
                            email,
                            existing_id,
                        )
                        return existing_id

            return None
        except Exception:
            logger.exception("XUI get_client_uuid_by_email exception: inbound_id=%s email=%s", inbound_id, email)
            return None

    async def add_or_update_client(
        self,
        inbound_id: int,
        email: str,
        enable: bool = True,
        vless: bool = True,
        limit_ip: int = 0,
        total_gb: int = 0,
        expire_time: int = 0,
        flow: str = "xtls-rprx-vision"
    ) -> Optional[str]:
        """Create client or update existing one (if email already exists).

        Returns the resulting client UUID or None.
        """
        created_id = await self.add_client(
            inbound_id=inbound_id,
            email=email,
            enable=enable,
            vless=vless,
            limit_ip=limit_ip,
            total_gb=total_gb,
            expire_time=expire_time,
            flow=flow,
        )
        if created_id:
            return created_id

        existing_id = await self.get_client_uuid_by_email(inbound_id=inbound_id, email=email)
        if not existing_id:
            logger.error(
                "XUI add_or_update_client failed: unable to create and existing client not found inbound_id=%s email=%s",
                inbound_id,
                email,
            )
            return None

        updated = await self.update_client(
            inbound_id=inbound_id,
            client_uuid=existing_id,
            email=email,
            enable=enable,
            vless=vless,
            limit_ip=limit_ip,
            total_gb=total_gb,
            expire_time=expire_time,
            flow=flow,
        )
        if not updated:
            logger.error(
                "XUI add_or_update_client failed on update: inbound_id=%s email=%s client_id=%s",
                inbound_id,
                email,
                existing_id,
            )
            return None

        logger.info(
            "XUI add_or_update_client updated existing client: inbound_id=%s email=%s client_id=%s",
            inbound_id,
            email,
            existing_id,
        )
        return existing_id

    async def delete_client(self, inbound_id: int, email: str) -> bool:
        """Удалить клиента по email."""
        url = f"{self.base_url}/panel/api/inbounds/{inbound_id}/delClient/{email}"
        response = await self.session.post(url)
        if response.status_code == 200:
            return response.json().get("success", False)
        return False

    async def get_client_traffic(self, email: str) -> Optional[Dict[str, Any]]:
        """Получить статистику трафика клиента."""
        url = f"{self.base_url}/panel/api/inbounds/getClientTraffics/{email}"
        response = await self.session.get(url)
        if response.status_code == 200:
            res = response.json()
            if res.get("success"):
                # Возвращает объект или массив (зависит от версии панели)
                return res.get("obj")
        return None

    async def update_client(
        self, 
        inbound_id: int, 
        client_uuid: str,
        email: str, 
        enable: bool = True, 
        vless: bool = True,
        limit_ip: int = 0, 
        total_gb: int = 0, 
        expire_time: int = 0,
        flow: str = "xtls-rprx-vision"
    ) -> bool:
        """Обновить настройки клиента."""
        url = f"{self.base_url}/panel/api/inbounds/updateClient/{client_uuid}"
        
        settings = {
            "clients": [
                {
                    "id": client_uuid,
                    "flow": flow if vless else "",
                    "email": email,
                    "limitIp": limit_ip,
                    "totalGB": total_gb,
                    "expiryTime": expire_time,
                    "enable": enable,
                    "tgId": "",
                    "subId": client_uuid
                }
            ]
        }
        
        data = {
            "id": inbound_id,
            "settings": json.dumps(settings)
        }
        try:
            response = await self.session.post(url, json=data)
            if response.status_code == 200:
                payload = response.json()
                success = payload.get("success", False)
                if not success:
                    logger.error(
                        "XUI update_client rejected: inbound_id=%s client_uuid=%s email=%s response=%s",
                        inbound_id,
                        client_uuid,
                        email,
                        response.text[:500],
                    )
                return success

            logger.error(
                "XUI update_client HTTP error: inbound_id=%s client_uuid=%s email=%s status=%s response=%s",
                inbound_id,
                client_uuid,
                email,
                response.status_code,
                response.text[:500],
            )
        except Exception:
            logger.exception(
                "XUI update_client exception: inbound_id=%s client_uuid=%s email=%s",
                inbound_id,
                client_uuid,
                email,
            )
        return False


def xui_from_config(config, secondary: bool = False) -> XUIServer:
    """Create XUIServer instance from the provided `config` object.
    If `secondary` is True and `config` contains `xui2`, use that, otherwise use `config.xui`.
    """
    cfg = None
    if secondary and getattr(config, "xui2", None):
        cfg = config.xui2
    else:
        cfg = config.xui

    return XUIServer(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
        https=cfg.https,
        web_base_path=cfg.web_base_path,
    )
