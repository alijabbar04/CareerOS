"""Minimal Google installed-app OAuth client for CareerOS.

Refresh/access tokens live in the operating-system credential store through
``keyring``.  The downloaded desktop-client JSON remains outside the repository
and is located through ``GMAIL_OAUTH_CLIENT_PATH``.  Nothing in this module logs
tokens, authorisation codes or client credentials.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import keyring
import requests

from pipeline import config, settings

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = (GMAIL_READONLY_SCOPE, GMAIL_MODIFY_SCOPE, CALENDAR_EVENTS_SCOPE)
KEYRING_SERVICE = "CareerOS Google OAuth"


class OAuthSetupError(RuntimeError):
    """Raised when the local OAuth client/token is missing or unusable."""


def account_name() -> str:
    return str(settings.Settings().get("inbox", "read_account", default="default"))


def _client_config(path: Path | None = None) -> dict[str, Any]:
    config.load_env()
    raw_path = path or os.environ.get("GMAIL_OAUTH_CLIENT_PATH")
    if not raw_path:
        raise OAuthSetupError(
            "GMAIL_OAUTH_CLIENT_PATH is not set. Follow docs/GMAIL-OAUTH-SETUP.md first."
        )
    client_path = Path(raw_path).expanduser()
    if not client_path.is_file():
        raise OAuthSetupError(f"OAuth desktop-client file not found: {client_path}")
    try:
        raw = json.loads(client_path.read_text(encoding="utf-8"))
        installed = raw["installed"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OAuthSetupError("OAuth client JSON is not a valid Google desktop-client file") from exc
    if not installed.get("client_id") or not installed.get("token_uri"):
        raise OAuthSetupError("OAuth client JSON is missing client_id or token_uri")
    return installed


def _load_token(account: str | None = None) -> dict[str, Any] | None:
    raw = keyring.get_password(KEYRING_SERVICE, account or account_name())
    if not raw:
        return None
    try:
        token = json.loads(raw)
    except ValueError as exc:
        raise OAuthSetupError("Stored Google OAuth token is invalid JSON") from exc
    return token if isinstance(token, dict) else None


def _save_token(token: dict[str, Any], account: str | None = None) -> None:
    keyring.set_password(
        KEYRING_SERVICE,
        account or account_name(),
        json.dumps(token, separators=(",", ":")),
    )


def token_present(account: str | None = None) -> bool:
    return _load_token(account) is not None


def granted_scopes(account: str | None = None) -> set[str]:
    """Return scope names only; never expose the stored token to a caller."""
    token = _load_token(account)
    if token is None:
        return set()
    scope = token.get("scope")
    if isinstance(scope, str) and scope:
        return set(scope.split())
    return {str(item) for item in (token.get("scopes") or [])}


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorise(
    client_path: Path | None = None, *, timeout_seconds: int = 300,
    account: str | None = None, scopes: tuple[str, ...] = SCOPES,
) -> None:
    """Run Google's browser consent flow and store the resulting token in keyring."""
    client = _client_config(client_path)
    verifier, challenge = _pkce_pair()
    expected_state = secrets.token_urlsafe(32)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            params = parse_qs(urlparse(self.path).query)
            for key in ("code", "state", "error"):
                if params.get(key):
                    result[key] = params[key][0]
            body = (
                b"CareerOS authorisation received. You can close this window."
                if result.get("code")
                else b"CareerOS authorisation failed. Return to the terminal."
            )
            self.send_response(200 if result.get("code") else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.timeout = timeout_seconds
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": expected_state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{client.get('auth_uri', 'https://accounts.google.com/o/oauth2/v2/auth')}?{urlencode(params)}"
    print("Opening Google consent in your browser. Review the requested Gmail/Calendar permissions on Google's page.")
    threading.Thread(target=webbrowser.open, args=(auth_url,), daemon=True).start()
    server.handle_request()
    server.server_close()
    if result.get("error"):
        raise OAuthSetupError(f"Google OAuth was declined: {result['error']}")
    if not result.get("code"):
        raise OAuthSetupError("Google OAuth timed out before the browser callback arrived")
    if not secrets.compare_digest(result.get("state", ""), expected_state):
        raise OAuthSetupError("Google OAuth state did not match; token was not stored")

    payload = {
        "client_id": client["client_id"],
        "code": result["code"],
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client.get("client_secret"):
        payload["client_secret"] = client["client_secret"]
    response = requests.post(client["token_uri"], data=payload, timeout=30)
    response.raise_for_status()
    token = response.json()
    if not token.get("access_token") or not token.get("refresh_token"):
        raise OAuthSetupError("Google did not return both access and refresh tokens")
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
    token["scopes"] = sorted(str(token.get("scope") or " ".join(scopes)).split())
    _save_token(token, account)
    print("Google OAuth token stored in Windows Credential Manager.")


class GoogleOAuthSession:
    """Requests wrapper that refreshes the keyring-backed Google access token."""

    def __init__(self, client_path: Path | None = None, account: str | None = None) -> None:
        self.client = _client_config(client_path)
        self.account = account or account_name()
        self.session = requests.Session()

    def _token(self, *, force_refresh: bool = False) -> dict[str, Any]:
        token = _load_token(self.account)
        if token is None:
            raise OAuthSetupError("No Google OAuth token is stored. Run: python -m pipeline.inbox auth")
        if not force_refresh and token.get("access_token") and float(token.get("expires_at", 0)) > time.time():
            return token
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise OAuthSetupError("Stored Google OAuth token has no refresh token; run auth again")
        payload = {
            "client_id": self.client["client_id"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client.get("client_secret"):
            payload["client_secret"] = self.client["client_secret"]
        response = requests.post(self.client["token_uri"], data=payload, timeout=30)
        response.raise_for_status()
        refreshed = response.json()
        token.update(refreshed)
        token["refresh_token"] = refresh_token
        token["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600)) - 60
        _save_token(token, self.account)
        return token

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", 30)
        token = self._token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token['access_token']}"
        response = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
        if response.status_code == 401:
            token = self._token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token['access_token']}"
            response = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
        return response

    def json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request(method, url, **kwargs)
        response.raise_for_status()
        if not response.content:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Google API response was not a JSON object")
        return data
