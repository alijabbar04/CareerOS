"""T-035 offline tests for the shared morning scan routine."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from pipeline import config, morning, notify


def _write_shortlist(path: Path, count: int = 12) -> None:
    lines = ["# Daily shortlist — 2026-09-23", ""]
    for rank in range(1, count + 1):
        lines.extend(
            (
                f"## {rank}. Role {rank} — Company {rank} ({90 - rank}/100)",
                "",
                "- Location: London",
                f"- Closing date: 2026-10-{rank:02d}",
                f"- Source: [greenhouse](https://example.com/jobs/{rank})",
                "",
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


class MorningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="careeros-morning-")
        self.root = Path(self.temp_dir.name)
        self.old_paths = {
            "REPO_ROOT": config.REPO_ROOT,
            "DATA_DIR": config.DATA_DIR,
            "LOCAL_DIR": config.LOCAL_DIR,
            "LOGS_DIR": config.LOGS_DIR,
            "PAUSED_FLAG": config.PAUSED_FLAG,
        }
        config.REPO_ROOT = self.root
        config.DATA_DIR = self.root / "data"
        config.LOCAL_DIR = self.root / "local"
        config.LOGS_DIR = config.LOCAL_DIR / "logs"
        config.PAUSED_FLAG = config.LOCAL_DIR / "PAUSED"

    def tearDown(self) -> None:
        for name, value in self.old_paths.items():
            setattr(config, name, value)
        self.temp_dir.cleanup()

    def test_ollama_is_idle_only_for_successful_header_only_output(self) -> None:
        idle = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "NAME ID SIZE PROCESSOR UNTIL\n", "")
        busy = lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "NAME ID SIZE PROCESSOR UNTIL\nqwen3:8b abc 5GB CPU 4m\n", ""
        )
        failed = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "not running")
        self.assertTrue(morning.ollama_is_idle(idle))
        self.assertFalse(morning.ollama_is_idle(busy))
        self.assertFalse(morning.ollama_is_idle(failed))

    def test_format_shortlist_posts_only_top_ten(self) -> None:
        path = self.root / "shortlist.md"
        _write_shortlist(path)
        body, count = morning.format_shortlist(path)
        self.assertEqual(count, 10)
        self.assertIn("Role 1", body)
        self.assertIn("Role 10", body)
        self.assertNotIn("Role 11", body)
        self.assertLessEqual(len(body), 2_800)

    def test_success_runs_scan_then_model_score_notifies_and_logs(self) -> None:
        shortlist = config.DATA_DIR / "shortlist-2026-09-23.md"
        _write_shortlist(shortlist)
        calls: list[tuple[str, list[str] | None]] = []
        notices: list[tuple[str, str, str, str]] = []
        events: list[dict[str, object]] = []

        result = morning.run_morning(
            run_date=date(2026, 9, 23),
            scan_main=lambda args: calls.append(("scan", args)),
            score_main=lambda args: calls.append(("score", args)),
            idle_check=lambda: True,
            digest_builder=lambda: "Operational digest",
            notifier=lambda title, body, level, channel: notices.append((title, body, level, channel))
            or {"toast_sent": True, "discord_sent": True},
            event_logger=lambda **kwargs: events.append(kwargs) or 1,
        )

        self.assertEqual(calls, [("scan", []), ("score", ["--date", "2026-09-23", "--models"])])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["models_used"])
        self.assertEqual(result["top_count"], 10)
        self.assertEqual(notices[0][3], "digest")
        self.assertIn("Role 10", notices[0][1])
        self.assertIn("Operational digest", notices[0][1])
        self.assertEqual(events[0]["type"], "job_scan_run")
        self.assertEqual(events[0]["detail"]["status"], "completed")

    def test_busy_ollama_runs_deterministic_score_only(self) -> None:
        _write_shortlist(config.DATA_DIR / "shortlist-2026-09-23.md", count=1)
        score_args: list[list[str] | None] = []
        result = morning.run_morning(
            run_date=date(2026, 9, 23),
            scan_main=lambda args: None,
            score_main=lambda args: score_args.append(args),
            idle_check=lambda: False,
            digest_builder=lambda: "Digest",
            notifier=lambda *args, **kwargs: {"toast_sent": False, "discord_sent": False},
            event_logger=lambda **kwargs: 1,
        )
        self.assertEqual(score_args, [["--date", "2026-09-23"]])
        self.assertFalse(result["models_used"])

    def test_scan_failure_stops_before_score_and_records_failure(self) -> None:
        score_main = mock.Mock()
        notices: list[dict[str, object]] = []
        events: list[dict[str, object]] = []

        def fail_scan(args: list[str] | None) -> None:
            raise RuntimeError("sensitive detail must not be copied")

        result = morning.run_morning(
            run_date=date(2026, 9, 23),
            scan_main=fail_scan,
            score_main=score_main,
            notifier=lambda *args, **kwargs: notices.append({"args": args, "kwargs": kwargs})
            or {"toast_sent": False, "discord_sent": False},
            event_logger=lambda **kwargs: events.append(kwargs) or 1,
        )

        score_main.assert_not_called()
        self.assertEqual(result["status"], "scan_failed")
        self.assertEqual(notices[0]["kwargs"]["level"], "urgent")
        self.assertNotIn("sensitive detail", notices[0]["args"][1])
        self.assertEqual(events[0]["detail"]["error_type"], "RuntimeError")

    def test_notify_digest_cli_builds_and_sends_digest(self) -> None:
        with (
            mock.patch.object(notify, "digest", return_value="Built digest"),
            mock.patch.object(notify, "notify", return_value={"toast_sent": False, "discord_sent": True}) as send,
        ):
            notify.main(["--digest"])
        send.assert_called_once_with(
            "CareerOS daily digest", "Built digest", level="info", channel="digest"
        )


if __name__ == "__main__":
    unittest.main()
