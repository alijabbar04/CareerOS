"""T-021 recipe for plain HTML application forms (fields addressed by their `name`), such as the ICAEW vacancy pages.

    python -m pipeline.recipes.html_form 61                          # dry run: every non-GET request blocked
    python -m pipeline.recipes.html_form 61 --ali-said "<his words>" # fill for the candidate: he solves any CAPTCHA and presses Submit

The field sheet (`form.yaml`) lists `fields:` by input name, each with `value`, `vault` (identity key) or `file` (an upload,
or for a textarea the prose of an approved final). CareerOS never solves, skips or works around a CAPTCHA: when a form
has one it fills everything in a visible window and waits for the candidate to complete the CAPTCHA and press Submit himself,
watches for the page to change, and records the submission as his. Nothing is ever submitted by this recipe.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pipeline import config, db, drafts, fill, settings, vault as vault_module
from pipeline.recipes.pinpoint import VIEWPORT, RecipeError, load_form, prose

SUCCESS_TEXT = re.compile(r"thank you|application (has been )?(submitted|received|sent)|successfully (applied|submitted)", re.I)
CAPTCHA = 'iframe[src*="recaptcha"], .g-recaptcha, iframe[src*="hcaptcha"], .h-captcha'


@dataclass
class Step:
    kind: str       # text | vault | file | check
    name: str
    value: Any


@dataclass
class Plan:
    url: str
    site: str
    steps: list[Step]
    missing: list[str] = field(default_factory=list)


def build_plan(folder: Path, form: dict[str, Any], tag_of: Callable[[str], str]) -> Plan:
    url = str(form["form_url"])
    plan = Plan(url=url, site=vault_module.canonical_site(url), steps=[])
    for name, spec in (form.get("fields") or {}).items():
        spec = spec or {}
        if str(spec.get("status", "")).startswith("pending"):
            plan.missing.append(name)
            continue
        if spec.get("vault"):
            plan.steps.append(Step("vault", name, spec["vault"]))
        elif spec.get("file"):
            path = Path(spec["file"]) if Path(spec["file"]).is_absolute() else folder / spec["file"]
            if not path.is_file():
                raise RecipeError(f"{name}: {path} does not exist")
            if tag_of(name) == "textarea":
                plan.steps.append(Step("text", name, prose(folder, spec["file"])))
            else:
                plan.steps.append(Step("file", name, path))
        elif isinstance(spec.get("value"), bool):
            plan.steps.append(Step("check", name, spec["value"]))
        elif spec.get("value") is not None:
            plan.steps.append(Step("text", name, str(spec["value"])))
    return plan


def _sel(name: str) -> str:
    return f'[name="{name}"]'


def _tag_of(page: Any) -> Callable[[str], str]:
    return lambda name: page.evaluate("n => { const e = document.querySelector(`[name=\"${n}\"]`); return e ? e.tagName.toLowerCase() : ''; }", name)


def _fill(page: Any, plan: Plan, *, dry_run: bool, vault_filler: Callable[[list[Step]], None] | None) -> list[str]:
    """Fill every step; returns the names that did not take their value (checked in the page, values never returned)."""
    vault_steps = [s for s in plan.steps if s.kind == "vault"]
    for step in plan.steps:
        target = page.locator(_sel(step.name)).first
        if step.kind == "vault":
            if dry_run:
                target.fill(f"[vault {step.value}]")
        elif step.kind == "text":
            target.fill(step.value)
        elif step.kind == "file":
            target.set_input_files(str(step.value))
        elif step.kind == "check":
            target.evaluate("(el, want) => { if (el.checked !== want) el.click(); }", step.value)
    if vault_steps and not dry_run:
        assert vault_filler is not None
        vault_filler(vault_steps)
    checks = [{"name": s.name, "kind": s.kind,
               "expected": s.value if s.kind in ("text", "check") else Path(s.value).name if s.kind == "file" else None}
              for s in plan.steps]
    ok = page.evaluate("""cs => cs.map(c => { const e = document.querySelector(`[name="${c.name}"]`); if (!e) return false;
        if (c.kind === 'vault') return (e.value || '').length > 0;
        if (c.kind === 'check') return e.checked === c.expected;
        if (c.kind === 'file') return e.files.length === 1 && e.files[0].name === c.expected;
        return (e.value || '').replace(/\\r\\n/g, '\\n').trim() === String(c.expected).replace(/\\r\\n/g, '\\n').trim(); })""", checks)
    return [s.name for s, good in zip(plan.steps, ok) if not good]


def dry_run(app_id: int, *, context: Any | None = None, folder: Path | None = None) -> tuple[int, list[str], list[str], int]:
    """Fill the live form headlessly with every non-GET request aborted. Returns (filled, failed, missing, blocked)."""
    from playwright.sync_api import sync_playwright

    folder = folder or drafts.find_folder(app_id)
    if folder is None:
        raise RecipeError(f"application {app_id} has no folder")
    form = load_form(folder)
    blocked: list[str] = []

    def guard(route: Any) -> None:
        if route.request.method != "GET":
            blocked.append(route.request.method)
            route.abort()
        else:
            route.fallback()

    def run(ctx: Any) -> tuple[int, list[str], list[str], int]:
        ctx.route("**/*", guard)
        page = ctx.new_page()
        page.goto(str(form["form_url"]), wait_until="networkidle", timeout=60000)
        plan = build_plan(folder, form, _tag_of(page))
        failed = _fill(page, plan, dry_run=True, vault_filler=None)
        page.close()
        return len(plan.steps) - len(failed), failed, plan.missing, len(blocked)

    if context is not None:
        return run(context)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            return run(browser.new_context(locale="en-GB", viewport=VIEWPORT))
        finally:
            browser.close()


def fill_for_ali(app_id: int, *, ali_said: str, wait_minutes: int = 30, log: Callable[[str], None] = print) -> str:
    """Fill the live form in a visible browser; the candidate completes any CAPTCHA and presses Submit. Returns the outcome."""
    from playwright.sync_api import sync_playwright

    folder = drafts.find_folder(app_id)
    if folder is None:
        raise RecipeError(f"application {app_id} has no folder")
    form = load_form(folder)
    active = settings.Settings()
    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    db.log_event("application", app_id, "fill_for_ali_started", {"recipe": "html_form", "ali_said": ali_said[:500]},
                 source="ali-chat", conn=conn)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(config.BROWSER_PROFILE_DIR), headless=False, locale="en-GB",
                                                   viewport={"width": 1100, "height": 900})
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(str(form["form_url"]), wait_until="networkidle", timeout=60000)
            plan = build_plan(folder, form, _tag_of(page))
            if plan.missing:
                raise RecipeError("still missing: " + ", ".join(plan.missing))

            def vault_filler(steps: list[Step]) -> None:
                fill.fill_fields(page, plan.site, [fill.FieldFromVault(_sel(s.name), s.name, s.value) for s in steps],
                                 active_settings=active, human_confirmed=True, conn=conn)
                fill.restore_field_types_for_validation(page)

            failed = _fill(page, plan, dry_run=False, vault_filler=vault_filler)
            if failed:
                raise RecipeError("did not take its value: " + ", ".join(failed))
            page.locator(_sel(plan.steps[0].name)).first.scroll_into_view_if_needed()
            has_captcha = page.locator(CAPTCHA).count() > 0
            log(f"Filled {len(plan.steps)} fields. " + ("Complete the CAPTCHA and press Submit in the browser window."
                                                       if has_captcha else "Check the form and press Submit in the browser window."))
            start_url, before = page.url, page.inner_text("body")
            deadline = time.monotonic() + wait_minutes * 60
            outcome = "not submitted"
            while time.monotonic() < deadline:
                if page.is_closed():
                    outcome = "window closed"
                    break
                after = page.inner_text("body")
                if (page.url != start_url) or (SUCCESS_TEXT.search(after) and not SUCCESS_TEXT.search(before)):
                    outcome = "submitted" if SUCCESS_TEXT.search(after) or page.url != start_url else "unclear"
                    shot = config.SCREENSHOTS_DIR / f"app{app_id:04d}-submitted-by-ali-{time.strftime('%Y%m%d-%H%M%S')}.png"
                    config.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(shot), full_page=True)
                    break
                time.sleep(2)
        finally:
            ctx.close()
    if outcome == "submitted":
        db.transition_application(app_id, "submitted", "ali", {"recipe": "html_form"}, conn=conn)
        conn.execute("UPDATE applications SET submitted_by='ali', submitted_at=datetime('now') WHERE id=?", (app_id,))
        conn.commit()
    db.log_event("application", app_id, "fill_for_ali_finished", {"outcome": outcome}, source="ali", conn=conn)
    conn.close()
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.recipes.html_form", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("application", type=int)
    parser.add_argument("--ali-said", help="the candidate's words when he asked for the form to be filled with him at the laptop")
    args = parser.parse_args(argv)
    try:
        if not args.ali_said:
            filled, failed, missing, blocked = dry_run(args.application)
            print(f"dry-run: {filled} fields filled and checked; blocked {blocked} outgoing request(s), nothing was sent")
            if failed:
                print("did not take its value: " + ", ".join(failed))
            if missing:
                print("still missing: " + ", ".join(missing))
            return 1 if failed else 0
        print(f"outcome: {fill_for_ali(args.application, ali_said=args.ali_said)}")
        return 0
    except (RecipeError, vault_module.VaultError, fill.FillError) as exc:
        print(f"error: {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
