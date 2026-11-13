"""Inspect the frozen PC EXE storage writers shipped with the Android APK.

No credentials, live API access or real order actions are required. Reports which
timezone tags and fills are written by original EXE modules for manual review.
"""
import base64
import io
import re
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).parents[1]/"app/src/main/assets/strategy_v070"

class EXETimestampSourceAudit(unittest.TestCase):
    def test_shipped_exe_code_has_storage_and_time_sources(self):
        parts=sorted(ROOT.glob("part_*.txt"))
        self.assertEqual(len(parts),22)
        archive=base64.b64decode("".join(p.read_text(encoding="ascii") for p in parts))
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            files=bundle.namelist()
            self.assertIn("d_gmo_score_research_paper.py",files)
            sources=[p for p in files if p.endswith(".py")]
            self.assertGreater(len(sources),10)
            matches=[]
            for file in sources:
                if not any(term in file.lower() for term in
                           ("coin","upbit","d_live","storage","gmo","ma_engine")):
                    continue
                lines=bundle.read(file).decode("utf-8","replace").splitlines()
                for lineno,line in enumerate(lines,1):
                    if re.search(
                        r'datetime\.(now|utcnow)|timezone\.utc|KST|ZoneInfo|'
                        r'UTC\b|isoformat\(|INSERT\s+INTO\s+executions|'
                        r'INSERT\s+INTO\s+coin_shadow_fills',line,re.IGNORECASE):
                        if ("import " in line or "print(" in line):
                            continue
                        matches.append(f"{file}:{lineno}: {line.strip()[:145]}")
            self.assertGreater(len(matches),5,"Cannot audit EXE date/time persistence")
            print("FROZEN_EXE_PERSISTENCE_TIME_POLICY_BEGIN")
            for line in matches[:55]:print(line)
            print("FROZEN_EXE_PERSISTENCE_TIME_POLICY_END")

if __name__=="__main__":
    unittest.main()
