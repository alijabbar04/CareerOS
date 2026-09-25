"""T-021 Pinpoint recipe: fill a Pinpoint application form from the application's `form.yaml`.

Two modes here, and neither presses Submit. Submission by the system happens only through `pipeline.submit`, which
checks the candidate's approval record, the recall window and the content hash before calling `submit_application` (T-022):

    python -m pipeline.recipes.pinpoint 55            # dry run (default)
    python -m pipeline.recipes.pinpoint 55 --fill     # the candidate at the keyboard

Dry run: headless, and every request other than a plain GET is aborted, so nothing can be sent or saved. Vault fields get
placeholders instead of real values. It fills the whole form, checks every field took its value and saves a screenshot.
Safe to run at any time.

Fill: opens a visible browser on the dedicated CareerOS profile, fills everything including the vault fields (masked by
`pipeline.fill`), saves a masked screenshot and leaves the window open for the candidate to review, tick the consent box and press
Submit himself (autonomy level 1). It needs a person at the keyboard: without an interactive terminal it refuses.

Pinpoint renders multiple-choice questions as a custom widget on desktop; the native mobile `<select>` beside it is only
usable in a narrow window, so the recipe always uses a phone-width viewport (see GOTCHAS 2026-09-23).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from pipeline import config, db, drafts, fill, settings, vault as vault_module

PREFIX = "application_form[application]"
VIEWPORT = {"width": 420, "height": 900}
MONTHS = ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
DIVERSITY_IDS = {
    "gender_identity": "Gender Identity",
    "sexual_orientation": "Sexual Orientation",
    "religion": "Religion",
    "age_bracket": "Age Bracket",
    "disability": "Disability",
    "ethnicity": "Ethnicity",
}
# A challenge the candidate must solve himself. Only visible elements count: invisible score-based widgets are not a stop.
CAPTCHA = ('iframe[src*="recaptcha/api2/bframe"], iframe[src*="hcaptcha"], .g-recaptcha, .h-captcha, '
           'iframe[title*="challenge" i], #challenge-form')

QUESTION_TYPES_JS = """() => { const o = {}; for (const h of document.querySelectorAll('input[type=hidden]')) {
  const m = (h.name || '').match(/answers_attributes\\]\\[(\\d+)\\]\\[question_type\\]/); if (m) o[m[1]] = h.value; }
  return o; }"""

# Returns one boolean per check and never a field's value, so vault values cannot leak into Python output.
CHECK_JS = """checks => checks.map(c => {
  if (c.kind === 'date') {   // target is the answer's name prefix, not a selector
    const part = p => document.querySelector(`select[name="${c.target}[date_answer(${p})]"]`);
    const txt = s => s && s.selectedOptions[0] ? s.selectedOptions[0].text.trim() : '';
    return txt(part('3i')) === c.expected[0] && txt(part('2i')) === c.expected[1] && txt(part('1i')) === c.expected[2];
  }
  if (c.kind === 'file') {   // a real Rails direct upload replaces the file input with a hidden input holding the blob id
    const f = document.querySelector(c.target);
    if (f && f.files.length === 1 && f.files[0].name === c.expected) return true;
    const h = document.querySelector(c.target.replace('[type=file]', '[type=hidden]'));
    return !!(h && h.value);
  }
  const el = document.querySelector(c.target);
  if (!el) return false;
  if (c.kind === 'vault') return (el.value || '').length > 0;
  if (c.kind === 'select') return !!el.selectedOptions[0] && el.selectedOptions[0].text.trim() === c.expected;
  if (c.kind === 'radio' || c.kind === 'check') return el.checked === true;
  const norm = s => (s || '').replace(/\\r\\n/g, '\\n').trim();
  return norm(el.value) === norm(c.expected);
})"""


class RecipeError(RuntimeError):
    pass


class Parked(RecipeError):
    """Stop rule: a CAPTCHA, verification step or platform warning. Park, tell the candidate, move on."""


@dataclass
class Step:
    kind: str            # text | select | date | radio | file | check | vault
    target: str          # CSS selector (for dates, the answer's name prefix)
    value: Any           # text, option label, date, bool, path, or vault key
    label: str           # what the step is called in logs; never the value
    vault_field: str | None = None


@dataclass
class Plan:
    url: str
    site: str
    steps: list[Step]
    missing: list[str] = field(default_factory=list)


@dataclass
class Result:
    mode: str
    filled: int
    missing: list[str]
    failed: list[str]
    screenshot: str | None
    blocked_requests: int = 0


def load_form(folder: Path) -> dict[str, Any]:
    path = folder / "form.yaml"
    if not path.is_file():
        raise RecipeError(f"{path} does not exist; build the field sheet first")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def prose(folder: Path, rel: str) -> str:
    """The text of a draft or final answer, without front matter or the citation map."""
    _meta, body = drafts.read_front_matter(folder / rel)
    return body.split("## Citations")[0].strip()


def _pending(spec: dict[str, Any]) -> bool:
    return str(spec.get("status", "")).startswith("pending")


def build_plan(folder: Path, form: dict[str, Any], question_types: dict[str, str], *, tick_declarations: bool) -> Plan:
    url = str(form["form_url"])
    plan = Plan(url=url, site=vault_module.canonical_site(url), steps=[])

    for key, spec in (form.get("personal") or {}).items():
        spec = spec or {}
        if spec.get("vault"):
            plan.steps.append(Step("vault", f'input[name="{PREFIX}[{key}]"]', spec["vault"], key, vault_field=key))
        elif spec.get("value") is None:
            if _pending(spec):
                plan.missing.append(key)
        elif key == "country":
            # Pinpoint gives a search box the same id as the real <select> (here and for diversity), so every select names the tag
            plan.steps.append(Step("select", f'select[id="{PREFIX}[country]"]', str(spec["value"]), key))
        else:
            plan.steps.append(Step("text", f'input[name="{PREFIX}[{key}]"]', str(spec["value"]), key))

    summary = (form.get("profile") or {}).get("summary") or {}
    if summary.get("file"):
        plan.steps.append(Step("text", f'textarea[name="{PREFIX}[summary]"]', prose(folder, summary["file"]), "summary"))
    cv = (form.get("profile") or {}).get("cv") or {}
    if cv.get("file"):  # Pinpoint's profile-level CV upload (Firm C, 2026-09-24); answers of type document are handled below
        path = Path(cv["file"]) if Path(cv["file"]).is_absolute() else folder / cv["file"]
        if not path.is_file():
            raise RecipeError(f"cv: upload file {path} does not exist")
        plan.steps.append(Step("file", f'input[type=file][name="{PREFIX}[cv]"]', path, "cv"))

    for number, spec in (form.get("answers") or {}).items():
        spec, n = spec or {}, str(number)
        label = f"answer {n}"
        base = f"{PREFIX}[answers_attributes][{n}]"
        value, file = spec.get("value"), spec.get("file")
        if value is None and file is None:
            if _pending(spec):
                plan.missing.append(label)
            continue
        qtype = question_types.get(n) or spec.get("type")  # conditional questions (revealed by an earlier answer) are absent from the page's type map, so form.yaml names their type
        if qtype in ("short_text", "long_text"):
            text = prose(folder, file) if file else str(value)
            plan.steps.append(Step("text", f'[name="{base}[text_answer]"]', text, label))
        elif qtype == "multiple_choice":
            plan.steps.append(Step("select", f'select[id="application_form_application_answers_attributes_{n}_mobile_select"]', str(value), label))
        elif qtype == "date":
            day = value if isinstance(value, date) else date.fromisoformat(str(value))
            plan.steps.append(Step("date", base, day, label))
        elif qtype == "boolean":
            yes = str(value).strip().lower() in ("yes", "true")
            plan.steps.append(Step("radio", f'input[type=radio][name="{base}[boolean_answer]"][value="{str(yes).lower()}"]', yes, label))
        elif qtype == "document":
            path = Path(file) if Path(file).is_absolute() else folder / file
            if not path.is_file():
                raise RecipeError(f"{label}: upload file {path} does not exist")
            plan.steps.append(Step("file", f'input[type=file][name="{base}[document]"]', path, label))
        else:
            raise RecipeError(f"{label}: unknown Pinpoint question type {qtype!r}")

    for key, choice in (form.get("diversity") or {}).items():
        if choice is None:
            continue
        if key not in DIVERSITY_IDS:
            raise RecipeError(f"unknown diversity question {key!r}")
        plan.steps.append(Step("select", f'select[id="application_form_equality_monitoring_{DIVERSITY_IDS[key]}"]', str(choice), key))

    if tick_declarations and (form.get("declarations") or {}).get("process_information"):
        plan.steps.append(Step("check", 'input[type=checkbox][name="application[process_information]"]', True, "process_information"))
    return plan


def _apply(page: Any, step: Step) -> None:
    if step.kind == "text":
        page.fill(step.target, step.value)
    elif step.kind == "select":
        page.locator(step.target).select_option(label=step.value)
    elif step.kind == "date":
        d = step.value
        for part, text in (("3i", str(d.day)), ("2i", MONTHS[d.month]), ("1i", str(d.year))):
            page.locator(f'select[name="{step.target}[date_answer({part})]"]').select_option(label=text)
    elif step.kind in ("radio", "check"):
        # Pinpoint hides the real inputs behind styled labels, so a coordinate click can hit the wrong element once the
        # layout shifts; the element's own click() fires the same input/change events without depending on position.
        page.locator(step.target).evaluate("el => { if (!el.checked) el.click(); }")
    elif step.kind == "file":
        page.set_input_files(step.target, str(step.value))
    else:
        raise RecipeError(f"unexpected step kind {step.kind}")


def _check_payload(step: Step) -> dict[str, Any]:
    expected: Any = None
    if step.kind in ("text", "select"):
        expected = step.value
    elif step.kind == "date":
        expected = [str(step.value.day), MONTHS[step.value.month], str(step.value.year)]
    elif step.kind == "file":
        expected = Path(step.value).name
    return {"kind": step.kind, "target": step.target, "expected": expected}


def park_if_challenged(page: Any) -> None:
    if page.locator(CAPTCHA).filter(visible=True).count():
        raise Parked("A CAPTCHA or verification step is showing; the candidate must deal with it himself")


def _screenshot_path(app_id: int, mode: str) -> Path:
    config.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return config.SCREENSHOTS_DIR / f"app{app_id:04d}-{mode}-{datetime.now():%Y%m%d-%H%M%S}.png"


def fill_page(page: Any, plan: Plan, *, mode: str, app_id: int, vault_filler: Callable[[list[Step]], None] | None = None) -> Result:
    """Fill the open form page from the plan and check every field. Never presses Submit."""
    park_if_challenged(page)
    vault_steps = [s for s in plan.steps if s.kind == "vault"]
    for step in plan.steps:
        if step.kind == "vault":
            if mode == "dry-run":
                page.fill(step.target, f"[vault {step.value}]")  # placeholder; real values only in fill mode
            continue
        try:
            _apply(page, step)
        except Exception as exc:  # non-vault steps hold no secrets, so the first line of the cause is safe to show
            reason = (str(exc).splitlines() or [type(exc).__name__])[0][:200]
            raise RecipeError(f"{step.label} could not be filled: {reason}") from None
        if step.kind == "file" and mode != "dry-run":
            # The file goes to Pinpoint's storage as soon as it is attached; wait for the blob id before going on,
            # so Submit is never pressed mid-upload (the dry run blocks the upload, so it keeps the file input).
            try:
                page.wait_for_selector(step.target.replace("[type=file]", "[type=hidden]"), state="attached", timeout=90000)
            except Exception:
                raise RecipeError(f"{step.label}: the upload did not finish") from None
    if vault_steps and mode != "dry-run":
        if vault_filler is None:
            raise RecipeError("fill mode needs a vault filler")
        vault_filler(vault_steps)
    park_if_challenged(page)
    results = page.evaluate(CHECK_JS, [_check_payload(s) for s in plan.steps])
    failed = [s.label for s, ok in zip(plan.steps, results) if not ok]
    shot = _screenshot_path(app_id, mode)
    page.screenshot(path=str(shot), full_page=True)
    return Result(mode, len(plan.steps) - len(failed), plan.missing, failed, str(shot))


def _question_types(page: Any) -> dict[str, str]:
    """The page's answers_attributes question types. Pinpoint renders them after the initial HTML, so wait for at least one
    (the 23:12 run on 2026-09-24 read an empty map at networkidle and failed with 'unknown question type None')."""
    try:
        page.wait_for_selector('input[type=hidden][name*="[question_type]"]', state="attached", timeout=30000)
    except Exception:
        park_if_challenged(page)
        raise RecipeError(f"no Pinpoint questions rendered on {page.url} (title {page.title()!r}); the form may have changed or the page did not load")
    return page.evaluate(QUESTION_TYPES_JS)


def dry_run(app_id: int, *, context: Any | None = None, folder: Path | None = None) -> Result:
    """Fill the live form headlessly with every non-GET request aborted. `context` lets tests supply routed pages."""
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

    def run(ctx: Any) -> Result:
        ctx.route("**/*", guard)
        page = ctx.new_page()
        page.goto(str(form["form_url"]), wait_until="networkidle", timeout=60000)
        plan = build_plan(folder, form, _question_types(page), tick_declarations=True)
        result = fill_page(page, plan, mode="dry-run", app_id=app_id)
        result.blocked_requests = len(blocked)
        page.close()
        return result

    if context is not None:
        return run(context)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            return run(browser.new_context(locale="en-GB", viewport=VIEWPORT))
        finally:
            browser.close()


def fill_for_ali(app_id: int, *, confirm: Callable[[str], str] = input) -> Result:
    """Level 1: fill the live form in a visible browser and leave it open for the candidate to check and submit himself."""
    from playwright.sync_api import sync_playwright

    folder = drafts.find_folder(app_id)
    if folder is None:
        raise RecipeError(f"application {app_id} has no folder")
    active = settings.Settings()
    if int(active.get("autonomy", "level", default=1) or 0) < 1:
        raise RecipeError("autonomy level 0 (observe) does not allow filling forms")
    form = load_form(folder)
    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(config.BROWSER_PROFILE_DIR), headless=False, viewport=VIEWPORT, locale="en-GB")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(str(form["form_url"]), wait_until="networkidle", timeout=60000)
            plan = build_plan(folder, form, _question_types(page), tick_declarations=False)

            def vault_filler(steps: list[Step]) -> None:
                fill.fill_fields(
                    page, plan.site,
                    [fill.FieldFromVault(s.target, s.vault_field or s.label, s.value) for s in steps],
                    active_settings=active, human_confirmed=True,
                )
                fill.restore_field_types_for_validation(page)

            result = fill_page(page, plan, mode="fill", app_id=app_id, vault_filler=vault_filler)
            db.log_event("application", app_id, "form_filled",
                         {"recipe": "pinpoint", "mode": "fill", "filled": result.filled, "missing": result.missing,
                          "failed": result.failed, "screenshot": Path(result.screenshot or "").name},
                         source="pinpoint-recipe")
            _report(result)
            confirm("Check the form in the browser window, tick the consent box and press Submit yourself. "
                    "Press Enter here when you have finished to close the window. ")
            return result
        finally:
            ctx.close()


SUBMIT_BUTTON = re.compile(r"submit application", re.I)
SUCCESS_TEXT = re.compile(r"thank you|application (has been )?(submitted|received)|successfully (applied|submitted)", re.I)
FORM_ERRORS = '.error, [role="alert"], .field_with_errors, .invalid-feedback, .form-error'


@dataclass
class Submission:
    outcome: str          # submitted | failed | unclear
    url: str
    screenshot: str
    errors: int


def submit_application(app_id: int, *, folder: Path | None = None, context: Any | None = None,
                       vault: vault_module.Vault | None = None,
                       active_settings: settings.Settings | None = None) -> Submission:
    """Fill every field, tick the declaration and press Submit once.

    Only `pipeline.submit` calls this, after it has checked the candidate's approval record, the recall window, the content hash and
    the settings; that record is the authority for the vault fill and the declaration. It never retries: a second press
    could send a second application.
    """
    from playwright.sync_api import sync_playwright

    folder = folder or drafts.find_folder(app_id)
    if folder is None:
        raise RecipeError(f"application {app_id} has no folder")
    form = load_form(folder)
    active = active_settings or settings.Settings()

    def run(ctx: Any) -> Submission:
        page = ctx.new_page()
        page.goto(str(form["form_url"]), wait_until="networkidle", timeout=60000)
        plan = build_plan(folder, form, _question_types(page), tick_declarations=True)
        if plan.missing:
            raise RecipeError("still missing: " + ", ".join(plan.missing))

        def vault_filler(steps: list[Step]) -> None:
            fill.fill_fields(
                page, plan.site,
                [fill.FieldFromVault(s.target, s.vault_field or s.label, s.value) for s in steps],
                vault=vault, active_settings=active, human_confirmed=True,
            )
            fill.restore_field_types_for_validation(page)

        result = fill_page(page, plan, mode="submit-ready", app_id=app_id, vault_filler=vault_filler)
        if result.failed:
            raise RecipeError("did not take its value: " + ", ".join(result.failed))
        park_if_challenged(page)
        before_url, before_text = page.url, page.inner_text("body")
        page.get_by_role("button", name=SUBMIT_BUTTON).first.click()
        try:
            page.wait_for_load_state("networkidle", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        park_if_challenged(page)
        shot = _screenshot_path(app_id, "submitted")
        page.screenshot(path=str(shot), full_page=True)
        after_text = page.inner_text("body")
        errors = page.locator(FORM_ERRORS).filter(visible=True).count()
        confirmed = bool(SUCCESS_TEXT.search(after_text)) and not SUCCESS_TEXT.search(before_text)
        moved = page.url != before_url and "/applications/new" not in page.url
        if (confirmed or moved) and not errors:
            outcome = "submitted"
        elif errors:
            outcome = "failed"
        else:
            outcome = "unclear"
        return Submission(outcome, page.url, str(shot), errors)

    if context is not None:
        return run(context)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            return run(browser.new_context(locale="en-GB", viewport=VIEWPORT))
        finally:
            browser.close()


def _report(result: Result) -> None:
    print(f"{result.mode}: {result.filled} fields filled and checked")
    if result.failed:
        print("did not take its value: " + ", ".join(result.failed))
    if result.missing:
        print("still missing (waiting on the candidate): " + ", ".join(result.missing))
    if result.blocked_requests:
        print(f"blocked {result.blocked_requests} outgoing request(s); nothing was sent")
    if result.screenshot:
        print(f"screenshot: {result.screenshot}")


def main(argv: list[str] | None = None, *, stdin_isatty: Callable[[], bool] = lambda: sys.stdin.isatty()) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.recipes.pinpoint", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("application", type=int)
    parser.add_argument("--fill", action="store_true", help="fill the live form for the candidate to submit himself")
    args = parser.parse_args(argv)
    try:
        if not args.fill:
            result = dry_run(args.application)
            _report(result)
            return 1 if result.failed else 0
        if not stdin_isatty():
            print("error: fill mode needs the candidate at the keyboard (an interactive terminal); T-022 adds approval records")
            return 3
        typed = input(f"Type {args.application} to let CareerOS fill this form with your vault details: ").strip()
        if typed != str(args.application):
            print("Not confirmed; nothing was filled.")
            return 3
        result = fill_for_ali(args.application)
        return 1 if result.failed else 0
    except Parked as exc:
        print(f"parked: {exc}")
        return 4
    except (RecipeError, vault_module.VaultError, fill.FillError) as exc:
        print(f"error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
