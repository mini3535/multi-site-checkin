"""Token imports and fixed-origin profile bootstrap use fixture credentials only."""
import json
import unittest
from unittest.mock import Mock, patch

from autosign.browser_auth import (
    TokenLoginError, normalize_token, prepare_token_session, token_storage_state,
)
from autosign.browser_runner import _run_browser_in_process, validate_browser_spec


SECRET = "fixture-private-token-123"


def token_spec(url="https://api.xingchiapi.com/dashboard", key=""):
    from urllib.parse import urlsplit
    state, login = token_storage_state("Bearer " + SECRET, url, key)
    return validate_browser_spec({
        "engine": "browser", "site_id": "custom", "start_url": url,
        "allowed_hosts": [urlsplit(url).hostname], "storage_state": state,
        "token_login": login, "steps": [], "success_text": "签到成功",
    })


class TokenImportTests(unittest.TestCase):
    def test_import_normalizes_bearer_and_uses_exact_origin_with_no_cookies(self):
        spec = token_spec("https://api.xingchiapi.com:443/dashboard")
        self.assertEqual(spec["token_login"], {"storage_key": "auth_token"})
        self.assertEqual(spec["storage_state"], {"cookies": [], "origins": [{
            "origin": "https://api.xingchiapi.com", "localStorage": [{"name": "auth_token", "value": SECRET}]}]})
        other = token_spec("https://forum.example:8443/daily", "site_token")
        self.assertEqual(other["storage_state"]["origins"][0]["origin"], "https://forum.example:8443")
        self.assertEqual(other["token_login"]["storage_key"], "site_token")
        self.assertEqual(token_spec("https://forum.example/daily")["token_login"]["storage_key"], "access_token")

    def test_invalid_tokens_and_keys_do_not_appear_in_errors(self):
        for token in ("", "Bearer ", "Authorization: Bearer " + SECRET, SECRET + "\nX: value",
                      "汉字", "a" * 16385, None, True):
            with self.subTest(token_type=type(token).__name__):
                with self.assertRaises(ValueError) as error:
                    normalize_token(token)
                self.assertNotIn(SECRET, str(error.exception))
        for key in ("invalid key", "bad\nkey", "a" * 129, True):
            with self.assertRaises(ValueError):
                token_storage_state(SECRET, "https://forum.example/", key)
        for url in ("file:///tmp/token", "https://user:password@forum.example/"):
            with self.assertRaises(ValueError):
                token_storage_state(SECRET, url)

    def test_token_cannot_be_reused_after_source_change_or_bad_metadata(self):
        for url in ("http://api.xingchiapi.com/dashboard", "https://api.xingchiapi.com:8443/dashboard",
                    "https://other.example/dashboard"):
            spec = token_spec()
            spec["start_url"] = url
            spec["allowed_hosts"] = ["api.xingchiapi.com", "other.example"]
            with self.assertRaises(ValueError):
                validate_browser_spec(spec)
        for login in ({}, {"storage_key": "missing"}, {"storage_key": "auth_token", "url": "https://other.example/"}, SECRET):
            spec = token_spec()
            spec["token_login"] = login
            with self.assertRaises(ValueError):
                validate_browser_spec(spec)


class TokenProfileTests(unittest.TestCase):
    def reply(self, status=200, body=None):
        return {"status_code": status, "headers": {}, "body": json.dumps(
            {"code": 0, "data": {"id": 7, "role": "user", "username": "fixture-user"}} if body is None else body)}

    def test_xingchi_profile_is_read_only_fixed_origin_and_does_not_modify_input(self):
        spec = token_spec()
        transport = Mock(return_value=self.reply())
        state = prepare_token_session(spec, transport=transport)
        transport.assert_called_once_with("GET", "https://api.xingchiapi.com/api/v1/auth/me",
                                          {"Authorization": "Bearer " + SECRET, "Accept": "application/json"}, "")
        entries = {e["name"]: e["value"] for e in state["origins"][0]["localStorage"]}
        self.assertEqual(entries["auth_token"], SECRET)
        self.assertEqual(json.loads(entries["auth_user"])["id"], 7)
        self.assertEqual(len(spec["storage_state"]["origins"][0]["localStorage"]), 1)

    def test_real_transport_uses_existing_pinned_fetch_without_redirects(self):
        with patch("autosign.site_adapters._fetch", return_value=self.reply()) as fetch:
            prepare_token_session(token_spec())
        self.assertEqual(fetch.call_args.kwargs["allowed_requests"], {("GET", "https://api.xingchiapi.com/api/v1/auth/me")})
        self.assertFalse(fetch.call_args.kwargs["allow_private"])

    def test_generic_sites_other_ports_and_other_storage_keys_never_receive_profile_call(self):
        for url, key in (("https://other.example/daily", ""), ("http://api.xingchiapi.com/dashboard", ""),
                         ("https://api.xingchiapi.com:8443/dashboard", "auth_token"),
                         ("https://api.xingchiapi.com/dashboard", "custom_key")):
            with self.subTest(url=url, key=key):
                spec = token_spec(url, key)
                transport = Mock()
                self.assertEqual(prepare_token_session(spec, transport=transport), spec["storage_state"])
                transport.assert_not_called()

    def test_auth_redirect_challenge_and_invalid_profiles_stop_without_retry(self):
        cases = [(self.reply(401), "needs_login"), (self.reply(403), "needs_login"),
                 (self.reply(302), "failed"), (self.reply(500), "failed"),
                 (self.reply(body={"code": "TOKEN_EXPIRED"}), "needs_login"),
                 (self.reply(body={"code": 0, "data": {"captcha": "captcha required"}}), "needs_verification")]
        for body in ({"code": True, "data": {"id": 7}}, {"code": "0", "data": {"id": 7}},
                     {"code": 0, "data": {"id": True}}, {"code": 0, "data": {"id": 0}},
                     {"code": 0, "data": {}}, {"code": 0, "data": []}, [], {"code": 7}):
            cases.append((self.reply(body=body), "failed"))
        cases.append(({"status_code": 200, "body": SECRET + "<html>bad response</html>"}, "failed"))
        for reply, status in cases:
            with self.subTest(status=status, body_type=type(reply["body"]).__name__):
                transport = Mock(return_value=reply)
                with self.assertRaises(TokenLoginError) as error:
                    prepare_token_session(token_spec(), transport=transport)
                self.assertEqual(error.exception.status, status)
                self.assertNotIn(SECRET, str(error.exception))
                transport.assert_called_once()

    def test_expired_token_stops_before_browser_or_checkin_actions(self):
        with patch("autosign.site_adapters._fetch", return_value=self.reply(401)), \
             patch("autosign.browser_runner._network_rules") as resolver:
            result = _run_browser_in_process(token_spec())
        self.assertEqual(result, {"status": "needs_login"})
        resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
