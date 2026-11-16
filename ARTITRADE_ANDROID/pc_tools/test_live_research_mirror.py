"""PC mirror must apply revisions without changing original EXE SQLite."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import artitrade_live_mirror as mirror

class PcReadOnlyMirrorTests(unittest.TestCase):
    def test_initial_sync_then_only_changed_revisions_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            state={'revision':1}
            seen=[]
            def request(_server,_token,path, params=None):
                seen.append(path)
                if path=="/v1/records/index":
                    return {'ok':True,'records':[{'market':'stock','strategy':'at',
                        'bucket':'research','revision':state['revision']}]}
                if path=="/v1/record":
                    return {'ok':True,'exists':True,'revision':state['revision'],
                            'data':{'items':[{'code':'005930','version':state['revision']}]}}
                raise AssertionError(path)
            with patch.object(mirror,'api',request):
                self.assertEqual(mirror.sync_once("http://127.0.0.1:8765","a"*64,Path(temp)),1)
                path=Path(temp)/'stock'/'at'/'research.json'
                self.assertEqual(json.loads(path.read_text())['items'][0]['version'],1)
                self.assertEqual(mirror.sync_once("http://127.0.0.1:8765","a"*64,Path(temp)),0)
                self.assertEqual(seen.count("/v1/record"),1)
                state['revision']=2
                self.assertEqual(mirror.sync_once("http://127.0.0.1:8765","a"*64,Path(temp)),1)
                self.assertEqual(json.loads(path.read_text())['items'][0]['version'],2)

    def test_nonwhitelisted_bucket_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(mirror,'api',return_value={'ok':True,'records':[
                    {'market':'stock','strategy':'../../','bucket':'ledger','revision':1}]}):
                with self.assertRaises(ValueError):
                    mirror.sync_once("http://127.0.0.1:8765","a"*64,Path(temp))
                self.assertFalse((Path(temp)/'stock').exists())

if __name__=='__main__': unittest.main()
