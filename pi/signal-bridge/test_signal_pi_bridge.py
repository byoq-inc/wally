import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from signal_pi_bridge import (
    OutboundMessageLedger,
    ConversationScheduler,
    LogicalThread,
    PiRpc,
    SendResponseCorrelator,
    ThreadRoutingState,
    conversation_key,
    deterministic_auth_command,
    deterministic_thread_command,
    extract_message,
    extract_reaction,
    extract_send_timestamp,
    format_quoted_reply_prompt,
    format_reaction_prompt,
    format_pi_auth_status,
    format_runtime_error,
    pi_auth_state,
    prompt_with_auth_recovery,
    reaction_routing_allowed,
    reaction_work_item,
    rpc_assistant_outcome,
    send_signal,
    sync_pi_auth,
)


class FakeSignalRpc:
    def __init__(self):
        self.sent = []

    def send(self, value):
        self.sent.append(value)


class LogicalThreadRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "private" / "threads.json"
        self.state = ThreadRoutingState(self.path, max_routes=20, max_threads=10)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def message(source="alice", timestamp=100, quote=None, text="hello", group_id=None):
        return {
            "source": source, "group_id": group_id, "timestamp": timestamp,
            "quote": quote, "text": text, "attachments": [],
        }

    def test_unquoted_messages_create_distinct_threads(self):
        first = self.state.route_inbound(self.message(timestamp=100))
        second = self.state.route_inbound(self.message(timestamp=101))
        self.assertNotEqual(first.id, second.id)

    def test_reply_routes_by_prior_user_and_outbound_timestamp(self):
        first = self.state.route_inbound(self.message(timestamp=100))
        user_reply = self.state.route_inbound(self.message(
            timestamp=101, quote={"target_timestamp": 100},
        ))
        self.assertEqual(user_reply.id, first.id)
        self.state.map_outbound(900, {
            "conversation": "dm:alice", "thread_id": first.id,
        })
        outbound_reply = self.state.route_inbound(self.message(
            timestamp=102, quote={"target_timestamp": 900},
        ))
        self.assertEqual(outbound_reply.id, first.id)

    def test_timestamp_route_is_isolated_by_conversation(self):
        alice = self.state.route_inbound(self.message(source="alice", timestamp=100))
        bob = self.state.route_inbound(self.message(
            source="bob", timestamp=101, quote={"target_timestamp": 100},
        ))
        group = self.state.route_inbound(self.message(
            source="alice", group_id="g1", timestamp=102, quote={"target_timestamp": 100},
        ))
        self.assertNotEqual(alice.id, bob.id)
        self.assertNotEqual(alice.id, group.id)
        self.state.map_outbound(900, {"conversation": "dm:bob", "thread_id": alice.id})
        self.assertNotIn(("dm:bob", 900), self.state.routes)

    def test_atomic_private_persistence_restart_and_no_message_bodies(self):
        thread = self.state.route_inbound(self.message(timestamp=100, text="DO NOT PERSIST"))
        self.state.rename(thread.id, "planning")
        self.state.set_cancelled(thread.id)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertNotIn("DO NOT PERSIST", self.path.read_text())
        restarted = ThreadRoutingState(self.path, max_routes=20, max_threads=10)
        reply = restarted.route_inbound(self.message(
            timestamp=101, quote={"target_timestamp": 100}, text="new body",
        ))
        self.assertEqual(reply.id, thread.id)
        self.assertEqual(reply.name, "planning")
        self.assertTrue(reply.cancelled)

    def test_malformed_or_cross_chat_state_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "version": 1,
            "threads": [{"id": "x", "conversation": "dm:alice", "created_at": 1, "updated_at": 1}],
            "routes": [["dm:bob", 99, "x"]],
        }))
        loaded = ThreadRoutingState(self.path)
        self.assertEqual(loaded.threads, {})
        routed = loaded.route_inbound(self.message(
            source="bob", timestamp=100, quote={"target_timestamp": 99},
        ))
        self.assertNotEqual(routed.id, "x")

    def test_routes_and_metadata_are_bounded(self):
        state = ThreadRoutingState(self.path, max_routes=2, max_threads=2)
        for stamp in range(1, 5):
            state.route_inbound(self.message(timestamp=stamp))
        self.assertLessEqual(len(state.routes), 2)
        self.assertLessEqual(len(state.threads), 2)


class SignalThreadCommandAndSendTests(unittest.TestCase):
    def test_commands_are_exact_and_rename_is_bounded(self):
        self.assertEqual(deterministic_thread_command("/threads"), ("/threads", None))
        self.assertEqual(deterministic_thread_command("/status"), ("/status", None))
        self.assertEqual(deterministic_thread_command("/cancel"), ("/cancel", None))
        self.assertEqual(deterministic_thread_command("/rename Roadmap"), ("/rename", "Roadmap"))
        for invalid in ("/Threads", "/status ", "/cancel now", "/rename", "/rename  bad", "/rename bad\nname"):
            self.assertIsNone(deterministic_thread_command(invalid))
        self.assertIsNone(deterministic_thread_command("/rename " + "x" * 81))

    def test_outbound_response_uses_signal_cli_quote_params(self):
        rpc = FakeSignalRpc()
        send_signal(rpc, {
            "source": "alice", "group_id": None,
            "quote_timestamp": 123, "quote_author": "alice", "quote_message": "latest inbound",
        }, "answer")
        params = rpc.sent[0]["params"]
        self.assertEqual(params["quoteTimestamp"], 123)
        self.assertEqual(params["quoteAuthor"], "alice")
        self.assertEqual(params["quoteMessage"], "latest inbound")

    def test_send_correlation_maps_outbound_timestamp_to_thread(self):
        seen = []
        correlator = SendResponseCorrelator(OutboundMessageLedger(), lambda timestamp, item: seen.append((timestamp, item)))
        rpc = FakeSignalRpc()
        send_signal(rpc, {
            "source": "alice", "group_id": None, "conversation": "dm:alice", "thread_id": "thread-a",
        }, "answer", correlator)
        correlator.handle_response({"id": rpc.sent[0]["id"], "result": {"timestamp": 999}})
        self.assertEqual(seen[0][0], 999)
        self.assertEqual(seen[0][1]["thread_id"], "thread-a")

    def test_cancel_writes_abort_without_waiting_for_prompt_lock(self):
        agent = PiRpc.__new__(PiRpc)
        agent.lock = threading.Lock()
        agent.stdin_lock = threading.Lock()
        agent.stdin = io.StringIO()
        agent.cancel()
        self.assertEqual(json.loads(agent.stdin.getvalue()), {"type": "abort"})


class ConversationSchedulerTests(unittest.TestCase):
    def test_caps_active_threads_at_four_and_queues_fifth(self):
        release = threading.Event()
        all_done = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum = 0
        started = []

        def handler(item):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                started.append(item["n"])
            release.wait(2)
            with lock:
                active -= 1
                if len(started) == 5 and active == 0:
                    all_done.set()

        scheduler = ConversationScheduler(handler, max_active=4)
        for number in range(5):
            scheduler.submit(LogicalThread(str(number), "dm:alice", 1, 1), {"n": number})
        deadline = time.monotonic() + 1
        while len(started) < 4 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(started), 4)
        self.assertEqual(maximum, 4)
        release.set()
        self.assertTrue(all_done.wait(2))
        self.assertEqual(started, [0, 1, 2, 3, 4])

    def test_fifo_order_within_one_thread(self):
        order = []
        done = threading.Event()

        def handler(item):
            order.append(item["n"])
            if len(order) == 3:
                done.set()

        scheduler = ConversationScheduler(handler, max_active=4)
        thread = LogicalThread("same", "dm:alice", 1, 1)
        for number in range(3):
            scheduler.submit(thread, {"n": number})
        self.assertTrue(done.wait(2))
        self.assertEqual(order, [0, 1, 2])


class QuotedReplyTests(unittest.TestCase):
    def test_extracts_bounded_quote_context(self):
        event = {
            "method": "receive",
            "params": {"envelope": {
                "sourceNumber": "<SIGNAL_E164_EXAMPLE>",
                "timestamp": 222,
                "dataMessage": {
                    "message": "Yes, do that",
                    "quote": {
                        "id": "111",
                        "authorNumber": "<SIGNAL_E164_EXAMPLE>",
                        "text": "The message being answered",
                        "attachments": [{"contentType": "image/jpeg"}],
                    },
                },
            }},
        }
        message = extract_message(event)
        self.assertEqual(message["text"], "Yes, do that")
        self.assertEqual(message["quote"], {
            "target_timestamp": 111,
            "author": "<SIGNAL_E164_EXAMPLE>",
            "text": "The message being answered",
            "attachment_count": 1,
            "truncated": False,
        })

    def test_prompt_prefers_exact_same_chat_outbound_text(self):
        ledger = OutboundMessageLedger()
        ledger.put(111, {"text": "Exact full outbound text", "source": "owner", "group_id": None})
        item = {
            "source": "owner", "group_id": None,
            "quote": {"target_timestamp": 111, "author": "pi", "text": "short preview",
                      "attachment_count": 0, "truncated": False},
        }
        prompt = format_quoted_reply_prompt(item, ledger)
        self.assertIn("Exact full outbound text", prompt)
        self.assertNotIn("short preview", prompt)
        self.assertIn("does not approve", prompt)

    def test_prompt_does_not_leak_ledger_text_across_chats(self):
        ledger = OutboundMessageLedger()
        ledger.put(111, {"text": "Other chat secret", "source": "other", "group_id": None})
        item = {
            "source": "owner", "group_id": None,
            "quote": {"target_timestamp": 111, "author": "other", "text": "Visible embedded quote",
                      "attachment_count": 0, "truncated": False},
        }
        prompt = format_quoted_reply_prompt(item, ledger)
        self.assertNotIn("Other chat secret", prompt)
        self.assertIn("Visible embedded quote", prompt)


class ReactionTests(unittest.TestCase):
    def test_extracts_exact_message_bound_signal_reaction(self):
        event = {
            "method": "receive",
            "params": {"envelope": {
                "sourceNumber": "<SIGNAL_E164_EXAMPLE>",
                "dataMessage": {"reaction": {
                    "emoji": "👍", "targetTimestamp": 123456789, "isRemove": False,
                }},
            }},
        }
        self.assertEqual(extract_reaction(event), {
            "source": "<SIGNAL_E164_EXAMPLE>",
            "emoji": "👍",
            "target_timestamp": 123456789,
            "remove": False,
            "group_id": None,
        })

    def test_rejects_non_reaction_and_invalid_target(self):
        self.assertIsNone(extract_reaction({"method": "receive", "params": {}}))
        event = {
            "method": "receive",
            "params": {"envelope": {
                "sourceNumber": "<SIGNAL_E164_EXAMPLE>",
                "dataMessage": {"reaction": {"emoji": "👍", "targetTimestamp": 0}},
            }},
        }
        self.assertIsNone(extract_reaction(event))

    def test_send_response_correlation_records_only_matching_successful_timestamp(self):
        ledger = OutboundMessageLedger(10)
        correlator = SendResponseCorrelator(ledger)
        message = {"text": "exact reply", "source": "owner", "group_id": None}
        correlator.register("request-1", message)
        self.assertFalse(correlator.handle_response({"id": "other", "result": {"timestamp": 11}}))
        self.assertTrue(correlator.handle_response({"id": "request-1", "result": {"timestamp": "123"}}))
        self.assertEqual(ledger.get(123), message)
        self.assertFalse(correlator.handle_response({"id": "request-1", "result": {"timestamp": 124}}))

    def test_send_timestamp_defensive_result_shapes(self):
        responses = [
            ({"result": 101}, 101),
            ({"result": {"timestamp": "102"}}, 102),
            ({"result": {"sentTimestamp": 103}}, 103),
            ({"result": [{"messageTimestamp": 104}]}, 104),
            ({"result": {"results": [{"timestamp": 105}]}}, 105),
            ({"result": {"timestamp": True}}, None),
            ({"error": {"message": "failed"}}, None),
        ]
        for response, expected in responses:
            with self.subTest(response=response):
                self.assertEqual(extract_send_timestamp(response), expected)

    def test_ledger_is_bounded_and_returns_copies(self):
        ledger = OutboundMessageLedger(2)
        ledger.put(1, {"text": "one"})
        ledger.put(2, {"text": "two"})
        ledger.put(3, {"text": "three"})
        self.assertEqual(len(ledger), 2)
        self.assertIsNone(ledger.get(1))
        result = ledger.get(2)
        result["text"] = "changed"
        self.assertEqual(ledger.get(2)["text"], "two")

    def test_reaction_prompts_include_exact_text_and_safeguard(self):
        reaction = {
            "source": "owner", "group_id": None, "emoji": "✅",
            "target_timestamp": 999, "remove": False,
        }
        prompt = format_reaction_prompt(reaction, {"text": "Line one\nLine two"})
        self.assertIn("✅", prompt)
        self.assertIn('"""\nLine one\nLine two\n"""', prompt)
        self.assertIn("must not approve", prompt)
        self.assertIn("proposal/email", prompt)

    def test_removal_and_unknown_target_have_distinct_feedback(self):
        reaction = {
            "source": "owner", "group_id": None, "emoji": "👍",
            "target_timestamp": 8675309, "remove": True,
        }
        prompt = format_reaction_prompt(reaction, None)
        self.assertIn("removed their reaction", prompt)
        self.assertIn("8675309", prompt)
        self.assertIn("text is unavailable", prompt)
        self.assertNotIn("the user reacted with", prompt)

    def test_work_item_preserves_conversation_route(self):
        ledger = OutboundMessageLedger()
        ledger.put(44, {"text": "group reply", "source": "someone", "group_id": "g1"})
        reaction = {
            "source": "member", "group_id": "g1", "emoji": "❤️",
            "target_timestamp": 44, "remove": False,
        }
        item = reaction_work_item(reaction, ledger)
        self.assertEqual((item["source"], item["group_id"]), ("member", "g1"))
        self.assertIn("group reply", item["text"])

    def test_work_item_does_not_disclose_text_across_conversations(self):
        ledger = OutboundMessageLedger()
        ledger.put(44, {"text": "owner secret", "source": "owner", "group_id": None})
        reaction = {
            "source": "other", "group_id": None, "emoji": "👀",
            "target_timestamp": 44, "remove": False,
        }
        item = reaction_work_item(reaction, ledger)
        self.assertNotIn("owner secret", item["text"])
        self.assertIn("text is unavailable", item["text"])

    def test_reaction_routing_matches_dm_and_group_permissions(self):
        args = ({"source": "owner", "group_id": None}, {"owner"}, False, set(), set())
        self.assertTrue(reaction_routing_allowed(*args))
        self.assertFalse(reaction_routing_allowed(
            {"source": "stranger", "group_id": None}, {"owner"}, True, set(), set()
        ))
        self.assertTrue(reaction_routing_allowed(
            {"source": "member", "group_id": "g1"}, {"owner"}, True, {"g1"}, {"member"}
        ))
        self.assertFalse(reaction_routing_allowed(
            {"source": "other", "group_id": "g1"}, {"owner"}, True, {"g1"}, {"member"}
        ))
        self.assertFalse(reaction_routing_allowed(
            {"source": "member", "group_id": "g2"}, {"owner"}, True, {"g1"}, {"member"}
        ))


class AuthFileTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.pi = root / "pi" / "auth.json"
        self.openclaw = root / "openclaw" / "auth-profiles.json"
        self.pi.parent.mkdir()
        self.openclaw.parent.mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_sync_uses_newest_valid_profile_and_preserves_other_providers(self):
        self.write_json(self.pi, {"anthropic": {"type": "api_key", "key": "keep"}, "openai-codex": {"old": True}})
        self.write_json(
            self.openclaw,
            {
                "version": 1,
                "profiles": {
                    "openai-codex:old": {
                        "type": "oauth", "provider": "openai-codex", "access": "old-access",
                        "refresh": "old-refresh", "expires": 100, "accountId": "old-account", "extra": "ignore",
                    },
                    "openai-codex:new": {
                        "type": "oauth", "provider": "openai-codex", "access": "new-access",
                        "refresh": "new-refresh", "expires": 200, "accountId": "new-account", "extra": "ignore",
                    },
                    "openai-codex:invalid": {
                        "type": "oauth", "provider": "openai-codex", "access": "later", "expires": 300,
                        "accountId": "invalid-account",
                    },
                },
            },
        )
        ok, message = sync_pi_auth(self.pi, self.openclaw)
        self.assertTrue(ok)
        result = json.loads(self.pi.read_text())
        self.assertEqual(result["anthropic"]["key"], "keep")
        self.assertEqual(
            result["openai-codex"],
            {"type": "oauth", "access": "new-access", "refresh": "new-refresh", "expires": 200, "accountId": "new-account"},
        )
        self.assertEqual(stat.S_IMODE(os.stat(self.pi).st_mode), 0o600)
        self.assertNotIn("new-access", message)
        self.assertNotIn("new-refresh", message)

    def test_sync_failure_is_safe_and_does_not_replace_destination(self):
        original = {"other": {"value": "preserved"}}
        self.write_json(self.pi, original)
        self.write_json(self.openclaw, {"profiles": {"openai-codex:bad": {"type": "oauth"}}})
        ok, message = sync_pi_auth(self.pi, self.openclaw)
        self.assertFalse(ok)
        self.assertEqual(json.loads(self.pi.read_text()), original)
        self.assertIn("manual reauth", message)

    def test_status_reports_expiration_without_secrets(self):
        self.write_json(
            self.pi,
            {"openai-codex": {"type": "oauth", "access": "ACCESS_SECRET", "refresh": "REFRESH_SECRET", "expires": 1000}},
        )
        state = pi_auth_state(self.pi, now_ms=2000)
        self.assertTrue(state["exists"])
        self.assertTrue(state["expired"])
        self.assertTrue(state["has_refresh"])
        message = format_pi_auth_status(state)
        self.assertIn("expired at", message)
        self.assertNotIn("ACCESS_SECRET", message)
        self.assertNotIn("REFRESH_SECRET", message)


class FakeAgent:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.prompts = []

    def prompt(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise RuntimeError(self.error)
        return self.result


class PiRpcSettlementTests(unittest.TestCase):
    @staticmethod
    def agent_with_events(events):
        agent = PiRpc.__new__(PiRpc)
        agent.lock = threading.Lock()
        agent.stdin = io.StringIO()
        agent.stdout = io.StringIO("".join(json.dumps(event) + "\n" for event in events))
        return agent

    def test_outcome_extracts_text_and_error(self):
        text, error = rpc_assistant_outcome({
            "type": "message_end",
            "message": {"role": "assistant", "stopReason": "stop", "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "Recovered"},
            ]},
        })
        self.assertEqual((text, error), ("Recovered", None))
        text, error = rpc_assistant_outcome({
            "type": "message_end",
            "message": {"role": "assistant", "stopReason": "error", "errorMessage": "WebSocket error"},
        })
        self.assertEqual((text, error), (None, "WebSocket error"))

    def test_transient_attempt_error_is_hidden_when_auto_retry_recovers(self):
        request_id = "request-1"
        events = [
            {"type": "response", "id": request_id, "success": True},
            {"type": "message_end", "message": {
                "role": "assistant", "stopReason": "error", "errorMessage": "WebSocket error",
            }},
            {"type": "agent_end", "willRetry": True, "messages": [{
                "role": "assistant", "stopReason": "error", "errorMessage": "WebSocket error",
            }]},
            {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3},
            {"type": "message_end", "message": {
                "role": "assistant", "stopReason": "stop",
                "content": [{"type": "text", "text": "Recovered response"}],
            }},
            {"type": "agent_end", "willRetry": False, "messages": []},
            {"type": "agent_settled"},
        ]
        agent = self.agent_with_events(events)
        with mock.patch("signal_pi_bridge.uuid.uuid4", return_value=request_id):
            self.assertEqual(agent.prompt("hello"), "Recovered response")

    def test_final_error_is_raised_only_after_agent_settled(self):
        request_id = "request-2"
        events = [
            {"type": "response", "id": request_id, "success": True},
            {"type": "message_end", "message": {
                "role": "assistant", "stopReason": "error", "errorMessage": "WebSocket error",
            }},
            {"type": "agent_end", "willRetry": False, "messages": []},
            {"type": "agent_settled"},
        ]
        agent = self.agent_with_events(events)
        with mock.patch("signal_pi_bridge.uuid.uuid4", return_value=request_id):
            with self.assertRaisesRegex(RuntimeError, "WebSocket error"):
                agent.prompt("hello")

    def test_agent_end_without_settled_is_not_treated_as_completion(self):
        request_id = "request-3"
        agent = self.agent_with_events([
            {"type": "response", "id": request_id, "success": True},
            {"type": "message_end", "message": {
                "role": "assistant", "stopReason": "stop",
                "content": [{"type": "text", "text": "too early"}],
            }},
            {"type": "agent_end", "willRetry": False, "messages": []},
        ])
        with mock.patch("signal_pi_bridge.uuid.uuid4", return_value=request_id):
            with self.assertRaisesRegex(EOFError, "RPC process exited"):
                agent.prompt("hello")


class RuntimeRecoveryTests(unittest.TestCase):
    def test_usage_limit_error_is_actionable_over_signal(self):
        message = format_runtime_error(RuntimeError("Codex error: The usage limit has been reached"))
        self.assertIn("Codex usage limit has been reached", message)
        self.assertIn("usage resets", message)
        self.assertIn("another available model", message)
        self.assertIn("Signal is still online", message)

    def test_quota_and_rate_limit_errors_have_distinct_messages(self):
        self.assertIn("Codex usage limit", format_runtime_error("insufficient_quota"))
        rate_limited = format_runtime_error("HTTP 429: Too Many Requests")
        self.assertIn("temporarily rate-limiting", rate_limited)
        self.assertNotIn("usage resets", rate_limited)

    def test_unknown_runtime_error_does_not_leak_provider_detail(self):
        message = format_runtime_error("provider failed with secret-token-value")
        self.assertIn("Check bridge logs", message)
        self.assertNotIn("secret-token-value", message)

    def test_deterministic_commands_are_exact_allowed_dms_only(self):
        allowed = {"owner"}
        self.assertEqual(
            deterministic_auth_command({"source": "owner", "group_id": None, "text": "help"}, allowed),
            "help",
        )
        self.assertEqual(
            deterministic_auth_command({"source": "owner", "group_id": None, "text": "pi auth status"}, allowed),
            "pi auth status",
        )
        self.assertIsNone(deterministic_auth_command({"source": "owner", "group_id": None, "text": "pi auth status "}, allowed))
        self.assertIsNone(deterministic_auth_command({"source": "owner", "group_id": None, "text": "Help"}, allowed))
        self.assertIsNone(deterministic_auth_command({"source": "stranger", "group_id": None, "text": "repair pi auth"}, allowed))
        self.assertIsNone(deterministic_auth_command({"source": "owner", "group_id": "group", "text": "repair pi auth"}, allowed))

    def test_auth_error_repairs_discards_and_retries_once_with_fresh_agent(self):
        failed = FakeAgent(error="401 unauthorized")
        fresh = FakeAgent(result="success")
        discarded = []
        repairs = []
        reply, returned_agent = prompt_with_auth_recovery(
            "original", failed, lambda: (repairs.append(True) or True, "repaired"), discarded.append, lambda: fresh
        )
        self.assertEqual(reply, "success")
        self.assertIs(returned_agent, fresh)
        self.assertEqual(repairs, [True])
        self.assertEqual(discarded, [failed])
        self.assertEqual(fresh.prompts, ["original"])

    def test_failed_repair_returns_safe_status_without_retry(self):
        failed = FakeAgent(error="token expired")
        created = []
        reply, returned_agent = prompt_with_auth_recovery(
            "original", failed, lambda: (False, "Transport remains online; manual reauth is needed."),
            lambda agent: None, lambda: created.append(True),
        )
        self.assertIn("manual reauth", reply)
        self.assertIsNone(returned_agent)
        self.assertEqual(created, [])

    def test_failed_retry_discards_both_cached_and_fresh_agents(self):
        failed = FakeAgent(error="401 unauthorized")
        fresh = FakeAgent(error="refresh failed")
        discarded = []
        with self.assertRaisesRegex(RuntimeError, "refresh failed"):
            prompt_with_auth_recovery("original", failed, lambda: (True, "repaired"), discarded.append, lambda: fresh)
        self.assertEqual(discarded, [failed, fresh])

    def test_non_auth_error_is_not_repaired(self):
        with self.assertRaisesRegex(RuntimeError, "network broke"):
            prompt_with_auth_recovery(
                "original", FakeAgent(error="network broke"), lambda: self.fail("repair called"),
                lambda agent: self.fail("discard called"), lambda: self.fail("create called"),
            )


if __name__ == "__main__":
    unittest.main()
