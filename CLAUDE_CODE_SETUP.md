# social-mcp — Claude Code Setup

This guide covers wiring `social-mcp` into **Claude Code** (the terminal CLI),
which is separate from Claude Desktop. Claude Code is installed via npm and runs
in your terminal. It supports the same stdio MCP servers but is configured
differently.

---

## 0. Prerequisites

- Claude Code installed. If not:
  ```powershell
  npm install -g @anthropic-ai/claude-code
  ```
  Verify: `claude --version`

- `social-mcp` already installed and authenticated (see `SETUP.md`). Verify:
  ```powershell
  social-mcp status
  ```
  Both `twitter` and `facebook` should show `connected: true` before continuing.

- Get your exact executable path — you'll need it below:
  ```powershell
  (Get-Command social-mcp).Source
  ```
  Example output: `D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\social-mcp.exe`

---

## 1. Add the MCP server (one command)

Run this in PowerShell. Replace the path with your actual `social-mcp.exe` path
from above. **Use forward slashes or escaped backslashes** in the command:

```powershell
claude mcp add --scope user social -- "D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\social-mcp.exe" serve
```

The `--scope user` flag makes the server available **across all your projects**,
not just the current folder. This is what you want for a personal social media
MCP.

**On Windows**, if that throws `Connection closed` or `command not found`, use
the `cmd /c` wrapper (Windows can't always exec `.exe` files directly from Claude
Code's process spawner):

```powershell
claude mcp add --scope user social -- cmd /c "D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\social-mcp.exe" serve
```

---

## 2. Verify it registered

```powershell
claude mcp list
```

You should see:

```
social    stdio    D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\social-mcp.exe
```

For detailed info:

```powershell
claude mcp get social
```

---

## 3. Start Claude Code and confirm tools load

```powershell
claude
```

On startup, Claude Code scans and connects to all registered MCP servers. You
should see:

```
✔ Found 1 MCP server
```

Or it may show in the welcome banner. Then inside Claude Code, run:

```
/mcp
```

This lists all connected MCP servers and their status. You should see:

```
MCP Server Status
• social: connected
```

If it shows `disconnected` or `error`, see §6 Troubleshooting.

---

## 4. Check all 22 tools are available

Inside Claude Code session, type:

```
/mcp
```

Then expand `social` to see all tools, or just ask Claude:

> List all the tools you have available from the social MCP server.

Claude will list all 22:
- `auth_status`, `auth_logout`
- `twitter_me`, `twitter_get_home_timeline`, `twitter_get_user_posts`,
  `twitter_search_posts`, `twitter_get_post`, `twitter_get_replies`,
  `twitter_post`, `twitter_upload_media`, `twitter_delete_post`
- `facebook_me`, `facebook_list_pages`, `facebook_get_page_posts`,
  `facebook_get_post`, `facebook_get_post_comments`, `facebook_post_to_page`,
  `facebook_post_photo`, `facebook_post_video`, `facebook_comment_on_post`,
  `facebook_reply_to_comment`, `facebook_delete_post`

---

## 5. Test it

Inside the Claude Code session, try these:

```
Who am I on X?
```
→ Claude calls `twitter_me` → returns your X profile.

```
Show me the Facebook Pages I manage.
```
→ Claude calls `facebook_list_pages` → returns your Pages.

```
What's on my X home timeline? Show me the last 5 posts.
```
→ Claude calls `twitter_get_home_timeline`.

```
Post "Testing social-mcp from Claude Code. Bismillah. #100xEngineers #0to100xEngineer #MCP" to X.
```
→ Claude calls `twitter_post` → live on your timeline.

---

## 6. Alternative: Manual JSON config (if the CLI command doesn't work)

Claude Code stores its config at:

- **Windows:** `%USERPROFILE%\.claude.json`
- **macOS / Linux:** `~/.claude.json`

Open it:

```powershell
notepad $env:USERPROFILE\.claude.json
```

If the file doesn't exist, create it. Add the `social` server inside
`mcpServers`. If the file already has content, merge carefully — don't
overwrite existing keys.

```json
{
  "mcpServers": {
    "social": {
      "type": "stdio",
      "command": "D:\\Claude\\MCPs\\twiter_fb_mcp\\.venv\\Scripts\\social-mcp.exe",
      "args": ["serve"]
    }
  }
}
```

**Remember:** every backslash in the Windows path must be doubled (`\\`) in JSON.

Save, then restart Claude Code (`claude`). Verify with `/mcp`.

**Windows `cmd /c` variant** (if the direct `.exe` call fails):

```json
{
  "mcpServers": {
    "social": {
      "type": "stdio",
      "command": "cmd",
      "args": ["/c", "D:\\Claude\\MCPs\\twiter_fb_mcp\\.venv\\Scripts\\social-mcp.exe", "serve"]
    }
  }
}
```

---

## 7. Scopes explained

Claude Code has three scopes for MCP servers:

| Scope | Where stored | When to use |
|---|---|---|
| `user` | `~/.claude.json` | Personal tools available in ALL projects — use this for social-mcp |
| `local` | `.claude.json` in current folder | Project-specific, not committed to git |
| `project` | `.mcp.json` in current folder | Shared with the whole team via git |

For `social-mcp`, `--scope user` is correct — it's a personal tool that should
work everywhere, not tied to any specific project.

---

## 8. Manage the server

```powershell
# List all registered servers
claude mcp list

# Show details for social
claude mcp get social

# Remove social (does not delete tokens or your .env)
claude mcp remove social

# Re-add after removal
claude mcp add --scope user social -- "D:\Claude\MCPs\twiter_fb_mcp\.venv\Scripts\social-mcp.exe" serve
```

---

## 9. Troubleshooting

**`claude` command not found**
Claude Code isn't installed or not in PATH. Install it:
```powershell
npm install -g @anthropic-ai/claude-code
```
Then close and reopen PowerShell.

**`/mcp` shows `social: disconnected` or `error`**
Claude Code tried to start the server but it failed. Check:

1. The path in your config is correct — verify with:
   ```powershell
   claude mcp get social
   ```
2. The server boots on its own:
   ```powershell
   social-mcp serve
   ```
   Should hang silently. If it errors, paste the error.

3. `.env` file is present in `D:\Claude\MCPs\twiter_fb_mcp\` (the working
   directory). Claude Code launches `social-mcp.exe` from whatever directory
   you ran `claude`, which may not be the project folder — the `.env` must be
   findable. Fix by adding `cwd` to the JSON config:

   ```json
   {
     "mcpServers": {
       "social": {
         "type": "stdio",
         "command": "D:\\Claude\\MCPs\\twiter_fb_mcp\\.venv\\Scripts\\social-mcp.exe",
         "args": ["serve"],
         "cwd": "D:\\Claude\\MCPs\\twiter_fb_mcp"
       }
     }
   }
   ```

**`Connection closed` immediately on Windows**
The `.exe` can't be launched directly. Use the `cmd /c` wrapper — see §6.

**Server connects but `social-mcp status` says "not authenticated"**
Tokens are stored in `%USERPROFILE%\.social_mcp\tokens.enc`. If you
authenticated in one shell but Claude Code runs in another context, the
token file path might differ. Verify:
```powershell
social-mcp status
```
If not authenticated, re-run:
```powershell
social-mcp authenticate twitter
social-mcp authenticate facebook
```

**`ModuleNotFoundError: No module named 'social_mcp'` in Claude Code logs**
The `.venv` wasn't activated when Claude Code launched the server. This
doesn't matter — we're calling the compiled `.exe` directly, not `python -m
social_mcp`. But if you see this error, it means the wrong Python is being
invoked. Make sure the `command` in your config points to the `.venv\Scripts\
social-mcp.exe`, not a bare `social-mcp` that might resolve to a different
Python.

**Claude Code works but Claude Desktop doesn't show the server (or vice versa)**
They use separate config files:
- Claude Desktop: `%APPDATA%\Claude\claude_desktop_config.json`
- Claude Code: `%USERPROFILE%\.claude.json`

Both need their own config entry. Having it in one doesn't affect the other.

---

## 10. Both Claude Desktop and Claude Code simultaneously

You can run `social-mcp` from both at the same time — each client spawns its
own independent `social-mcp.exe` subprocess. They share the same encrypted
token file (`~/.social_mcp/tokens.enc`) which is safe because all reads are
non-destructive and writes use atomic rename. No conflict.

Config summary:

| Client | Config file | Entry |
|---|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | `"mcpServers": { "social": { ... } }` |
| Claude Code | `%USERPROFILE%\.claude.json` | `"mcpServers": { "social": { ... } }` |

Both use the exact same `command` and `args` values.
