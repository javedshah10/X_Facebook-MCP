"""
refresh_twitter.py
------------------
Silent background Twitter token refresh.
Run every 90 minutes via Windows Task Scheduler (or cron on Linux/macOS).
No browser interaction required — uses the refresh token stored in tokens.enc.

Setup (Windows Task Scheduler — run once as Administrator):
------------------------------------------------------------
$action = New-ScheduledTaskAction `
    -Execute "D:\\Claude\\MCPs\\twiter_fb_mcp\\.venv\\Scripts\\python.exe" `
    -Argument "D:\\Claude\\MCPs\\twiter_fb_mcp\\refresh_twitter.py" `
    -WorkingDirectory "D:\\Claude\\MCPs\\twiter_fb_mcp"

$trigger = New-ScheduledTaskTrigger `
    -RepetitionInterval (New-TimeSpan -Minutes 90) `
    -Once -At (Get-Date)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName "SocialMCP-TwitterRefresh" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Refreshes social-mcp Twitter token every 90 min silently" `
    -RunLevel Highest `
    -Force

Setup (Linux/macOS cron — run `crontab -e` and add):
----------------------------------------------------
*/90 * * * * /path/to/.venv/bin/python /path/to/refresh_twitter.py >> /tmp/twitter_refresh.log 2>&1

Manual test:
------------
& "D:\\Claude\\MCPs\\twiter_fb_mcp\\.venv\\Scripts\\python.exe" refresh_twitter.py

Why this is needed:
-------------------
X OAuth 2.0 access tokens always expire after 2 hours even with offline.access scope.
offline.access gives a refresh token (not a permanent token). The MCP auto-refreshes
when a tool is called, but if Claude Desktop is closed overnight and no tool runs,
the token expires. This script runs in the background every 90 minutes and silently
refreshes the token so it is always ready when you open Claude.

Facebook does NOT need this — its long-lived token lasts 60 days.
"""

import asyncio
import sys
from pathlib import Path

# -----------------------------------------------------------------------
# Update this path if your project is in a different location
# -----------------------------------------------------------------------
PROJECT_ROOT = Path(r"D:\Claude\MCPs\twiter_fb_mcp")
# -----------------------------------------------------------------------

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from social_mcp.token_store import get_store
from social_mcp.twitter import get_client


async def refresh() -> None:
    cred = get_store().get("twitter")

    if cred is None:
        print("Twitter not authenticated — run `social-mcp authenticate twitter` first.")
        return

    # Refresh 30 minutes before actual expiry (skew_seconds=1800)
    if not cred.is_expired(skew_seconds=1800):
        print("Twitter token still valid — no refresh needed.")
        return

    print("Twitter token expiring soon — refreshing...")
    client = get_client()
    try:
        await client._refresh_if_needed()
        print("Twitter token refreshed successfully.")
    except Exception as e:
        print(f"Refresh failed: {e}")
        print("Run `social-mcp authenticate twitter` to re-authenticate manually.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(refresh())
