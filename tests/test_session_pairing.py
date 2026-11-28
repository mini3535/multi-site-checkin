import unittest

from autosign.session_pairing import SessionPairingManager


def state(secret="private-cookie"):
    return {
        "cookies": [{"name": "session", "value": secret, "domain": ".example.com",
                     "path": "/", "secure": True, "httpOnly": True}],
        "origins": [{"origin": "https://forum.example.com", "localStorage": [
            {"name": "token", "value": "private-token"}]}],
    }


class SessionPairingTests(unittest.TestCase):
    def test_discovery_pairing_binds_to_scanned_page_and_claims_once(self):
        manager = SessionPairingManager()
        pairing = manager.create_discovery("owner-a")
        self.assertEqual(manager.status(pairing["id"], "owner-a")["status"], "waiting")
        self.assertEqual(manager.complete(pairing["code"], "https://forum.example.com/daily", state(), {
            "button_texts": ["每日签到"], "success_texts": [],
            "already_done_texts": ["今天已签到"],
        }, page_title=" Example Forum\nDaily ")["status"], "connected")

        discovered = manager.claim_discovery(pairing["id"], pairing["code"])
        self.assertEqual(discovered["start_url"], "https://forum.example.com/daily")
        self.assertEqual(discovered["page_title"], "Example Forum Daily")
        self.assertEqual(discovered["hints"]["button_texts"], ["每日签到"])
        self.assertEqual(discovered["hints"]["resource_hosts"], [])
        self.assertEqual(discovered["state"]["cookies"][0]["value"], "private-cookie")
        self.assertIsNone(manager.claim_discovery(pairing["id"], pairing["code"]))

        manager.finish_discovery(pairing["id"], pairing["code"], task_id=42)
        status = manager.status(pairing["id"], "owner-a")
        self.assertEqual(status["task_id"], 42)
        self.assertEqual(manager.discovery_result(pairing["id"], pairing["code"])["task_id"], 42)
        self.assertIsNone(manager.claim_discovery(pairing["id"], "wrong-code"))
        with self.assertRaisesRegex(ValueError, "连接码不正确"):
            manager.complete(pairing["id"] + ".wrong", "https://forum.example.com/daily", state())

    def test_pairing_is_owner_scoped_host_bound_and_consumed_once(self):
        manager = SessionPairingManager()
        pairing = manager.create("owner-a", "https://forum.example.com/daily", ["example.com"])

        self.assertEqual(manager.status(pairing["id"], "owner-a")["status"], "waiting")
        self.assertEqual(manager.status(pairing["id"], "owner-b")["status"], "expired")
        with self.assertRaisesRegex(ValueError, "不一致"):
            manager.complete(pairing["code"], "https://other.example.com/daily", state())

        self.assertEqual(manager.complete(pairing["code"], "https://forum.example.com/daily", state(), {
            "button_texts": ["每日签到", "每日签到", "x" * 300, 4],
            "success_texts": ["签到已完成", "今日签到成功", "今天已签到"],
            "already_done_texts": ["今天已签到", "今日已签到", "unrelated page text"],
        })["status"],
                         "connected")
        status = manager.status(pairing["id"], "owner-a")
        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["hints"], {
            "button_texts": ["每日签到"],
            "success_texts": ["签到已完成", "今日签到成功"],
            "already_done_texts": ["今天已签到", "今日已签到"],
            "resource_hosts": [],
        })
        self.assertIsNone(manager.consume(pairing["id"], "owner-b", "https://forum.example.com/daily"))
        self.assertIsNone(manager.consume(pairing["id"], "owner-a", "https://other.example.com/daily"))
        imported = manager.consume(pairing["id"], "owner-a", "https://forum.example.com/daily")
        self.assertEqual(imported["cookies"][0]["value"], "private-cookie")
        self.assertEqual(imported["origins"][0]["localStorage"][0]["value"], "private-token")
        self.assertEqual(manager.status(pairing["id"], "owner-a")["status"], "expired")

    def test_pairing_code_is_required_and_invalid_attempts_are_bounded(self):
        manager = SessionPairingManager()
        pairing = manager.create("owner-a", "https://forum.example.com/daily", [])
        pair_id, _secret = pairing["code"].split(".", 1)
        for _ in range(5):
            with self.assertRaisesRegex(ValueError, "连接码不正确"):
                manager.complete(pair_id + ".wrong", "https://forum.example.com/daily", state())
        self.assertEqual(manager.status(pairing["id"], "owner-a")["status"], "expired")

    def test_pairing_rejects_http_and_empty_or_foreign_session_state(self):
        manager = SessionPairingManager()
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            manager.create("owner-a", "http://forum.example.com/daily", [])
        pairing = manager.create("owner-a", "https://forum.example.com/daily", [])
        with self.assertRaisesRegex(ValueError, "没有找到"):
            manager.complete(pairing["code"], "https://forum.example.com/daily", {
                "cookies": [{"name": "outside", "value": "ignored", "domain": ".other.com", "path": "/"}],
                "origins": [],
            })

    def test_pairing_expires(self):
        now = [10.0]
        manager = SessionPairingManager(clock=lambda: now[0])
        pairing = manager.create("owner-a", "https://forum.example.com/daily", [])
        now[0] += 301
        self.assertEqual(manager.status(pairing["id"], "owner-a")["status"], "expired")

    def test_pairing_rejects_host_urls_and_owner_cleanup(self):
        manager = SessionPairingManager()
        with self.assertRaises(ValueError):
            manager.create("owner-a", "https://forum.example.com/daily", ["example.com@evil.test"])
        first = manager.create("owner-a", "https://forum.example.com/daily", [])
        second = manager.create("owner-b", "https://forum.example.com/daily", [])
        self.assertEqual(manager.status(first["id"], "owner-a")["status"], "waiting")
        manager.discard_owner("owner-a")
        self.assertEqual(manager.status(first["id"], "owner-a")["status"], "expired")
        self.assertEqual(manager.status(second["id"], "owner-b")["status"], "waiting")


if __name__ == "__main__":
    unittest.main()
