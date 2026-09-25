"""T-021 Pinpoint recipe tests against a local page shaped like a Pinpoint application form (no network)."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from pipeline import config
from pipeline.recipes import pinpoint

A = "application_form[application]"


def _qt(n: int, kind: str) -> str:
    return f'<input type="hidden" name="{A}[answers_attributes][{n}][question_type]" value="{kind}">'


def _date(n: int) -> str:
    days = "".join(f"<option>{d}</option>" for d in range(1, 32))
    months = "".join(f"<option value='{i}'>{m}</option>" for i, m in enumerate(pinpoint.MONTHS) if m)
    years = "".join(f"<option>{y}</option>" for y in range(2030, 2009, -1))
    base = f"{A}[answers_attributes][{n}]"
    return (_qt(n, "date") + f'<select name="{base}[date_answer(3i)]"><option>Day</option>{days}</select>'
            f'<select name="{base}[date_answer(2i)]"><option>Month</option>{months}</select>'
            f'<select name="{base}[date_answer(1i)]"><option>Year</option>{years}</select>')


FORM = f"""<!doctype html><html><body><form action="/apply" method="post">
<input name="{A}[first_name]"><input name="{A}[last_name]">
<input type="email" name="{A}[email]"><input type="tel" name="{A}[phone]">
<select id="{A}[country]" name="{A}[country]"><option>Select...</option><option>Jamaica</option><option>United Kingdom</option></select>
<textarea name="{A}[summary]"></textarea>
{_qt(0, "multiple_choice")}
<select id="application_form_application_answers_attributes_0_mobile_select"
  onchange="document.getElementById('a0').value = this.selectedOptions[0].text">
  <option>Select...</option><option>ICAEW Website</option><option>Other</option></select>
<input type="hidden" id="a0" name="{A}[answers_attributes][0][answer_options_attributes][0][text]" value="">
{_qt(1, "short_text")}<input name="{A}[answers_attributes][1][text_answer]">
{_date(3)}
{_qt(27, "document")}<input type="file" name="{A}[answers_attributes][27][document]">
{_qt(30, "long_text")}<textarea name="{A}[answers_attributes][30][text_answer]"></textarea>
{_qt(46, "boolean")}
<label><input type="radio" name="{A}[answers_attributes][46][boolean_answer]" value="true">Yes</label>
<label><input type="radio" name="{A}[answers_attributes][46][boolean_answer]" value="false">No</label>
<select id="application_form_equality_monitoring_Gender Identity" name="application_form[equality_monitoring_options][]">
  <option>Select...</option><option value="1">Male</option><option value="9">Prefer Not To Say</option></select>
<input type="hidden" name="application[process_information]" value="0">
<input type="checkbox" name="application[process_information]" value="1">
<button type="submit">Submit Application</button>
</form>
<script>document.addEventListener('change', () => fetch('/autosave', {{method: 'POST', body: 'x'}}).catch(() => {{}}));</script>
</body></html>"""

FORM_YAML = """
form_url: https://jobs.example.test/apply/new
personal:
  first_name: {value: "the candidate"}
  last_name: {value: "Name"}
  email: {vault: identity.email}
  phone: {vault: identity.phone}
  country: {value: "United Kingdom"}
  date_of_birth: {value: null}
profile:
  summary: {file: final/answer-1.md}
answers:
  0: {value: "Other"}
  1: {value: null}
  3: {value: 2022-09-19}
  27: {file: null, status: pending-ali}
  30: {file: final/answer-5.md}
  46: {value: "Yes"}
diversity:
  gender_identity: "Prefer Not To Say"
  religion: null
declarations:
  process_information: {value: tick}
"""

ANSWER = """---
kind: answer-1
---
First paragraph of a synthetic summary.

Second paragraph.

## Citations
- S1: C-0001
"""


class PinpointRecipeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="careeros-pinpoint-test-")
        self.folder = Path(self.tmp.name) / "0099-example"
        (self.folder / "final").mkdir(parents=True)
        (self.folder / "form.yaml").write_text(FORM_YAML, encoding="utf-8")
        (self.folder / "final" / "answer-1.md").write_text(ANSWER, encoding="utf-8")
        (self.folder / "final" / "answer-5.md").write_text(ANSWER.replace("answer-1", "answer-5"), encoding="utf-8")
        self.old_shots = config.SCREENSHOTS_DIR
        config.SCREENSHOTS_DIR = Path(self.tmp.name) / "screenshots"

    def tearDown(self) -> None:
        config.SCREENSHOTS_DIR = self.old_shots
        self.tmp.cleanup()

    def _dry_run(self, html: str = FORM) -> pinpoint.Result:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport=pinpoint.VIEWPORT)
                ctx.route("https://jobs.example.test/**",
                          lambda route: route.fulfill(body=html, content_type="text/html"))
                return pinpoint.dry_run(99, context=ctx, folder=self.folder)
            finally:
                browser.close()

    def test_dry_run_fills_and_checks_every_field_and_sends_nothing(self) -> None:
        result = self._dry_run()
        self.assertEqual(result.failed, [])
        self.assertEqual(result.missing, ["answer 27"])
        # personal x5 (two vault placeholders), summary, answers 0/3/30/46, diversity, consent
        self.assertEqual(result.filled, 12)
        self.assertGreaterEqual(result.blocked_requests, 1)   # the page's autosave POSTs were aborted
        self.assertTrue(Path(result.screenshot).is_file())

    def test_visible_captcha_parks_the_application(self) -> None:
        html = FORM.replace("<form", '<div class="g-recaptcha" style="width:300px;height:80px">challenge</div><form', 1)
        with self.assertRaises(pinpoint.Parked):
            self._dry_run(html)

    def test_plan_maps_pinpoint_question_types(self) -> None:
        form = pinpoint.load_form(self.folder)
        types = {"0": "multiple_choice", "3": "date", "30": "long_text", "46": "boolean", "27": "document"}
        plan = pinpoint.build_plan(self.folder, form, types, tick_declarations=False)
        kinds = {s.label: (s.kind, s.value) for s in plan.steps}
        self.assertEqual(kinds["answer 3"], ("date", date(2022, 9, 19)))
        self.assertEqual(kinds["answer 46"][0], "radio")
        self.assertIn('[value="true"]', next(s.target for s in plan.steps if s.label == "answer 46"))
        self.assertEqual(kinds["email"], ("vault", "identity.email"))
        self.assertNotIn("## Citations", kinds["summary"][1])
        self.assertNotIn("process_information", kinds)       # declarations stay for the candidate in fill mode

    def test_unknown_question_type_and_missing_upload_are_errors(self) -> None:
        form = pinpoint.load_form(self.folder)
        with self.assertRaises(pinpoint.RecipeError):
            pinpoint.build_plan(self.folder, form, {"0": "mystery"}, tick_declarations=True)
        form["answers"][27] = {"file": "missing.pdf"}
        with self.assertRaises(pinpoint.RecipeError):
            pinpoint.build_plan(self.folder, form, {"0": "multiple_choice", "3": "date", "27": "document",
                                                     "30": "long_text", "46": "boolean"}, tick_declarations=True)

    def test_fill_mode_refuses_without_a_person_at_the_keyboard(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pinpoint.main(["99", "--fill"], stdin_isatty=lambda: False)
        self.assertEqual(code, 3)
        self.assertIn("keyboard", out.getvalue())


if __name__ == "__main__":
    unittest.main()
