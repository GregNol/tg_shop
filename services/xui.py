import httpx
import uuid
import json
from typing import Optional, Dict, Any, List

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
            if response.status_code == 200 and response.json().get("success"):
                return True
        except Exception as e:
            print(f"XUI Login Error: {e}")
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
        
        response = await self.session.post(url, json=data)
        if response.status_code == 200 and response.json().get("success", False):
            return client_id
        return None

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
        
        response = await self.session.post(url, json=data)
        if response.status_code == 200:
            return response.json().get("success", False)
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
