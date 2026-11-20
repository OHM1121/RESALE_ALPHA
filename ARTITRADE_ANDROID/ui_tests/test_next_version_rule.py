"""Owner rule R1: 0.0.20 -> 0.1.0 -> ... -> 0.1.9 -> 0.2.0 (numeric only)."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/artitrade"))
import next_version as nv  # noqa: E402


class NextVersion(unittest.TestCase):
    def test_owner_examples(self):
        self.assertEqual(nv.next_version("0.0.20"), "0.1.0")
        self.assertEqual(nv.next_version("0.1.9"), "0.2.0")

    def test_sequence_has_no_gaps_or_repeats(self):
        v, seen = "0.0.20", []
        for _ in range(25):
            v = nv.next_version(v)
            seen.append(v)
        self.assertEqual(seen[:11], ["0.1.0", "0.1.1", "0.1.2", "0.1.3", "0.1.4", "0.1.5",
                                     "0.1.6", "0.1.7", "0.1.8", "0.1.9", "0.2.0"])
        self.assertEqual(len(set(seen)), len(seen))
        self.assertEqual(seen[-1], "0.3.4")

    def test_nine_rolls_over_for_any_middle_number(self):
        self.assertEqual(nv.next_version("0.9.9"), "0.10.0")
        self.assertEqual(nv.next_version("1.2.9"), "1.3.0")

    def test_anything_but_digits_and_dots_is_refused(self):
        for bad in ("v0.1.0", "0.1.0-FIX", "0.1", "0.1.0.apk", "0.1.0 TEST", "0.0.20-a"):
            with self.assertRaises(ValueError, msg=bad):
                nv.next_version(bad)

    def test_command_line(self):
        out = subprocess.run([sys.executable, "-I", str(ROOT / "tools/artitrade/next_version.py"), "0.1.9"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "0.2.0")

    def test_the_rule_is_written_in_the_standing_rules(self):
        text = (ROOT / "docs/artitrade/USER_STANDING_RULES.md").read_text(encoding="utf8")
        self.assertIn("0.1.0", text)
        self.assertIn("0.2.0", text)
        self.assertIn("next_version.py", text)
        self.assertIn("Automatic delivery", text)


if __name__ == "__main__":
    unittest.main()
