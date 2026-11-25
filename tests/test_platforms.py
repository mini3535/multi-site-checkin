"""Protocol fixtures only: never contact a platform or use real credentials."""
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet

from autosign.app import create_app
from autosign.catalog import SITES
from autosign.executors import validate_spec, execute
from autosign.platforms import PLATFORMS
from autosign.site_adapters import run_site


SECRET = "fixture-private-session"


def spec(key):
    value = {"engine": "site", "site_id": key}
    value.update({f["key"]: SECRET for f in PLATFORMS[key]["fields"]})
    if key == "qianfan":
        value["cookie"] = 'BDUSS=' + SECRET + '; bce-user-info="csrf-value"'
    if key == "huawei":
        value["cookie"] = SECRET + "; devclouddevuibjtcftk=csrf-value"
    return value


def reply(data, status=200, headers=None):
    return {"status_code": status, "headers": headers or {},
            "body": data if isinstance(data, str) else json.dumps(data)}


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError("Unexpected extra request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def wb(done):
    return reply({"code": 0, "data": {"today_checked_in": done}})


def qf(done):
    return reply({"success": True, "result": {"hasIssued": done}})


def mm(status):
    return reply({"base_resp": {"status_code": 0}, "data": {"days": [{"is_today": True, "status": status}]}})


def lx(status):
    return reply({"data": {"tasks": [{"task_key": "daily_check_in", "status": status}]}})


def hw(status="AVAILABLE", **changes):
    item = {"campaignId": 1, "type": "USER_LOGIN", "status": status, **changes}
    return reply({"code": 0, "data": {"items": [item]}})


def qd(status="CLAIMABLE", **changes):
    return reply({"campaigns": [{"campaignId": "daily-1", "actionType": "CLAIM_BENEFIT",
                                 "benefit": {"kind": "CREDITS"}, "claimStatus": status, **changes}]})


class PlatformProtocolTests(unittest.TestCase):
    def run_fixture(self, key, responses, status):
        transport = Transport(*responses)
        outcome = run_site(spec(key), transport=transport)
        self.assertEqual(outcome["status"], status, outcome)
        self.assertEqual(outcome["ok"], status in ("success", "already_done"))
        self.assertEqual(transport.responses, [], "Not all expected requests were made")
        self.assertNotIn(SECRET, json.dumps(outcome))
        return transport

    def test_validation_is_offline_rejects_injection_and_discards_foreign_urls(self):
        for key, platform in PLATFORMS.items():
            with self.subTest(platform=key), patch("socket.getaddrinfo", side_effect=AssertionError):
                normalized = validate_spec({**spec(key), "url": "http://127.0.0.1/", "headers": {"X": SECRET}})
                self.assertEqual(normalized, spec(key))
                for field in platform["fields"]:
                    for bad in (None, [], 3, "bad\r\nHost: evil", "bad\tvalue", "中文", "a" * 16385):
                        with self.assertRaises(ValueError):
                            validate_spec({**spec(key), field["key"]: bad})

    def test_missing_credentials_stops_offline(self):
        for key, platform in PLATFORMS.items():
            for field in platform["fields"]:
                with self.subTest(platform=key, field=field["key"]):
                    transport = Transport()
                    outcome = run_site({**spec(key), field["key"]: ""}, transport=transport)
                    self.assertEqual(outcome["status"], "needs_login")
                    self.assertEqual(transport.calls, [])

    def test_workbuddy_claim_checks_before_and_after_without_chat(self):
        transport = self.run_fixture("workbuddy", [wb(False), reply({"code": 0}), wb(True)], "success")
        self.assertEqual([urlsplit(c[1]).path.rsplit("/", 1)[-1] for c in transport.calls],
                         ["checkin-activity-status", "daily-checkin", "checkin-activity-status"])
        for method, url, headers, body in transport.calls:
            self.assertEqual(method, "POST")
            self.assertEqual(urlsplit(url).hostname, "copilot.tencent.com")
            self.assertEqual(headers["X-User-Id"], SECRET)
            self.assertEqual(headers["Authorization"], "Bearer " + SECRET)
            self.assertEqual(json.loads(body), {})
        self.run_fixture("workbuddy", [wb(True)], "already_done")
        self.run_fixture("workbuddy", [wb(False), reply({"code": 10001})], "already_done")

    def test_workbuddy_does_not_infer_success_from_code_zero_alone(self):
        self.run_fixture("workbuddy", [wb(False), reply({"code": 0}), wb(False)], "failed")
        self.run_fixture("workbuddy", [reply({"code": 0, "data": {}})], "unsupported")
        self.run_fixture("workbuddy", [wb(False), reply({"code": 3001, "msg": "daily chat required"})], "failed")

    def test_qianfan_direct_cookie_csrf_and_boolean_result(self):
        transport = self.run_fixture("qianfan", [qf(False), reply({"success": True, "result": True}), qf(True)], "success")
        self.assertEqual([urlsplit(c[1]).path.rsplit("/", 1)[-1] for c in transport.calls],
                         ["loginBonusInfo", "loginBonus", "loginBonusInfo"])
        self.assertTrue(all(urlsplit(c[1]).hostname == "console.bce.baidu.com" for c in transport.calls))
        self.assertEqual(transport.calls[1][2]["csrftoken"], "csrf-value")
        self.assertEqual(transport.calls[1][2]["Cookie"], spec("qianfan")["cookie"])
        self.run_fixture("qianfan", [qf(True)], "already_done")
        self.run_fixture("qianfan", [qf(False), reply({"success": True}), qf(False)], "failed")
        self.run_fixture("qianfan", [reply({"success": True, "result": {}})], "unsupported")

    def test_qianfan_expired_and_guest_sessions_never_claim(self):
        self.run_fixture("qianfan", [reply({"success": False, "message": {"redirect": "https://login.bce.baidu.com/"}})], "needs_login")
        transport = Transport()
        outcome = run_site({**spec("qianfan"), "cookie": "guest=value"}, transport=transport)
        self.assertEqual(outcome["status"], "needs_login")
        self.assertEqual(transport.calls, [])

    def test_qoder_cn_claim_and_confirmation(self):
        transport = self.run_fixture("qoder", [qd(), reply({"benefit": {"amount": 100}}), qd("CLAIMED")], "success")
        self.assertTrue(all(urlsplit(c[1]).hostname == "openapi.qoder.com.cn" for c in transport.calls))
        self.assertTrue(transport.calls[1][1].endswith("/daily-1/claim"))
        self.assertEqual(transport.calls[1][2]["Cosy-MachineId"], SECRET)
        self.run_fixture("qoder", [qd("CLAIMED")], "already_done")
        self.run_fixture("qoder", [reply({"campaigns": []})], "unsupported")
        self.run_fixture("qoder", [qd(actionType="VIEW_DETAILS")], "unsupported")

    def test_qoder_path_injection_unknown_and_rejected_claim_are_not_success(self):
        self.run_fixture("qoder", [qd(campaignId="../../elsewhere")], "unsupported")
        self.run_fixture("qoder", [qd(), reply({"code": 9004})], "failed")
        self.run_fixture("qoder", [qd(), reply({}), qd()], "failed")

    def test_minimax_signature_uses_own_device_and_exact_transmitted_body(self):
        with patch("autosign.platforms.time.time", return_value=1800000000.25):
            transport = self.run_fixture("minimax", [mm(2), reply({"base_resp": {"status_code": 0}, "data": {"claim_result": 1}})], "success")
        for method, url, headers, body in transport.calls:
            params = parse_qs(urlsplit(url).query)
            for key in ("token", "user_id", "device_id", "uuid"):
                self.assertEqual(params[key], [SECRET])
            self.assertEqual(headers["x-timestamp"], "1800000000")
            self.assertEqual(headers["x-signature"], hashlib.md5(("1800000000I*7Cf%WZ#S&%1RlZJ&C2" + body).encode()).hexdigest())
        self.run_fixture("minimax", [mm(3)], "already_done")
        self.run_fixture("minimax", [mm(1)], "unsupported")

    def test_minimax_requires_real_result_types(self):
        for result in (True, False, "1", None, 0):
            with self.subTest(result=result):
                self.run_fixture("minimax", [mm(2), reply({"base_resp": {"status_code": 0}, "data": {"claim_result": result}})], "failed")
        self.run_fixture("minimax", [mm(2), reply({"base_resp": {"status_code": 0}, "data": {"claim_result": 2}})], "already_done")

    def test_linkai_captcha_and_idempotence_do_not_trigger_solver_or_extra_requests(self):
        for payload, state in (({"success": True}, "success"), ({"code": 834}, "already_done"),
                               ({"code": 870}, "needs_verification"), ({"code": 999}, "failed"),
                               ({"success": 1}, "failed"), ({}, "failed")):
            with self.subTest(payload=payload):
                transport = self.run_fixture("linkai", [reply(payload)], state)
                self.assertEqual(len(transport.calls), 1)

    def test_lingxi_checks_task_before_and_after_claim_with_shanghai_date(self):
        transport = self.run_fixture("lingxi", [lx("incomplete"), reply({"data": {}}), lx("claimed")], "success")
        dates = [parse_qs(urlsplit(c[1]).query)["date"] for c in transport.calls]
        self.assertEqual(dates[0], dates[1])
        self.assertEqual(dates[0], dates[2])
        self.run_fixture("lingxi", [lx("claimed")], "already_done")
        self.run_fixture("lingxi", [lx("incomplete"), reply({}), lx("incomplete")], "failed")
        self.run_fixture("lingxi", [lx("locked")], "unsupported")

    def test_trae_exchanges_cookie_once_and_sends_actual_device_id(self):
        transport = self.run_fixture("trae", [reply({"Result": {"Token": "fresh-session-token"}}),
                                               reply({"code": 0, "checked_in": False}), reply({"code": 0})], "success")
        self.assertIn("Cookie", transport.calls[0][2])
        self.assertNotIn("Cookie", transport.calls[1][2])
        self.assertEqual(transport.calls[1][2]["Authorization"], "Cloud-IDE-JWT fresh-session-token")
        self.assertEqual(transport.calls[2][2]["x-device-id"], SECRET)
        self.assertEqual(json.loads(transport.calls[2][3]), {"req_source": 2})
        self.run_fixture("trae", [reply({"Result": {"Token": "fresh"}}), reply({"code": 0, "checked_in": True})], "already_done")

    def test_trae_reject_codes_are_not_already_done_and_not_retried(self):
        for code in (9004, 9074, False, "0", None):
            with self.subTest(code=code):
                self.run_fixture("trae", [reply({"Result": {"Token": "fresh"}}),
                                          reply({"code": 0, "checked_in": False}), reply({"code": code})], "failed")
        self.run_fixture("trae", [reply({"Result": {"Token": "fresh"}}), reply({"code": 0})], "unsupported")

    def test_coze_requires_daily_grant_flag_and_does_not_chat(self):
        for data, status in (({"granted_this_time": True}, "success"), ({"granted_today": True}, "already_done"),
                             ({"granted_today": "true"}, "unsupported"), ({}, "unsupported")):
            with self.subTest(data=data):
                transport = self.run_fixture("coze", [reply({"code": 0, "data": {"activity_grant_infos": [
                    {"activity_type": 2, **data}]}})], status)
                self.assertEqual(json.loads(transport.calls[0][3]), {"activity_types": [2], "source": "web_new"})
                self.assertEqual(len(transport.calls), 1)
        self.run_fixture("coze", [reply({"code": 0, "data": {}})], "unsupported")

    def test_huawei_uses_csrf_and_requires_both_claim_and_confirm(self):
        transport = self.run_fixture("huawei", [hw(), reply({"code": 0}), reply({"code": 0}), hw("CONFIRMED")], "success")
        self.assertEqual(transport.calls[0][2]["cftk"], "csrf-value")
        claim = json.loads(transport.calls[1][3])
        self.assertEqual(claim["campaignId"], 1)
        self.assertEqual(claim["channel"], "WEB")
        self.assertTrue(claim["idempotentKey"].startswith("claim_1_"))
        self.assertEqual(json.loads(transport.calls[2][3]), {"campaignId": 1})
        self.run_fixture("huawei", [hw("CLAIMED")], "already_done")
        self.run_fixture("huawei", [hw(), reply({"code": 0}), reply({"code": 1})], "failed")
        self.run_fixture("huawei", [hw(), reply({"code": 1})], "failed")

    def test_huawei_dynamic_campaign_and_sub_benefits_require_readback(self):
        pending = [{"benefitId": "first", "status": "ELIGIBLE"}, {"benefitId": "second", "status": "ELIGIBLE"}]
        before = hw("ELIGIBLE", campaignId=34, claimActions=pending)
        after = hw("ELIGIBLE", campaignId=34, claimActions=[{**a, "status": "CLAIMED"} for a in pending])
        transport = self.run_fixture("huawei", [before, reply({"code": 0}), reply({"code": 0}), reply({"code": 0}), after], "success")
        self.assertEqual([json.loads(c[3])["benefitId"] for c in transport.calls[1:3]], ["first", "second"])
        self.assertTrue(all(json.loads(c[3])["campaignId"] == 34 for c in transport.calls[1:4]))
        self.run_fixture("huawei", [hw(), reply({"code": 0}), reply({"code": 0}), hw()], "failed")
        self.run_fixture("huawei", [hw(type="SUBSCRIBE", campaignId=1)], "unsupported")
        self.run_fixture("huawei", [hw("ELIGIBLE", claimActions=pending * 4)], "unsupported")

    def test_cross_platform_http_login_redirect_challenge_and_malformed_response(self):
        for key in PLATFORMS:
            for response, expected in ((reply({}, 401), "needs_login"),
                                       (reply("", 302, {"Location": "https://login.example/"}), "needs_login"),
                                       (reply("", 302, {"Location": "https://foreign.example/"}), "unsupported"),
                                       (reply("请先登录"), "needs_login"),
                                       (reply("请完成人机验证"), "needs_verification"),
                                       (reply({"code": 20113}), "needs_login"),
                                       (reply({}, 500), "failed"), (reply("[]"), "unsupported"),
                                       (reply("not json"), "unsupported"),
                                       (reply("x" * (1024 * 1024 + 1)), "unsupported")):
                with self.subTest(platform=key, expected=expected):
                    self.run_fixture(key, [response], expected)

    def test_remote_secrets_and_exceptions_never_appear_in_results(self):
        for key in PLATFORMS:
            for response in (RuntimeError(SECRET), TimeoutError(SECRET), reply({"message": SECRET}),
                             reply({}, 403)):
                with self.subTest(platform=key):
                    transport = Transport(response)
                    outcome = run_site(spec(key), transport=transport)
                    self.assertFalse(outcome["ok"])
                    self.assertNotIn(SECRET, json.dumps(outcome))
                    self.assertEqual(len(transport.calls), 1)

    def test_dispatch_uses_bounded_transport_and_does_not_accept_spec_url(self):
        with patch("autosign.site_adapters._fetch", return_value=reply({"code": 834})) as fetch:
            outcome = run_site({**spec("linkai"), "url": "http://127.0.0.1/"})
        self.assertEqual(outcome["status"], "already_done")
        self.assertEqual(fetch.call_args.args[1], "https://link-ai.tech/api/chat/web/app/user/sign/in")
        self.assertEqual(fetch.call_args.kwargs["allowed_requests"], {(fetch.call_args.args[0], fetch.call_args.args[1])})

    def test_process_dispatch_accepts_platform_and_masks_child_messages(self):
        child = unittest.mock.Mock()
        child.returncode = 0
        child.poll.return_value = 0
        child.communicate.return_value = (json.dumps({"ok": True, "status": "already_done", "status_code": 200,
                                                     "message": SECRET}).encode(), b"")
        with patch("autosign.executors.subprocess.Popen", return_value=child):
            outcome = execute(spec("linkai"))
        self.assertEqual(outcome["status"], "already_done")
        self.assertNotIn(SECRET, json.dumps(outcome))


class PlatformFormTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"DATA_DIR": self.temp.name, "ADMIN_PASSWORD": "platform-test-password",
                               "ENCRYPTION_KEY": Fernet.generate_key().decode(), "TESTING": True,
                               "ALLOW_PRIVATE_TARGETS": False})
        self.client = self.app.test_client()
        self.store = self.app.extensions["store"]
        self.client.get("/login")
        self.client.post("/login", data={"csrf_token": self.csrf(), "password": "platform-test-password"})
        self.client.get("/sites")

    def tearDown(self):
        self.temp.cleanup()

    def csrf(self):
        with self.client.session_transaction() as session:
            return session.get("csrf", "")

    def fields(self, key, **changes):
        values = {"csrf_token": self.csrf(), "name": PLATFORMS[key]["name"],
                  "daily_time": "08:30", "timezone": "Asia/Shanghai", **spec(key), **changes}
        return values

    def test_catalog_has_all_ten_platforms_and_labels_unsupported(self):
        page = self.client.get("/sites").get_data(as_text=True)
        for key in (*PLATFORMS, "doubao"):
            self.assertIn(SITES[key]["name"], page)
        self.assertIn("暂不自动签到", page)
        self.assertEqual(self.client.get("/sites/doubao/new").status_code, 404)

    def test_all_forms_save_encrypted_and_pause_new_tasks_without_json_steps(self):
        for key in PLATFORMS:
            with self.subTest(platform=key):
                response = self.client.get(f"/sites/{key}/new")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('name="steps"', response.get_data(as_text=True))
                response = self.client.post(f"/sites/{key}/new", data=self.fields(key, enabled="on"))
                self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
                task = self.store.list_tasks()[0]
                self.assertFalse(task["enabled"])
                self.assertEqual(self.store.spec(task), spec(key))
                self.assertNotIn(SECRET, task["sealed_spec"])
                self.assertNotIn(SECRET.encode(), self.store.path.read_bytes())
                for path in ("/", f"/tasks/{task['id']}/site-edit"):
                    self.assertNotIn(SECRET, self.client.get(path).get_data(as_text=True))

    def test_blank_edit_preserves_credentials_and_clear_pauses(self):
        for key in PLATFORMS:
            with self.subTest(platform=key):
                self.client.post(f"/sites/{key}/new", data=self.fields(key))
                task = self.store.list_tasks()[0]
                blank = {f["key"]: "" for f in PLATFORMS[key]["fields"]}
                response = self.client.post(f"/tasks/{task['id']}/site-edit", data=self.fields(key, **blank, enabled="on"))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.store.spec(self.store.get_task(task["id"])), spec(key))
                response = self.client.post(f"/tasks/{task['id']}/site-edit", data=self.fields(key, **blank, clear_session="1", enabled="on"))
                self.assertEqual(response.status_code, 302)
                current = self.store.get_task(task["id"])
                self.assertFalse(current["enabled"])
                self.assertTrue(all(not self.store.spec(current)[f["key"]] for f in PLATFORMS[key]["fields"]))
                page = self.client.get(f"/tasks/{task['id']}/site-edit").get_data(as_text=True)
                self.assertNotIn('class="credential-note"', page)

    def test_invalid_form_masks_all_credentials_and_does_not_save(self):
        for key in PLATFORMS:
            with self.subTest(platform=key):
                response = self.client.post(f"/sites/{key}/new", data=self.fields(key, daily_time="bad"))
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(SECRET, response.get_data(as_text=True))
                first = PLATFORMS[key]["fields"][0]["key"]
                response = self.client.post(f"/sites/{key}/new", data=self.fields(key, **{first: "   "}))
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(SECRET, response.get_data(as_text=True))
        self.assertEqual(self.store.list_tasks(), [])

    def test_partial_credential_update_and_csrf_authentication(self):
        self.client.post("/sites/workbuddy/new", data=self.fields("workbuddy"))
        task = self.store.list_tasks()[0]
        response = self.client.post(f"/tasks/{task['id']}/site-edit", data=self.fields("workbuddy", token="rotated-token", uid=""))
        self.assertEqual(response.status_code, 302)
        saved = self.store.spec(self.store.get_task(task["id"]))
        self.assertEqual(saved["token"], "rotated-token")
        self.assertEqual(saved["uid"], SECRET)
        for key in PLATFORMS:
            self.assertEqual(self.app.test_client().get(f"/sites/{key}/new").status_code, 302)
            self.assertEqual(self.client.post(f"/sites/{key}/new", data=self.fields(key, csrf_token="")).status_code, 400)


if __name__ == "__main__":
    unittest.main()
