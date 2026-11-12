"""Offline tests: never read real ledgers, tokens, or contact a remote server."""
import json
import unittest
from unittest.mock import patch

import artitrade_ledger_migrator as app


class MigratorConnectionTests(unittest.TestCase):
    def test_android_tailnet_peer_only(self):
        details = {
            "BackendState": "Running",
            "Peer": {
                "android": {"OS": "android", "HostName": "galaxy-s21", "Online": True,
                            "TailscaleIPs": ["100.93.11.22", "fd7a:115c:a1e0::1"]},
                "windows": {"OS": "windows", "Online": True, "TailscaleIPs": ["100.91.33.44"]},
                "offline": {"OS": "android", "Online": False, "TailscaleIPs": ["100.91.44.55"]},
            },
        }
        with patch.object(app, "_tailscale_program", return_value="tailscale.exe"), \
             patch.object(app, "_command", return_value=(0, json.dumps(details))):
            self.assertEqual(app.tailscale_candidates(),
                             [("http://100.93.11.22:8765", "galaxy-s21")])

    def test_auto_discovery_probes_identity_without_token(self):
        with patch.object(app, "tailscale_candidates",
                          return_value=[("http://100.93.11.22:8765", "galaxy-s21")]), \
             patch.object(app, "probe_mserver", return_value=True) as probe, \
             patch.object(app, "usb_forward_available",
                          side_effect=AssertionError("Do not use USB when tailnet available")):
            self.assertEqual(app.resolve_server("자동 검색"), "http://100.93.11.22:8765")
            probe.assert_called_once_with("http://100.93.11.22:8765")

    def test_pc_loopback_not_phone_without_usb(self):
        with patch.object(app, "tailscale_candidates", return_value=[]), \
             patch.object(app, "usb_forward_available", return_value=False):
            with self.assertRaisesRegex(ConnectionError, "127.0.0.1"):
                app.resolve_server("http://127.0.0.1:8765")

    def test_usb_forward_is_verified(self):
        with patch.object(app, "tailscale_candidates", return_value=[]), \
             patch.object(app, "usb_forward_available", return_value=True), \
             patch.object(app, "probe_mserver",
                          side_effect=lambda url: url == "http://127.0.0.1:8765"):
            self.assertEqual(app.resolve_server("자동 검색"), "http://127.0.0.1:8765")

    def test_ambiguous_android_phones_are_not_auto_selected(self):
        peers = [("http://100.93.11.22:8765", "s21"), ("http://100.101.12.2:8765", "pixel")]
        with patch.object(app, "tailscale_candidates", return_value=peers), \
             patch.object(app, "probe_mserver", return_value=True):
            with self.assertRaisesRegex(ConnectionError, "여러 대"):
                app.resolve_server("자동 검색")

    def test_bad_token_stops_before_network(self):
        with patch.object(app, "resolve_server", side_effect=AssertionError("must not scan")):
            with self.assertRaisesRegex(ValueError, "64자리"):
                app.verified_connection("자동 검색", "")

    def test_non_mserver_rejected(self):
        with patch.object(app, "request", return_value={"ok": True, "name": "not ARTITRADE MSERVER"}):
            self.assertFalse(app.probe_mserver("http://100.93.11.22:8765"))

    def test_status_unauthorized_fails(self):
        token = "a" * 64
        with patch.object(app, "resolve_server", return_value="http://100.93.11.22:8765"), \
             patch.object(app, "request", return_value={"ok": False, "error": "unauthorized", "http_status": 401}):
            with self.assertRaises(PermissionError):
                app.verified_connection("자동 검색", token)

    def test_status_approved(self):
        token = "a" * 64
        with patch.object(app, "resolve_server", return_value="http://100.93.11.22:8765"), \
             patch.object(app, "request", return_value={"ok": True, "owner": "phone"}):
            self.assertEqual(app.verified_connection("자동 검색", token),
                             "http://100.93.11.22:8765")


if __name__ == "__main__":
    unittest.main()
