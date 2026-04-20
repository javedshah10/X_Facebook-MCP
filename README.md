# social-mcp

An MCP server that lets Claude (and any MCP-compatible agent) read and publish
on **Twitter / X** and **Facebook Pages** on behalf of an authenticated user.

Built on official APIs only — no scraping, no ToS violations, no unofficial
endpoints. Designed to age well.

---

## What it does

### Twitter / X

| Capability | Available |
|---|:-:|
| Read home timeline (accounts you follow) | ✅ |
| Read any user's recent posts | ✅ |
| Search recent posts (last 7 days) | ✅ |
| Read replies to a post | ✅ |
| Publish a post / reply / quote-tweet | ✅ |
| Upload image, GIF, or video | ✅ |
| Delete your post | ✅ |
| Likes, retweets, bookmarks, DMs, polls | ❌ not yet built |

### Facebook

| Capability | Available |
|---|:-:|
| List Pages you manage | ✅ |
| Read posts on your Pages | ✅ |
| Read comments on your Page posts | ✅ |
| Publish post / photo / video to your Page | ✅ |
| Comment on or reply to comments on your Page | ✅ |
| Delete a post from your Page | ✅ |
| Read personal news feed / friends' posts | ❌ Meta removed from API (2018) |
| Read or post to Facebook Groups | ❌ Meta deprecated Groups API (April 2024) |
| Read other Pages you don't admin | ❌ not permitted by Meta |

> **Facebook scope:** this MCP manages **Pages you own/admin**. It is not a
> personal feed reader — Meta's Graph API does not expose the personal news
> feed or friends' posts to third-party apps.

---

## Upgrading from v0.1 → v0.2

Media upload on X requires the `media.write` OAuth scope added in v0.2.
Run `social-mcp authenticate twitter` once to re-consent. Text-only posting
continues to work without this.

---

## What it deliberately doesn't support, and why

- **Facebook Groups** — Meta deprecated the entire Groups API in Graph API v19
  and removed it from all versions on 22 April 2024, including
  `publish_to_groups` and `groups_access_member_info`. Any tool claiming to
  support this will 404.
- **Facebook personal timeline / news feed** — removed from the Graph API in
  2018. Not coming back.
- **Reading friends' posts** — same removal.

---

## Architecture

```
┌────────────────────────┐
│   Claude / MCP client  │  (Claude Desktop or Claude Code)
└───────────┬────────────┘
            │ stdio (JSON-RPC)
┌───────────▼────────────┐          ┌─────────────────────────┐
│   social_mcp.server    │──────────▶  X API v2 (api.x.com)   │
│   (FastMCP, 22 tools)  │          └─────────────────────────┘
│                        │          ┌─────────────────────────┐
│                        │──────────▶  Graph API v21.0         │
└─────┬──────────────────┘          └─────────────────────────┘
      │
      ▼
┌────────────────────┐       ┌──────────────────────────┐
│  token_store.py    │◀──────│  OS keyring (Fernet key) │
│  Fernet-encrypted  │       └──────────────────────────┘
│  JSON on disk      │
└────────────────────┘
```

- **`config.py`** — pydantic-settings loads `.env` / env vars once.
- **`oauth_flow.py`** — one-shot loopback HTTP server on `localhost`. Opens
  the browser, captures the `?code=…` redirect, shuts down. Plain HTTP for
  both providers — no TLS certs, no browser warnings, works on corporate
  laptops without admin rights.
- **`token_store.py`** — Fernet-encrypted JSON vault. Key lives in OS keyring
  (Keychain / Credential Manager / Secret Service). For headless VPS, set
  `SOCIAL_MCP_FERNET_KEY`.
- **`twitter.py`** — OAuth 2.0 + PKCE, automatic token refresh, chunked media
  upload with processing poll.
- **`facebook.py`** — OAuth code → short-lived → long-lived user token →
  per-Page tokens fetched on demand. Page tokens from a long-lived user token
  are non-expiring.
- **`server.py`** — FastMCP server, 22 tools, Pydantic input validation,
  JSON output.

---

## Quick start

```bash
# 1. Install
pip install -e .

# 2. Copy and fill credentials
cp .env.example .env
$EDITOR .env

# 3. One-time browser auth per provider
social-mcp authenticate twitter
social-mcp authenticate facebook

# 4. Verify
social-mcp status

# 5a. Claude Desktop — edit claude_desktop_config.json (see SETUP.md)
# 5b. Claude Code
claude mcp add --scope user social -- /path/to/.venv/bin/social-mcp serve
```

Full credential setup: see [**SETUP.md**](./SETUP.md)
Claude Code setup: see [**CLAUDE_CODE_SETUP.md**](./CLAUDE_CODE_SETUP.md)

---

## What you pay

- **X API** — pay-as-you-go since 6 February 2026. ~$0.005/post read,
  $0.01/post created. Load credits in the X Developer Console before first use.
- **Facebook Graph API** — free. App Review required for production use of
  `pages_manage_posts` and related permissions.

---

## Security

- Tokens are **never logged** and never returned to the MCP client.
- OAuth callback binds to `localhost` only and shuts down after one redirect.
- `state` parameter verified on every callback to prevent CSRF.
- Token file written with mode `0600`. Key stored in OS keyring by default.
- To revoke: `social-mcp logout twitter` or `social-mcp logout facebook`.
  Also revoke app access in the provider's settings UI — local deletion alone
  does not invalidate the token server-side.

---

## License

MIT. Use in accordance with X's and Meta's developer policies.
