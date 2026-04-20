"""Facebook Graph API client.

Scope of this module is deliberately limited to **Facebook Pages that the
authenticated user manages**. We do *not* expose Group tools. Meta deprecated
the Groups API in Graph API v19 (removed from all versions on April 22, 2024),
including ``publish_to_groups`` and ``groups_access_member_info``. Any MCP tool
that pretended to support groups would 404 today; we refuse to ship it.

For Pages, we obtain a user access token via OAuth, exchange it for a
long-lived user token, and derive per-Page access tokens (which are themselves
non-expiring when issued from a long-lived user token). Those Page tokens are
what you use to read + publish on a Page.

Reference:
  * Facebook Login flow: https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow
  * Page tokens:         https://developers.facebook.com/docs/pages-api/getting-started
  * Groups deprecation:  https://developers.facebook.com/blog/post/2024/01/23/introducing-facebook-graph-and-marketing-api-v19/
"""

from __future__ import annotations

import logging
import secrets
import time
import webbrowser
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .oauth_flow import capture_callback
from .token_store import Credential, get_store

log = logging.getLogger(__name__)

PROVIDER = "facebook"
GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
AUTHORIZE_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
TOKEN_URL = f"{GRAPH_BASE}/oauth/access_token"

# Minimal set of permissions needed to list the user's Pages, read their posts
# and engagement metrics, and publish + moderate on their behalf.
DEFAULT_SCOPES = (
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_manage_engagement",
    "public_profile",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FacebookError(RuntimeError):
    """Raised for Graph API errors with actionable context."""


def _friendly_http_error(resp: httpx.Response) -> FacebookError:
    code = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    msg = err.get("message") or body
    subcode = err.get("error_subcode")

    if code == 401 or err.get("code") == 190:
        return FacebookError(
            f"Facebook token invalid or expired: {msg}. "
            "Re-run `social-mcp authenticate facebook`."
        )
    if code == 403 or err.get("code") == 10:
        return FacebookError(
            f"Facebook denied the request: {msg}. Your app likely lacks the "
            "required permission (pages_manage_posts / pages_read_engagement). "
            "Grant it during OAuth, and ensure the app has passed App Review "
            "for production use."
        )
    if code == 429 or err.get("code") in (4, 17, 32, 613):
        return FacebookError(f"Facebook rate-limited the request: {msg}")
    if subcode:
        return FacebookError(f"Graph API error {code}/{subcode}: {msg}")
    return FacebookError(f"Graph API error {code}: {msg}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class FacebookClient:
    """Async wrapper over the subset of Graph API we actually support."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=GRAPH_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "social-mcp/0.1 (+https://joodei.com)"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- Auth flow ----------------------------------------------------------

    async def authenticate(self, *, open_browser: bool = True) -> Credential:
        """Full click-through OAuth flow; persists a long-lived user token and
        caches a Page-token lookup for convenience."""
        settings = get_settings()
        settings.require_facebook()

        state = secrets.token_urlsafe(24)
        params = {
            "client_id": settings.facebook_app_id,
            "redirect_uri": settings.facebook_redirect_uri,
            "scope": ",".join(DEFAULT_SCOPES),
            "response_type": "code",
            "state": state,
        }
        authorize_url = str(httpx.URL(AUTHORIZE_URL, params=params))

        if open_browser:
            log.info("Opening browser for Facebook authorization...")
            webbrowser.open(authorize_url)
        else:
            print(f"Open this URL to authorize:\n{authorize_url}")  # noqa: T201

        result = await capture_callback(
            expected_path="/facebook/callback",
            use_tls=False,
        )
        if result.error:
            raise FacebookError(
                f"Facebook denied authorization: {result.error} "
                f"({result.error_description or 'no detail provided'})"
            )
        if not result.code:
            raise FacebookError("Facebook callback did not include a code.")
        if result.state != state:
            raise FacebookError("OAuth state mismatch — refusing to continue.")

        short_lived = await self._exchange_code(result.code)
        long_lived = await self._exchange_for_long_lived(short_lived["access_token"])
        return self._persist(long_lived)

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        settings = get_settings()
        resp = await self._http.get(
            "/oauth/access_token",
            params={
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
                "redirect_uri": settings.facebook_redirect_uri,
                "code": code,
            },
        )
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        return resp.json()

    async def _exchange_for_long_lived(self, short_token: str) -> dict[str, Any]:
        settings = get_settings()
        resp = await self._http.get(
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
                "fb_exchange_token": short_token,
            },
        )
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        return resp.json()

    def _persist(self, token_payload: dict[str, Any]) -> Credential:
        expires_in = token_payload.get("expires_in")
        cred = Credential(
            provider=PROVIDER,
            access_token=token_payload["access_token"],
            expires_at=(time.time() + expires_in) if expires_in else None,
            scope=",".join(DEFAULT_SCOPES),
            extra={},
        )
        get_store().put(cred)
        return cred

    def _current_token(self) -> str:
        cred = get_store().get(PROVIDER)
        if cred is None:
            raise FacebookError(
                "Not authenticated with Facebook. Run "
                "`social-mcp authenticate facebook` first."
            )
        if cred.is_expired():
            raise FacebookError(
                "Facebook long-lived token has expired. Long-lived user tokens "
                "usually last 60 days. Run `social-mcp authenticate facebook` to "
                "re-issue."
            )
        return cred.access_token

    # -- HTTP helpers -------------------------------------------------------

    async def _get(
        self,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        p: dict[str, Any] = dict(params or {})
        p["access_token"] = token or self._current_token()
        resp = await self._http.get(path, params=p)
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        return resp.json()

    async def _post(
        self,
        path: str,
        *,
        token: str,
        data: dict[str, Any],
    ) -> Any:
        payload = {**data, "access_token": token}
        resp = await self._http.post(path, data=payload)
        if resp.status_code not in (200, 201):
            raise _friendly_http_error(resp)
        return resp.json()

    async def _delete(self, path: str, *, token: str) -> Any:
        resp = await self._http.delete(path, params={"access_token": token})
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        return resp.json()

    # -- Page-token helpers -------------------------------------------------

    async def _page_token(self, page_id: str) -> str:
        """Fetch the access token for a specific Page the user manages.

        Page tokens derived from a long-lived user token are themselves
        non-expiring, so we could cache these, but the Graph API's /me/accounts
        endpoint is cheap and always correct.
        """
        data = await self._get(
            "/me/accounts",
            params={"fields": "id,name,access_token", "limit": 100},
        )
        for p in data.get("data", []):
            if p["id"] == page_id:
                return p["access_token"]
        raise FacebookError(
            f"Page {page_id} not found among Pages you manage. Call "
            "`facebook_list_pages` to see the IDs of Pages you have access to."
        )

    # -- High-level operations ---------------------------------------------

    async def me(self) -> dict[str, Any]:
        return await self._get("/me", params={"fields": "id,name"})

    async def list_pages(self) -> dict[str, Any]:
        """Return the list of Pages the user manages.

        WARNING: The ``access_token`` field is redacted before returning to the
        caller so Page tokens never leak into model context.
        """
        data = await self._get(
            "/me/accounts",
            params={"fields": "id,name,category,tasks", "limit": 100},
        )
        return data

    async def page_posts(self, page_id: str, *, limit: int = 20) -> dict[str, Any]:
        token = await self._page_token(page_id)
        return await self._get(
            f"/{page_id}/posts",
            token=token,
            params={
                "fields": (
                    "id,message,created_time,permalink_url,"
                    "reactions.summary(true).limit(0),"
                    "comments.summary(true).limit(0),"
                    "shares"
                ),
                "limit": limit,
            },
        )

    async def get_post(self, post_id: str, *, page_id: str) -> dict[str, Any]:
        token = await self._page_token(page_id)
        return await self._get(
            f"/{post_id}",
            token=token,
            params={
                "fields": (
                    "id,message,created_time,permalink_url,"
                    "reactions.summary(true).limit(0),"
                    "comments.summary(true).limit(0)"
                ),
            },
        )

    async def post_comments(
        self, post_id: str, *, page_id: str, limit: int = 25,
    ) -> dict[str, Any]:
        token = await self._page_token(page_id)
        return await self._get(
            f"/{post_id}/comments",
            token=token,
            params={
                "fields": "id,from,message,created_time,like_count,comment_count",
                "limit": limit,
                "order": "chronological",
            },
        )

    async def publish_to_page(
        self,
        page_id: str,
        message: str,
        *,
        link: str | None = None,
    ) -> dict[str, Any]:
        token = await self._page_token(page_id)
        data: dict[str, Any] = {"message": message}
        if link:
            data["link"] = link
        return await self._post(f"/{page_id}/feed", token=token, data=data)

    async def reply_to_comment(
        self, comment_id: str, *, page_id: str, message: str,
    ) -> dict[str, Any]:
        token = await self._page_token(page_id)
        return await self._post(
            f"/{comment_id}/comments",
            token=token,
            data={"message": message},
        )

    async def comment_on_post(
        self, post_id: str, *, page_id: str, message: str,
    ) -> dict[str, Any]:
        token = await self._page_token(page_id)
        return await self._post(
            f"/{post_id}/comments",
            token=token,
            data={"message": message},
        )

    async def publish_photo(
        self,
        page_id: str,
        *,
        caption: str | None = None,
        photo_path: str | None = None,
        photo_url: str | None = None,
        published: bool = True,
    ) -> dict[str, Any]:
        """Publish a photo to a Page.

        Exactly one of ``photo_path`` (local file) or ``photo_url`` (remote,
        publicly reachable URL) must be given. When ``published=False``, the
        photo is uploaded but not shown on the Page; the returned ``id`` can
        then be attached to a feed post for multi-photo composites.
        """
        if bool(photo_path) == bool(photo_url):
            raise FacebookError(
                "Provide exactly one of photo_path or photo_url."
            )
        token = await self._page_token(page_id)
        data: dict[str, Any] = {"published": "true" if published else "false"}
        if caption:
            data["caption"] = caption
        if photo_url:
            data["url"] = photo_url
            return await self._post(f"/{page_id}/photos", token=token, data=data)
        # Local file: multipart upload with the raw bytes in ``source``.
        return await self._upload_multipart(
            f"/{page_id}/photos", token=token, data=data,
            file_path=Path(photo_path).expanduser().resolve(),  # type: ignore[arg-type]
            field="source",
        )

    async def publish_video(
        self,
        page_id: str,
        *,
        description: str | None = None,
        video_path: str | None = None,
        video_url: str | None = None,
    ) -> dict[str, Any]:
        """Publish a video to a Page.

        Exactly one of ``video_path`` (local file) or ``video_url`` (remote
        URL Facebook can fetch) must be given. For videos >1 GB, Facebook
        recommends its resumable upload API; that is out of scope here.
        """
        if bool(video_path) == bool(video_url):
            raise FacebookError(
                "Provide exactly one of video_path or video_url."
            )
        token = await self._page_token(page_id)
        data: dict[str, Any] = {}
        if description:
            data["description"] = description
        if video_url:
            data["file_url"] = video_url
            return await self._post(f"/{page_id}/videos", token=token, data=data)
        return await self._upload_multipart(
            f"/{page_id}/videos", token=token, data=data,
            file_path=Path(video_path).expanduser().resolve(),  # type: ignore[arg-type]
            field="source",
        )

    async def _upload_multipart(
        self,
        path: str,
        *,
        token: str,
        data: dict[str, Any],
        file_path: Path,
        field: str,
    ) -> dict[str, Any]:
        if not file_path.is_file():
            raise FacebookError(f"File not found: {file_path}")
        with file_path.open("rb") as fh:
            files = {field: (file_path.name, fh, "application/octet-stream")}
            payload = {**data, "access_token": token}
            resp = await self._http.post(path, data=payload, files=files)
        if resp.status_code not in (200, 201):
            raise _friendly_http_error(resp)
        return resp.json()

    async def delete_post(self, post_id: str, *, page_id: str) -> dict[str, Any]:
        token = await self._page_token(page_id)
        return await self._delete(f"/{post_id}", token=token)


_client: FacebookClient | None = None


def get_client() -> FacebookClient:
    global _client
    if _client is None:
        _client = FacebookClient()
    return _client
