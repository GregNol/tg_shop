import logging
import uuid as uuid_lib
from datetime import datetime
from typing import Optional, Dict, Any, List

import httpx

logger = logging.getLogger(__name__)

GB = 1024 ** 3


class RemnawaveAPIError(Exception):
    """Raised on an ambiguous API failure (network, non-2xx, non-JSON body).

    Distinct from a confirmed 404, so callers can avoid destructive fallbacks
    (e.g. recreating a user) when the panel is merely misconfigured/unreachable.
    """


def gb_to_bytes(total_gb: int) -> int:
    """Convert GB to bytes. 0 means unlimited (Remnawave treats 0 as no limit)."""
    try:
        return int(total_gb) * GB if total_gb and int(total_gb) > 0 else 0
    except (TypeError, ValueError):
        return 0


def to_remnawave_datetime(value: Optional[datetime]) -> Optional[str]:
    """Format a (naive, UTC) datetime as ISO-8601 with milliseconds and Z suffix.

    The bot stores expiry as naive UTC datetimes, Remnawave expects e.g.
    `2025-12-31T23:59:59.000Z`.
    """
    if value is None:
        return None
    return value.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def parse_remnawave_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a Remnawave ISO-8601 datetime (e.g. `2025-12-31T23:59:59.000Z`) into
    a naive UTC datetime, matching how the bot stores expiry."""
    if not value:
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1]
    # drop timezone offset if present (keep it simple, assume UTC)
    for sep in ('+',):
        if sep in text[11:]:
            text = text[:11] + text[11:].split(sep)[0]
    try:
        # try with milliseconds first, then without
        for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        logger.warning("Could not parse Remnawave datetime: %s", value)
        return None


def make_username(telegram_id: int) -> str:
    """Generate a Remnawave-valid unique username (`^[a-zA-Z0-9_-]{6,34}$`)."""
    suffix = uuid_lib.uuid4().hex[:6]
    return f"tg_{telegram_id}_{suffix}"


class RemnawaveAPI:
    """Thin async client for the Remnawave Panel API.

    Auth is a Bearer API token (Panel → API Tokens). Responses are wrapped in a
    top-level `response` object, which this client unwraps for callers.
    """

    def __init__(self, base_url: str, token: str, verify_ssl: bool = True, cookie: str = ""):
        base = base_url.strip().rstrip('/')
        if not base.endswith('/api'):
            base = f"{base}/api"
        self.base_url = base
        self.token = token
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # eGames reverse-proxy hides the panel behind a secret access cookie;
        # without it Caddy serves the static SPA instead of proxying to the API.
        if cookie:
            headers["Cookie"] = cookie.strip()
        self.session = httpx.AsyncClient(headers=headers, verify=verify_ssl, timeout=30.0)

    async def close(self):
        await self.session.aclose()

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if isinstance(payload, dict) and "response" in payload:
            return payload["response"]
        return payload

    async def create_user(
        self,
        username: str,
        expire_at: Optional[datetime],
        squad_uuids: List[str],
        total_gb: int = 0,
        telegram_id: Optional[int] = None,
        traffic_limit_strategy: str = "NO_RESET",
        status: str = "ACTIVE",
    ) -> Optional[Dict[str, Any]]:
        """Create a Remnawave user. Returns the user object (incl. uuid,
        subscriptionUuid, subscriptionUrl) or None on failure."""
        url = f"{self.base_url}/users"
        body: Dict[str, Any] = {
            "username": username,
            "status": status,
            "trafficLimitBytes": gb_to_bytes(total_gb),
            "trafficLimitStrategy": traffic_limit_strategy,
            "activeInternalSquads": [s for s in squad_uuids if s],
        }
        if expire_at is not None:
            body["expireAt"] = to_remnawave_datetime(expire_at)
        if telegram_id is not None:
            body["telegramId"] = telegram_id

        try:
            response = await self.session.post(url, json=body)
            if response.status_code in (200, 201):
                data = self._unwrap(response.json())
                logger.info(
                    "Remnawave create_user succeeded: username=%s uuid=%s",
                    username,
                    data.get("uuid"),
                )
                return data
            logger.error(
                "Remnawave create_user failed: username=%s status=%s response=%s",
                username,
                response.status_code,
                response.text[:500],
            )
        except Exception:
            logger.exception("Remnawave create_user exception: username=%s", username)
        return None

    async def update_user(
        self,
        user_uuid: str,
        expire_at: Optional[datetime] = None,
        total_gb: Optional[int] = None,
        status: Optional[str] = None,
        squad_uuids: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Patch a Remnawave user. Only provided fields are sent."""
        url = f"{self.base_url}/users"
        body: Dict[str, Any] = {"uuid": user_uuid}
        if expire_at is not None:
            body["expireAt"] = to_remnawave_datetime(expire_at)
        if total_gb is not None:
            body["trafficLimitBytes"] = gb_to_bytes(total_gb)
        if status is not None:
            body["status"] = status
        if squad_uuids is not None:
            body["activeInternalSquads"] = [s for s in squad_uuids if s]

        try:
            response = await self.session.patch(url, json=body)
            if response.status_code == 200:
                return self._unwrap(response.json())
            logger.error(
                "Remnawave update_user failed: uuid=%s status=%s response=%s",
                user_uuid,
                response.status_code,
                response.text[:500],
            )
        except Exception:
            logger.exception("Remnawave update_user exception: uuid=%s", user_uuid)
        return None

    async def get_user(self, user_uuid: str) -> Optional[Dict[str, Any]]:
        """Return the user dict, or None only on a confirmed 404.

        Raises RemnawaveAPIError on any other failure (network, non-2xx, non-JSON)
        so callers don't mistake a misconfigured endpoint for "user missing".
        """
        url = f"{self.base_url}/users/{user_uuid}"
        try:
            response = await self.session.get(url)
        except Exception as e:
            raise RemnawaveAPIError(f"GET user {user_uuid} request failed: {e}") from e

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RemnawaveAPIError(
                f"GET user {user_uuid}: HTTP {response.status_code} (body: {response.text[:120]!r})"
            )
        try:
            return self._unwrap(response.json())
        except ValueError as e:
            snippet = response.text[:120].replace("\n", " ")
            raise RemnawaveAPIError(
                f"GET user {user_uuid}: 200 but non-JSON body — is REMNAWAVE_BASE_URL "
                f"the panel API host? body: {snippet!r}"
            ) from e

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/users/by-username/{username}"
        try:
            response = await self.session.get(url)
            if response.status_code == 200:
                data = self._unwrap(response.json())
                # by-username may return a list
                if isinstance(data, list):
                    return data[0] if data else None
                return data
        except Exception:
            logger.exception("Remnawave get_user_by_username exception: username=%s", username)
        return None

    async def enable_user(self, user_uuid: str) -> bool:
        return await self.update_user(user_uuid, status="ACTIVE") is not None

    async def disable_user(self, user_uuid: str) -> bool:
        return await self.update_user(user_uuid, status="DISABLED") is not None

    async def delete_user(self, user_uuid: str) -> bool:
        url = f"{self.base_url}/users/{user_uuid}"
        try:
            response = await self.session.delete(url)
            if response.status_code in (200, 204):
                return True
            logger.error(
                "Remnawave delete_user failed: uuid=%s status=%s response=%s",
                user_uuid,
                response.status_code,
                response.text[:300],
            )
        except Exception:
            logger.exception("Remnawave delete_user exception: uuid=%s", user_uuid)
        return False


def remnawave_from_config(config) -> RemnawaveAPI:
    """Create a RemnawaveAPI instance from `config.remnawave`."""
    cfg = config.remnawave
    return RemnawaveAPI(
        base_url=cfg.base_url,
        token=cfg.token,
        verify_ssl=cfg.verify_ssl,
        cookie=getattr(cfg, "cookie", ""),
    )


def squads_for_tariff(config, premium: bool) -> List[str]:
    """Resolve the internal-squad UUID list for a tariff.

    Premium gets the premium squads (falling back to standard if not configured),
    standard gets the standard squads.
    """
    cfg = config.remnawave
    if premium:
        return cfg.squads_premium or cfg.squads_standard
    return cfg.squads_standard
