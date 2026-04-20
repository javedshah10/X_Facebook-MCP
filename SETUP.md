# social-mcp — Setup

A real, tested walkthrough from a freshly-downloaded folder to 22 working tools
inside a local MCP client such as Claude Desktop, Codex app, or Claude Code. Allow ~30 minutes the first time; most of that is Meta's
and X's developer-portal steps, not our code.

The setup has two phases:

1. **One-time app setup** (manual — every platform requires this): creating the
   X and Meta developer apps and copying 4 credentials into `.env`.
2. **User authentication** (one click per provider): `social-mcp authenticate`
   opens the browser, you click *Allow*, and encrypted tokens are stored.

---

## 0. Prerequisites

- **Python 3.11 or newer.** Check: `python --version`.
- **Windows PowerShell**, macOS Terminal, or any Linux shell.
- **A local MCP client** such as Claude Desktop, Codex app, or Claude Code.
  The web version of Claude at claude.ai does NOT support this MCP — see §7
  for why.
- A Facebook account that admins the Page you want to post to.
- An X (Twitter) developer account with **credits loaded**, or a legacy
  Basic/Pro subscription. New X developers in 2026 have no free tier — load
  a few dollars in the Developer Console → Billing before you start, or your
  first API call returns HTTP 402.

## 1. Folder layout (critical — the package must live in the right place)

Download the project and make sure it looks **exactly** like this. This is
non-negotiable because `pyproject.toml` tells pip the package is at
`src/social_mcp/`:

```
twitter_fb_mcp/                 ← your working folder, any name is fine
├── .env.example                ← copy this to .env and fill in credentials
├── .gitignore
├── pyproject.toml              ← do not move
├── README.md
├── SETUP.md
└── src/
    └── social_mcp/             ← the Python package — all 8 files below must be HERE
        ├── __init__.py
        ├── __main__.py
        ├── config.py
        ├── facebook.py
        ├── oauth_flow.py
        ├── server.py
        ├── token_store.py
        └── twitter.py
```

**Common mistake:** unzipping dumps all 8 `.py` files into the root folder
alongside `pyproject.toml`. If that happens, `pip install -e .` appears to
succeed but every command dies with `ModuleNotFoundError: No module named
'social_mcp'`. Fix on Windows PowerShell:

```powershell
mkdir src\social_mcp
move __init__.py     src\social_mcp\
move __main__.py     src\social_mcp\
move config.py       src\social_mcp\
move token_store.py  src\social_mcp\
move oauth_flow.py   src\social_mcp\
move twitter.py      src\social_mcp\
move facebook.py     src\social_mcp\
move server.py       src\social_mcp\
```

On macOS/Linux:
```bash
mkdir -p src/social_mcp
mv __init__.py __main__.py config.py token_store.py oauth_flow.py twitter.py facebook.py server.py src/social_mcp/
```

Verify with `ls src/social_mcp` — all 8 files must show up.

## 2. Install

Open a terminal in the project root (the folder containing `pyproject.toml`):

**Windows PowerShell:**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .
```

If activation fails with "execution policy" error:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

Verify:
```
social-mcp --help
```

You should see:
```
usage: social-mcp [-h] {authenticate,status,logout,serve} ...
```

If you instead get `ModuleNotFoundError: No module named 'social_mcp'`, the
folder structure in §1 is wrong — fix it and re-run `pip install -e . --force-reinstall`.

## 3. Create the X (Twitter) app

1. Go to <https://developer.x.com> → sign in → **Developer Portal**.
2. **Projects & Apps** → create a Project → create an **App** inside it.
3. Open the app → **Settings** → **User authentication settings** → **Set up**:
   - **App permissions**: *Read and write*
   - **Type of App**: *Web App, Automated App or Bot*  (NOT Native App)
   - **Callback URI / Redirect URL**: exactly
     ```
     http://localhost:8765/twitter/callback
     ```
     *(X accepts plain HTTP on loopback. Use `localhost` — this matches what
     the MCP will send, and it keeps Twitter and Facebook consistent.)*
   - **Website URL**: your own site (e.g. `https://joodei.com`).
4. Save. **Wait 30-60 seconds** before the first auth attempt — X takes a
   moment to propagate callback-URL changes.
5. **Keys and tokens** tab → under **OAuth 2.0 Client ID and Client Secret**
   → copy **both**. These are the ONLY credentials our MCP needs. Ignore:
   - Bearer Token (app-only auth, not user-scoped)
   - OAuth 1.0 Consumer Key / Access Token (legacy flow, not used)
6. Developer Console → **Billing / Credits** → load credits (pay-as-you-go).

## 4. Create the Meta (Facebook) app

Meta's dashboard was rebuilt in 2024-2025 around **use cases** instead of the
old "Products" menu. The steps below match the current layout.

1. <https://developers.facebook.com/apps/> → **Create app**.
2. Pick app type **Business** → give it a name (e.g. `mcp`) → create.
3. In the app dashboard, click **Add use cases** (top-right button) → pick
   **"Manage everything on your Page"** → add it.
4. On that use case's page, **Customize** → under **Permissions**, grant:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `pages_manage_engagement`
5. In the left sidebar, expand **Facebook Login for Business** → click its
   **Settings** (sometimes labeled "Configurations"). You'll see two sections
   on this page:
   - **Valid OAuth Redirect URIs** (this is the allowlist — the one that matters)
   - **Redirect URI Validator** (just a tester, further down the page — does NOT save anything)
6. In **Valid OAuth Redirect URIs**, paste exactly:
   ```
   http://localhost:8765/facebook/callback
   ```
   Press Tab or Enter so it registers as an entry.
7. Under **Client OAuth settings** (same page), make sure these toggles are ON:
   - Client OAuth login: **Yes**
   - Web OAuth login: **Yes**
   - Enforce HTTPS: **Yes**
   - Use Strict Mode for redirect URIs: **Yes** (exact matching — safer and what we want)
8. **Save changes** at the bottom of the page. Missing this step is the #1
   reason the validator still says "invalid".
9. Left sidebar → **App settings → Basic**:
   - **App Domains**: add `localhost`
   - Copy **App ID** (visible at the top, a ~16-digit number).
   - Click **Show** next to **App Secret** and copy it.
   - Save changes.

**Important:** Do NOT use the field labeled "Authorize callback URL" on the
**App settings → Advanced** page. That's a different mechanism (server-to-server
notifications) and it actively rejects `127.0.0.1` / `localhost`. Leave it empty.

**Important:** Use `localhost`, not `127.0.0.1`, in the redirect URI. Meta
rejects the IP form but accepts `localhost` for local development.

## 5. Fill in `.env`

In the project root:

**Windows:**
```powershell
copy .env.example .env
notepad .env
```

**macOS / Linux:**
```bash
cp .env.example .env
$EDITOR .env
```

Fill in the four values. **No quotes around the values. No spaces around the
`=`. No trailing spaces on the line.**

```
TWITTER_CLIENT_ID=<from X developer portal, "OAuth 2.0 Client ID">
TWITTER_CLIENT_SECRET=<from X developer portal, "OAuth 2.0 Client Secret">
FACEBOOK_APP_ID=<the ~16-digit number from Meta "App settings → Basic">
FACEBOOK_APP_SECRET=<from Meta "App settings → Basic", revealed via Show>
OAUTH_CALLBACK_HOST=localhost
OAUTH_CALLBACK_PORT=8765
```

Note especially `OAUTH_CALLBACK_HOST=localhost` — this must match what you
entered in both the X and Meta callback URL fields. The default in
`.env.example` may still say `127.0.0.1`; change it to `localhost`.

**Gotcha:** the most common failure at authentication time is "Invalid App ID:
The provided app ID does not look like a valid app ID." That error means
`FACEBOOK_APP_ID` is either empty, wrapped in quotes, or you accidentally
pasted the App **Secret** there instead of the App **ID**. The App ID is the
number shown unhidden at the top of App settings → Basic.

Quick check (PowerShell):
```powershell
Get-Content .env | Select-String "FACEBOOK"
Get-Content .env | Select-String "CALLBACK"
```
Values should be unquoted; App ID should be digits only; host should be `localhost`.

## 6. Authenticate (the one-click part)

```
social-mcp authenticate facebook
```

What happens:

1. A local HTTP server starts on `http://localhost:8765` — plain HTTP, no TLS, no cert warnings.
2. Your default browser opens Facebook's consent screen.
3. **Cert warning** ("Your connection isn't private" /
   `NET::ERR_CERT_AUTHORITY_INVALID`) — click **Advanced → Continue to
   localhost (unsafe)**. Safe because the cert is generated on your machine
   and bound to `localhost`; it never leaves your device.
4. Facebook shows *"mcp wants to access…"* → **Continue** → pick your Page(s)
   → **Next**.
5. Browser redirects back to `localhost`, server captures the code, terminal
   prints `✓ Facebook authenticated.`

Then X:

```
social-mcp authenticate twitter
```

Same flow — plain HTTP on loopback, no warnings.

Verify:

```
social-mcp status
```

Both providers should show `connected: true`.

Tokens are written encrypted (Fernet) to:
- **Windows:** `%USERPROFILE%\.social_mcp\tokens.enc`
- **macOS / Linux:** `~/.social_mcp/tokens.enc`

The Fernet key is stored in your OS keyring by default. On headless VPS, set
`SOCIAL_MCP_FERNET_KEY` in `.env` instead (§10).

## 7. Wire into your MCP client

### ⚠️ This works with local MCP clients, not claude.ai web

Local MCP clients such as **Claude Desktop**, **Codex app**, and **Claude Code** can run local stdio MCP servers like this one. The web version at
claude.ai supports MCP only through public HTTPS "Connectors" (paid plans) —
our MCP is a local stdio server, not a public HTTPS endpoint. Using claude.ai
web would require deploying the server to a public URL with TLS, which is
extra work for no real benefit on a single-user setup.

Download Claude Desktop: <https://claude.ai/download>

### Configure

Get the exact path to the installed executable:

**Windows:**
```powershell
(Get-Command social-mcp).Source
```

**macOS / Linux:**
```bash
which social-mcp
```

Copy that path. Then open the Claude Desktop config:

**Windows:**
```powershell
New-Item -ItemType Directory -Force -Path "$env:APPDATA\Claude" | Out-Null
notepad $env:APPDATA\Claude\claude_desktop_config.json
```

If Notepad says the file doesn't exist → click **Yes** to create it.

**macOS:**
```bash
open -e "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

Paste this (replace the path with yours, **doubling every backslash on
Windows**):

```json
{
  "mcpServers": {
    "social": {
      "command": "D:\\Claude\\MCPs\\twitter_fb_mcp\\.venv\\Scripts\\social-mcp.exe",
      "args": ["serve"]
    }
  }
}
```

macOS / Linux example:
```json
{
  "mcpServers": {
    "social": {
      "command": "/Users/javed/code/twitter_fb_mcp/.venv/bin/social-mcp",
      "args": ["serve"]
    }
  }
}
```

If the file already has an `mcpServers` block with other servers, add the
`"social"` entry inside it — don't overwrite the file.

When saving in Notepad, set **"Save as type"** to **"All Files"** (not "Text
Documents") to avoid a sneaky `.txt` extension.

Verify the file saved correctly:
```powershell
Get-Content $env:APPDATA\Claude\claude_desktop_config.json
```

Every `\` in the path should be `\\` — single backslashes break JSON parsing.

### Restart Claude Desktop

**Fully quit Claude Desktop** (right-click tray icon → Quit on Windows; ⌘Q on
macOS — just closing the window isn't enough), then reopen it.

### Find the tools

In Claude Desktop, click the **`+` icon** in the chat input, then hover
**Connectors**. You should see `social` listed alongside any other enabled
integrations (Gmail, Drive, etc.) with a blue toggle. Make sure the toggle
is ON.

## 8. First test

In Claude Desktop, try:

> Who am I on X?

Claude should call `twitter_me` and return your X profile.

> Show me the Facebook Pages I manage.

Claude should call `facebook_list_pages`.

> Post a test tweet saying "social-mcp is live. Alhamdulillah."

Claude will call `twitter_post`. You're in business.

## 9. Verifying the MCP is running

There is no "always running" state — the MCP server runs as a **subprocess of
Claude Desktop** and exits when Claude exits. You don't start it manually.

**Check if Claude has it running** (Windows):
```powershell
Get-Process | Where-Object {$_.ProcessName -like "*social-mcp*"}
```
If a process shows, Claude Desktop has your MCP live.

**Manual smoke test** (bypasses Claude entirely):
```
social-mcp serve
```
Should hang silently (it's reading JSON-RPC from stdin). Ctrl+C to exit. If
it errors, the error tells you what's wrong.

**Full functional test** (sends a real JSON-RPC request and gets the tool list
back):
```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | social-mcp serve
```
Should print a big JSON response listing all 22 tools.

## 10. VPS / headless deployment (optional)

On servers without a keyring (most Linux VPS), set the Fernet key explicitly:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the result in `.env`:
```
SOCIAL_MCP_FERNET_KEY=<the generated key>
```

Authenticate once on a machine with a browser, then `scp` these files to the
server (do NOT commit them to git):
```
~/.social_mcp/tokens.enc
~/.social_mcp/loopback.crt
~/.social_mcp/loopback.key
```

Systemd unit example:
```ini
[Unit]
Description=social-mcp
After=network.target

[Service]
Type=simple
User=javed
WorkingDirectory=/opt/twitter_fb_mcp
EnvironmentFile=/etc/social-mcp.env
ExecStart=/opt/twitter_fb_mcp/.venv/bin/social-mcp serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 11. Troubleshooting

### Installation

**`ModuleNotFoundError: No module named 'social_mcp'`**
Folder layout is wrong. See §1. Fix the structure, then
`pip install -e . --force-reinstall`.

**PowerShell blocks `.venv\Scripts\Activate.ps1`**
Run once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### Facebook auth

**`Invalid App ID: The provided app ID does not look like a valid app ID.`**
`FACEBOOK_APP_ID` in `.env` is wrong — quotes around the value, extra spaces,
or you pasted the App Secret by mistake. Re-copy the App ID (digits only) from
App settings → Basic.

**`URL Blocked: This redirect failed because the redirect URI is not
whitelisted in the app's Client OAuth Settings.`**
The redirect URI that the MCP sends doesn't match what's whitelisted. Common
causes, in order:

1. **`.env` still has `127.0.0.1`** but you whitelisted `localhost` (or vice
   versa). Check with `Get-Content .env | Select-String "CALLBACK_HOST"` and
   make sure it says `localhost`. Paste your Facebook authorize URL and
   decode the `redirect_uri=` parameter to see what's actually being sent.
2. You pasted into the **Redirect URI Validator** box (just a tester) instead
   of the **Valid OAuth Redirect URIs** list above it on the same page.
3. You didn't click **Save Changes** at the bottom of the page after adding
   the URI.
4. Client/Web OAuth login toggles are off in Client OAuth Settings.

**`Redirect URI Validator` keeps saying "invalid"**
The validator only shows green when the URI is saved in the allowlist on the
same page. Scroll up → paste into **Valid OAuth Redirect URIs** → Save Changes
at the bottom → the validator clears.

**Browser cert warning (`NET::ERR_CERT_AUTHORITY_INVALID`) for Facebook**
This means the MCP is still using `https` for the Facebook callback. Make sure:
1. `config.py` has `facebook_redirect_uri` returning `http://` not `https://`
2. Meta dashboard Valid OAuth Redirect URI is `http://localhost:8765/facebook/callback`
3. Run `pip install -e . --force-reinstall` then retry auth.

### Twitter / X auth

**`Something went wrong. You weren't able to give access to the App.`**
X rejected the authorize request. Causes in order of likelihood:

1. **Callback URL mismatch.** The URL in **User authentication settings →
   Callback URI** doesn't match what the MCP is sending. If you changed `.env`
   from `127.0.0.1` to `localhost`, update the X app's callback URL to
   `http://localhost:8765/twitter/callback` and wait 30-60 seconds for X to
   propagate.
2. **Type of App** is set to Native. Change to *"Web App, Automated App or
   Bot"*.
3. **App permissions** set to Read-only. Change to *Read and write*.
4. **Website URL** field empty in User authentication settings. Fill any
   valid URL (e.g. `https://joodei.com`).

**`Unauthorized from X API`** (during tool calls, not auth)
Token expired and no refresh was possible. Run `social-mcp authenticate twitter` again.

**`Forbidden from X API: 402 Payment Required`**
You haven't loaded credits in the X Developer Console. New 2026 accounts
have no free tier — load a few dollars in Billing.

### Claude Desktop

**Claude.ai web doesn't show my MCP**
Correct — the web version doesn't support local stdio MCP servers. You need
the Claude Desktop installer from <https://claude.ai/download>. See §7.

**Claude Desktop doesn't show the `social` server**
99% of the time, one of:

1. **Wrong path in `claude_desktop_config.json`.** Re-check with
   `(Get-Command social-mcp).Source` and escape backslashes in the JSON.
2. **Single backslashes in the JSON path on Windows.** JSON requires `\\`
   everywhere. Verify with `Get-Content $env:APPDATA\Claude\claude_desktop_config.json`
   — every `\` should appear as `\\`.
3. **Didn't fully quit Claude Desktop.** Closing the window is not enough —
   right-click system tray icon → Quit → wait 5s → reopen.
4. **File saved as `.json.txt`.** When saving in Notepad, "Save as type" must
   be "All Files", not "Text Documents". Verify:
   ```powershell
   Get-ChildItem $env:APPDATA\Claude\
   ```
   The file name must end in exactly `.json`.
5. **Looking at claude.ai web, not Claude Desktop.** They look almost
   identical. Desktop has a File/Edit/View menu bar; web does not.

**Claude Desktop logs**
If the `social` server still doesn't appear after all of the above, check
the MCP logs:
```powershell
Get-ChildItem "$env:APPDATA\Claude\logs\" -ErrorAction SilentlyContinue
Get-Content "$env:APPDATA\Claude\logs\mcp-server-social.log" -Tail 50
```
Errors from your server's startup appear here. If the logs directory doesn't
exist at all, Claude Desktop has never tried to start the MCP — meaning
either the app isn't running, or the config isn't being read (usually a
file name / path issue).

### OAuth flow

**"OAuth callback was not received within 300s"**
Either you didn't click through the browser, or port `8765` is in use.
Change `OAUTH_CALLBACK_PORT` in `.env` and update the redirect URI in both
Meta and X consoles to match.

**`Facebook denied the request… pages_manage_posts`**
App is still in Development mode and you're trying to act on a Page you're
not an admin/developer/tester of. Either add yourself as a tester in App
roles, or submit the four permissions through App Review for production use.

---

## 12. Token Refresh — Keeping Twitter Connected

### Why Twitter disconnects overnight

X OAuth 2.0 access tokens **always expire after 2 hours** — even with `offline.access` scope. The `offline.access` scope gives you a *refresh token*, not a permanent token. The MCP auto-refreshes when Claude calls a tool, but if Claude Desktop is closed overnight and no tool is called, the token expires. Next morning's first call fails silently.

**Facebook is not affected** — its long-lived token lasts 60 days.

### The fix — a background refresh script on Task Scheduler

Create a Python script that runs every 90 minutes in the background and silently refreshes the Twitter token. No browser, no interaction needed.

**Step 1 — Create the refresh script**

Create a file called `refresh_twitter.py` in your project root (`D:\Claude\MCPs\twiter_fb_mcp\`):

```python
"""
refresh_twitter.py
------------------
Silent background Twitter token refresh.
Run every 90 minutes via Windows Task Scheduler.
No browser interaction required — uses the refresh token stored in tokens.enc.
"""
import asyncio
import sys

sys.path.insert(0, r"D:\Claude\MCPs\twiter_fb_mcp\src")

from social_mcp.token_store import get_store
from social_mcp.twitter import get_client


async def refresh():
    cred = get_store().get("twitter")
    if cred is None:
        print("Twitter not authenticated — skipping.")
        return
    if not cred.is_expired(skew_seconds=1800):  # refresh 30 min before actual expiry
        print("Token still valid — no refresh needed.")
        return
    client = get_client()
    await client._refresh_if_needed()
    await client.aclose()
    print("Twitter token refreshed successfully.")


asyncio.run(refresh())
```

Update the `sys.path.insert` line if your project is in a different folder.

**Step 2 — Test it manually first**

```powershell
& "D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\python.exe" "D:\Claude\MCPs\twiter_fb_mcp\refresh_twitter.py"
```

Expected output (if token is still fresh):
```
Token still valid — no refresh needed.
```

Expected output (if token needed refreshing):
```
Twitter token refreshed successfully.
```

**Step 3 — Register as a Windows Task Scheduler job (run once, as Administrator)**

```powershell
$action = New-ScheduledTaskAction `
    -Execute "D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\python.exe" `
    -Argument "D:\Claude\MCPs\twiter_fb_mcp\refresh_twitter.py" `
    -WorkingDirectory "D:\Claude\MCPs\twiter_fb_mcp"

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
```

**Step 4 — Verify it registered**

```powershell
Get-ScheduledTask -TaskName "SocialMCP-TwitterRefresh"
```

Should show `Ready` state. From now on, Twitter will never disconnect on its own.

**To remove the task if needed:**
```powershell
Unregister-ScheduledTask -TaskName "SocialMCP-TwitterRefresh" -Confirm:$false
```

### Checking Claude Desktop MCP logs

If something stops working, check the logs:

```powershell
# See all log files
Get-ChildItem "$env:APPDATA\Claude\logs\" | Sort-Object LastWriteTime -Descending

# Check MCP server log
Get-Content "$env:APPDATA\Claude\logs\mcp-server-social.log" -Tail 30

# Check main Claude log
Get-Content "$env:APPDATA\Claude\logs\mcp.log" -Tail 30
```

Log files location: `C:\Users\<you>\AppData\Roaming\Claude\logs\`

Key log files:
- `mcp-server-social.log` — errors from your social-mcp server startup
- `mcp.log` — MCP connection events
- `main.log` — Claude Desktop general log
- `claude.ai-web.log` — web connector activity

### Quick daily health check

```powershell
# Check both tokens are valid
social-mcp status

# Manually trigger refresh if needed
& "D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\python.exe" "D:\Claude\MCPs\twiter_fb_mcp\refresh_twitter.py"
```


