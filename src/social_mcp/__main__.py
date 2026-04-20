"""Command-line entry point.

Usage:
    social-mcp authenticate twitter
    social-mcp authenticate facebook
    social-mcp status
    social-mcp logout twitter|facebook
    social-mcp serve                    # run the MCP stdio server

The ``authenticate`` subcommand is the *simple click auth*: it opens the
user's default browser to the provider consent screen, listens on a local
loopback port for the redirect, and stores encrypted credentials on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .facebook import get_client as get_fb_client
from .token_store import get_store
from .twitter import get_client as get_tw_client

log = logging.getLogger(__name__)


async def _authenticate(provider: str, *, open_browser: bool) -> None:
    if provider == "twitter":
        client = get_tw_client()
        try:
            cred = await client.authenticate(open_browser=open_browser)
        finally:
            await client.aclose()
    elif provider == "facebook":
        client = get_fb_client()
        try:
            cred = await client.authenticate(open_browser=open_browser)
        finally:
            await client.aclose()
    else:
        raise SystemExit(f"Unknown provider: {provider}")

    print(  # noqa: T201
        f"\n\u2713 {provider.capitalize()} authenticated. "
        f"Token stored encrypted at {get_store()._path}."
    )
    if cred.scope:
        print(f"  Scopes: {cred.scope}")  # noqa: T201


def _status() -> None:
    store = get_store()
    report: dict[str, object] = {}
    for p in ("twitter", "facebook"):
        cred = store.get(p)
        report[p] = (
            "not connected"
            if cred is None
            else {
                "connected": True,
                "expires_at": cred.expires_at,
                "is_expired": cred.is_expired(),
                "scope": cred.scope,
            }
        )
    print(json.dumps(report, indent=2, default=str))  # noqa: T201


def _logout(provider: str) -> None:
    deleted = get_store().delete(provider)
    print(  # noqa: T201
        f"{'Removed' if deleted else 'Nothing to remove for'} {provider}."
    )


def _serve() -> None:
    # Imported lazily so `social-mcp --help` never pays the FastMCP import cost.
    from .server import run_stdio

    run_stdio()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="social-mcp",
        description="MCP server for Twitter/X and Facebook Pages.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_auth = sub.add_parser("authenticate", help="Run the browser OAuth flow.")
    p_auth.add_argument("provider", choices=["twitter", "facebook"])
    p_auth.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorize URL instead of launching a browser.",
    )

    sub.add_parser("status", help="Show which providers are connected.")

    p_logout = sub.add_parser("logout", help="Delete stored credentials.")
    p_logout.add_argument("provider", choices=["twitter", "facebook"])

    sub.add_parser("serve", help="Run the MCP server over stdio.")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if args.cmd == "authenticate":
        try:
            asyncio.run(_authenticate(args.provider, open_browser=not args.no_browser))
        except KeyboardInterrupt:
            print("\nAborted.", file=sys.stderr)  # noqa: T201
            sys.exit(130)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)  # noqa: T201
            sys.exit(1)
    elif args.cmd == "status":
        _status()
    elif args.cmd == "logout":
        _logout(args.provider)
    elif args.cmd == "serve":
        _serve()


if __name__ == "__main__":
    main()
