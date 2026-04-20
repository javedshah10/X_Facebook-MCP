"""Centralized configuration.

All knobs live here. Values come from, in order: environment variables, a local
``.env`` file (if present), then hard-coded defaults. Nothing secret is ever
logged.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Twitter / X ---
    twitter_client_id: str = Field(default="", description="X OAuth 2.0 client ID")
    twitter_client_secret: str = Field(
        default="",
        description=(
            "X OAuth 2.0 client secret. Required only for confidential clients; "
            "public clients with PKCE can leave it blank."
        ),
    )

    # --- Facebook ---
    facebook_app_id: str = Field(default="", description="Meta App ID")
    facebook_app_secret: str = Field(default="", description="Meta App Secret")

    # --- OAuth callback ---
    oauth_callback_host: str = Field(default="localhost")
    oauth_callback_port: int = Field(default=8765, ge=1024, le=65535)

    # --- Storage ---
    social_mcp_store_path: Path | None = Field(default=None)
    social_mcp_fernet_key: str | None = Field(
        default=None,
        description="If set, overrides OS keyring for the token-encryption key.",
    )

    @property
    def store_path(self) -> Path:
        """Absolute path to the encrypted token store."""
        if self.social_mcp_store_path is not None:
            return self.social_mcp_store_path
        return Path.home() / ".social_mcp" / "tokens.enc"

    @property
    def twitter_redirect_uri(self) -> str:
        return f"http://{self.oauth_callback_host}:{self.oauth_callback_port}/twitter/callback"

    @property
    def facebook_redirect_uri(self) -> str:
        # Facebook allows plain http://localhost for development-mode apps.
        # No TLS cert needed — zero browser warnings in Chrome, Edge, Firefox.
        # Works on corporate laptops without admin rights or mkcert.
        # Only switch to https if deploying a production/live app.
        return f"http://{self.oauth_callback_host}:{self.oauth_callback_port}/facebook/callback"

    def require_twitter(self) -> None:
        if not self.twitter_client_id:
            raise RuntimeError(
                "TWITTER_CLIENT_ID is not set. Create an X app at "
                "https://developer.x.com and add the client ID to your .env file."
            )

    def require_facebook(self) -> None:
        if not (self.facebook_app_id and self.facebook_app_secret):
            raise RuntimeError(
                "FACEBOOK_APP_ID and/or FACEBOOK_APP_SECRET are not set. Create a Meta "
                "app at https://developers.facebook.com and add the credentials to "
                "your .env file."
            )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
