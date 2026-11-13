import unittest
from artitrade_pc_monitor_core import automatic_preload


class AutoPreloadTests(unittest.TestCase):
    def test_remembered_phone_and_token_download_immediately_without_discovery(self):
        created=[]
        class FakeClient:
            base="http://100.84.3.9:8765"
            token="a"*64
            def fetch(self, progress=None):
                if progress: progress(1,1,"stock/at/ledger")
                return {"connected":True,"records":{"stock":1}}
        def factory(base,token):
            created.append((base,token))
            return FakeClient()
        def forbidden_discovery():
            self.fail("stored address must not trigger Tailscale search")
        updates=[]
        client, data, base=automatic_preload(
            "http://100.84.3.9:8765","a"*64,forbidden_discovery,
            client_factory=factory,progress=lambda d,t,k:updates.append((d,t,k)))
        self.assertTrue(data["connected"])
        self.assertEqual(base,"http://100.84.3.9:8765")
        self.assertEqual(len(created),1)
        self.assertEqual(updates,[(1,1,"stock/at/ledger")])

    def test_stale_saved_url_falls_back_to_single_verified_tailscale_android(self):
        visited=[]
        class FakeClient:
            def __init__(self,base,token):
                self.base=base;self.token=token
            def fetch(self,progress=None):
                visited.append(self.base)
                if self.base.endswith("1.1:8765"):
                    raise ConnectionError("old IP offline")
                return {"connected":True,"records":{"coin":2}}
        checks=[]
        def health(base):
            checks.append(base)
            return {"ok":True,"name":"ARTITRADE MSERVER"}
        c,data,base=automatic_preload(
            "http://100.88.1.1:8765","b"*64,lambda:"http://100.88.1.2:8765",
            client_factory=FakeClient,verify_discovered=health)
        self.assertEqual(visited,["http://100.88.1.1:8765","http://100.88.1.2:8765"])
        self.assertEqual(checks,["http://100.88.1.2:8765"])
        self.assertEqual(base,"http://100.88.1.2:8765")

    def test_missing_pairing_requires_one_time_setup_without_network(self):
        called=[]
        def discover(): called.append("discovered");return ""
        with self.assertRaisesRegex(ValueError,"PAIRING_REQUIRED"):
            automatic_preload("http://100.99.1.2:8765","",discover)
        self.assertEqual(called,[])

    def test_wrong_discovered_server_never_receives_token(self):
        calls=[]
        def client_factory(base,token):
            calls.append(("token",base))
            self.fail("Do not send bearer token to unverified service")
        with self.assertRaises(ConnectionError):
            automatic_preload("", "f"*64,lambda:"http://100.89.1.5:8765",
                client_factory=client_factory,
                verify_discovered=lambda base:{"name":"Unexpected service","ok":True})
        self.assertEqual(calls,[])

    def test_cached_client_is_reused_instead_of_clearing_revisions(self):
        class Cached:
            base="http://100.80.0.2:8765"
            token="e"*64
            def __init__(self):self.calls=0
            def fetch(self,progress=None):
                self.calls+=1
                return {"connected":True}
        cached=Cached()
        for _ in range(2):
            c,d,u=automatic_preload(cached.base,cached.token,lambda:None,
                existing_client=cached,
                client_factory=lambda b,t:self.fail("should reuse client"))
        self.assertIs(c,cached)
        self.assertEqual(cached.calls,2)

if __name__=="__main__":
    unittest.main()
