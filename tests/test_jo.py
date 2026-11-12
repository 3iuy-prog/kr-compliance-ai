"""조문 번호(--jo) 형식 검증 시험.

실행: python3 -m unittest discover -s tests
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import legalize  # noqa: E402
import relations  # noqa: E402

GOOD = {
    "21": "제21조", "21조": "제21조", "제21": "제21조", "제21조": "제21조", " 제21조 ": "제21조",
    "21의3": "제21조의3", "21조의3": "제21조의3", "제21의3": "제21조의3", "제21조의3": "제21조의3",
}
BAD = ["제21의3조", "21-3", "21조3항", "제21조제1항", "", "   ", "0", "021", "21의0", "21의03",
       "제 21 조", "삼십", "21조의", "의3"]


class NormJo(unittest.TestCase):
    def test_accepts_and_normalizes(self):
        for raw, want in GOOD.items():
            with self.subTest(raw=raw):
                self.assertEqual(legalize.norm_jo(raw), want)

    def test_rejects_malformed(self):
        for raw in BAD:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    legalize.norm_jo(raw)

    def test_relations_none_vs_empty(self):
        self.assertIsNone(relations._jo(None))
        with self.assertRaises(ValueError):
            relations._jo("")

    def test_by_law_fails_before_reextract(self):
        with mock.patch.object(relations, "ensure_delegations") as ensure, \
             mock.patch.object(relations, "load_all", return_value=[]):
            with self.assertRaises(ValueError):
                relations.by_law("전자금융거래법", "제21의3조")
            ensure.assert_not_called()


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=60)

    def test_cli_rejects_malformed_with_exit_2(self):
        cases = [
            [str(TOOLS / "legalize.py"), "show", "전자금융거래법", "--jo", "제21의3조"],
            [str(TOOLS / "crossmap.py"), "by-law", "전자금융거래법", "--jo", "제21의3조"],
            [str(TOOLS / "relations.py"), "by-law", "전자금융거래법", "--jo", "제21의3조"],
            [str(TOOLS / "crossmap.py"), "by-law", "전자금융거래법", "--jo", ""],
        ]
        for args in cases:
            with self.subTest(args=args[0:3] + args[4:]):
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("조문 번호 형식 오류", r.stderr)


if __name__ == "__main__":
    unittest.main()
