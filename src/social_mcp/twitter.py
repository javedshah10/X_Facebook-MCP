"""Twitter / X OAuth 2.0 (PKCE) + API client.

All calls go through a single async HTTPX client. Access tokens are auto-
refreshed when within 60s of expiry. The same ``TwitterClient`` instance is
reused for the life of the MCP server.

Reference:
  * Authorization code + PKCE: https://docs.x.com/resources/fundamentals/authentication/oauth-2-0/authorization-code
  * Rate limits:                 https://docs.x.com/x-api/fundamentals/rate-limits
"""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import secrets
import time
import webbrowser
from pathlib import Path
from typing import Any

import anyio
import httpx

from .config import get_settings
from .oauth_flow import capture_callback
from .token_store import Credential, get_store

log = logging.getLogger(__name__)

PROVIDER = "twitter"
API_BASE = "https://api.x.com/2"
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"

# Scopes: read timelines + user info, post + delete tweets, upload media, refresh
# without re-consent. ``media.write`` is required for the v2 /media/upload endpoint;
# anyone who authenticated before this scope was added must re-run
# ``social-mcp authenticate twitter``.
DEFAULT_SCOPES = (
    "tweet.read",
    "tweet.write",
    "users.read",
    "follows.read",
    "media.write",
    "offline.access",
)

# Media upload tuning
MEDIA_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB, comfortably below X's 5 MiB per-chunk cap
MEDIA_POLL_MAX_SECONDS = 300  # hard ceiling for video processing wait
MEDIA_POLL_MAX_ATTEMPTS = 30


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` following RFC 7636."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _basic_auth_header(client_id: str, client_secret: str) -> dict[str, str]:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class TwitterError(RuntimeError):
    """Raised for API errors with actionable context."""


def _friendly_http_error(resp: httpx.Response) -> TwitterError:
    code = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    detail = body.get("detail") or body.get("title") or body.get("errors") or body
    if code == 401:
        return TwitterError(
            f"Unauthorized from X API: {detail}. The token is invalid or revoked. "
            "Re-run `social-mcp authenticate twitter`."
        )
    if code == 403:
        return TwitterError(
            f"Forbidden from X API: {detail}. Your app likely lacks the required "
            "access level, or the user revoked the requested scopes."
        )
    if code == 429:
        reset = resp.headers.get("x-rate-limit-reset")
        hint = f" Window resets at epoch {reset}." if reset else ""
        return TwitterError(f"Rate-limited by X API.{hint}")
    return TwitterError(f"X API error {code}: {detail}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class TwitterClient:
    """Thin, well-mannered wrapper over the X v2 REST API."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "social-mcp/0.1 (+https://joodei.com)"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- Auth flow ----------------------------------------------------------

    async def authenticate(self, *, open_browser: bool = True) -> Credential:
        """Run the full browser-click OAuth 2.0 + PKCE flow and persist tokens."""
        settings = get_settings()
        settings.require_twitter()

        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        params = {
            "response_type": "code",
            "client_id": settings.twitter_client_id,
            "redirect_uri": settings.twitter_redirect_uri,
            "scope": " ".join(DEFAULT_SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        authorize_url = str(httpx.URL(AUTHORIZE_URL, params=params))

        if open_browser:
            log.info("Opening browser for X authorization...")
            webbrowser.open(authorize_url)
        else:
            print(f"Open this URL to authorize:\n{authorize_url}")  # noqa: T201

        result = await capture_callback(
            expected_path="/twitter/callback",
            use_tls=False,
        )
        if result.error:
            raise TwitterError(
                f"X denied authorization: {result.error} "
                f"({result.error_description or 'no detail provided'})"
            )
        if not result.code:
            raise TwitterError("X callback did not include an authorization code.")
        if result.state != state:
            raise TwitterError("OAuth state mismatch — possible CSRF, refusing to continue.")

        return await self._exchange_code(result.code, verifier)

    async def _exchange_code(self, code: str, verifier: str) -> Credential:
        settings = get_settings()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.twitter_redirect_uri,
            "client_id": settings.twitter_client_id,
            "code_verifier": verifier,
        }
        if settings.twitter_client_secret:
            headers.update(_basic_auth_header(
                settings.twitter_client_id, settings.twitter_client_secret,
            ))

        resp = await self._http.post(TOKEN_URL, data=data, headers=headers)
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        payload = resp.json()
        return self._persist_token_response(payload)

    def _persist_token_response(self, payload: dict[str, Any]) -> Credential:
        expires_in = payload.get("expires_in")
        cred = Credential(
            provider=PROVIDER,
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=(time.time() + expires_in) if expires_in else None,
            scope=payload.get("scope"),
        )
        get_store().put(cred)
        return cred

    async def _refresh_if_needed(self) -> Credential:
        cred = get_store().get(PROVIDER)
        if cred is None:
            raise TwitterError(
                "Not authenticated with X. Run `social-mcp authenticate twitter` first."
            )
        if not cred.is_expired():
            return cred
        if not cred.refresh_token:
            raise TwitterError(
                "X access token expired and no refresh token is available. "
                "Run `social-mcp authenticate twitter` again."
            )
        settings = get_settings()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "refresh_token",
            "refresh_token": cred.refresh_token,
            "client_id": settings.twitter_client_id,
        }
        if settings.twitter_client_secret:
            headers.update(_basic_auth_header(
                settings.twitter_client_id, settings.twitter_client_secret,
            ))
        resp = await self._http.post(TOKEN_URL, data=data, headers=headers)
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        return self._persist_token_response(resp.json())

    async def _auth_headers(self) -> dict[str, str]:
        cred = await self._refresh_if_needed()
        return {"Authorization": f"Bearer {cred.access_token}"}

    # -- HTTP helpers -------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._http.get(path, params=params, headers=await self._auth_headers())
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        return resp.json()

    async def _post(self, path: str, json: dict[str, Any]) -> Any:
        headers = {**await self._auth_headers(), "Content-Type": "application/json"}
        resp = await self._http.post(path, json=json, headers=headers)
        if resp.status_code not in (200, 201):
            raise _friendly_http_error(resp)
        return resp.json()

    async def _delete(self, path: str) -> Any:
        resp = await self._http.delete(path, headers=await self._auth_headers())
        if resp.status_code != 200:
            raise _friendly_http_error(resp)
        return resp.json()

    # -- High-level operations ---------------------------------------------

    async def me(self) -> dict[str, Any]:
        """Return the authenticated user's profile."""
        return await self._get(
            "/users/me",
            params={"user.fields": "id,name,username,created_at,description,verified"},
        )

    async def home_timeline(self, *, max_results: int = 20) -> dict[str, Any]:
        """Posts from accounts the authenticated user follows."""
        me = (await self.me())["data"]
        return await self._get(
            f"/users/{me['id']}/timelines/reverse_chronological",
            params={
                "max_results": max_results,
                "tweet.fields": "id,text,created_at,author_id,conversation_id,public_metrics",
                "expansions": "author_id",
                "user.fields": "username,name,verified",
            },
        )

    async def user_posts(self, username: str, *, max_results: int = 20) -> dict[str, Any]:
        """Recent posts by the given username."""
        user = await self._get(
            f"/users/by/username/{username}",
            params={"user.fields": "id,username,name"},
        )
        if "data" not in user:
            raise TwitterError(f"X user @{username} not found.")
        uid = user["data"]["id"]
        return await self._get(
            f"/users/{uid}/tweets",
            params={
                "max_results": max_results,
                "tweet.fields": "id,text,created_at,conversation_id,public_metrics",
                "exclude": "retweets,replies",
            },
        )

    async def search_posts(self, query: str, *, max_results: int = 20) -> dict[str, Any]:
        """Search recent (last 7 days) posts matching the query.

        The full query syntax is documented at
        https://docs.x.com/x-api/fundamentals/query-building.
        """
        return await self._get(
            "/tweets/search/recent",
            params={
                "query": query,
                "max_results": max_results,
                "tweet.fields": "id,text,created_at,author_id,public_metrics",
                "expansions": "author_id",
                "user.fields": "username,name",
            },
        )

    async def get_post(self, post_id: str) -> dict[str, Any]:
        return await self._get(
            f"/tweets/{post_id}",
            params={
                "tweet.fields": "id,text,created_at,author_id,conversation_id,public_metrics",
                "expansions": "author_id",
                "user.fields": "username,name",
            },
        )

    async def get_replies(self, post_id: str, *, max_results: int = 20) -> dict[str, Any]:
        """Replies to a given post (uses the conversation_id search trick)."""
        original = await self.get_post(post_id)
        conv_id = original["data"]["conversation_id"]
        author = original["data"]["author_id"]
        return await self._get(
            "/tweets/search/recent",
            params={
                "query": f"conversation_id:{conv_id} to:{author}",
                "max_results": max_results,
                "tweet.fields": "id,text,created_at,author_id,in_reply_to_user_id",
                "expansions": "author_id",
                "user.fields": "username,name",
            },
        )

    async def create_post(
        self,
        text: str,
        *,
        reply_to_id: str | None = None,
        quote_of_id: str | None = None,
        media_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Post a new tweet, optionally as a reply, a quote, and/or with media.

        ``media_ids`` come from ``upload_media`` and let you attach up to 4
        images, 1 GIF, or 1 video. Mixing types is not allowed by X.
        """
        body: dict[str, Any] = {"text": text}
        if reply_to_id:
            body["reply"] = {"in_reply_to_tweet_id": reply_to_id}
        if quote_of_id:
            body["quote_tweet_id"] = quote_of_id
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        return await self._post("/tweets", json=body)

    async def upload_media(
        self,
        file_path: str,
        *,
        media_category: str | None = None,
    ) -> dict[str, Any]:
        """Upload an image, GIF, or video to X and return its ``media_id``.

        Uses the v2 chunked upload (INIT -> APPEND... -> FINALIZE) so the same
        code path works for tiny images and large videos. For videos and GIFs,
        this method also polls ``command=STATUS`` until X finishes processing
        before returning; the returned ``media_id`` is therefore always ready
        to attach to a post via :meth:`create_post`.

        Requires the ``media.write`` scope. If you authenticated before this
        scope was added, re-run ``social-mcp authenticate twitter``.
        """
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise TwitterError(f"Media file not found: {path}")

        mime, _ = mimetypes.guess_type(path.name)
        if not mime:
            raise TwitterError(
                f"Could not detect MIME type of {path.name}. Pass a file with a "
                "standard extension (.jpg, .png, .gif, .mp4, .mov)."
            )
        total_bytes = path.stat().st_size
        if media_category is None:
            if mime == "image/gif":
                media_category = "tweet_gif"
            elif mime.startswith("image/"):
                media_category = "tweet_image"
            elif mime.startswith("video/"):
                media_category = "tweet_video"
            else:
                raise TwitterError(
                    f"Unsupported media MIME type {mime!r}. X accepts images, "
                    "GIFs, and MP4/MOV videos."
                )

        media_id = await self._media_init(mime, total_bytes, media_category)
        await self._media_append(media_id, path)
        finalized = await self._media_finalize(media_id)

        processing_info = finalized.get("data", {}).get("processing_info")
        if processing_info:
            finalized = await self._media_wait(media_id)

        return finalized

    async def _media_init(
        self, mime: str, total_bytes: int, media_category: str,
    ) -> str:
        resp = await self._http.post(
            "/media/upload",
            headers=await self._auth_headers(),
            data={
                "command": "INIT",
                "media_type": mime,
                "total_bytes": str(total_bytes),
                "media_category": media_category,
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise _friendly_http_error(resp)
        data = resp.json().get("data") or resp.json()
        media_id = data.get("id") or data.get("media_id_string") or data.get("media_id")
        if not media_id:
            raise TwitterError(f"INIT response missing media id: {resp.text[:200]}")
        return str(media_id)

    async def _media_append(self, media_id: str, path: Path) -> None:
        headers = await self._auth_headers()
        segment_index = 0
        async with await anyio.open_file(path, "rb") as f:
            while True:
                chunk = await f.read(MEDIA_CHUNK_SIZE)
                if not chunk:
                    break
                resp = await self._http.post(
                    "/media/upload",
                    headers=headers,
                    data={
                        "command": "APPEND",
                        "media_id": media_id,
                        "segment_index": str(segment_index),
                    },
                    files={"media": ("chunk", chunk, "application/octet-stream")},
                )
                if resp.status_code not in (200, 201, 204):
                    raise _friendly_http_error(resp)
                segment_index += 1

    async def _media_finalize(self, media_id: str) -> dict[str, Any]:
        resp = await self._http.post(
            "/media/upload",
            headers=await self._auth_headers(),
            data={"command": "FINALIZE", "media_id": media_id},
        )
        if resp.status_code not in (200, 201):
            raise _friendly_http_error(resp)
        return resp.json()

    async def _media_wait(self, media_id: str) -> dict[str, Any]:
        """Poll STATUS until processing succeeds, fails, or we give up."""
        deadline = time.monotonic() + MEDIA_POLL_MAX_SECONDS
        for attempt in range(MEDIA_POLL_MAX_ATTEMPTS):
            resp = await self._http.get(
                "/media/upload",
                headers=await self._auth_headers(),
                params={"command": "STATUS", "media_id": media_id},
            )
            if resp.status_code != 200:
                raise _friendly_http_error(resp)
            payload = resp.json()
            info = (payload.get("data") or {}).get("processing_info", {})
            state = info.get("state")
            if state == "succeeded":
                return payload
            if state == "failed":
                err = info.get("error", {})
                raise TwitterError(
                    f"X rejected media {media_id}: {err.get('message', 'unknown error')}"
                )
            wait = min(info.get("check_after_secs", 2), max(1, int(deadline - time.monotonic())))
            if wait <= 0 or time.monotonic() >= deadline:
                break
            await anyio.sleep(wait)
            _ = attempt  # attempts cap is a belt-and-braces safety net
        raise TwitterError(
            f"Timed out after {MEDIA_POLL_MAX_SECONDS}s waiting for X to process "
            f"media {media_id}."
        )

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        return await self._delete(f"/tweets/{post_id}")


_client: TwitterClient | None = None


def get_client() -> TwitterClient:
    global _client
    if _client is None:
        _client = TwitterClient()
    return _client
