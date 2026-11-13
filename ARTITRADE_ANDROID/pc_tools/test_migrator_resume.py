"""Repeat interrupted PC->MSERVER uploads, including lost chunk/commit ACKs."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC=Path(__file__).with_name("artitrade_ledger_migrator.py")
spec=importlib.util.spec_from_file_location("artitrade_resume_under_test",SRC)
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class PCMigrationResumeTests(unittest.TestCase):
    def test_durable_resume_and_duplicate_ack_12_cycles(self):
        data=(bytes(range(256))*7813)[:2_000_017]  # 3 upload chunks
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/"ledger.zip";archive.write_bytes(data)
            manifest={"archive_sha256":hashlib.sha256(data).hexdigest(),"files":{}}
            n=(len(data)+app.UPLOAD_CHUNK-1)//app.UPLOAD_CHUNK
            self.assertEqual(n,3)
            for cycle in range(12):
                prefix=cycle%3
                prior="durable_session_"+str(cycle).zfill(4)
                received=[data[i*app.UPLOAD_CHUNK:(i+1)*app.UPLOAD_CHUNK]
                          for i in range(prefix)]
                state={"received":received,"commit_calls":0,"once":set()}
                def fake_request(base,token,method,endpoint,payload=None,params=None,timeout=20):
                    if endpoint=="/v1/migration/begin":
                        self.assertEqual(payload["archive_sha256"],manifest["archive_sha256"])
                        return {"ok":True,"session":prior,"received":prefix,"resumed":prefix>0}
                    if endpoint=="/v1/migration/chunk":
                        self.assertEqual(payload["session"],prior)
                        idx=payload["index"]
                        chunk=app.base64.b64decode(payload["data_b64"])
                        self.assertEqual(app.sha256_bytes(chunk),payload["sha256"])
                        self.assertEqual(chunk,data[idx*app.UPLOAD_CHUNK:(idx+1)*app.UPLOAD_CHUNK])
                        if idx<len(state["received"]):
                            self.assertEqual(chunk,state["received"][idx])
                            return {"ok":True,"duplicate":True}
                        self.assertEqual(idx,len(state["received"]))
                        state["received"].append(chunk)
                        # Simulate server writing data and then losing the HTTP ACK.
                        if idx not in state["once"]:
                            state["once"].add(idx)
                            raise TimeoutError("lost chunk ACK after durable save")
                        return {"ok":True}
                    if endpoint=="/v1/migration/commit":
                        self.assertEqual(payload["session"],prior)
                        self.assertEqual(b"".join(state["received"]),data)
                        state["commit_calls"]+=1
                        if state["commit_calls"]==1:
                            raise TimeoutError("lost commit ACK after atomic rename")
                        return {"ok":True,"ready":True,"duplicate":True}
                    self.fail("Unexpected endpoint: "+endpoint)
                with patch.object(app,"request",side_effect=fake_request), \
                     patch.object(app.time,"sleep",return_value=None):
                    result=app.upload_archive("http://100.64.0.1:8765","0"*64,archive,manifest)
                self.assertEqual(result["session"],prior)
                self.assertEqual(b"".join(received),data)
                self.assertEqual(state["commit_calls"],2)

    def test_invalid_server_offset_fails_before_sending_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/"ledger.zip";archive.write_bytes(b"x")
            manifest={"archive_sha256":hashlib.sha256(b"x").hexdigest(),"files":{}}
            def reply(base,token,method,endpoint,payload=None,params=None,timeout=20):
                self.assertEqual(endpoint,"/v1/migration/begin")
                return {"ok":True,"session":"valid_session_123","received":99999}
            with patch.object(app,"request",side_effect=reply):
                with self.assertRaisesRegex(ValueError,"offset"):
                    app.upload_archive("http://100.64.0.1:8765","0"*64,archive,manifest)


if __name__=="__main__":
    unittest.main()
