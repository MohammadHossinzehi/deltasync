import contextlib
import io
import random
import tempfile
import unittest
from pathlib import Path

from deltasync.__main__ import main


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(args))
        return code, out.getvalue()

    def test_signature_delta_patch_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rng = random.Random(3)
            old = rng.randbytes(40_000)
            new = old[:9000] + b"changed" + old[9100:]
            (d / "old").write_bytes(old)
            (d / "new").write_bytes(new)
            self.assertEqual(self.run_cli("signature", str(d / "old"), str(d / "sig"))[0], 0)
            code, out = self.run_cli("delta", str(d / "sig"), str(d / "new"), str(d / "dlt"))
            self.assertEqual(code, 0)
            self.assertIn("reused", out)
            self.assertEqual(self.run_cli("patch", str(d / "old"), str(d / "dlt"), str(d / "out"))[0], 0)
            self.assertEqual((d / "out").read_bytes(), new)
            self.assertLess((d / "dlt").stat().st_size, 2000)

    def test_patch_against_wrong_file_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "old").write_bytes(b"a" * 5000)
            (d / "new").write_bytes(b"a" * 4000 + b"b" * 1000)
            (d / "other").write_bytes(b"c" * 5000)
            self.run_cli("signature", str(d / "old"), str(d / "sig"))
            self.run_cli("delta", str(d / "sig"), str(d / "new"), str(d / "dlt"))
            code, out = self.run_cli("patch", str(d / "other"), str(d / "dlt"), str(d / "out"))
            self.assertEqual(code, 1)
            self.assertIn("error", out)

    def test_demo_runs(self):
        code, out = self.run_cli("demo", "--size", "100000", "--edits", "5")
        self.assertEqual(code, 0)
        self.assertIn("FastCDC", out)


if __name__ == "__main__":
    unittest.main()
