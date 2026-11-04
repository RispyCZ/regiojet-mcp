import time
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

from .config import Settings


class RegioJetClient:
    """Thin async wrapper over the RegioJet Affiliate REST API."""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.settings = settings
        auth = None
        token_header = {}
        if settings.token:
            token_header = {"Authorization": f"Bearer {settings.token}"}
        elif settings.username and settings.password:
            auth = httpx.BasicAuth(settings.username, settings.password)
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            auth=auth,
            timeout=settings.timeout,
            transport=transport,
            headers={
                "Accept": "application/json",
                "X-Application-Origin": "AFF",
                **token_header,
                **settings.extra_headers,
            },
        )
        # Public files (vehicle layout SVGs) are fetched without account credentials.
        self._public = httpx.AsyncClient(timeout=settings.timeout, transport=transport)
        self._cache: dict[tuple, tuple[float, Any]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._public.aclose()

    async def get_public_text(self, url: str) -> str:
        """GET a public absolute URL (no auth headers); raises httpx.HTTPError on failure."""
        resp = await self._public.get(url)
        resp.raise_for_status()
        return resp.text

    def _headers(self, lang: str | None, currency: str | None) -> dict[str, str]:
        return {
            "X-Lang": lang or self.settings.lang,
            "X-Currency": currency or self.settings.currency,
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        lang: str | None = None,
        currency: str | None = None,
    ) -> httpx.Response:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            resp = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                headers={**self._headers(lang, currency), **(headers or {})},
            )
        except httpx.HTTPError as e:
            raise ToolError(f"RegioJet API unreachable: {e}") from e
        if resp.is_error:
            raise ToolError(
                f"RegioJet API error {resp.status_code}: {_error_message(resp)}"
            )
        return resp

    def require_token(self) -> None:
        if not self.settings.token:
            raise ToolError(
                "This tool needs a RegioJet account token: set REGIOJET_API_KEY."
            )

    async def get_json(self, path: str, **kwargs: Any) -> Any:
        return (await self.request("GET", path, **kwargs)).json()

    async def get_cached(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        lang: str | None = None,
    ) -> Any:
        """GET with in-memory TTL cache; for rarely changing /consts endpoints."""
        key = (path, tuple(sorted((params or {}).items())), lang or self.settings.lang)
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < self.settings.cache_ttl:
            return hit[1]
        data = await self.get_json(path, params=params, lang=lang)
        self._cache[key] = (now, data)
        return data


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500] or resp.reason_phrase
    if isinstance(body, dict):
        msg = body.get("message") or resp.reason_phrase
        fields = body.get("errorFields")
        return f"{msg} {fields}" if fields else msg
    return str(body)[:500]
