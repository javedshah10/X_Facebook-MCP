"""Loopback OAuth callback server.

This module implements the *simple click auth* experience:

  1. The CLI starts a tiny HTTP(S) server on localhost.
  2. It opens the user's default browser at the provider's authorize URL.
  3. The provider redirects back to https://localhost:PORT/<provider>/callback
     with ``?code=...&state=...`` in the query string.
  4. The server captures the code and shuts down.

Twitter accepts plain HTTP on loopback. Facebook requires HTTPS.

Certificate strategy (in priority order):
  1. mkcert — if installed, generates a browser-trusted cert with no warnings
     in Chrome, Firefox, or Edge. Run ``mkcert -install`` once to set up.
     Install: https://github.com/FiloSottile/mkcert
  2. Self-signed fallback — generated automatically if mkcert is absent.
     Chrome will show a cert warning; click Advanced → Proceed to continue.
     Firefox and Edge usually show a simpler one-click bypass.

For the best user experience (no cert warnings in any browser), install mkcert:
  Windows:  choco install mkcert  (or winget install mkcert)
  macOS:    brew install mkcert
  Linux:    see https://github.com/FiloSottile/mkcert#installation
Then run (once, as administrator on Windows):
  mkcert -install
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import logging
import shutil
import ssl
import subprocess
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .config import get_settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Certificate helpers
# ---------------------------------------------------------------------------


def _cert_dir() -> Path:
    d = get_settings().store_path.parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _try_mkcert() -> tuple[Path, Path] | None:
    """Generate a browser-trusted cert via mkcert if it is installed.

    Returns (cert_path, key_path) on success, None if mkcert is not available.
    """
    if not shutil.which("mkcert"):
        return None

    cert_dir = _cert_dir()
    cert_path = cert_dir / "mkcert-localhost.pem"
    key_path = cert_dir / "mkcert-localhost-key.pem"

    # Regenerate if either file is missing.
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    try:
        result = subprocess.run(
            [
                "mkcert",
                "-cert-file", str(cert_path),
                "-key-file", str(key_path),
                "localhost",
                "127.0.0.1",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info("mkcert generated a browser-trusted certificate for localhost.")
            return cert_path, key_path
        else:
            log.warning("mkcert failed: %s", result.stderr.strip())
            return None
    except Exception as e:
        log.warning("mkcert not usable: %s", e)
        return None


def _ensure_cert() -> tuple[Path, Path]:
    """Return (cert_path, key_path) — mkcert if available, self-signed otherwise."""
    mkcert_result = _try_mkcert()
    if mkcert_result:
        return mkcert_result

    # Fallback: self-signed cert (causes browser cert warnings).
    log.info(
        "mkcert not found — using a self-signed certificate. "
        "Chrome users will see a cert warning and must click Advanced → Proceed. "
        "Install mkcert to eliminate this warning: https://github.com/FiloSottile/mkcert"
    )
    return _ensure_self_signed_cert()


def _ensure_self_signed_cert() -> tuple[Path, Path]:
    """Create (or return) a self-signed cert + key for localhost / 127.0.0.1."""
    cert_path = _cert_dir() / "loopback.crt"
    key_path = _cert_dir() / "loopback.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "social-mcp-loopback"),
    ])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.chmod(0o600)
    log.info("Generated self-signed loopback certificate at %s", cert_path)
    return cert_path, key_path


# ---------------------------------------------------------------------------
# Callback capture
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CallbackResult:
    """Query parameters captured from the OAuth redirect."""

    code: str | None
    state: str | None
    error: str | None
    error_description: str | None
    path: str


_SUCCESS_HTML = b"""<!doctype html><html><head><meta charset="utf-8">
<title>social-mcp</title>
<style>body{font-family:system-ui,sans-serif;max-width:520px;margin:80px auto;
padding:0 16px;color:#222}h1{color:#2e7d32}</style></head><body>
<h1>&#x2713; Authentication complete</h1>
<p>You can close this tab and return to your terminal.</p>
</body></html>"""


_ERROR_HTML_TMPL = b"""<!doctype html><html><head><meta charset="utf-8">
<title>social-mcp</title>
<style>body{font-family:system-ui,sans-serif;max-width:520px;margin:80px auto;
padding:0 16px;color:#222}h1{color:#c62828}pre{background:#f5f5f5;
padding:12px;border-radius:6px;overflow:auto}</style></head><body>
<h1>&#x2717; Authentication failed</h1><pre>%s</pre></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    # Populated by the factory.
    expected_path: str = ""
    future: asyncio.Future[CallbackResult] | None = None
    loop: asyncio.AbstractEventLoop | None = None

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Silence the default stderr access log.
        log.debug("loopback %s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802  (stdlib mandates the name)
        parsed = urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not Found\n")
            return

        qs = parse_qs(parsed.query)
        result = CallbackResult(
            code=(qs.get("code") or [None])[0],
            state=(qs.get("state") or [None])[0],
            error=(qs.get("error") or [None])[0],
            error_description=(qs.get("error_description") or [None])[0],
            path=parsed.path,
        )

        if result.error:
            body = _ERROR_HTML_TMPL % (
                f"{result.error}: {result.error_description or ''}".encode("utf-8", "replace")
            )
            self.send_response(HTTPStatus.BAD_REQUEST)
        else:
            body = _SUCCESS_HTML
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        if self.future is not None and self.loop is not None and not self.future.done():
            self.loop.call_soon_threadsafe(self.future.set_result, result)


async def capture_callback(
    *,
    expected_path: str,
    use_tls: bool,
    timeout_seconds: float = 300.0,
) -> CallbackResult:
    """Run a one-shot loopback server and return the first matching callback.

    Args:
        expected_path: e.g. ``/twitter/callback`` — requests to any other path
            return 404 and do not complete the future.
        use_tls: When True, serves HTTPS with an auto-generated self-signed cert.
            Required for Facebook. Twitter uses plain HTTP.
        timeout_seconds: Give up if the user never finishes in the browser.

    Returns:
        The captured CallbackResult.

    Raises:
        TimeoutError: If the callback never arrives within ``timeout_seconds``.
    """
    settings = get_settings()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[CallbackResult] = loop.create_future()

    handler_cls = type(
        "BoundHandler",
        (_Handler,),
        {"expected_path": expected_path, "future": future, "loop": loop},
    )

    server = HTTPServer((settings.oauth_callback_host, settings.oauth_callback_port), handler_cls)

    if use_tls:
        cert_path, key_path = _ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    thread = Thread(target=server.serve_forever, name="oauth-loopback", daemon=True)
    thread.start()
    try:
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            f"OAuth callback was not received within {timeout_seconds:.0f}s. "
            "Make sure you completed the flow in the browser, or retry."
        ) from e
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
