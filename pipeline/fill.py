"""T-020 deterministic Playwright fill helper.

Callers pass vault key names, never values. Secret fields are marked and masked
before a value is inserted, so later screenshots remain redacted. The audit log
contains only the site origin, logical field name and vault key.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from pipeline import config, db, inbox, settings, vault as vault_module

MASK_STYLE = """
[data-careeros-secret="true"] {
  -webkit-text-security: disc !important;
  color: transparent !important;
  text-shadow: 0 0 7px rgba(0, 0, 0, 0.85) !important;
  caret-color: transparent !important;
}
"""
MASK_ELEMENT_SCRIPT = """element => {
  element.setAttribute('data-careeros-secret', 'true');
  element.setAttribute('data-careeros-key', 'redacted');
  element.setAttribute('autocomplete', 'off');
  // Playwright aria snapshots (and so Playwright MCP) expose input values even
  // for type=password; hiding the field from the accessibility tree keeps the
  // value out of model transcripts.
  element.setAttribute('aria-hidden', 'true');
  if (element.tagName === 'INPUT') {
    element.setAttribute('data-careeros-original-type', element.getAttribute('type') || 'text');
    try { element.setAttribute('type', 'password'); } catch (_) {}
  }
}"""
RESTORE_TYPES_SCRIPT = """() => {
  for (const element of document.querySelectorAll('[data-careeros-secret="true"]')) {
    const original = element.getAttribute('data-careeros-original-type');
    if (original !== null) element.setAttribute('type', original);
  }
}"""


class FillError(RuntimeError):
    pass


class ApprovalRequired(FillError):
    pass


@dataclass(frozen=True)
class FieldFromVault:
    selector: str
    field: str
    key: str


@dataclass(frozen=True)
class FillResult:
    site: str
    fields: tuple[str, ...]
    screenshot: str | None


def _domain_allowed(domain: str, allowlist: Iterable[str]) -> bool:
    for raw in allowlist:
        try:
            allowed = vault_module.canonical_site(str(raw))
        except vault_module.VaultError:
            continue
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def _playwright_debug_active() -> bool:
    # pw:api / pw:protocol logging and the inspector print fill() arguments.
    debug = os.environ.get("DEBUG", "")
    return "pw:" in debug or bool(os.environ.get("PWDEBUG"))


def enforce_fill_policy(
    site: str,
    *,
    active_settings: settings.Settings,
    human_confirmed: bool,
) -> str:
    domain = vault_module.canonical_site(site)
    override = active_settings.override("fill_identity_fields")
    if override == "never":
        raise FillError("Vault filling is disabled by settings")
    if _playwright_debug_active():
        raise FillError("Playwright debug logging is on; it would record vault values")
    allowlist = active_settings.get("vault", "autofill_domains_allowlist", default=[]) or []
    if human_confirmed:
        return domain
    if config.PAUSED_FLAG.exists():
        raise ApprovalRequired(f"CareerOS is paused; the candidate must approve vault filling on {domain}")
    if override == "auto" and _domain_allowed(domain, allowlist):
        return domain
    raise ApprovalRequired(f"the candidate must approve vault filling on {domain}")


def _verify_page_domain(page: Any, expected_domain: str) -> None:
    try:
        current_url = page.url
        if callable(current_url):
            current_url = current_url()
        current_domain = vault_module.canonical_site(str(current_url))
    except Exception:
        raise FillError("The current browser page has no verifiable web origin") from None
    if current_domain != expected_domain:
        raise FillError("The current browser page does not match the approved site")


def _screenshot_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    config.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        root = config.SCREENSHOTS_DIR.resolve(strict=True)
        candidate = path.resolve(strict=False)
    except OSError:
        raise FillError("Screenshot path is invalid") from None
    if candidate.parent != root:
        raise FillError("Sensitive form screenshots must stay in the local screenshots folder")
    return candidate


def _open_audit_connection(conn: sqlite3.Connection | None) -> tuple[sqlite3.Connection, bool]:
    if conn is not None:
        return conn, False
    active = db.connect()
    db.migrate(active)
    return active, True


def _mask_locator(locator: Any, field: str) -> None:
    try:
        locator.evaluate(MASK_ELEMENT_SCRIPT)
    except Exception:
        raise FillError(f"Could not mask sensitive field {field}") from None


def fill_fields(
    page: Any,
    site: str,
    fields: Iterable[FieldFromVault],
    *,
    vault: vault_module.Vault | None = None,
    active_settings: settings.Settings | None = None,
    human_confirmed: bool = False,
    screenshot_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> FillResult:
    """Fill and permanently mask fields. It never returns or prints a value."""
    active = active_settings or settings.Settings()
    domain = enforce_fill_policy(site, active_settings=active, human_confirmed=human_confirmed)
    _verify_page_domain(page, domain)
    store = vault or vault_module.Vault()
    requested = tuple(fields)
    if not requested:
        raise FillError("No vault fields were requested")
    destination = _screenshot_path(screenshot_path)
    audit_conn, owns_conn = _open_audit_connection(conn)
    filled: list[str] = []
    try:
        try:
            page.add_style_tag(content=MASK_STYLE)
        except Exception:
            raise FillError("Secret-field masking could not be installed") from None
        for item in requested:
            if not item.selector or not item.field:
                raise FillError("A fill mapping is invalid")
            # Re-check per field: a redirect after an earlier fill must not receive the next value.
            _verify_page_domain(page, domain)
            locator = page.locator(item.selector)
            _mask_locator(locator, item.field)
            try:
                store.fill_locator(locator, item.key, site=domain)
            except vault_module.VaultError as exc:
                raise FillError(str(exc)) from None
            db.log_event(
                entity="vault",
                entity_id=0,
                type="field_filled",
                detail={"site": domain, "field": item.field, "key": item.key},
                source="deterministic-fill",
                conn=audit_conn,
            )
            filled.append(item.field)
        if destination is not None:
            try:
                page.screenshot(path=str(destination), full_page=True)
            except Exception:
                raise FillError("Masked form screenshot could not be saved") from None
        return FillResult(domain, tuple(filled), str(destination) if destination else None)
    finally:
        if owns_conn:
            audit_conn.close()


def fill_portal_account_form(
    page: Any,
    site: str,
    *,
    email_selector: str,
    password_selector: str,
    password_confirm_selector: str | None = None,
    vault: vault_module.Vault | None = None,
    active_settings: settings.Settings | None = None,
    human_confirmed: bool = False,
    screenshot_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> FillResult:
    active = active_settings or settings.Settings()
    account_override = active.override("create_accounts")
    if account_override == "never":
        raise FillError("Portal account creation is disabled by settings")
    if account_override != "auto" and not human_confirmed:
        raise ApprovalRequired("the candidate must approve portal account creation")
    # Check the fill policy and the page before any credentials are generated for the site.
    _verify_page_domain(page, enforce_fill_policy(site, active_settings=active, human_confirmed=human_confirmed))
    store = vault or vault_module.Vault()
    keys = vault_module.prepare_portal_credentials(
        site, vault=store, active_settings=active
    )
    mappings = [
        FieldFromVault(email_selector, "account_email", keys.email_key),
        FieldFromVault(password_selector, "account_password", keys.password_key),
    ]
    if password_confirm_selector:
        mappings.append(
            FieldFromVault(password_confirm_selector, "account_password_confirmation", keys.password_key)
        )
    return fill_fields(
        page,
        site,
        mappings,
        vault=store,
        active_settings=active,
        human_confirmed=human_confirmed,
        screenshot_path=screenshot_path,
        conn=conn,
    )


def restore_field_types_for_validation(page: Any) -> None:
    """Restore input semantics before submit while leaving screenshot masking active."""
    try:
        page.evaluate(RESTORE_TYPES_SCRIPT)
    except Exception:
        raise FillError("Sensitive field types could not be restored for validation") from None


def fill_latest_verification_code(
    page: Any,
    site: str,
    *,
    selector: str,
    email_conn: sqlite3.Connection,
    received_after: datetime | None = None,
    active_settings: settings.Settings | None = None,
    human_confirmed: bool = False,
    screenshot_path: Path | None = None,
    audit_conn: sqlite3.Connection | None = None,
) -> FillResult:
    """Move a recent email code straight into the browser without displaying it."""
    active = active_settings or settings.Settings()
    domain = enforce_fill_policy(site, active_settings=active, human_confirmed=human_confirmed)
    _verify_page_domain(page, domain)
    cutoff = received_after or (datetime.now(timezone.utc) - timedelta(minutes=30))
    code = inbox.find_verification_code(site, received_after=cutoff, conn=email_conn)
    if code is None:
        raise FillError("No recent site-matched verification code was found")
    destination = _screenshot_path(screenshot_path)
    active_audit, owns_conn = _open_audit_connection(audit_conn)
    try:
        try:
            page.add_style_tag(content=MASK_STYLE)
            _verify_page_domain(page, domain)
            locator = page.locator(selector)
            _mask_locator(locator, "verification_code")
            locator.fill(code)
        except FillError:
            raise
        except Exception:
            raise FillError("Verification code could not be filled") from None
        finally:
            code = ""
        db.log_event(
            entity="vault",
            entity_id=0,
            type="verification_code_filled",
            detail={
                "site": domain,
                "field": "verification_code",
                "key": "inbox:latest_site_matched_code",
            },
            source="deterministic-fill",
            conn=active_audit,
        )
        if destination is not None:
            try:
                page.screenshot(path=str(destination), full_page=True)
            except Exception:
                raise FillError("Masked form screenshot could not be saved") from None
        return FillResult(domain, ("verification_code",), str(destination) if destination else None)
    finally:
        if owns_conn:
            active_audit.close()
