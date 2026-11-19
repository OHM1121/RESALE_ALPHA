"""Phone 0.0.9 12:23:04 TypeError crash: _gmo_preplan.capture recovery kw."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app/src/main/python'))
import entry_plan_hooks_v107 as audit

class ReconnectionHook(unittest.TestCase):
    def test_gmo_capture_forwards_recovery_without_intercepting_owner(self):
        seen=[]
        class Native:
            class Preplanner:
                def prepare(self,venue,at=None,recovery=False):
                    seen.append((venue,at,recovery))
                    return ('000680',) if recovery else ()
        with patch.object(audit,'_root',lambda core:None):
            audit._gmo_preplan(object(),Native)
            planner=Native.Preplanner()
            result=planner.prepare('KRX','2026-11-19T12:23:04+09:00',recovery=True)
        self.assertEqual(result,('000680',))
        self.assertEqual(seen,[('KRX','2026-11-19T12:23:04+09:00',True)])
    def test_original_preopen_prepare_signature_unchanged(self):
        seen=[]
        class Native:
            class Preplanner:
                def prepare(self,venue,at=None,recovery=False):
                    seen.append((venue,at,recovery))
                    return ()
        with patch.object(audit,'_root',lambda core:None):
            audit._gmo_preplan(object(),Native)
            Native.Preplanner().prepare('KRX','2026-11-19T09:52:00+09:00')
        self.assertEqual(seen,[('KRX','2026-11-19T09:52:00+09:00',False)])

if __name__=='__main__':unittest.main()
