"""T-021 generic HTML form recipe: dry run against a local page with a CAPTCHA widget (no network)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from pipeline.recipes import html_form
from pipeline.recipes.pinpoint import RecipeError, load_form

PAGE = """<!doctype html><html><body><form method="post" action="/job/1" enctype="multipart/form-data">
<input name="firstName"><input name="lastName"><input type="email" name="email">
<input type="file" name="cv"><textarea name="coverLetter"></textarea>
<label><input type="checkbox" name="jobAlerts" checked> Send me job alerts</label>
<div class="g-recaptcha" style="width:300px;height:78px">captcha</div>
<button type="submit">Submit</button></form></body></html>"""

LETTER = "---\nkind: cover-letter\n---\nDear Recruitment Team,\n\nA synthetic letter.\n\nYours faithfully,\ncandidate name\n\n## Citations\n- S1: greeting\n"


class HtmlFormTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="careeros-htmlform-test-")
        self.folder = Path(self.tmp.name) / "0061-example"
        (self.folder / "final").mkdir(parents=True)
        (self.folder / "final" / "cover-letter-r1.md").write_text(LETTER, encoding="utf-8")
        (self.folder / "cv.pdf").write_bytes(b"%PDF-1.4 synthetic")
        (self.folder / "form.yaml").write_text(
            "form_url: https://jobs.example.test/job/1\nfields:\n"
            "  firstName: {value: the candidate}\n  lastName: {value: Name}\n  email: {vault: identity.email}\n"
            "  cv: {file: cv.pdf}\n  coverLetter: {file: final/cover-letter-r1.md}\n  jobAlerts: {value: false}\n",
            encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_fills_every_field_leaves_the_captcha_alone_and_sends_nothing(self) -> None:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context()
                ctx.route("https://jobs.example.test/**", lambda route: route.fulfill(body=PAGE, content_type="text/html"))
                filled, failed, missing, blocked = html_form.dry_run(61, context=ctx, folder=self.folder)
            finally:
                browser.close()
        self.assertEqual((filled, failed, missing), (6, [], []))
        self.assertEqual(blocked, 0)

    def test_textareas_get_the_letter_prose_and_files_must_exist(self) -> None:
        form = load_form(self.folder)
        plan = html_form.build_plan(self.folder, form, lambda name: "textarea" if name == "coverLetter" else "input")
        steps = {s.name: s for s in plan.steps}
        self.assertEqual(steps["coverLetter"].kind, "text")
        self.assertNotIn("## Citations", steps["coverLetter"].value)
        self.assertTrue(steps["coverLetter"].value.startswith("Dear Recruitment Team"))
        self.assertEqual((steps["jobAlerts"].kind, steps["jobAlerts"].value), ("check", False))
        self.assertEqual(steps["email"].kind, "vault")
        form["fields"]["cv"] = {"file": "missing.pdf"}
        with self.assertRaises(RecipeError):
            html_form.build_plan(self.folder, form, lambda name: "input")


if __name__ == "__main__":
    unittest.main()
