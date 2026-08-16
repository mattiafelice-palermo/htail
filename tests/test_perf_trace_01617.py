from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PerformanceTraceTests(unittest.TestCase):
    def test_opt_in_trace_writes_coarse_jsonl_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            watched = root / "sample.log"
            watched.write_text("one\ntwo\nthree\n", encoding="utf-8")
            trace = root / "perf.jsonl"
            script = r'''
import io
from unittest import mock
from htail_app import app, core
args = app.parse_args([WATCHED, "--no-native-watch", "--no-color", "--no-self-install-prompt"])
a = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
a.dimensions = lambda: (80, 20)
with mock.patch("sys.stdout", io.StringIO()):
    a.render()
    a.handle_input("UP")
    a.render()
    a.__exit__(None, None, None)
'''.replace("WATCHED", repr(str(watched)))
            env = os.environ.copy()
            env["HTAIL_PERF_TRACE"] = str(trace)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
            result = subprocess.run([sys.executable, "-c", script], env=env, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["event"], "start")
            self.assertTrue(any(row["event"] == "sample" for row in rows))
            self.assertEqual(rows[-1]["event"], "stop")


if __name__ == "__main__":
    unittest.main()
