"""T-016: load settings.yaml (git-ignored -- may name accounts and channels), falling
back to the checked-in settings.example.yaml, with the small accessors the rest of the
pipeline needs instead of re-parsing YAML and re-deriving defaults everywhere.

    python -m pipeline.settings    # print which file is active and a short summary

settings.yaml can change while the system is running (see its own header comment), so
build a fresh Settings() whenever a script starts a unit of work rather than holding
one for the lifetime of a long process.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from pipeline import config

SETTINGS_PATH = config.REPO_ROOT / "settings.yaml"
EXAMPLE_SETTINGS_PATH = config.REPO_ROOT / "settings.example.yaml"

# Fallbacks used only when a key is missing from the active file entirely (so a
# hand-trimmed settings.yaml still behaves safely rather than crashing or, worse,
# silently defaulting to something permissive).
DEFAULT_DRIVER = "claude"
DEFAULT_AUTONOMY_LEVEL = 0            # 0 = observe only, the safe floor
DEFAULT_OVERRIDE = "ask"              # never assume 'auto' for an unlisted action
DEFAULT_CAPS: dict[str, int] = {
    "weekly_applications": 25,
    "daily_linkedin_actions": 10,
    "daily_emails": 10,
    "per_employer_per_cycle": 1,
}


def active_path() -> Path:
    """settings.yaml if the candidate has created one, else the checked-in example file."""
    return SETTINGS_PATH if SETTINGS_PATH.exists() else EXAMPLE_SETTINGS_PATH


def load(path: Path | str | None = None) -> dict[str, Any]:
    """Parse the active settings file into a dict. Returns {} if even the example
    file is missing (should not happen; it is checked into the repository)."""
    p = Path(path) if path is not None else active_path()
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Settings:
    """A loaded settings file plus the accessors callers need."""

    def __init__(self, data: dict[str, Any] | None = None, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else active_path()
        self.data = data if data is not None else load(self.path)

    def get(self, *keys: str, default: Any = None) -> Any:
        """Walk a dotted path of nested keys, e.g. get('notifications', 'discord', 'channels')."""
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def driver(self) -> str:
        """'claude' | 'codex' | 'split' (see plan/PLAN.md 4.7)."""
        return self.data.get("driver", DEFAULT_DRIVER)

    def autonomy_level(self) -> int:
        """0 observe · 1 draft and fill, stop before Submit · 2 submit · 3 autonomous
        (plan/PLAN.md section 5)."""
        return int(self.get("autonomy", "level", default=DEFAULT_AUTONOMY_LEVEL))

    def override(self, action: str) -> str:
        """'auto' | 'ask' | 'never' for a named autonomy override action (e.g.
        'submit_applications', 'send_emails'). Defaults to 'ask' -- the safe choice --
        for any action not listed, rather than assuming 'auto'."""
        overrides = self.get("autonomy", "overrides", default={}) or {}
        return overrides.get(action, DEFAULT_OVERRIDE)

    def cap(self, name: str) -> int | None:
        """An integer cap from the `caps` section (e.g. 'weekly_applications'). Falls
        back to the documented default for the four caps PLAN.md names if the settings
        file omits them, else None for a name neither file nor defaults know about."""
        value = self.get("caps", name, default=None)
        return value if value is not None else DEFAULT_CAPS.get(name)


def current() -> Settings:
    """Convenience: a fresh Settings() reflecting the file on disk right now."""
    return Settings()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.settings", description=__doc__)
    parser.parse_args(argv)
    s = Settings()
    print(f"active file:             {s.path}")
    print(f"driver:                  {s.driver()}")
    print(f"autonomy level:          {s.autonomy_level()}")
    print(f"weekly_applications cap: {s.cap('weekly_applications')}")


if __name__ == "__main__":
    main()
