import json
"""Offline lifecycle regression tests; no HTTP requests or live state mutations."""
import copy
from contextlib import ExitStack
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import autotune as a

# offline tests never read the user's live pick list
a.CONFIG["selected_models_file"] = None

# the real function, captured before test setUp replaces it with a stub
_REAL_ADMISSION_CHECKS = a.admission_checks


class _LifecycleBase(unittest.TestCase):
    """Shared offline fixture (no tests of its own)."""

    def setUp(self):
        self.now = a.now_utc()
        # fixed fixture: lifecycle logic is tested with two preferred models regardless of the live CONFIG
        self.models = ["antigravity/gemini-3.8-flash-medium", "antigravity/gpt-oss-120b-medium"]
        patcher = patch.dict(a.CONFIG, {"preferred_models": self.models, "pinned_models": [],
                                        "provider_only_models": {},
                                        "max_per_provider_override": {"antigravity": 3}})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = {"probes": {m: self.good_probe() for m in self.models}}
        self.conns = {"antigravity": [{"id": "test", "active": True, "test": "ok", "rl_until": None}]}
        self.cat = {m: {"context_length": 1048576, "capabilities": {"tool_calling": True}}
                    for m in self.models}
        self.hist = {}
        self.quota = {}
        self.patches = [patch.object(a, "now_utc", side_effect=lambda: self.now),
                        patch.object(a, "resilience", {"open": {}, "locked": {}}),
                        # extra admission checks are covered by AdmissionChecksTests; keep these tests offline
                        patch.object(a, "admission_checks", return_value=("", False))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def good_probe(self):
        return {"ts": self.now.isoformat(), "ok": True, "status": 200, "latency": 20.0,
                "err": "", "provider": "antigravity"}

    def choose(self, dry=False):
        return a.preferred_candidates(self.state, self.cat, self.hist, self.quota, self.conns, {}, dry)


class PreferredLifecycleTests(_LifecycleBase):
    def test_user_order_survives_provider_diversity_and_lower_score(self):
        chosen, out, _ = self.choose()
        other = {**chosen[0], "model": "grok-cli/grok-4.7", "provider": "grok-cli", "score": 0.99}
        other.pop("preferred_rank")
        ordered = a.order_for_fallback(chosen + [other], self.state["probes"])
        self.assertEqual([d["model"] for d in ordered[:2]], self.models)
        self.assertFalse(out)

    def test_gemini_quota_does_not_remove_gpt(self):
        self.quota = {"antigravity": {"gemini_weekly": {"remaining": 0, "exhausted": True,
                     "reset": (self.now + timedelta(hours=1)).isoformat()}}}
        with patch.object(a, "probe") as probe:
            chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        self.assertEqual(out[0]["model"], self.models[0])
        probe.assert_not_called()

    def test_medium_uses_tiered_quota_without_affecting_gpt(self):
        self.quota = {"antigravity": {"gemini-3.8-flash-tiered": {"remaining": 0, "exhausted": True,
                     "reset": (self.now + timedelta(hours=1)).isoformat()}}}
        chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        self.assertIn("tiered", out[0]["why"])

    def test_fresh_positive_quota_triggers_probe_before_old_reset(self):
        window = {"remaining": 0, "exhausted": True, "reset": (self.now + timedelta(hours=1)).isoformat()}
        self.quota = {"antigravity": {"gemini_weekly": window}}
        self.choose()
        window.update(remaining=100, exhausted=False)
        with patch.object(a, "probe", return_value=self.good_probe()) as probe:
            chosen, _, _ = self.choose()
        probe.assert_called_once_with(self.models[0])
        self.assertEqual(chosen[0]["model"], self.models[0])

    def test_exact_medium_bucket_wins_over_tiered_sibling(self):
        self.quota = {"antigravity": {
            "gemini-3.8-flash-medium": {"remaining": 100, "exhausted": False, "reset": None},
            "gemini-3.8-flash-tiered": {"remaining": 0, "exhausted": True, "reset": None}}}
        chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], self.models)
        self.assertFalse(out)

    def test_one_exhausted_account_does_not_remove_model_from_other_accounts(self):
        self.quota = {"antigravity": {"__accounts__": {
            "account-a": {"gemini-3.8-flash-tiered": {"remaining": 0, "exhausted": True, "reset": None}},
            "account-b": {"gemini-3.8-flash-tiered": {"remaining": 80, "exhausted": False, "reset": None}}}}}
        chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], self.models)
        self.assertEqual(chosen[0]["quota"], 80)
        self.assertFalse(out)

    def test_all_accounts_exhausted_suspends_only_affected_family(self):
        reset = (self.now + timedelta(hours=1)).isoformat()
        self.quota = {"antigravity": {"__accounts__": {
            name: {"gemini-3.8-flash-tiered": {"remaining": 0, "exhausted": True, "reset": reset}}
            for name in ("account-a", "account-b")}}}
        chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        self.assertEqual(out[0]["until"], reset)

    def test_reset_requires_successful_probe_before_original_rank_returns(self):
        self.quota = {"antigravity": {"gemini_weekly": {"remaining": 0, "exhausted": True,
                     "reset": (self.now + timedelta(hours=1)).isoformat()}}}
        self.choose()
        self.now += timedelta(hours=2)
        # Expired quota snapshot must not permanently exclude the model.
        with patch.object(a, "probe", return_value=self.good_probe()) as probe:
            chosen, out, _ = self.choose()
        probe.assert_called_once_with(self.models[0])
        self.assertEqual([d["model"] for d in chosen], self.models)
        self.assertFalse(out)

    def test_failed_recovery_stays_out_and_is_not_hammered(self):
        self.state["preferred_status"] = {self.models[0]: {
            "suspended": True, "retry_at": (self.now - timedelta(minutes=1)).isoformat(), "reason": "quota"}}
        failed = {**self.good_probe(), "ok": False, "status": 429, "err": "quota still exhausted"}
        with patch.object(a, "probe", return_value=failed) as probe:
            chosen, out, _ = self.choose()
            self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        self.assertTrue(out)
        self.assertEqual(probe.call_count, 1)

    def test_new_production_failure_beats_old_successful_probe(self):
        self.hist[self.models[0]] = {"last": {"ts": (self.now + timedelta(seconds=1)).isoformat(),
                                            "status": 429, "err": "quota exhausted"}}
        with patch.object(a, "probe") as probe:
            chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        self.assertIn("429", out[0]["why"])
        probe.assert_not_called()

    def test_unknown_reset_retries_after_interval(self):
        self.quota = {"antigravity": {"gemini_weekly": {"remaining": 0, "exhausted": True, "reset": None}}}
        self.choose()
        retry = self.state["preferred_status"][self.models[0]]["retry_at"]
        self.now += timedelta(minutes=20)
        self.choose()
        self.assertEqual(self.state["preferred_status"][self.models[0]]["retry_at"], retry)
        self.now += timedelta(minutes=11)
        self.quota = {}
        with patch.object(a, "probe", return_value=self.good_probe()) as probe:
            chosen, _, _ = self.choose()
        probe.assert_called_once_with(self.models[0])
        self.assertEqual(chosen[0]["model"], self.models[0])

    def test_dry_run_never_probes_or_reintroduces_suspended_model(self):
        self.state["preferred_status"] = {self.models[0]: {
            "suspended": True, "reason": "quota", "retry_at": (self.now - timedelta(minutes=1)).isoformat()}}
        with patch.object(a, "probe") as probe:
            chosen, out, _ = self.choose(dry=True)
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        self.assertTrue(out)
        probe.assert_not_called()

    def test_health_checker_sees_recovery_without_any_traffic(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("create table call_logs(requested_model, path, status, duration, timestamp)")
        with patch.object(a, "quotas", return_value={}):
            reasons = a.preferred_check_reasons(self.state, con, set())
        self.assertEqual(len(reasons), 2)

    def test_persisted_suspension_cannot_bypass_recovery_test(self):
        self.state["preferred_status"] = {self.models[0]: {
            "suspended": True, "reason": "quota", "retry_at": (self.now - timedelta(minutes=1)).isoformat()}}
        self.state = copy.deepcopy(self.state)
        with patch.object(a, "probe", return_value=self.good_probe()) as probe:
            self.choose()
        probe.assert_called_once_with(self.models[0])


class AdmissionChecksTests(PreferredLifecycleTests):
    """2026-10-28: a preferred model is admitted only after json_schema + repeated tool-call probes."""

    def suspended_state(self):
        self.state["preferred_status"] = {self.models[0]: {
            "suspended": True, "reason": "quota", "retry_at": (self.now - timedelta(minutes=1)).isoformat()}}

    def test_json_schema_failure_keeps_model_out_for_hours_not_minutes(self):
        self.suspended_state()
        failing = ("json_schema probe failed (400): name must be non-empty", True)
        with patch.object(a, "probe", return_value=self.good_probe()), \
                patch.object(a, "admission_checks", return_value=failing):
            chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        retry = a.parse_ts(self.state["preferred_status"][self.models[0]]["retry_at"])
        self.assertGreaterEqual(retry - self.now, timedelta(hours=a.CONFIG["preferred_admission_fail_hours"]) - timedelta(seconds=1))
        self.assertIn("json_schema", out[0]["why"])

    def test_flaky_admission_failure_retries_after_normal_interval(self):
        self.suspended_state()
        with patch.object(a, "probe", return_value=self.good_probe()), \
                patch.object(a, "admission_checks", return_value=("probe 2/2 failed (429): quota", False)):
            chosen, _, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[1]])
        retry = a.parse_ts(self.state["preferred_status"][self.models[0]]["retry_at"])
        self.assertEqual(retry - self.now, timedelta(minutes=a.CONFIG["preferred_retry_minutes"]))

    def test_passing_admission_readmits_in_original_rank(self):
        self.suspended_state()
        with patch.object(a, "probe", return_value=self.good_probe()), \
                patch.object(a, "admission_checks", return_value=("", False)):
            chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], self.models)
        self.assertFalse(out)

    def test_checks_reject_model_whose_json_schema_call_fails_with_400(self):
        bad = {"ts": "", "ok": False, "status": 400, "latency": 1.0, "err": "name non-empty"}
        with patch.object(a, "probe_json_schema", return_value=bad):
            why, deterministic = self._real_admission(self.models[0])
        self.assertIn("json_schema", why)
        self.assertTrue(deterministic)

    def test_checks_treat_json_schema_timeout_as_not_deterministic(self):
        slow = {"ts": "", "ok": False, "status": 0, "latency": 90.0, "err": "TimeoutError"}
        with patch.object(a, "probe_json_schema", return_value=slow):
            why, deterministic = self._real_admission(self.models[0])
        self.assertTrue(why)
        self.assertFalse(deterministic)

    def test_checks_require_repeated_tool_probe_success(self):
        ok_json = {"ts": "", "ok": True, "status": 200, "latency": 3.0, "err": ""}
        failed_second = {"ts": "", "ok": False, "status": 429, "latency": 20.0, "err": "exhausted"}
        with patch.object(a, "probe_json_schema", return_value=ok_json), \
                patch.object(a, "probe", return_value=failed_second) as probe:
            why, deterministic = self._real_admission(self.models[0])
        self.assertIn("probe 2/2", why)
        self.assertFalse(deterministic)
        probe.assert_called_once_with(self.models[0])

    def test_checks_pass_when_everything_succeeds(self):
        good = {"ts": "", "ok": True, "status": 200, "latency": 3.0, "err": ""}
        with patch.object(a, "probe_json_schema", return_value=good), patch.object(a, "probe", return_value=good):
            self.assertEqual(self._real_admission(self.models[0]), ("", False))

    @staticmethod
    def _real_admission(model):
        return _REAL_ADMISSION_CHECKS(model)


class PinnedModelTests(_LifecycleBase):
    """2026-10-29 user: gemini-3.8-flash-medium is always #1 and the only antigravity model, slow or not."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(a.CONFIG, {"preferred_models": [self.models[0]], "pinned_models": [self.models[0]],
                                        "provider_only_models": {"antigravity": [self.models[0]]}})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pinned_kept_when_quota_exhausted_and_never_probed(self):
        self.quota = {"antigravity": {"gemini_weekly": {"remaining": 0, "exhausted": True,
                     "reset": (self.now + timedelta(hours=1)).isoformat()}}}
        with patch.object(a, "probe") as probe:
            chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[0]])
        self.assertEqual(chosen[0]["preferred_rank"], 1)
        self.assertFalse(out)
        probe.assert_not_called()

    def test_pinned_kept_when_slow_and_last_call_failed(self):
        self.state["probes"][self.models[0]] = {**self.good_probe(), "latency": 200.0}
        self.hist = {self.models[0]: {"n": 5, "last": {"status": 504, "ts": self.now.isoformat(), "err": "slow"}}}
        chosen, out, _ = self.choose()
        self.assertEqual([d["model"] for d in chosen], [self.models[0]])
        self.assertFalse(out)

    def test_pinned_agy_model_reports_its_antigravity_logged_history(self):
        agy = "agy/gemini-3.8-flash-medium"
        self.hist = {"antigravity/gemini-3.8-flash-medium": {"n": 10, "ok": 9, "ok_big7": 4, "last": None}}
        with patch.dict(a.CONFIG, {"preferred_models": [agy], "pinned_models": [agy],
                                   "provider_only_models": {}, "provider_groups": {"agy": "antigravity"}}), \
                patch.object(a, "evaluate", return_value=(None, {})):
            chosen, _, _ = self.choose()
        self.assertEqual((chosen[0]["n48h"], chosen[0]["success"]), (10, 0.9))

    def test_pinned_first_in_fallback_order(self):
        chosen, _, _ = self.choose()
        other = {**chosen[0], "model": "grok-cli/grok-4.7", "provider": "grok-cli", "score": 0.99}
        other.pop("preferred_rank")
        ordered = a.order_for_fallback([other] + chosen, self.state["probes"])
        self.assertEqual(ordered[0]["model"], self.models[0])

    def test_provider_only_models_blocks_other_antigravity_models(self):
        self.assertTrue(a.provider_allows("antigravity", self.models[0]))
        self.assertFalse(a.provider_allows("antigravity", "antigravity/gemini-3-flash"))
        # 2026-10-29 user: agy is grouped with antigravity (same executor, accounts and cooldowns)
        self.assertFalse(a.provider_allows("agy", "agy/gemini-3-flash"))
        self.assertFalse(a.provider_allows("agy", "agy/gemini-3.8-flash-high"))
        self.assertTrue(a.provider_allows("grok-cli", "grok-cli/grok-4.7"))
        self.assertEqual(a.provider_group("agy"), "antigravity")
        self.assertEqual(a.provider_group("grok-cli"), "grok-cli")

    def test_pinned_failures_do_not_trigger_retune_reasons(self):
        con = sqlite3.connect(":memory:")
        con.execute("create table call_logs (requested_model text, path text, status int, duration int, timestamp text)")
        con.execute("insert into call_logs values (?, '/v1/chat/completions', 429, 1000, ?)",
                    (self.models[0], self.now.isoformat()))
        with patch.object(a, "quotas", return_value={}):
            self.assertEqual(a.preferred_check_reasons(self.state, con, {self.models[0]}), [])


class RequiredToolGateTests(unittest.TestCase):
    """2026-10-29: a model that writes the tool call as text under tool_choice=required broke Hindsight reflect."""
    M = "kilocode/nvidia/nemotron-3-ultra-550b-a55b:free"

    def reply(self, status=200, calls=True):
        msg = {"tool_calls": [{"function": {"name": "search_observations", "arguments": '{"query": "operating system"}'}}]} if calls else \
              {"content": '[{"name": "search_observations", "parameters": {}}]'}
        return status, {"choices": [{"message": msg}]}, 2.0

    def test_text_only_tool_call_fails_and_is_cached(self):
        state = {}
        with patch.object(a, "api", return_value=self.reply(calls=False)) as api, patch.object(a, "log"):
            self.assertEqual(a.required_tool_ok(state, self.M, False)[0], False)
            self.assertEqual(a.required_tool_ok(state, self.M, False)[0], False)  # cached: no second probe
        self.assertEqual(api.call_count, 1)

    def test_pass_needs_two_real_tool_calls(self):
        with patch.object(a, "api", return_value=self.reply()) as api, patch.object(a, "log"):
            self.assertEqual(a.required_tool_ok({}, self.M, False), (True, ""))
        self.assertEqual(api.call_count, 2)

    def test_recent_failure_needs_three_passes(self):
        old = (a.now_utc() - timedelta(hours=7)).isoformat()
        state = {"required_tool": {"m": {"ok": False, "ts": old, "err": "text"}}}
        ok = {"ts": a.now_utc().isoformat(), "ok": True, "status": 200, "latency": 1.0, "err": ""}
        with patch.object(a, "probe_required_tool", return_value=ok) as pr, patch.object(a, "log"):
            self.assertTrue(a.required_tool_ok(state, "m", False)[0])
        self.assertEqual(pr.call_count, 3)
        self.assertEqual(state["required_tool"]["m"]["last_fail"], old)

    def test_unpaid_or_rate_limited_is_not_cached_as_failure(self):
        state = {}
        with patch.object(a, "api", return_value=self.reply(status=402)):
            self.assertTrue(a.required_tool_ok(state, self.M, False)[0])
        self.assertNotIn(self.M, state.get("required_tool", {}))

    def test_probe_sends_required_tool_choice_and_no_memory_header(self):
        with patch.object(a, "api", return_value=self.reply()) as api:
            a.probe_required_tool(self.M)
        body, headers = api.call_args.args[2], api.call_args.kwargs["headers"]
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual(body["reasoning_effort"], a.CONFIG["probe_effort"])
        self.assertEqual(headers.get("x-omniroute-no-memory"), "true")

    def test_truncated_required_tool_is_not_cached(self):
        body = {"choices": [{"finish_reason": "length", "message": {"content": None}}]}
        state = {}
        with patch.object(a, "api", return_value=(200, body, 1)), patch.object(a, "log"):
            ok, why = a.required_tool_ok(state, self.M, False)
        self.assertTrue(ok)
        self.assertIn("truncat", why.lower())
        self.assertNotIn(self.M, state.get("required_tool", {}))

    def test_transient_required_tool_errors_are_not_cached(self):
        for status in (500, 502, 503, 504):
            state = {}
            with self.subTest(status=status), patch.object(a, "api", return_value=self.reply(status=status)):
                ok, why = a.required_tool_ok(state, self.M, False)
                self.assertTrue(ok)
                self.assertIn("not checked", why)
                self.assertNotIn(self.M, state.get("required_tool", {}))


class SlowSuccessTests(unittest.TestCase):
    def history_of(self, rows):
        con = sqlite3.connect(":memory:")
        con.execute("create table call_logs (requested_model text, provider text, status int, duration int, tokens_in int,"
                    " timestamp text, error_summary text, path text)")
        con.executemany("insert into call_logs values (?,?,?,?,?,?,?, '/v1/chat/completions')", rows)
        con.create_function("datetime", 2, lambda a_, b_: "2000-01-01")
        return a.history(con)

    def test_very_slow_success_counts_as_hang_and_does_not_clear_it(self):
        now = a.now_utc()
        t = lambda m: (now - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        m = "anyapi/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
        h = self.history_of([(m, "anyapi", 499, 299000, 9000, t(10), ""), (m, "anyapi", 200, 260000, 9000, t(5), "")])[m]
        self.assertEqual(h["last_hang"], t(5))
        self.assertLess(h.get("last_ok") or "", h["last_hang"])

    def test_normal_success_still_clears(self):
        now = a.now_utc()
        t = lambda m: (now - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        m = "agy/gemini-3-flash"
        h = self.history_of([(m, "agy", 499, 299000, 9000, t(10), ""), (m, "agy", 200, 3000, 9000, t(5), "")])[m]
        self.assertGreater(h["last_ok"], h["last_hang"])


class HangAliasTests(unittest.TestCase):
    def test_base_model_matches_aliases_of_one_provider(self):
        self.assertEqual(a.base_model("kilocode/cohere/north-mini-code:free"), a.base_model("kc/cohere/north-mini-code:free"))


class NoMemoryHeaderTests(unittest.TestCase):
    """Every chat call must opt out of OmniRoute memory injection, or memory_* tools change the reply."""

    def chat_headers(self, call):
        with patch.object(a, "api", return_value=(200, {"model": "m", "choices": []}, 1.0)) as api:
            call()
        chats = [c for c in api.call_args_list if c.args[1] == "/v1/chat/completions"]
        self.assertTrue(chats)
        return [c.kwargs.get("headers") or {} for c in chats]

    def test_verify_combo_sends_no_memory_header(self):
        for h in self.chat_headers(lambda: a.verify_combo("hermes-fast-agent")):
            self.assertEqual(h.get("x-omniroute-no-memory"), "true")
            self.assertEqual(h.get("x-omniroute-no-cache"), "true")

    def test_probe_sends_no_memory_header(self):
        for h in self.chat_headers(lambda: a.probe("antigravity/gemini-3-flash")):
            self.assertEqual(h.get("x-omniroute-no-memory"), "true")


class HindsightLaneTests(unittest.TestCase):
    """2026-10-29: Hindsight gets its own combo of models that pass a Hindsight-shaped structured-output probe."""

    def test_structured_lane_keeps_order_and_only_passing_models(self):
        sel = [{"model": "antigravity/gemini-3.8-flash-medium", "provider": "antigravity", "quality": 0.6, "structured_ok": True},
               {"model": "kilocode/cohere/north-mini-code:free", "provider": "kilocode", "quality": 0.5, "structured_ok": False},
               {"model": "grok-cli/grok-4.7", "provider": "grok-cli", "quality": 0.8, "structured_ok": True}]
        lanes = a.lane_orders(sel)
        hs = [order for lane, order in lanes if lane.get("structured")]
        self.assertEqual([d["model"] for d in hs[0]], ["antigravity/gemini-3.8-flash-medium", "grok-cli/grok-4.7"])

    def test_recent_hangs_counts_long_499_only_within_window(self):
        now = a.now_utc()
        con = sqlite3.connect(":memory:")
        con.execute("create table call_logs (requested_model text, status int, duration int, timestamp text)")
        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        con.executemany("insert into call_logs values (?, ?, ?, ?)", [
            ("kc/north", 499, 299000, (now - timedelta(hours=2)).strftime(fmt)),
            ("kc/north", 499, 299000, (now - timedelta(hours=3)).strftime(fmt)),
            ("kc/north", 499, 5000, (now - timedelta(hours=1)).strftime(fmt)),     # hedge cancel, not a hang
            ("old/model", 499, 299000, (now - timedelta(hours=60)).strftime(fmt)),  # outside 48 h
            ("ok/model", 200, 299000, (now - timedelta(hours=1)).strftime(fmt))])
        self.assertEqual(a.recent_hangs(con), {"kc/north": 2})

    def test_hang_pattern_needs_repeats_and_spares_pinned(self):
        hangs = {"kc/north": 24, "grok-cli/grok-4.7": 1, "antigravity/gemini-3.8-flash-medium": 2}
        with patch.dict(a.CONFIG, {"pinned_models": ["antigravity/gemini-3.8-flash-medium"], "structured_hang_limit": 3}):
            self.assertTrue(a.hang_pattern("kc/north", hangs))
            self.assertFalse(a.hang_pattern("grok-cli/grok-4.7", hangs))
            self.assertFalse(a.hang_pattern("antigravity/gemini-3.8-flash-medium", {"antigravity/gemini-3.8-flash-medium": 9}))

    def test_structured_lane_skipped_when_nothing_passes(self):
        sel = [{"model": "kc/x", "provider": "kilocode", "quality": 0.5, "structured_ok": False}]
        self.assertFalse([l for l, _ in a.lane_orders(sel) if l.get("structured")])

    def test_structured_ok_caches_and_rate_limit_gives_no_verdict(self):
        state = {}
        fail = {"ts": a.now_utc().isoformat(), "ok": False, "status": 200, "latency": 300.0, "err": "too slow"}
        with patch.object(a, "probe_structured", return_value=fail) as pr, patch.object(a, "log"):
            self.assertEqual(a.structured_ok(state, "m", False), (False, "too slow"))
            self.assertEqual(a.structured_ok(state, "m", False), (False, "too slow"))
        self.assertEqual(pr.call_count, 1)  # cached
        limited = {**fail, "status": 429, "err": "rate"}
        with patch.object(a, "probe_structured", return_value=limited), patch.object(a, "log"):
            ok, why = a.structured_ok({}, "m2", False)
        self.assertFalse(ok)
        self.assertIn("429", why)

    def test_probe_structured_rejects_slow_valid_answer(self):
        body = {"choices": [{"message": {"content": '{"facts": [{"what": "x", "when": "y", "who": "z"}]}'}}]}
        with patch.object(a, "api", return_value=(200, body, 150.0)):
            res = a.probe_structured("m")
        self.assertFalse(res["ok"])
        self.assertIn("too slow", res["err"])
        with patch.object(a, "api", return_value=(200, body, 12.0)):
            self.assertTrue(a.probe_structured("m")["ok"])

    def test_truncated_structured_response_is_inconclusive_and_not_cached(self):
        body = {"choices": [{"finish_reason": "length", "message": {"content": '{"facts":[{"what":"incomplete'}}]}
        state = {}
        with patch.object(a, "api", return_value=(200, body, 2.0)) as api, patch.object(a, "log"):
            ok, why = a.structured_ok(state, "m", False)
        self.assertFalse(ok)
        self.assertIn("truncat", why.lower())
        self.assertNotIn("m", state.get("structured", {}))
        self.assertGreaterEqual(api.call_args.args[2]["max_tokens"], 4096)
        self.assertEqual(api.call_args.args[2]["reasoning_effort"], a.CONFIG["probe_effort"])

    def test_transient_structured_errors_are_not_cached(self):
        for status in (0, 500, 502, 503, 504):
            state = {}
            response = {"ts": a.now_utc().isoformat(), "ok": False, "status": status, "latency": 1, "err": "temporary"}
            with self.subTest(status=status), patch.object(a, "probe_structured", return_value=response), patch.object(a, "log"):
                ok, why = a.structured_ok(state, "m", False)
                self.assertFalse(ok)
                self.assertIn("not checked", why)
                self.assertNotIn("m", state.get("structured", {}))


class SelectedModelsTests(unittest.TestCase):
    """2026-10-29 user: candidates come only from the models picked on the picker page."""

    def test_only_picked_and_pinned_models_allowed(self):
        picks = {"kilocode/cohere/north-mini-code:free": "kilocode", "agy/gemini-3.8-flash-high": "agy"}
        with patch.object(a, "selected_models", return_value=picks),              patch.dict(a.CONFIG, {"pinned_models": ["antigravity/gemini-3.8-flash-medium"], "provider_only_models": {}}):
            self.assertTrue(a.provider_allows("kilocode", "kilocode/cohere/north-mini-code:free"))
            self.assertFalse(a.provider_allows("kilocode", "kilocode/nvidia/nemotron-3-ultra-550b-a55b:free"))
            self.assertTrue(a.provider_allows("antigravity", "antigravity/gemini-3.8-flash-medium"))
            self.assertTrue(a.provider_allows("agy", "agy/gemini-3.8-flash-high"))

    def test_no_pick_list_keeps_old_behaviour(self):
        with patch.object(a, "selected_models", return_value=None), patch.dict(a.CONFIG, {"provider_only_models": {}}):
            self.assertTrue(a.provider_allows("kilocode", "kilocode/anything"))

    def test_agy_pick_uses_antigravity_history(self):
        hist = {"antigravity/gemini-3-flash": {"n7": 100, "ok7": 99}}
        with patch.dict(a.CONFIG, {"provider_groups": {"agy": "antigravity"}}):
            self.assertEqual(a.hist_for("agy/gemini-3-flash", hist)["ok7"], 99)
            self.assertIsNone(a.hist_for("agy/unknown", hist))

    def test_target_size_grows_to_number_of_picks(self):
        with patch.object(a, "selected_models", return_value={f"p{i}/m": f"p{i}" for i in range(20)}):
            self.assertEqual(a.target_size(), 20)

    def test_extra_picks_are_candidates_beside_the_provider_pick(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "sel.json").write_text('{"picks": {"kiro": "kiro/auto", "cline": "__none__"},'
                                           ' "extra_picks": ["kiro/glm-5", "agy/gemini-4-argon-high"]}', encoding="utf-8")
            with patch.object(a, "ROOT", Path(d)), patch.dict(a.CONFIG, {"selected_models_file": "sel.json"}):
                self.assertEqual(a.selected_models(), {"kiro/auto": "kiro", "kiro/glm-5": "kiro",
                                                       "agy/gemini-4-argon-high": "agy"})


class ProviderBlockTests(unittest.TestCase):
    def blocks(self, rows):
        con = sqlite3.connect(":memory:")
        con.execute("create table call_logs (provider text, status int, timestamp text, error_summary text, path text)")
        con.executemany("insert into call_logs values (?,?,?,?, '/v1/chat/completions')", rows)
        con.create_function("datetime", 2, lambda a_, b_: "2000-01-01")
        return a.provider_blocks(con)

    def test_402_for_one_model_outside_the_plan_does_not_block_the_provider(self):
        rows = [("ollama-cloud", 200, "2026-10-30T10:00:00", ""),
                ("ollama-cloud", 402, "2026-10-30T11:00:00", "deepseek-v4-pro is not included in your free usage")]
        self.assertEqual(self.blocks(rows), {})

    def test_credit_402_still_blocks(self):
        rows = [("eurouter", 200, "2026-10-30T10:00:00", ""),
                ("eurouter", 402, "2026-10-30T11:00:00", "credit limit exceeded")]
        self.assertEqual(self.blocks(rows)["eurouter"]["status"], 402)


class UserAgentTests(unittest.TestCase):
    """2026-10-29: groq/chatanywhere/aion 403 the urllib default agent (OmniRoute forwards it, #15632)."""

    def test_api_sends_openai_sdk_user_agent(self):
        seen = {}

        class Resp:
            status = 200
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *exc): return False

        def fake_urlopen(req, timeout=0):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return Resp()
        with patch.object(a.urllib.request, "urlopen", fake_urlopen):
            a.api("GET", "/v1/models")
        self.assertTrue(seen.get("user-agent", "").startswith("OpenAI/Python"))


class UpdateAllowedTests(unittest.TestCase):
    """2026-10-29: a 3-model list was refused (< min_size 5) in favour of the old 2-model list, and the combo ran out."""

    def test_new_list_bigger_than_short_old_list_is_used(self):
        self.assertTrue(a.update_allowed(3, 2, False))

    def test_shrinking_below_min_size_keeps_old_list(self):
        self.assertFalse(a.update_allowed(3, 6, False))
        self.assertTrue(a.update_allowed(2, 2, False))  # same size: swap a locked-out member for a live one
        self.assertFalse(a.update_allowed(1, 2, False))

    def test_min_size_reached_or_forced(self):
        self.assertTrue(a.update_allowed(5, 8, False))
        self.assertTrue(a.update_allowed(1, 6, True))
        self.assertFalse(a.update_allowed(0, 6, True))


class CheckHangTests(unittest.TestCase):
    """2026-10-29: a tune 16 s before north-mini hung held the reaction back (min gap) and it hung twice more;
    afterwards the same, already-rested hang re-triggered a tune every 3 minutes."""

    def run_check(self, last_tune_ago_s, hang_ago_s):
        now = a.now_utc()
        con = sqlite3.connect(":memory:")
        con.execute("create table call_logs (requested_model text, status int, duration int, timestamp text, combo_name text)")
        ts = (now - timedelta(seconds=hang_ago_s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        con.execute("insert into call_logs values ('kc/x/north', 499, 280000, ?, 'hermes-fast-agent')", (ts,))
        state = {"probes": {}, "last_tune": (now - timedelta(seconds=last_tune_ago_s)).isoformat()}
        with patch.object(a, "load_state", return_value=state), patch.object(a, "db", return_value=con),              patch.object(a, "combo_get", return_value={"models": []}),              patch.object(a, "preferred_check_reasons", return_value=[]),              patch.object(a, "log_stray_auto"), patch.object(a, "log"),              patch.object(a, "cmd_tune", return_value=0) as tune:
            a.cmd_check()
        return tune

    def test_hang_after_recent_tune_tunes_at_once(self):
        self.run_check(last_tune_ago_s=30, hang_ago_s=14).assert_called_once()

    def test_hang_already_handled_by_last_tune_does_not_retune(self):
        self.run_check(last_tune_ago_s=400, hang_ago_s=500).assert_not_called()


def _cand(model, structured_ok=True, quality=0.7):
    return {"model": model, "provider": model.split("/")[0], "quality": quality, "structured_ok": structured_ok}


ARGON = "agy/gemini-4-argon-low"


class RoutePinTests(unittest.TestCase):
    """2026-10-30 user: agy steps leaked to the antigravity connection of the same account (always 429)."""

    conns = {"agy": [{"id": "agy-on", "active": True}, {"id": "agy-off", "active": False}],
             "antigravity": [{"id": "ag-on", "active": True}]}
    sel = [_cand("agy/gemini-3.8-flash-medium"), _cand("antigravity/gemini-3-flash"), _cand("grok-cli/grok-4.7")]

    def build(self, conns):
        with patch.dict(a.CONFIG, {"route_pinned_providers": ["agy"]}), patch.object(a, "log") as log:
            models = a.build_models(self.sel, a.route_pins(conns), "lane")
        return {m["model"]: m for m in models}, log

    def test_agy_steps_pinned_to_active_agy_connections_only(self):
        steps, _ = self.build(self.conns)
        self.assertEqual(steps["agy/gemini-3.8-flash-medium"]["allowedConnectionIds"], ["agy-on"])
        self.assertNotIn("allowedConnectionIds", steps["antigravity/gemini-3-flash"])
        self.assertNotIn("allowedConnectionIds", steps["grok-cli/grok-4.7"])

    def test_no_active_agy_connection_leaves_step_unpinned_and_warns(self):
        steps, log = self.build({"agy": [{"id": "agy-off", "active": False}]})
        self.assertNotIn("allowedConnectionIds", steps["agy/gemini-3.8-flash-medium"])
        self.assertIn("WARNING", log.call_args[0][0])

    def sync(self, live_models, new_pins):
        combo = {"id": "c1", "name": "lane", "strategy": a.CONFIG["strategy"], "models": live_models,
                 "config": {"maxRetries": a.CONFIG["combo_max_retries"]}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(a, "ROOT", Path(tmp)), \
                patch.object(a, "put_combo", return_value=(200, {})) as put, \
                patch.object(a, "verify_combo", return_value=(True, "ok")):
            (Path(tmp) / "backups").mkdir()
            res = a.sync_lane("lane", self.sel, [combo], new_pins)
        return res, put

    def test_same_models_with_different_allow_list_is_updated(self):
        unpinned = a.build_models(self.sel, {}, "lane")
        res, put = self.sync(unpinned, {"agy": ["agy-on"]})
        self.assertTrue(res.startswith("updated"))
        self.assertEqual(put.call_args[0][1][0]["allowedConnectionIds"], ["agy-on"])

    def test_same_models_and_allow_list_is_unchanged(self):
        pinned = a.build_models(self.sel, {"agy": ["agy-on"]}, "lane")
        res, put = self.sync(pinned, {"agy": ["agy-on"]})
        self.assertEqual(res, "unchanged")
        put.assert_not_called()


class LaneRuleTests(unittest.TestCase):
    """2026-10-30 user: lane "primary" and lane-only "pinned" models (argon in the Hindsight lane)."""

    def lanes(self, sel, lane_pins):
        return {lane["combo"]: [d["model"] for d in order] for lane, order in a.lane_orders(sel, lane_pins)}

    def test_unqualified_pin_cannot_enter_hindsight_lane(self):
        healthy = _cand("grok-cli/grok-4.7")
        for selected, pins in [([healthy, _cand(ARGON, structured_ok=False)], {}),
                               ([healthy], {ARGON: _cand(ARGON, structured_ok=False)}),
                               ([healthy], {ARGON: {"model": ARGON, "provider": "agy", "quality": 0.7}})]:
            with self.subTest(selected=selected, pins=pins):
                lanes = self.lanes(selected, pins)
                self.assertNotIn(ARGON, lanes["hindsight-structured"])
                self.assertEqual(lanes["hermes-fast-agent"], [d["model"] for d in selected])

    def test_primary_moves_to_front(self):
        sel = [_cand("agy/gemini-3.8-flash-medium"), _cand("grok-cli/grok-4.7"), _cand("agy/gemini-3-flash")]
        lanes = self.lanes(sel, {})
        self.assertEqual(lanes["hindsight-refresh"],
                         ["agy/gemini-3-flash", "agy/gemini-3.8-flash-medium", "grok-cli/grok-4.7"])

    def test_lane_pin_inserted_at_its_rank_only_in_its_lane(self):
        sel = [_cand("agy/gemini-3.8-flash-medium"), _cand("agy/gemini-3-flash"), _cand("grok-cli/grok-4.7")]
        lanes = self.lanes(sel, {ARGON: _cand(ARGON)})
        self.assertEqual(lanes["hindsight-structured"],
                         [ARGON, "agy/gemini-3.8-flash-medium", "agy/gemini-3-flash", "grok-cli/grok-4.7"])
        self.assertEqual(lanes["hermes-fast-agent"], [d["model"] for d in sel])
        self.assertNotIn(ARGON, lanes["hindsight-refresh"])

    def test_argon_survives_full_antigravity_group(self):
        # max_per_provider_override caps the shared selection; argon joins the lane after it, on top of the cap
        group = ["agy/gemini-3.8-flash-medium", "agy/gemini-3-flash", "antigravity/gemini-3.8-flash-high"]
        sel = [_cand(m) for m in group] + [_cand("grok-cli/grok-4.7")]
        self.assertEqual(a.CONFIG["max_per_provider_override"]["antigravity"], 3)
        lanes = self.lanes(sel, {ARGON: _cand(ARGON)})
        self.assertEqual(lanes["hindsight-structured"][0], ARGON)
        for m in group:
            self.assertIn(m, lanes["hindsight-structured"])

    def test_pinned_model_already_selected_is_moved_not_duplicated(self):
        sel = [_cand("grok-cli/grok-4.7"), _cand("agy/gemini-3-flash"), _cand(ARGON)]
        lanes = self.lanes(sel, {})
        self.assertEqual(lanes["hindsight-structured"], [ARGON, "grok-cli/grok-4.7", "agy/gemini-3-flash"])

    def test_lane_pinned_candidate_without_evidence_has_report_fields(self):
        with patch.object(a, "resilience", {"open": {}, "locked": {}}):
            d = a.pinned_candidate(ARGON, {}, {}, {}, {}, {}, {})
        self.assertEqual((d["model"], d["provider"], d["score"]), (ARGON, "agy", 0.5))
        for field in ("success", "median_s", "quota", "quality", "n48h"):
            self.assertIn(field, d)


class LanePinnedCheckTests(unittest.TestCase):
    """A lane-pinned model never leaves its lane, so its failures must not start a re-tune every few minutes."""

    def run_check(self, model):
        now = a.now_utc()
        con = sqlite3.connect(":memory:")
        con.execute("create table call_logs (requested_model text, status int, duration int, timestamp text, combo_name text)")
        for i in range(3):
            ts = (now - timedelta(seconds=30 + i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            con.execute("insert into call_logs values (?, 429, 20000, ?, 'hindsight-structured')", (model, ts))
        state = {"probes": {}, "last_tune": (now - timedelta(hours=1)).isoformat()}
        with patch.object(a, "load_state", return_value=state), patch.object(a, "db", return_value=con), \
                patch.object(a, "combo_get", return_value={"models": []}), \
                patch.object(a, "preferred_check_reasons", return_value=[]), \
                patch.object(a, "log_stray_auto"), patch.object(a, "log"), \
                patch.object(a, "cmd_tune", return_value=0) as tune:
            a.cmd_check()
        return tune

    def test_lane_pinned_failures_do_not_retune(self):
        self.run_check(ARGON).assert_not_called()

    def test_other_model_failures_still_retune(self):
        self.run_check("grok-cli/grok-4.7").assert_called_once()


class LanePinCooldownTests(_LifecycleBase):
    """2026-11-01 user: a lane-pinned first member (argon in the Hindsight lane) stays first by design, but while its
    quota is exhausted it leaves #1 (it answered 429 first, then the next member 20-40 s later) and returns to #1
    after a passing recovery probe, the same cooldown preferred models get."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(a.CONFIG, {"preferred_models": [], "pinned_models": []})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = {"probes": {ARGON: {**self.good_probe(), "provider": "agy"}}}
        self.conns = {"agy": [{"id": "agy-on", "active": True, "test": "ok", "rl_until": None}]}
        self.cat = {ARGON: {"context_length": 1048576, "capabilities": {"tool_calling": True}}}
        self.sel = [_cand("agy/gemini-3-flash"), _cand("grok-cli/grok-4.7")]

    def exhaust(self, until):
        self.quota = {"agy": {"gemini-4-argon-low": {"remaining": 0, "exhausted": True, "reset": until.isoformat()}}}

    def hindsight_order(self):
        pins, cooling, _, _ = a.lane_pin_candidates(self.state, self.cat, self.hist, self.quota, self.conns, {})
        for candidate in pins.values():
            candidate["structured_ok"] = True
        orders = a.lane_orders(self.sel, pins, cooling)
        return [d["model"] for lane, order in orders if lane["combo"] == "hindsight-structured" for d in order]

    def test_healthy_pin_stays_first(self):
        self.assertEqual(self.hindsight_order()[0], ARGON)

    def test_exhausted_pin_leaves_rank_one_and_returns_after_recovery_probe(self):
        self.exhaust(self.now + timedelta(hours=3))
        with patch.object(a, "probe") as probe:
            order = self.hindsight_order()
        self.assertNotIn(ARGON, order)
        self.assertEqual(order[0], "agy/gemini-3-flash")
        probe.assert_not_called()
        self.now += timedelta(hours=4)
        self.quota = {}
        with patch.object(a, "probe", return_value={**self.good_probe(), "provider": "agy"}) as probe:
            order = self.hindsight_order()
        probe.assert_called_once_with(ARGON)
        self.assertEqual(order[0], ARGON)

    def test_failed_recovery_probe_keeps_pin_out_until_retry(self):
        self.exhaust(self.now + timedelta(hours=1))
        self.hindsight_order()
        self.now += timedelta(hours=2)
        self.quota = {}
        failed = {**self.good_probe(), "ok": False, "status": 429, "err": "still exhausted", "provider": "agy"}
        with patch.object(a, "probe", return_value=failed) as probe:
            self.assertNotIn(ARGON, self.hindsight_order())
            self.assertNotIn(ARGON, self.hindsight_order())
        self.assertEqual(probe.call_count, 1)

    def test_slow_pin_without_an_availability_problem_keeps_its_rank(self):
        self.hist = {ARGON: {"n": 5, "ok": 1, "last": None}}
        self.state["probes"][ARGON]["latency"] = 200.0
        self.assertEqual(self.hindsight_order()[0], ARGON)

    def test_cooling_pin_is_dropped_even_when_ordinary_ranking_selected_it(self):
        self.exhaust(self.now + timedelta(hours=3))
        self.sel.insert(0, _cand(ARGON))
        self.assertNotIn(ARGON, self.hindsight_order())

    def test_check_watches_lane_pin_quota_and_recovery_without_looping(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("create table call_logs(requested_model, path, status, duration, timestamp)")
        with patch.object(a, "quotas", return_value={}):
            self.assertEqual(a.preferred_check_reasons(self.state, con, set()), [])
        self.exhaust(self.now + timedelta(hours=3))
        with patch.object(a, "quotas", return_value=self.quota), \
                patch.object(a, "api", return_value=(200, {"combos": []}, 0)):
            self.assertEqual(len(a.preferred_check_reasons(self.state, con, set())), 1)
            self.hindsight_order()
            self.assertEqual(a.preferred_check_reasons(self.state, con, set()), [])
        self.now += timedelta(hours=4)
        with patch.object(a, "quotas", return_value={}), patch.object(a, "api", return_value=(200, {"combos": []}, 0)):
            self.assertEqual(len(a.preferred_check_reasons(self.state, con, set())), 1)

    def test_slow_recovery_probe_still_runs_admission_and_keeps_pin(self):
        self.state["preferred_status"] = {ARGON: {"suspended": True, "retry_at": (self.now - timedelta(minutes=1)).isoformat()}}
        slow = {**self.good_probe(), "latency": 200, "provider": "agy"}
        with patch.object(a, "probe", return_value=slow), patch.object(a, "admission_checks", return_value=("", False)) as admission:
            self.assertEqual(self.hindsight_order()[0], ARGON)
            admission.assert_called_once_with(ARGON)

    def test_stale_live_lane_retries_after_failed_update(self):
        self.state["preferred_status"] = {ARGON: {"suspended": True, "reason": "quota 'x'", "retry_at": (self.now + timedelta(hours=1)).isoformat()}}
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("create table call_logs(requested_model, path, status, duration, timestamp)")
        stale = {"combos": [{"name": "hindsight-structured", "models": [{"model": ARGON}]}]}
        with patch.object(a, "quotas", return_value={}), patch.object(a, "api", return_value=(200, stale, 0)):
            self.assertTrue(any("still in lane" in r for r in a.preferred_check_reasons(self.state, con, set())))
        stale["combos"][0]["models"] = []
        with patch.object(a, "quotas", return_value={}), patch.object(a, "api", return_value=(200, stale, 0)):
            self.assertEqual(a.preferred_check_reasons(self.state, con, set()), [])

    def test_tune_updates_cooling_lane_despite_main_size_guard(self):
        replacement = {**_cand("grok-cli/grok-4.7", structured_ok=True), "score": 0.7, "success": 1.0, "median_s": 1, "quota": None, "n48h": 1}
        old = {"models": [{"model": f"old/{i}"} for i in range(5)], "strategy": a.CONFIG["strategy"]}
        fallback = [{"model": f"provider/fallback{i}", "providerId": "provider", "weight": i + 2,
                     "allowedConnectionIds": [f"connection{i}"]} for i in range(4)]
        lane = {"id": "lane-id", "name": "hindsight-structured", "models": [{"model": ARGON}] + fallback,
                "strategy": "priority", "config": {"maxRetries": 17, "candidatePool": ["keep"]}}
        mocks = {"load_state": self.state, "db": object(), "combo_get": old, "catalog": {},
                 "history": {}, "quotas": {}, "connections": {}, "quality": {}, "provider_blocks": {},
                 "free_budgets": {}, "live_resilience": {}, "route_pins": {},
                 "preferred_candidates": ([replacement], [], []),
                 "lane_pin_candidates": ({}, {ARGON}, [], []), "pick_probes": [], "selected_models": {ARGON: "agy"},
                 "evaluate": (None, {}), "write_report": "offline", "update_allowed": False,
                 "verify_combo": (True, "ok")}
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            (root / "backups").mkdir()
            stack.enter_context(patch.object(a, "ROOT", root))
            patched = {name: stack.enter_context(patch.object(a, name, return_value=value)) for name, value in mocks.items()}
            api = stack.enter_context(patch.object(a, "api", side_effect=[(200, {"combos": [lane]}, 0), (200, {}, 0)]))
            stack.enter_context(patch.object(a, "save_state"))
            stack.enter_context(patch.object(a, "log"))
            a.cmd_tune()
            body = api.call_args_list[1].args[2]
            self.assertEqual(body, {"name": lane["name"], "strategy": lane["strategy"], "config": lane["config"], "models": fallback})
            self.assertEqual(len(list((root / "backups").glob("*.json"))), 1)
            patched["verify_combo"].assert_called_once_with("hindsight-structured")
            for outcome in ("ROLLBACK FAILED 500", "cannot update: lane missing", "cannot update: no surviving models"):
                with self.subTest(outcome=outcome), patch.object(a, "api", return_value=(200, {"combos": [lane]}, 0)), patch.object(a, "remove_cooling_lane_pins", return_value=(outcome, [])):
                    self.assertEqual(a.cmd_tune(), 1)

    def test_verification_failure_restores_original_lane(self):
        lane = {"id": "id", "name": "hindsight-structured", "models": [{"model": ARGON}, {"model": "p/fallback", "weight": 9}],
                "strategy": "priority", "config": {"custom": True}}
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            (root / "backups").mkdir()
            stack.enter_context(patch.object(a, "ROOT", root))
            api = stack.enter_context(patch.object(a, "api", side_effect=[(200, {}, 0), (200, {}, 0)]))
            stack.enter_context(patch.object(a, "verify_combo", return_value=(False, "bad tool call")))
            result, _ = a.remove_cooling_lane_pins({"combo": lane["name"]}, {ARGON}, [lane], False)
            self.assertIn("ROLLED BACK", result)
            self.assertEqual(api.call_args_list[1].args[2], {k: lane[k] for k in ("name", "strategy", "config", "models")})

    def test_no_survivors_are_not_written(self):
        lane = {"name": "hindsight-structured", "models": [{"model": ARGON}]}
        with patch.object(a, "api") as api:
            result, retained = a.remove_cooling_lane_pins({"combo": lane["name"]}, {ARGON}, [lane], False)
        self.assertIn("no surviving models", result)
        self.assertEqual(retained, [])
        api.assert_not_called()


class VerifyComboTests(unittest.TestCase):
    def test_requires_complete_actual_tool_call(self):
        good = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
            {"type": "function", "function": {"name": "report_status", "arguments": '{"status":"ok"}'}}]}}]}
        for response in ({"model": "m", "choices": [{"message": {"content": "report_status ok"}}]},
                         {"model": "m", "choices": []},
                         {**good, "choices": [{**good["choices"][0], "finish_reason": "length"}]}):
            with self.subTest(response=response), patch.object(a, "api", return_value=(200, response, 1)) as api:
                self.assertFalse(a.verify_combo()[0])
                self.assertEqual(api.call_count, 2)
        with patch.object(a, "api", return_value=(200, good, 1)) as api:
            self.assertTrue(a.verify_combo()[0])
            body = api.call_args.args[2]
            self.assertGreaterEqual(body["max_tokens"], 1024)
            self.assertEqual(body["reasoning_effort"], a.CONFIG["probe_effort"])
            self.assertEqual(body["tool_choice"], "required")
            self.assertIn("status", body["tools"][0]["function"]["parameters"]["required"])
            first_request = api.call_args.args[2]["messages"][0]["content"]
            a.verify_combo()
            self.assertNotEqual(first_request, api.call_args.args[2]["messages"][0]["content"])


class IgnoredBudgetTests(unittest.TestCase):
    """2026-10-30: kiro was marked ignore_budget_providers, yet probe_affordable still read the stale 0% pool."""

    def setUp(self):
        self.saved = (dict(a.budgets), list(a.CONFIG["ignore_budget_providers"]))
        a.budgets.clear()
        a.budgets["kiro"] = {"budget": 25_000, "remaining_frac": 0.0, "pool": "kiro"}

    def tearDown(self):
        a.budgets.clear()
        a.budgets.update(self.saved[0])
        a.CONFIG["ignore_budget_providers"] = self.saved[1]

    def test_ignored_provider_can_be_probed(self):
        a.CONFIG["ignore_budget_providers"] = ["kiro"]
        self.assertTrue(a.probe_affordable("kiro"))

    def test_exhausted_pool_blocks_probe_when_not_ignored(self):
        a.CONFIG["ignore_budget_providers"] = []
        self.assertFalse(a.probe_affordable("kiro"))


class ProbeSlotTests(unittest.TestCase):
    """2026-10-30: unpicked models filled every probe slot, so the user's pick kiro/auto was never probed."""

    def test_unpicked_models_do_not_take_probe_slots(self):
        hist = {"kiro/auto": {"provider": "kiro", "n": 26, "ok": 26, "ok7": 26, "ok_big7": 0}}
        cat = {"x/strong": {"owned_by": "x", "context_length": 200_000, "capabilities": {"tool_calling": True}}}
        with patch.object(a, "selected_models", return_value={"kiro/auto": "kiro"}),                 patch.object(a, "evaluate", return_value=(None, {"why": "never handled an agent-sized request"})),                 patch.object(a, "tier_quality", side_effect=lambda m: 0.9 if m == "x/strong" else None),                 patch.object(a, "quota_for", return_value=(None, None)),                 patch.dict(a.CONFIG, {"probes_per_run": 1}):
            live = [{"active": True, "test": "ok", "rl_until": None}]
            picked = a.pick_probes(hist, cat, {}, {"kiro": live, "x": live}, {}, {})
        self.assertEqual(picked, [("kiro/auto", "kiro")])


class HedgingTests(unittest.TestCase):
    """2026-10-30 user: pinned #1 medium is slow half the time; the main combo hedges to #2 from another group."""

    def put(self, name, config):
        with patch.object(a, "api", return_value=(200, {}, 0.0)) as api:
            a.put_combo({"id": "c1", "name": name, "config": config}, [{"providerId": "agy"}])
        return api.call_args[0][2]["config"]

    def test_main_combo_gets_hedging_and_keeps_other_keys(self):
        cfg = self.put(a.CONFIG["combo_name"], {"circuitBreaker": {"enabled": True}})
        self.assertTrue(cfg["hedging"] and cfg["zeroLatencyOptimizationsEnabled"])
        self.assertEqual(cfg["hedgeDelayMs"], a.CONFIG["main_combo_config"]["hedgeDelayMs"])
        self.assertEqual(cfg["circuitBreaker"], {"enabled": True})

    def test_lane_combo_gets_no_hedging(self):
        self.assertNotIn("hedging", self.put("hindsight-structured", {}))

    def test_missing_hedging_is_drift(self):
        cfg = {"maxRetries": a.CONFIG["combo_max_retries"]}
        self.assertTrue(a.config_drift({"config": cfg}))
        self.assertFalse(a.config_drift({"config": {**cfg, **a.CONFIG["main_combo_config"]}}))

    def test_second_comes_from_another_group_behind_pinned_first(self):
        sel = [_cand("agy/gemini-3.8-flash-medium"), _cand("agy/gemini-3-flash"), _cand("kiro/auto")]
        sel[2]["ok_big7d"] = 1
        with patch.dict(a.CONFIG, {"provider_groups": {"agy": "antigravity"}}):
            order = a.second_from_other_group(list(sel), {})
        self.assertEqual([d["model"] for d in order], ["agy/gemini-3.8-flash-medium", "kiro/auto", "agy/gemini-3-flash"])

    def test_unproven_other_group_does_not_move(self):
        sel = [_cand("agy/gemini-3.8-flash-medium"), _cand("agy/gemini-3-flash"), _cand("kiro/auto")]
        self.assertEqual(a.second_from_other_group(list(sel), {}), sel)


class RoleQuarantineRegressionTests(unittest.TestCase):
    def test_tune_checks_lane_only_pin_before_admission(self):
        for required, structure, duplicate, lane_result in [
                ((False, 'invalid query'), (True, ''), False, 'checked'),
                ((True, 'not checked (429)'), (True, ''), False, 'checked'),
                ((True, ''), (False, 'invalid JSON'), False, 'checked'),
                ((True, ''), (True, ''), False, 'checked'),
                ((True, 'not checked (429)'), (True, ''), True, 'checked'),
                ((True, ''), (True, ''), True, 'checked'),
                *[((True, ''), (True, ''), False, result) for result in
                  ['CREATE FAILED 500', 'UPDATE FAILED 500', 'ROLLED BACK (verify failed)',
                   'created but VERIFY FAILED: bad reply', 'unavailable', 'malformed']]]:
            model = ARGON if duplicate else 'provider/working'
            with self.subTest(required=required, structure=structure, duplicate=duplicate), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                row = {**_cand(model), 'score': 0.8, 'success': 1.0, 'median_s': 1,
                       'quota': None, 'n48h': 1, 'last_ok': a.now_utc().isoformat()}
                pin = {**_cand(ARGON), 'score': 0.8, 'success': 1.0, 'median_s': 1,
                       'quota': None, 'n48h': 1}
                pin.pop('structured_ok')
                combo = {'id': 'main-id', 'name': 'hermes-fast-agent', 'models': [{'model': 'old/model'}],
                         'strategy': 'priority', 'config': {}}
                mocks = {'load_state': {'probes': {}}, 'db': object(), 'combo_get': combo,
                         'catalog': {}, 'history': {}, 'quotas': {}, 'connections': {}, 'quality': {},
                         'provider_blocks': {}, 'free_budgets': {}, 'live_resilience': {}, 'route_pins': {},
                         'preferred_candidates': ([], [], []), 'lane_pin_candidates': ({ARGON: pin}, set(), [], []),
                         'pick_probes': [], 'selected_models': {model: row['provider']}, 'evaluate': (0.8, row),
                         'recent_hangs': {}, 'write_report': 'offline', 'update_allowed': True,
                         'verify_combo': (True, 'ok')}
                root = Path(temp)
                (root / 'backups').mkdir()
                stack.enter_context(patch.object(a, 'ROOT', root))
                for name, value in mocks.items():
                    stack.enter_context(patch.object(a, name, return_value=value))
                def api_reply(method, path, *args, **kwargs):
                    if method == 'GET' and path == '/api/combos' and lane_result == 'unavailable':
                        return 503, {}, 0
                    if method == 'GET' and path == '/api/combos' and lane_result == 'malformed':
                        return 200, {}, 0
                    return 200, {'combos': []}, 0
                stack.enter_context(patch.object(a, 'api', side_effect=api_reply))
                stack.enter_context(patch.object(a, 'save_state'))
                stack.enter_context(patch.object(a, 'log'))
                req = stack.enter_context(patch.object(a, 'required_tool_ok', side_effect=lambda state, m, dry: required if m == ARGON else (True, '')))
                structured = stack.enter_context(patch.object(a, 'structured_ok', side_effect=lambda state, m, dry: structure if m == ARGON else (True, '')))
                sync = stack.enter_context(patch.object(a, 'sync_lane', return_value=lane_result))
                exit_code = a.cmd_tune()
                self.assertEqual(sum(call.args[1] == ARGON for call in req.call_args_list), 1)
                self.assertEqual(any(call.args[1] == ARGON for call in structured.call_args_list), required == (True, ''))
                self.assertEqual(pin['structured_ok'], required == (True, '') and structure[0])
                hindsight = next((call.args[1] for call in sync.call_args_list if call.args[0] == 'hindsight-structured'), [])
                self.assertEqual(ARGON in [d['model'] for d in hindsight],
                                 required == (True, '') and structure[0] and lane_result not in ('unavailable', 'malformed'))
                self.assertEqual(exit_code, 1 if lane_result != 'checked' or (duplicate and not hindsight) else 0)

    def test_hindsight_tool_failure_does_not_remove_healthy_hermes_candidate(self):
        model = 'provider/working'
        row = {'model': model, 'provider': 'provider', 'score': 0.8, 'quality': 0.8,
               'success': 1.0, 'median_s': 1, 'quota': None, 'n48h': 1,
               'last_ok': a.now_utc().isoformat()}
        combo = {'id': 'main-id', 'name': 'hermes-fast-agent', 'models': [{'model': 'old/model'}],
                 'strategy': 'priority', 'config': {}}
        mocks = {'load_state': {'probes': {}}, 'db': object(), 'combo_get': combo,
                 'catalog': {}, 'history': {}, 'quotas': {}, 'connections': {}, 'quality': {},
                 'provider_blocks': {}, 'free_budgets': {}, 'live_resilience': {}, 'route_pins': {},
                 'preferred_candidates': ([], [], []), 'lane_pin_candidates': ({}, set(), [], []),
                 'pick_probes': [], 'selected_models': {model: 'provider'},
                 'evaluate': (0.8, row), 'required_tool_ok': (False, 'invalid query'),
                 'recent_hangs': {}, 'write_report': 'offline', 'update_allowed': True,
                 'verify_combo': (True, 'ok')}
        lanes = [{'combo': 'hermes-fast-agent', 'role': 'work'},
                 {'combo': 'hindsight-structured', 'role': 'memory', 'structured': True}]
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            (root / 'backups').mkdir()
            stack.enter_context(patch.object(a, 'ROOT', root))
            stack.enter_context(patch.dict(a.CONFIG, {'lanes': lanes}))
            for name, value in mocks.items():
                stack.enter_context(patch.object(a, name, return_value=value))
            api = stack.enter_context(patch.object(a, 'api', return_value=(200, {'combos': []}, 0)))
            stack.enter_context(patch.object(a, 'save_state'))
            stack.enter_context(patch.object(a, 'log'))
            a.cmd_tune()
            main_updates = [call.args[2] for call in api.call_args_list
                            if call.args[:2] == ('PUT', '/api/combos/main-id')]
            self.assertEqual(len(main_updates), 1)
            self.assertEqual([m['model'] for m in main_updates[0]['models']], [model])

    def test_role_filter_preserves_other_roles_and_pins(self):
        rows = [_cand("kiro/auto", structured_ok=True), _cand("kiro/glm-5", structured_ok=True),
                _cand("electronhub/healthy", structured_ok=True),
                _cand("cline/dots-studio/dots-3-note-preview:free", structured_ok=True),
                _cand("cline/nvidia/nemotron-3-ultra-550b-a55b:free", structured_ok=True)]
        main = a.role_candidates("hermes-fast-agent", rows)
        self.assertEqual([r["model"] for r in main], [r["model"] for r in rows[1:]])
        self.assertEqual([r["model"] for r in a.role_candidates("hindsight-refresh", rows)],
                         [r["model"] for r in rows[:3]])
        lane = {"combo": "hindsight-refresh", "pinned": [{"model": rows[3]["model"], "rank": 1}]}
        self.assertNotIn(rows[3], a.apply_lane_rules(lane, rows[:3], rows, {}))
        self.assertEqual(len(rows), 5)

    def test_required_tool_validates_arguments_and_truncation(self):
        for args, finish, expected in [('{"query":"Windows"}', 'tool_calls', True),
                                       ('{}', 'tool_calls', False), ('{"query":"   "}', 'tool_calls', False),
                                       ('{"query":"---"}', 'tool_calls', False), ('{"query":2}', 'tool_calls', False),
                                       ("{'query': 'Windows'}", 'tool_calls', False),
                                       ('{"query":"Windows"}', 'length', False)]:
            reply = {"choices": [{"finish_reason": finish, "message": {"tool_calls": [
                {"function": {"name": "search_observations", "arguments": args}}]}}]}
            with self.subTest(args=args, finish=finish), patch.object(a, "api", return_value=(200, reply, 1)) as api:
                self.assertEqual(a.probe_required_tool("m")["ok"], expected)
                self.assertEqual(api.call_args.kwargs["headers"].get("x-omniroute-no-cache"), "true")

    def test_structured_validates_each_fact(self):
        valid = {"what": "fixed", "when": "today", "who": "user"}
        for facts, expected in [([valid], True), ([], False), ([{}], False), ([valid, 2], False),
                                ([{**valid, "what": " "}], False), ([{**valid, "who": 1}], False),
                                ([{**valid, "extra": "x"}], False)]:
            reply = {"choices": [{"message": {"content": json.dumps({"facts": facts})}}]}
            with self.subTest(facts=facts), patch.object(a, "api", return_value=(200, reply, 1)) as api:
                self.assertEqual(a.probe_structured("m")["ok"], expected)
                self.assertEqual(api.call_args.kwargs["headers"].get("x-omniroute-no-cache"), "true")


if __name__ == "__main__":
    unittest.main()
