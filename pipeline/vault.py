r"""T-020 encrypted local vault.

The age X25519 identity is stored by ``keyring`` in Windows Credential Manager;
only age ciphertext and a value-free index are written below
``%USERPROFILE%\CareerOS-data\vault``.  There is deliberately no CLI command that
prints a value.  Browser code asks this module to fill a locator by key name.

Typical commands::

    python -m pipeline.vault init
    python -m pipeline.vault set identity.phone --kind identity --field phone
    python -m pipeline.vault portal https://example.wd3.myworkdayjobs.com
    python -m pipeline.vault list
    python -m pipeline.vault export --confirm-plaintext
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import stat
import string
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import keyring
from openpyxl import Workbook
from pyrage import decrypt as age_decrypt
from pyrage import encrypt as age_encrypt
from pyrage import x25519

from pipeline import config, db, settings

INDEX_VERSION = 1
RECORD_VERSION = 1
KEYRING_SERVICE = "CareerOS Vault"
KEYRING_ACCOUNT = "age-x25519-identity-v1"
MAX_INDEX_BYTES = 1024 * 1024
MAX_VALUE_BYTES = 32 * 1024
MAX_CIPHERTEXT_BYTES = 128 * 1024
KEY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")
SAFE_FILE_RE = re.compile(r"^[0-9a-f]{64}\.age$")
ALLOWED_METADATA = {"kind", "site", "field"}


class VaultError(RuntimeError):
    """Fixed, redacted vault error safe to display."""


class CredentialStore(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class VaultRecordInfo:
    key: str
    kind: str
    site: str | None
    field: str | None
    updated_at: str


@dataclass(frozen=True)
class PortalAccountKeys:
    site: str
    email_key: str
    password_key: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_site(value: str) -> str:
    raw = value.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise VaultError("Site is invalid")
    return host


def _validate_key(key: str) -> str:
    if not KEY_RE.fullmatch(key):
        raise VaultError("Vault key is invalid")
    return key


def _normalise_metadata(metadata: dict[str, str] | None) -> dict[str, str]:
    source = metadata or {}
    if set(source) - ALLOWED_METADATA:
        raise VaultError("Vault metadata contains unsupported fields")
    result: dict[str, str] = {}
    for name, raw_value in source.items():
        value = str(raw_value).strip()
        if not value or len(value) > 253:
            raise VaultError("Vault metadata is invalid")
        if name == "site":
            value = canonical_site(value)
        elif name in {"kind", "field"} and not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", value):
            raise VaultError("Vault metadata is invalid")
        result[name] = value
    result.setdefault("kind", "identity")
    return result


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        raise VaultError("Vault data could not be stored") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _restrict_windows_acl(path: Path) -> None:
    """Limit the vault directory to the current user, SYSTEM and Administrators."""
    if os.name != "nt":
        raise VaultError("The production vault requires Windows key protection")
    identity = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    principal = f"{domain}\\{identity}" if domain and identity else identity
    if not principal:
        raise VaultError("The Windows user identity is unavailable")
    result = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{principal}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise VaultError("The vault directory permissions could not be secured")


class Vault:
    def __init__(
        self,
        root: Path | str | None = None,
        *,
        credential_store: CredentialStore | None = None,
        enforce_windows_backend: bool = True,
        enforce_acl: bool = True,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self.root = Path(root) if root is not None else config.SECRET_VAULT_DIR
        self.index_path = self.root / "index.json"
        self.credential_store = credential_store or keyring
        self.enforce_windows_backend = enforce_windows_backend
        self.enforce_acl = enforce_acl
        self.now = now

    def _prepare_root(self) -> None:
        if self.root.exists() and self.root.is_symlink():
            raise VaultError("The vault directory cannot be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.enforce_acl:
            _restrict_windows_acl(self.root)

    def _check_backend(self) -> None:
        if not self.enforce_windows_backend:
            return
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if os.name != "nt" or not module.startswith("keyring.backends.Windows"):
            raise VaultError("Windows Credential Manager is unavailable")

    def _identity(self, *, create: bool) -> x25519.Identity:
        self._check_backend()
        try:
            encoded = self.credential_store.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        except Exception:
            raise VaultError("The protected vault identity is unavailable") from None
        if encoded is None:
            if not create:
                raise VaultError("The vault has not been initialised")
            identity = x25519.Identity.generate()
            try:
                self.credential_store.set_password(
                    KEYRING_SERVICE, KEYRING_ACCOUNT, str(identity)
                )
            except Exception:
                raise VaultError("The protected vault identity could not be stored") from None
            return identity
        try:
            return x25519.Identity.from_str(encoded)
        except Exception:
            raise VaultError("The protected vault identity is invalid") from None

    def initialise(self) -> bool:
        self._prepare_root()
        existed = False
        try:
            existed = self.credential_store.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) is not None
        except Exception:
            raise VaultError("The protected vault identity is unavailable") from None
        self._identity(create=True)
        if not self.index_path.exists():
            self._write_index({"version": INDEX_VERSION, "records": {}})
        else:
            self._read_index()
        return not existed

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"version": INDEX_VERSION, "records": {}}
        if self.index_path.is_symlink():
            raise VaultError("The vault index is invalid")
        try:
            raw = self.index_path.read_bytes()
            if len(raw) > MAX_INDEX_BYTES:
                raise ValueError("oversized")
            value = json.loads(raw)
        except (OSError, ValueError, TypeError):
            raise VaultError("The vault index is invalid") from None
        if not isinstance(value, dict) or set(value) != {"version", "records"}:
            raise VaultError("The vault index is invalid")
        if value["version"] != INDEX_VERSION or not isinstance(value["records"], dict):
            raise VaultError("The vault index is invalid")
        for key, record in value["records"].items():
            _validate_key(str(key))
            if not isinstance(record, dict) or set(record) != {
                "file", "kind", "site", "field", "updated_at"
            }:
                raise VaultError("The vault index is invalid")
            if record["file"] != self._filename(str(key)) or not SAFE_FILE_RE.fullmatch(record["file"]):
                raise VaultError("The vault index is invalid")
            if not isinstance(record["kind"], str) or not isinstance(record["updated_at"], str):
                raise VaultError("The vault index is invalid")
            for optional in ("site", "field"):
                if record[optional] is not None and not isinstance(record[optional], str):
                    raise VaultError("The vault index is invalid")
            metadata = {"kind": record["kind"]}
            metadata.update(
                {name: record[name] for name in ("site", "field") if record[name] is not None}
            )
            if (
                _normalise_metadata(metadata) != metadata
                or not record["updated_at"]
                or len(record["updated_at"]) > 64
            ):
                raise VaultError("The vault index is invalid")
        return value

    def _write_index(self, value: dict[str, Any]) -> None:
        encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_INDEX_BYTES:
            raise VaultError("The vault index is too large")
        _atomic_write(self.index_path, encoded)

    @staticmethod
    def _filename(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest() + ".age"

    def _record_path(self, key: str) -> Path:
        return self.root / self._filename(key)

    def set(self, key: str, value: str, *, metadata: dict[str, str] | None = None) -> None:
        key = _validate_key(key)
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_VALUE_BYTES:
            raise VaultError("Vault value is empty or too large")
        normalised = _normalise_metadata(metadata)
        self.initialise()
        identity = self._identity(create=False)
        timestamp = self.now()
        payload = {
            "version": RECORD_VERSION,
            "key": key,
            "value": value,
            "metadata": normalised,
            "updated_at": timestamp,
        }
        try:
            plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ciphertext = age_encrypt(plaintext, [identity.to_public()])
        except Exception:
            raise VaultError("Vault value could not be encrypted") from None
        if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
            raise VaultError("Encrypted vault value is too large")
        _atomic_write(self._record_path(key), ciphertext)
        index = self._read_index()
        index["records"][key] = {
            "file": self._filename(key),
            "kind": normalised["kind"],
            "site": normalised.get("site"),
            "field": normalised.get("field"),
            "updated_at": timestamp,
        }
        self._write_index(index)

    def _read_value(self, key: str) -> str:
        key = _validate_key(key)
        index = self._read_index()
        entry = index["records"].get(key)
        if not entry:
            raise VaultError("Vault key was not found")
        path = self._record_path(key)
        if path.is_symlink():
            raise VaultError("Encrypted vault record is invalid")
        try:
            ciphertext = path.read_bytes()
        except OSError:
            raise VaultError("Encrypted vault record is unavailable") from None
        if not ciphertext or len(ciphertext) > MAX_CIPHERTEXT_BYTES:
            raise VaultError("Encrypted vault record is invalid")
        identity = self._identity(create=False)
        try:
            plaintext = age_decrypt(ciphertext, [identity])
            if len(plaintext) > MAX_VALUE_BYTES + 4096:
                raise ValueError("oversized")
            payload = json.loads(plaintext)
        except Exception:
            raise VaultError("Encrypted vault record could not be authenticated") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "key", "value", "metadata", "updated_at"}
            or payload["version"] != RECORD_VERSION
            or payload["key"] != key
            or not isinstance(payload["value"], str)
            or not payload["value"]
            or len(payload["value"].encode("utf-8")) > MAX_VALUE_BYTES
            or not isinstance(payload["metadata"], dict)
            or _normalise_metadata(payload["metadata"]) != payload["metadata"]
        ):
            raise VaultError("Encrypted vault record is invalid")
        indexed_metadata = {"kind": entry["kind"]}
        indexed_metadata.update(
            {name: entry[name] for name in ("site", "field") if entry[name] is not None}
        )
        if payload["metadata"] != indexed_metadata or payload["updated_at"] != entry["updated_at"]:
            raise VaultError("Encrypted vault record is invalid")
        return payload["value"]

    def fill_locator(self, locator: Any, key: str, *, site: str | None = None) -> None:
        """Decrypt directly into a Playwright-like locator without returning it.

        A record stored with a ``site`` (portal credentials) fills only on exactly
        that site, so a recipe or model cannot send one portal's password to another.
        """
        entry = self._read_index()["records"].get(_validate_key(key))
        if entry and entry["site"] is not None:
            if site is None or canonical_site(site) != entry["site"]:
                raise VaultError(f"Vault key {key} is bound to another site")
        value = self._read_value(key)
        try:
            locator.fill(value)
        except Exception:
            raise VaultError(f"Field could not be filled from vault key {key}") from None
        finally:
            value = ""

    def contains(self, key: str) -> bool:
        key = _validate_key(key)
        return key in self._read_index()["records"]

    def list_records(self) -> list[VaultRecordInfo]:
        index = self._read_index()
        return [
            VaultRecordInfo(
                key=key,
                kind=record["kind"],
                site=record["site"],
                field=record["field"],
                updated_at=record["updated_at"],
            )
            for key, record in sorted(index["records"].items())
        ]

    def delete(self, key: str) -> bool:
        key = _validate_key(key)
        index = self._read_index()
        if key not in index["records"]:
            return False
        index["records"].pop(key)
        self._write_index(index)
        try:
            self._record_path(key).unlink(missing_ok=True)
        except OSError:
            raise VaultError("Encrypted vault record could not be removed") from None
        return True

    def export_credentials(
        self,
        *,
        path: Path | None = None,
        conn: Any | None = None,
    ) -> Path:
        """Create the explicitly requested plaintext XLSX inside the protected vault."""
        self._prepare_root()
        destination = path or (self.root / "credentials-export.xlsx")
        try:
            resolved_root = self.root.resolve(strict=True)
            resolved_parent = destination.parent.resolve(strict=True)
        except OSError:
            raise VaultError("Credential export path is invalid") from None
        if resolved_parent != resolved_root:
            raise VaultError("Credential exports must stay inside the protected vault folder")

        grouped: dict[str, dict[str, str]] = {}
        for record in self.list_records():
            if record.kind != "portal_credential" or not record.site or not record.field:
                continue
            grouped.setdefault(record.site, {})[record.field] = self._read_value(record.key)

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Portal credentials"
        sheet.append(["Site", "Email", "Username", "Password"])
        for site, values in sorted(grouped.items()):
            sheet.append(
                [site, values.get("email", ""), values.get("username", ""), values.get("password", "")]
            )
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:D{max(1, sheet.max_row)}"
        for column, width in {"A": 42, "B": 36, "C": 30, "D": 34}.items():
            sheet.column_dimensions[column].width = width
        workbook.properties.creator = "Candidate Name"

        temporary = self.root / f".credentials-export-{uuid.uuid4().hex}.xlsx"
        try:
            workbook.save(temporary)
            with temporary.open("rb+") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, stat.S_IREAD | stat.S_IWRITE)
        except PermissionError:
            raise VaultError("Credential export is open in another application") from None
        except OSError:
            raise VaultError("Credential export could not be created") from None
        finally:
            workbook.close()
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        owns_conn = conn is None
        audit_conn = conn or db.connect()
        try:
            db.migrate(audit_conn)
            db.log_event(
                entity="vault",
                entity_id=0,
                type="credentials_exported",
                detail={"record_count": len(grouped)},
                source="ali",
                conn=audit_conn,
            )
        finally:
            if owns_conn:
                audit_conn.close()
        return destination


def _password() -> str:
    groups = (
        string.ascii_lowercase,
        string.ascii_uppercase,
        string.digits,
        "!@#%+=_-",
    )
    characters = [secrets.choice(group) for group in groups]
    alphabet = "".join(groups)
    characters.extend(secrets.choice(alphabet) for _ in range(20))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def prepare_portal_credentials(
    site: str,
    *,
    vault: Vault | None = None,
    active_settings: settings.Settings | None = None,
) -> PortalAccountKeys:
    active = active_settings or settings.Settings()
    store = vault or Vault()
    store.initialise()
    domain = canonical_site(site)
    slug = domain.replace(".", "-")
    email_key = f"portal.{slug}.email"
    password_key = f"portal.{slug}.password"
    email = str(active.get("vault", "applications_email", default="") or "").strip()
    if not email or "@" not in email:
        raise VaultError("Set vault.applications_email in settings.yaml first")
    policy = str(active.get("vault", "password_policy", default="unique"))
    if policy not in {"unique", "single"}:
        raise VaultError("vault.password_policy must be unique or single")
    if not store.contains(email_key):
        store.set(
            email_key,
            email,
            metadata={"kind": "portal_credential", "site": domain, "field": "email"},
        )
    if not store.contains(password_key):
        if policy == "single":
            shared_key = "portal.shared.password"
            if not store.contains(shared_key):
                store.set(
                    shared_key,
                    _password(),
                    metadata={"kind": "portal_shared", "field": "password"},
                )
            password = store._read_value(shared_key)
        else:
            password = _password()
        store.set(
            password_key,
            password,
            metadata={"kind": "portal_credential", "site": domain, "field": "password"},
        )
        password = ""
    return PortalAccountKeys(domain, email_key, password_key)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipeline.vault", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="initialise the keyring identity and encrypted vault")
    set_parser = sub.add_parser("set", help="prompt privately for and store a value")
    set_parser.add_argument("key")
    set_parser.add_argument("--kind", default="identity")
    set_parser.add_argument("--site")
    set_parser.add_argument("--field")
    sub.add_parser("list", help="list key names and non-sensitive metadata only")
    delete_parser = sub.add_parser("delete", help="delete one encrypted record")
    delete_parser.add_argument("key")
    delete_parser.add_argument("--confirm", action="store_true")
    portal = sub.add_parser("portal", help="prepare email/password keys for a portal")
    portal.add_argument("site")
    export = sub.add_parser("export", help="create a plaintext XLSX inside the vault folder")
    export.add_argument("--confirm-plaintext", action="store_true")
    sub.add_parser("status", help="show record counts without values")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = Vault()
    try:
        if args.command == "init":
            created = store.initialise()
            print("Vault initialised." if created else "Vault already initialised.")
        elif args.command == "set":
            first = getpass.getpass("Value: ")
            second = getpass.getpass("Confirm value: ")
            if not secrets.compare_digest(first, second):
                raise VaultError("Values did not match")
            metadata = {"kind": args.kind}
            if args.site:
                metadata["site"] = args.site
            if args.field:
                metadata["field"] = args.field
            store.set(args.key, first, metadata=metadata)
            first = second = ""
            print(f"Stored vault key: {args.key}")
        elif args.command == "list":
            records = store.list_records()
            if not records:
                print("Vault contains no records.")
            for record in records:
                suffix = f" site={record.site}" if record.site else ""
                print(f"{record.key} kind={record.kind}{suffix}")
        elif args.command == "delete":
            if not args.confirm:
                raise VaultError("Deletion requires --confirm")
            print("Deleted." if store.delete(args.key) else "Vault key was not found.")
        elif args.command == "portal":
            keys = prepare_portal_credentials(args.site, vault=store)
            print(f"Portal keys ready for {keys.site}: {keys.email_key}, {keys.password_key}")
        elif args.command == "export":
            if not args.confirm_plaintext:
                raise VaultError("Plaintext export requires --confirm-plaintext")
            path = store.export_credentials()
            print(f"Credential export created inside the protected vault: {path.name}")
        elif args.command == "status":
            records = store.list_records()
            print(f"Vault records: {len(records)}")
            print(f"Portal sites: {len({r.site for r in records if r.site})}")
    except VaultError as exc:
        print(f"error: {exc}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
