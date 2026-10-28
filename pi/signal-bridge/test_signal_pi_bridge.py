import io
import hashlib
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

class PrivateTmuxTestIsolation:
    """Delegate namespace cleanup to a guardian that survives its test parent."""

    def __init__(self, prefix="bridge-private-tmux-"):
        self.root = Path(tempfile.mkdtemp(prefix=prefix))
        guardian = Path(__file__).with_name("test_tmux_namespace_guard.py")
        self.guard = subprocess.Popen(
            [sys.executable, str(guardian), str(self.root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )

    def __enter__(self):
        if self.guard.poll() is not None:
            raise RuntimeError("private tmux test guardian exited during startup")
        return self

    def cleanup(self):
        # EOF also triggers this cleanup if the test runner exits or is killed.
        try:
            stdout, stderr = self.guard.communicate("cleanup\n", timeout=15)
        except subprocess.TimeoutExpired as error:
            self.guard.kill()
            self.guard.wait(timeout=2)
            raise AssertionError("private tmux test guardian timed out") from error
        try:
            result = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as error:
            raise AssertionError(
                f"private tmux test guardian returned invalid output: {stderr.strip()}"
            ) from error
        if self.guard.returncode or result["survivors"] or result["cleanup_errors"]:
            raise AssertionError(f"private tmux test cleanup failed: {result}")

    def __exit__(self, exc_type, exc, traceback):
        self.cleanup()
        return False


from durable_lifecycle import DurableLifecycleController
from signal_pi_bridge import (
    AgentCache,
    HarnessRegistry,
    apply_group_harness_command,
    OutboundMessageLedger,
    PiRpc,
    IdentityProofTimeout,
    RegistrarError,
    SendResponseCorrelator,
    ThreadDispatcher,
    ThreadStore,
    command_for,
    conversation_id,
    construct_agent,
    initial_terminal_topic,
    deterministic_auth_command,
    discard_cached_agent,
    extract_message,
    extract_reaction,
    extract_send_timestamp,
    format_quoted_reply_prompt,
    format_reaction_prompt,
    format_pi_auth_status,
    format_runtime_error,
    format_thread,
    group_sender_allowed,
    harness_command_for,
    identity_proof_timeout,
    invoke_effective_group_route_plugin,
    invoke_group_route_plugin,
    matches_trigger_prefix,
    normalize_group_route_escape,
    launch_tmux_session,
    load_personal_admin_gog_credential,
    normalize_attachments,
    open_tmux_rpc_fifos,
    pi_session_command,
    pi_auth_state,
    prepare_effective_group_route_item,
    prompt_with_auth_recovery,
    reaction_routing_allowed,
    reaction_work_item,
    rpc_assistant_outcome,
    sync_pi_auth,
    tmux_control_timeout,
    trigger_route,
    send_signal,
    validate_group_route_plugins,
    worker_backend,
    worker_child_environment,
)


class HarnessRegistryTests(unittest.TestCase):
    MAPPED_GROUPS = {"harness-lab", "experimental-research-agents", "future-group"}

    def _registry(self) -> HarnessRegistry:
        registry = HarnessRegistry({
            "harnesses": {
                "normal-pi": {
                    "command": ["/bin/true"], "mode": "sandbox",
                    "allowed_group_ids": sorted(self.MAPPED_GROUPS),
                },
                "host-pi": {
                    "command": ["/bin/true"], "mode": "host",
                    "allowed_group_ids": ["harness-lab"], "allowed_senders": ["owner"],
                },
                "sandbox-pi": {
                    "command": ["/bin/true"], "mode": "sandbox",
                    "allowed_group_ids": ["harness-lab"], "allowed_senders": ["owner"],
                },
            },
            "default_harness": "normal-pi",
            "direct_harness": "normal-pi",
            "default_harness_by_group_id": {"harness-lab": "host-pi"},
        })
        registry.validate_group_scope(self.MAPPED_GROUPS, "harness-lab")
        return registry

    def test_dms_and_every_non_lab_group_execute_only_normal_pi(self):
        registry = self._registry()
        self.assertEqual(registry.effective(None, None, "owner"), "normal-pi")
        for group_id in ("experimental-research-agents", "future-group"):
            self.assertEqual(registry.default_for(group_id), "normal-pi")
            self.assertEqual(registry.effective(None, group_id, "owner"), "normal-pi")
            self.assertFalse(registry.authorized("host-pi", group_id, "owner"))
            self.assertFalse(registry.authorized("sandbox-pi", group_id, "owner"))

    def test_stale_alternate_selection_outside_lab_fails_closed_to_normal(self):
        registry = self._registry()
        self.assertEqual(
            registry.effective("host-pi", "experimental-research-agents", "owner"),
            "normal-pi",
        )
        self.assertEqual(registry.effective("sandbox-pi", "future-group", "owner"), "normal-pi")
        self.assertEqual(registry.effective("host-pi", None, "owner"), "normal-pi")

    def test_invalid_direct_or_broad_alternate_authorization_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "direct_harness"):
            HarnessRegistry({
                "harnesses": {"normal-pi": {"command": ["/bin/true"]}},
                "default_harness": "normal-pi", "direct_harness": "host-pi",
            })
        registry = HarnessRegistry({
            "harnesses": {
                "normal-pi": {"command": ["/bin/true"], "allowed_group_ids": sorted(self.MAPPED_GROUPS)},
                "host-pi": {
                    "command": ["/bin/true"], "mode": "host",
                    "allowed_group_ids": ["harness-lab", "future-group"], "allowed_senders": ["owner"],
                },
            },
            "default_harness": "normal-pi", "direct_harness": "normal-pi",
        })
        with self.assertRaisesRegex(ValueError, "restricted exactly to Harness Lab"):
            registry.validate_group_scope(self.MAPPED_GROUPS, "harness-lab")

    @unittest.skip("Host deployment contract requires private config.json; never export it")
    def test_active_config_exactly_satisfies_routing_policy(self):
        config = json.loads(Path(__file__).with_name("config.json").read_text())
        labels = {
            value["signal_group_id"]: value["label"]
            for value in config["vaults"]["groups"].values()
        }
        lab_ids = {group_id for group_id, label in labels.items() if label == "Harness Lab"}
        self.assertEqual(len(lab_ids), 1)
        registry = HarnessRegistry(config["pi"])
        registry.validate_group_scope(set(labels), next(iter(lab_ids)))
        self.assertEqual(registry.default, "normal-pi")
        self.assertEqual(registry.direct, "normal-pi")


class HarnessCommandTests(unittest.TestCase):
    def _registry(self):
        mapped = {"harness-lab", "ordinary"}
        registry = HarnessRegistry({
            "harnesses": {
                "normal-pi": {
                    "command": ["/bin/true"], "mode": "sandbox",
                    "allowed_group_ids": sorted(mapped),
                },
                "host-pi": {
                    "command": ["/bin/true"], "mode": "host",
                    "allowed_group_ids": ["harness-lab"], "allowed_senders": ["owner"],
                },
                "sandbox-pi": {
                    "command": ["/bin/true"], "mode": "sandbox",
                    "allowed_group_ids": ["harness-lab"], "allowed_senders": ["owner"],
                },
            },
            "default_harness": "normal-pi",
            "direct_harness": "normal-pi",
            "default_harness_by_group_id": {"harness-lab": "host-pi"},
        })
        registry.validate_group_scope(mapped, "harness-lab")
        return registry

    def test_harness_selection_is_disabled_for_ordinary_groups(self):
        store = mock.Mock()
        response = apply_group_harness_command(
            store, self._registry(),
            {"group_id": "ordinary", "source": "owner"},
            harness_command_for("/harness info"),
        )
        self.assertEqual(response, "Harness selection is not enabled for this group.")
        store.selected_harness.assert_not_called()
        store.set_selected_harness.assert_not_called()

    def test_info_shows_host_pi_for_authorized_harness_lab_member(self):
        response = apply_group_harness_command(
            mock.Mock(selected_harness=mock.Mock(return_value="sandbox-pi")),
            self._registry(),
            {"group_id": "harness-lab", "source": "owner"},
            harness_command_for("/harness info"),
        )
        self.assertIn("Configured selection: sandbox-pi", response)
        self.assertIn("Effective harness: sandbox-pi", response)
        self.assertIn("Default harness: host-pi", response)
        self.assertIn("Available harnesses:\n- host-pi\n- normal-pi\n- sandbox-pi", response)

    def test_alternates_are_unavailable_to_unauthorized_lab_sender(self):
        registry = self._registry()
        self.assertEqual(registry.effective("host-pi", "harness-lab", "stranger"), "normal-pi")
        self.assertFalse(registry.selection_enabled("harness-lab", "stranger"))
        self.assertTrue(registry.selection_enabled("harness-lab", "owner"))

    def test_info_is_group_only_and_invalid_usage_mentions_info(self):
        store = mock.Mock()
        registry = self._registry()
        self.assertEqual(harness_command_for("/harness info"), ("info", ""))
        self.assertEqual(
            apply_group_harness_command(
                store, registry, {"source": "owner"}, ("info", ""),
            ),
            "Harness selection is available only in Signal groups.",
        )
        store.selected_harness.assert_not_called()
        self.assertEqual(
            apply_group_harness_command(
                store, registry, {"group_id": "harness-lab", "source": "owner"},
                harness_command_for("/harness nonsense"),
            ),
            "Usage: /harness use <name>, /harness default, /harness status, or /harness info.",
        )


class GroupRoutePluginTests(unittest.TestCase):
    def test_validation_is_group_scoped_and_requires_absolute_command(self):
        group_id = "approved"
        value = validate_group_route_plugins({group_id: {
            "command": ["/usr/bin/python3", "/safe/router.py"], "timeout_seconds": 30,
        }}, {group_id})
        self.assertEqual(value[group_id]["timeout_seconds"], 30.0)
        with self.assertRaisesRegex(Exception, "approved group"):
            validate_group_route_plugins({"other": {
                "command": ["/bin/true"], "timeout_seconds": 1,
            }}, {group_id})
        with self.assertRaisesRegex(Exception, "absolute command"):
            validate_group_route_plugins({group_id: {
                "command": ["python3"], "timeout_seconds": 1,
            }}, {group_id})

    def test_invocation_uses_bounded_json_contract(self):
        command = [sys.executable, "-c", (
            "import json,sys; x=json.load(sys.stdin); "
            "print(json.dumps({'handled':True,'reply':x['text']}))"
        )]
        result = invoke_group_route_plugin(
            {"command": command, "timeout_seconds": 2},
            {"group_id": "g", "source": "+1", "timestamp": 1, "text": "hello"},
        )
        self.assertEqual(result, {"handled": True, "reply": "hello"})

    def _registry(self):
        registry = HarnessRegistry({
            "harnesses": {
                "normal-pi": {
                    "command": ["/bin/true"], "mode": "sandbox",
                    "allowed_group_ids": ["harness-lab"],
                },
                "host-pi": {
                    "command": ["/bin/true"], "mode": "host",
                    "allowed_group_ids": ["harness-lab"], "allowed_senders": ["owner"],
                },
                "sandbox-pi": {
                    "command": ["/bin/true"], "mode": "sandbox",
                    "allowed_group_ids": ["harness-lab"], "allowed_senders": ["owner"],
                },
            },
            "default_harness": "normal-pi", "direct_harness": "normal-pi",
        })
        registry.validate_group_scope({"harness-lab"}, "harness-lab")
        return registry

    def test_effective_host_bypasses_plugin_while_sandbox_invokes_it(self):
        registry = self._registry()
        plugin = {"command": ["/bin/false"], "timeout_seconds": 1}
        item = {
            "group_id": "harness-lab", "source": "owner", "timestamp": 1,
            "text": "hello",
        }
        with mock.patch(
            "signal_pi_bridge.invoke_group_route_plugin",
            return_value={"handled": False},
        ) as invoke:
            prepare_effective_group_route_item(
                registry, item, plugin, "host-pi", False,
            )
            self.assertNotIn("group_route_plugin", item)
            self.assertIsNone(invoke_effective_group_route_plugin(registry, item))
            invoke.assert_not_called()

            prepare_effective_group_route_item(
                registry, item, plugin, "sandbox-pi", False,
            )
            self.assertEqual(
                invoke_effective_group_route_plugin(registry, item),
                {"handled": False},
            )
            invoke.assert_called_once_with(plugin, item)

    def test_explicit_host_escape_bypasses_plugin_under_effective_sandbox(self):
        registry = self._registry()
        plugin = {"command": ["/bin/false"], "timeout_seconds": 1}
        text, explicit_host_escape = normalize_group_route_escape(
            "/host hello", plugin,
        )
        item = {
            "group_id": "harness-lab", "source": "owner", "timestamp": 1,
            "text": text,
        }
        prepare_effective_group_route_item(
            registry, item, plugin, "sandbox-pi", explicit_host_escape,
        )

        self.assertEqual(item["text"], "hello")
        self.assertEqual(item["harness"], "sandbox-pi")
        self.assertNotIn("group_route_plugin", item)
        with mock.patch("signal_pi_bridge.invoke_group_route_plugin") as invoke:
            self.assertIsNone(invoke_effective_group_route_plugin(registry, item))
            invoke.assert_not_called()


class PersonalAdminGogEnvironmentTests(unittest.TestCase):
    def test_only_personal_admin_receives_gog_values(self):
        base = {
            "PATH": "/usr/bin", "CREDENTIALS_DIRECTORY": "/run/credentials/unit",
            "GOG_KEYRING_PASSWORD": "stale", "GOG_ACCOUNT": "stale-account",
            "GOG_KEYRING": "stale-keyring", "GOG_SECRET": "stale-secret",
        }
        credential = ("correct-horse", "personal@example.invalid")
        personal = worker_child_environment("Personal/Admin", credential, base)
        self.assertEqual(personal["GOG_KEYRING_PASSWORD"], credential[0])
        self.assertEqual(personal["GOG_ACCOUNT"], credential[1])
        self.assertNotIn("CREDENTIALS_DIRECTORY", personal)
        for profile in ("GovFiles", "FRNK", "System/Automation", "Pi — Research", "BYOQ", "Harness Lab", "Experimental Research Agents", "Alerts", "Direct"):
            environment = worker_child_environment(profile, credential, base)
            self.assertEqual(environment, {"PATH": "/usr/bin"})

    def test_agora_read_context_is_exact_vault_scoped_and_fails_closed(self):
        stale = {"PATH": "/usr/bin", "PI_SIGNAL_VAULT_ID": "experimental-research-agents"}
        inside = worker_child_environment(
            "Experimental Research Agents", None, stale, vault_id="experimental-research-agents",
        )
        self.assertEqual(inside["PI_SIGNAL_VAULT_ID"], "experimental-research-agents")
        for terminal_group, vault_id in (
            ("Experimental Research Agents", None),
            ("Pi — Research", "research"),
            ("Harness Lab", "experimental-research-agents-typo"),
            ("Direct", None),
        ):
            with self.subTest(terminal_group=terminal_group, vault_id=vault_id):
                outside = worker_child_environment(terminal_group, None, stale, vault_id=vault_id)
                self.assertNotIn("PI_SIGNAL_VAULT_ID", outside)

    def test_signal_delivery_destination_is_exactly_conversation_bound(self):
        stale = {
            "PATH": "/usr/bin", "PI_SIGNAL_BOUND_DESTINATION": '{"recipient":"stale"}',
            "PI_SIGNAL_THREAD_STATE_PATH": "/stale/state.sqlite3",
        }
        group = worker_child_environment(
            "GovFiles", None, stale, signal_destination={"groupId": "group-fixture"},
            signal_thread_state_path=Path("/bound/threads.sqlite3"),
        )
        self.assertEqual(
            json.loads(group["PI_SIGNAL_BOUND_DESTINATION"]), {"groupId": "group-fixture"},
        )
        self.assertEqual(group["PI_SIGNAL_THREAD_STATE_PATH"], "/bound/threads.sqlite3")
        direct = worker_child_environment(
            "Direct", None, stale, signal_destination={"recipient": "owner-fixture"},
        )
        self.assertEqual(
            json.loads(direct["PI_SIGNAL_BOUND_DESTINATION"]), {"recipient": "owner-fixture"},
        )
        cleared = worker_child_environment("GovFiles", None, stale)
        self.assertNotIn("PI_SIGNAL_BOUND_DESTINATION", cleared)
        self.assertNotIn("PI_SIGNAL_THREAD_STATE_PATH", cleared)
        for invalid in ({}, {"recipient": "owner", "groupId": "group"}, {"groupId": ""}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(Exception, "destination"):
                worker_child_environment("GovFiles", None, stale, signal_destination=invalid)

    def test_personal_admin_fails_closed_without_credential(self):
        with self.assertRaisesRegex(Exception, "credential is unavailable"):
            worker_child_environment("Personal/Admin", None, {"PATH": "/usr/bin"})

    def test_systemd_credential_load_is_private_and_single_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            credential = directory / "gog-keyring-password"
            credential.write_text("in-memory-only", encoding="utf-8")
            os.chmod(credential, 0o400)
            loaded = load_personal_admin_gog_credential(
                {
                    "personal_admin_gog_credential": "gog-keyring-password",
                    "personal_admin_gog_account": "personal@example.invalid",
                },
                {"CREDENTIALS_DIRECTORY": temporary},
            )
            self.assertEqual(loaded, ("in-memory-only", "personal@example.invalid"))
            credential.write_text("bad\nvalue", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "one non-empty line"):
                load_personal_admin_gog_credential(
                    {
                        "personal_admin_gog_credential": "gog-keyring-password",
                        "personal_admin_gog_account": "personal@example.invalid",
                    },
                    {"CREDENTIALS_DIRECTORY": temporary},
                )

    def test_secret_is_environment_only_not_command_or_log_rendering(self):
        secret = "sentinel-gog-secret"
        environment = worker_child_environment(
            "Personal/Admin", (secret, "personal@example.invalid"), {"PATH": "/usr/bin"},
        )
        command = ["/root/.pi/agent/bin/pi", "--mode", "rpc"]
        self.assertNotIn(secret, " ".join(command))
        self.assertNotIn(secret, repr(command))
        self.assertEqual(environment["GOG_KEYRING_PASSWORD"], secret)


class AttachmentNormalizationTests(unittest.TestCase):
    def test_inaccessible_attachment_path_preserves_safe_metadata(self):
        raw = {
            "attachments": [{
                "id": "opaque-attachment-id",
                "contentType": "image/jpeg",
                "filename": "photo.jpg",
                "size": 1234,
                "width": 640,
                "height": 480,
                "path": "/private/attachment.jpg",
            }],
        }
        with mock.patch("signal_pi_bridge.Path.exists", side_effect=PermissionError("denied")):
            attachments = normalize_attachments(raw)

        self.assertEqual(attachments, [{
            "id": "opaque-attachment-id",
            "contentType": "image/jpeg",
            "filename": "photo.jpg",
            "size": 1234,
            "width": 640,
            "height": 480,
            "path": None,
        }])
        self.assertNotIn("/private/attachment.jpg", repr(attachments))

    def test_unreadable_attachment_does_not_drop_voice_memo_metadata(self):
        event = {
            "method": "receive",
            "params": {"envelope": {
                "sourceNumber": "+15555550123",
                "timestamp": 222,
                "dataMessage": {"attachments": [{
                    "attachmentId": "voice-id",
                    "content_type": "audio/aac",
                    "filename": "voice-note.aac",
                    "size": 4321,
                    "storedFilename": "/private/voice-note.aac",
                }]},
            }},
        }
        with mock.patch("signal_pi_bridge.Path.exists", side_effect=OSError("unreadable")):
            message = extract_message(event)

        self.assertIsNotNone(message)
        self.assertEqual(message["attachments"][0], {
            "id": "voice-id",
            "contentType": "audio/aac",
            "filename": "voice-note.aac",
            "size": 4321,
            "width": None,
            "height": None,
            "path": None,
        })


class ForwardRecoveryTests(unittest.TestCase):
    @staticmethod
    def _isolated_registrar(root):
        registry = root / "registry"
        registry.mkdir(mode=0o700)
        registrar = root / "registrar.py"
        registrar.write_text(
            "#!/usr/bin/env python3\n"
            "import json,pathlib,sys,uuid\n"
            f"root=pathlib.Path({str(registry)!r})\n"
            "args=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n"
            "value=uuid.uuid4().hex; name='test-'+value[:8]\n"
            "record={'schemaVersion':4,'id':value,'name':name,'target':args['--target'],'socketPath':args['--socket'],'piSessionId':args['--pi-session-id'],'group':args['--group']}\n"
            "artifact=root/(value+'.json'); artifact.write_text(json.dumps(record)); artifact.chmod(0o600)\n"
            "alias=root/('.'+name+'.alias'); alias.write_bytes(b''); alias.chmod(0o600)\n"
            "print(value)\n",
            encoding="utf-8",
        )
        registrar.chmod(0o700)
        return registry, registrar

    def test_external_guardian_reaps_namespace_after_parent_sigkill(self):
        code = """import os,signal,subprocess,sys
from test_signal_pi_bridge import PrivateTmuxTestIsolation
isolation=PrivateTmuxTestIsolation(prefix='guard-crash-contract-')
subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],cwd=isolation.root)
print(isolation.root,flush=True)
os.kill(os.getpid(),signal.SIGKILL)
"""
        child = subprocess.Popen(
            [sys.executable, "-c", code], cwd=Path(__file__).parent,
            stdout=subprocess.PIPE, text=True,
        )
        assert child.stdout is not None
        root = Path(child.stdout.readline().strip())
        child.stdout.close()
        self.assertTrue(str(root).startswith("/tmp/guard-crash-contract-"))
        child.wait(timeout=5)
        deadline = time.monotonic() + 15
        while root.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assertFalse(root.exists())

    def test_identity_budget_is_distinct_bounded_and_defaults_to_twenty_seconds(self):
        self.assertEqual(identity_proof_timeout({}), 20.0)
        self.assertEqual(identity_proof_timeout({"identity_proof_timeout_seconds": 17}), 17.0)
        for invalid in (True, 0, 0.9, 61, "unbounded"):
            with self.assertRaises(ValueError):
                identity_proof_timeout({"identity_proof_timeout_seconds": invalid})

    def test_delayed_identity_within_its_own_budget_succeeds(self):
        release = threading.Event()
        entered = threading.Event()

        class DelayedStream:
            def __iter__(self):
                entered.set()
                release.wait(1)
                yield json.dumps({
                    "type": "response", "id": "identity", "success": True,
                    "data": {"sessionId": "persistent-session-123"},
                }) + "\n"

        releaser = threading.Thread(target=lambda: (entered.wait(1), release.set()))
        releaser.start()
        try:
            value = PiRpc._read_pi_session_identity(
                DelayedStream(), "identity", time.monotonic() + 1,
            )
        finally:
            releaser.join(1)
        self.assertEqual(value, "persistent-session-123")

    def test_identity_timeout_retries_construction_exactly_once_with_same_session_id(self):
        commands = []
        sessions = []
        prompts = []

        class ReadyAgent:
            kind = "tmux"

            def prompt(self, value):
                prompts.append(value)
                return "ok"

        def factory(command, unused_cwd, session, unused_runtime, **unused_kwargs):
            commands.append(command)
            sessions.append(session)
            if len(commands) == 1:
                raise IdentityProofTimeout("bounded identity timeout")
            return ReadyAgent()

        agent = construct_agent(
            {"thread_id": "logical-thread", "group_id": None},
            {"id": "logical-thread", "pi_session": "persistent-pi-session", "name": "DM"},
            {"command": ["pi", "--mode", "rpc"]}, "tmux", Path("/synthetic"), None,
            factory, identity_timeout=20,
        )
        self.assertEqual(agent.kind, "tmux")
        self.assertEqual(len(commands), 2)
        self.assertNotEqual(sessions[0], sessions[1])
        self.assertEqual(commands[0], commands[1])
        self.assertEqual(commands[0][commands[0].index("--session-id") + 1], "persistent-pi-session")
        self.assertFalse(any(part == "prompt" for command in commands for part in command))
        self.assertEqual(agent.prompt("user message"), "ok")
        self.assertEqual(prompts, ["user message"])

    def test_second_identity_timeout_is_not_retried_again(self):
        factory = mock.Mock(side_effect=IdentityProofTimeout("bounded identity timeout"))
        with self.assertRaises(IdentityProofTimeout):
            construct_agent(
                {"thread_id": "logical-thread", "group_id": None},
                {"id": "logical-thread", "pi_session": "persistent-pi-session", "name": "DM"},
                {"command": ["pi"]}, "tmux", Path("/synthetic"), None, factory,
            )
        self.assertEqual(factory.call_count, 2)

    def test_non_retryable_startup_failures_are_never_retried(self):
        for failure in (ValueError("profile invalid"), RegistrarError("artifact", 1), RuntimeError("invalid proof")):
            factory = mock.Mock(side_effect=failure)
            with self.assertRaises(type(failure)):
                construct_agent(
                    {"thread_id": "logical-thread", "group_id": None},
                    {"id": "logical-thread", "pi_session": "persistent-pi-session", "name": "DM"},
                    {"command": ["pi"]}, "tmux", Path("/synthetic"), None, factory,
                )
            self.assertEqual(factory.call_count, 1)

    def test_initial_terminal_topic_removes_route_prefix_controls_and_bounds(self):
        self.assertEqual(
            initial_terminal_topic({"text": "wally Restore relevant generated session names", "matched_prefix": "wally"}),
            "Restore relevant generated session names",
        )
        self.assertEqual(initial_terminal_topic({"text": "hello\x00world", "matched_prefix": ""}), "hello world")
        self.assertEqual(len(initial_terminal_topic({"text": "x" * 600, "matched_prefix": ""})), 512)

    def test_configured_backend_is_shared_by_groups_and_dms(self):
        self.assertEqual(worker_backend("tmux", "mapped-group"), "tmux")
        self.assertEqual(worker_backend("tmux", None), "tmux")
        self.assertEqual(worker_backend("process", "mapped-group"), "process")
        self.assertEqual(worker_backend("process", None), "process")
        with self.assertRaises(ValueError):
            worker_backend("unsafe", None)

    def test_construction_failure_does_not_kill_dispatcher_worker(self):
        handled = []
        completed = threading.Event()

        def handler(item):
            handled.append(item)
            if item == "construction-fails":
                raise RuntimeError("constructor rejected")
            completed.set()

        dispatcher = ThreadDispatcher(handler, max_concurrent=1)
        dispatcher.submit("first", "construction-fails")
        dispatcher.submit("second", "still-runs")
        self.assertTrue(completed.wait(2))
        self.assertEqual(handled, ["construction-fails", "still-runs"])

    def test_cleanup_is_idempotent_and_concurrency_safe(self):
        class Closable:
            def __init__(self):
                self.close_count = 0
                self.lock = threading.Lock()

            def close(self):
                with self.lock:
                    self.close_count += 1

        agent = Closable()
        cache = AgentCache()
        cache.agents["thread"] = agent
        results = []
        callers = [threading.Thread(
            target=lambda: results.append(discard_cached_agent(cache, "thread", agent))
        ) for _ in range(16)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join()
        self.assertEqual(results.count(True), 1)
        self.assertEqual(agent.close_count, 1)
        self.assertFalse(discard_cached_agent(cache, "thread", agent))
        self.assertFalse(discard_cached_agent(cache, "missing", None))

    def test_blocked_retirement_allows_other_keys_but_same_key_waits(self):
        close_started = threading.Event()
        permit_close = threading.Event()
        replacement_constructed = threading.Event()

        class OldAgent:
            def close(self):
                close_started.set()
                self.asserted = permit_close.wait(2)

        old = OldAgent()
        cached_other = SimpleNamespace(name="cached")
        replacement = SimpleNamespace(name="replacement")
        cache = AgentCache()
        cache.agents.update({"thread": old, "cached-other": cached_other})

        remover = threading.Thread(
            target=discard_cached_agent,
            args=(cache, "thread", old),
        )

        def replace():
            cache.get_or_create("thread", lambda: (replacement_constructed.set() or replacement))

        remover.start()
        self.assertTrue(close_started.wait(2))
        constructor = threading.Thread(target=replace)
        constructor.start()
        self.assertFalse(replacement_constructed.wait(0.1))
        self.assertIs(cache.get_or_create("cached-other", self.fail), cached_other)
        new_other = SimpleNamespace(name="new")
        self.assertIs(cache.get_or_create("new-other", lambda: new_other), new_other)

        permit_close.set()
        remover.join(2)
        constructor.join(2)
        self.assertFalse(remover.is_alive())
        self.assertFalse(constructor.is_alive())
        self.assertTrue(replacement_constructed.is_set())
        self.assertIs(cache.agents["thread"], replacement)

    def test_retirement_exception_releases_same_key_and_dispatcher(self):
        class Broken:
            def close(self):
                raise RuntimeError("close failed")

        cache = AgentCache()
        old = Broken()
        cache.agents["thread"] = old
        with self.assertRaisesRegex(RuntimeError, "close failed"):
            cache.discard("thread", old)
        replacement = SimpleNamespace()
        self.assertIs(cache.get_or_create("thread", lambda: replacement), replacement)
        handled = threading.Event()
        dispatcher = ThreadDispatcher(lambda item: handled.set(), max_concurrent=1)
        dispatcher.submit("unrelated", {})
        self.assertTrue(handled.wait(2))

    def test_retirement_timeout_releases_same_key(self):
        class TimedOut:
            def close(self):
                raise subprocess.TimeoutExpired(["tmux", "kill-session"], 0.25)

        cache = AgentCache()
        old = TimedOut()
        cache.agents["thread"] = old
        with self.assertRaises(subprocess.TimeoutExpired):
            cache.discard("thread", old)
        replacement = SimpleNamespace()
        self.assertIs(cache.get_or_create("thread", lambda: replacement), replacement)

    def test_blocking_close_deadline_releases_retirement_and_dispatcher_capacity(self):
        gate = threading.Event()
        class Blocked:
            def close(self): gate.wait(1)
        cache = AgentCache(close_timeout=0.02, rendezvous_timeout=0.1)
        old = Blocked()
        cache.agents["blocked"] = old
        completed = threading.Event()
        dispatcher = ThreadDispatcher(
            lambda item: cache.discard("blocked", old) if item == "blocked" else completed.set(),
            max_concurrent=1,
        )
        dispatcher.submit("blocked", "blocked")
        dispatcher.submit("unrelated", "unrelated")
        self.assertTrue(completed.wait(1))
        replacement = SimpleNamespace()
        self.assertIs(cache.get_or_create("blocked", lambda: replacement), replacement)
        gate.set()

    def test_same_key_tmux_replacement_has_generation_unique_exact_identity(self):
        sessions = []
        def factory(*args, **kwargs):
            sessions.append(args[2])
            return SimpleNamespace()
        arguments = (
            {"thread_id": "same", "group_id": None},
            {"id": "same", "pi_session": "durable", "name": "DM"},
            {"command": ["pi"]}, "tmux", Path("/synthetic"), None, factory,
        )
        construct_agent(*arguments)
        construct_agent(*arguments)
        self.assertNotEqual(sessions[0], sessions[1])
        old_kill = ["tmux", "kill-session", "-t", f"={sessions[0]}"]
        self.assertNotEqual(old_kill[-1], f"={sessions[1]}")

    def test_mapped_group_construction_uses_profile_paths_and_proof_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault_dir = Path(temporary) / "groups" / "vault-one"
            sessions = vault_dir / "runtime" / "sessions"
            sessions.mkdir(parents=True)
            skill = vault_dir / "skills" / "safe-skill" / "SKILL.md"

            class FakeFs:
                def dirfd(self, parts):
                    self.parts = parts
                    return os.open(sessions, os.O_RDONLY | os.O_DIRECTORY)

            class FakeVaults:
                fs = FakeFs()
                labels = {"vault-one": "Experimental Research Agents"}

                def skill_paths(self, group_id):
                    self.skill_group = group_id
                    return [skill]

                def extension_paths(self, group_id):
                    self.extension_group = group_id
                    return []

                def group_vault_id(self, group_id):
                    self.vault_group = group_id
                    return "vault-one"

                def vault_dir(self, vault_id):
                    self.selected_vault = vault_id
                    return vault_dir

            calls = []

            def factory(*args, **kwargs):
                calls.append((args, kwargs))
                return SimpleNamespace(kind="process")

            vaults = FakeVaults()
            proof = "/root/.pi/agent/bin/pi"
            agent = construct_agent(
                {"thread_id": "thread-one", "group_id": "mapped-group"},
                {"id": "thread-one", "pi_session": "session-one", "name": "Scoped group"},
                {"command": [proof, "--mode", "rpc"], "working_directory": "/unscoped"},
                "process", Path(temporary) / "tmux-runtime", vaults, factory,
            )

        self.assertEqual(agent.kind, "process")
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        command, cwd = args
        self.assertEqual(command[:3], [proof, "--mode", "rpc"])
        self.assertEqual(cwd, str(vault_dir))
        self.assertNotIn("/usr/bin/pi", command)
        self.assertNotIn("--no-approve", command)
        self.assertIn(str(skill), command)
        self.assertEqual(command[command.index("--session-dir") + 1], str(sessions))
        self.assertEqual(command[command.index("--session-id") + 1], "session-one")
        self.assertNotIn("PI_SIGNAL_VAULT_ID", kwargs["child_env"])
        self.assertEqual(vaults.fs.parts, ("groups", "vault-one", "runtime", "sessions"))

    def test_dm_construction_retains_configured_tmux_backend(self):
        calls = []

        def factory(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(kind="tmux")

        runtime_dir = Path("/synthetic/tmux-runtime")
        agent = construct_agent(
            {"thread_id": "dm-thread", "group_id": None},
            {"id": "dm-thread", "pi_session": "dm-session", "name": "DM"},
            {"command": ["pi", "--mode", "rpc"], "working_directory": "/dm-work"},
            "tmux", runtime_dir, None, factory,
        )
        self.assertEqual(agent.kind, "tmux")
        args, kwargs = calls[0]
        self.assertEqual(args[0][:3], ["pi", "--mode", "rpc"])
        self.assertEqual(args[1], "/dm-work")
        self.assertEqual(args[3], runtime_dir)
        self.assertTrue(args[2].startswith("pi-signal-dm-thread-"))
        self.assertEqual(kwargs["startup_timeout"], 10.0)
        self.assertEqual(kwargs["registrar"], "/opt/shawty/current/bin/register-terminal.js")
        self.assertEqual(kwargs["registry_root"], Path("/var/lib/shawty/terminals"))
        self.assertEqual(kwargs["terminal_group"], "Direct")
        self.assertIsNone(kwargs["initial_topic"])
        self.assertNotIn("PI_SIGNAL_VAULT_ID", kwargs["child_env"])

    def test_tmux_exit_125_retries_without_socket_or_session_deletion(self):
        outcomes = [
            subprocess.CompletedProcess([], 125, stderr="stale socket"),
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 0, stderr=""),
        ]
        with mock.patch("signal_pi_bridge.subprocess.run", side_effect=outcomes) as run:
            launch_tmux_session(["tmux", "new-session", "-s", "dm"], "dm")
        self.assertEqual(run.call_count, 3)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1], ["tmux", "has-session", "-t", "=dm"])
        self.assertEqual(commands[0], commands[2])
        self.assertFalse(any("kill-session" in command or "rm" in command for command in commands))
        self.assertTrue(all(call.kwargs["timeout"] == 10.0 for call in run.call_args_list))

    def test_tmux_timeout_is_validated_and_safely_derived(self):
        self.assertEqual(tmux_control_timeout({}), 10.0)
        self.assertEqual(tmux_control_timeout({"subagent_startup_timeout_seconds": 7}), 7.0)
        self.assertEqual(tmux_control_timeout({
            "subagent_startup_timeout_seconds": 7,
            "tmux_control_timeout_seconds": 2.5,
        }), 2.5)
        for invalid in (True, 0, -1, 61, "forever"):
            with self.assertRaises(ValueError):
                tmux_control_timeout({"tmux_control_timeout_seconds": invalid})

    def test_tmux_control_timeout_fails_without_destructive_fallback(self):
        launch = ["tmux", "new-session", "-s", "dm"]
        with mock.patch(
            "signal_pi_bridge.subprocess.run",
            side_effect=subprocess.TimeoutExpired(launch, 0.25),
        ) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                launch_tmux_session(launch, "dm", 0.25)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], launch)
        self.assertEqual(run.call_args.kwargs["timeout"], 0.25)

    def test_tmux_fifo_rendezvous_is_bounded_without_live_tmux(self):
        no_reader = OSError(6, "no reader")
        with mock.patch("signal_pi_bridge.os.open", side_effect=[91, no_reader]), \
                mock.patch("signal_pi_bridge.os.close"), \
                mock.patch("signal_pi_bridge.time.monotonic", side_effect=[0.0, 0.2]), \
                mock.patch("signal_pi_bridge.time.sleep"):
            with self.assertRaisesRegex(TimeoutError, "stdin rendezvous"):
                open_tmux_rpc_fifos(Path("stdin.fifo"), Path("stdout.fifo"), 0.1)

    def test_stdout_fifo_keepalive_prevents_pre_writer_eof(self):
        with tempfile.TemporaryDirectory() as temporary:
            fifo = Path(temporary) / "stdout.fifo"
            os.mkfifo(fifo, 0o600)
            reader, keepalive = PiRpc._open_fifo_reader_with_keepalive(fifo)
            outcome = []
            finished = threading.Event()

            def read_line():
                outcome.append(reader.readline())
                finished.set()

            worker = threading.Thread(target=read_line)
            worker.start()
            self.assertFalse(finished.wait(0.05), "reader observed EOF before the relay writer opened")
            os.write(keepalive, b"identity-response\n")
            os.close(keepalive)
            self.assertTrue(finished.wait(1))
            worker.join()
            reader.close()
            self.assertEqual(outcome, ["identity-response\n"])

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required")
    def test_private_tmux_parallel_rpc_launches_do_not_observe_transient_stdout_eof(self):
        fake_rpc = """import json,sys
for line in sys.stdin:
 event=json.loads(line)
 if event.get('type')=='get_state':
  print(json.dumps({'type':'response','id':event['id'],'success':True,'data':{'sessionId':'isolated-test-session'}}),flush=True)
 elif event.get('type')=='prompt':
  print(json.dumps({'type':'response','id':event['id'],'success':True}),flush=True)
  print(json.dumps({'type':'message_end','message':{'role':'assistant','content':'ok'}}),flush=True)
  print(json.dumps({'type':'agent_settled'}),flush=True)
"""
        with PrivateTmuxTestIsolation() as isolation, \
                mock.patch.dict(os.environ, {"TMUX_TMPDIR": str(isolation.root)}):
            temporary = str(isolation.root)
            runtime = isolation.root / "runtime"
            registry, registrar = self._isolated_registrar(isolation.root)
            agents = []
            results = []
            errors = []

            def launch(index):
                try:
                    agent = PiRpc(
                        [sys.executable, "-u", "-c", fake_rpc], temporary,
                        f"offline-dm-{index}", runtime, startup_timeout=3,
                        registrar=str(registrar), registry_root=registry,
                    )
                    agents.append(agent)
                    results.append(agent.prompt("hello"))
                except BaseException as exc:
                    errors.append(exc)

            workers = [threading.Thread(target=launch, args=(index,)) for index in range(24)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
            try:
                self.assertFalse(any(worker.is_alive() for worker in workers))
                self.assertEqual(errors, [])
                self.assertEqual(results, ["ok"] * len(workers))
            finally:
                for agent in agents:
                    agent.close()

            for _ in range(8):
                agent = PiRpc(
                    [sys.executable, "-u", "-c", fake_rpc], temporary,
                    "offline-reuse", runtime, startup_timeout=3,
                    registrar=str(registrar), registry_root=registry,
                )
                try:
                    self.assertEqual(agent.prompt("reuse"), "ok")
                finally:
                    agent.close()

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required")
    def test_durable_controller_owns_and_retires_one_real_tmux_session(self):
        fake_rpc = """import json,sys
for line in sys.stdin:
 event=json.loads(line)
 if event.get('type')=='get_state':
  print(json.dumps({'type':'response','id':event['id'],'success':True,'data':{'sessionId':'persistent-session-123'}}),flush=True)
 elif event.get('type')=='prompt':
  print(json.dumps({'type':'response','id':event['id'],'success':True}),flush=True)
  print(json.dumps({'type':'message_end','message':{'role':'assistant','content':'ready:'+event['message']}}),flush=True)
  print(json.dumps({'type':'agent_settled'}),flush=True)
"""
        with PrivateTmuxTestIsolation(prefix="bridge-private-lifecycle-") as isolation:
            registry, registrar = self._isolated_registrar(isolation.root)
            runtime = isolation.root / "runtime"
            state = isolation.root / "lifecycle"
            controller = DurableLifecycleController(state, runtime, registry, lease_seconds=30)
            agent = PiRpc(
                [sys.executable, "-u", "-c", fake_rpc], str(isolation.root),
                "durable", runtime, registrar=str(registrar), registry_root=registry,
                startup_timeout=5, identity_timeout=5, terminal_group="Direct",
                lifecycle_controller=controller, lifecycle_owner_key="thread:harness",
                logical_session_id="persistent-session-123",
            )
            generation = agent.lifecycle_generation_id
            self.assertIsNotNone(generation)
            record = controller.inspect("thread:harness")
            self.assertEqual(record["state"], "active")
            self.assertEqual(record["generation_id"], generation)
            self.assertEqual(agent.prompt("hello"), "ready:hello")
            agent.close()
            self.assertEqual(controller.inspect("thread:harness")["state"], "retired")
            self.assertTrue((controller.archive / generation / "stderr.log").exists())
            self.assertEqual(list(registry.glob("*.json")), [])

    def test_private_tmux_reports_fake_rpc_exit_status(self):
        fake_rpc = """import json,sys
for line in sys.stdin:
 event=json.loads(line)
 if event.get('type')=='get_state':
  print(json.dumps({'type':'response','id':event['id'],'success':True,'data':{'sessionId':'exit-test-session'}}),flush=True)
 elif event.get('type')=='prompt':
  raise SystemExit(17)
"""
        with PrivateTmuxTestIsolation() as isolation, \
                mock.patch.dict(os.environ, {"TMUX_TMPDIR": str(isolation.root)}):
            temporary = str(isolation.root)
            registry, registrar = self._isolated_registrar(isolation.root)
            agent = PiRpc(
                [sys.executable, "-u", "-c", fake_rpc], temporary,
                "offline-exit", Path(temporary) / "runtime", startup_timeout=3,
                registrar=str(registrar), registry_root=registry,
            )
            try:
                with self.assertRaisesRegex(EOFError, "status 17"):
                    agent.prompt("hello")
            finally:
                agent.close()

    def test_tmux_exit_125_accepts_session_created_on_first_attempt(self):
        outcomes = [subprocess.CompletedProcess([], 125), subprocess.CompletedProcess([], 0)]
        with mock.patch("signal_pi_bridge.subprocess.run", side_effect=outcomes) as run:
            launch_tmux_session(["tmux", "new-session", "-s", "dm"], "dm")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0], ["tmux", "has-session", "-t", "=dm"])

    def test_tmux_exit_125_retries_only_once(self):
        outcomes = [
            subprocess.CompletedProcess([], 125, stderr="first failure"),
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 125, stderr="second failure"),
        ]
        launch = ["tmux", "new-session", "-s", "dm"]
        with mock.patch("signal_pi_bridge.subprocess.run", side_effect=outcomes) as run:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                launch_tmux_session(launch, "dm")
        self.assertEqual(run.call_count, 3)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            launch,
            ["tmux", "has-session", "-t", "=dm"],
            launch,
        ])
        self.assertEqual(raised.exception.returncode, 125)
        self.assertEqual(raised.exception.stderr, "second failure")

    def test_tmux_startup_uses_private_socket_and_invokes_registrar(self):
        stdin = mock.Mock()
        stdout = mock.Mock()
        stdout.readline.return_value = json.dumps({
            "type": "response", "id": "shawty-session-identity", "success": True,
            "data": {"sessionId": "durable-session-id"},
        }) + "\n"
        outcomes = [subprocess.CompletedProcess([], 0, "A" * 32 + "\n", "")]
        agent = mock.Mock()
        agent.registration_path = None
        agent.tmux_session_id = "$7"
        agent._absolute_root.side_effect = lambda path, unused: path
        agent._tmux.side_effect = lambda unused_socket, *args, **unused_kwargs: subprocess.CompletedProcess(
            [], 0, "$7\n" if "new-session" in args else "$7\tdm\t0\t0\n", "",
        )
        agent._open_fifo.return_value = stdin
        agent._open_fifo_reader_with_keepalive.return_value = (stdout, os.open(os.devnull, os.O_WRONLY))
        agent._read_pi_session_identity.return_value = "durable-session-id"
        with tempfile.TemporaryDirectory() as runtime_dir, \
                mock.patch("signal_pi_bridge.os.mkfifo"), \
                mock.patch("signal_pi_bridge.Path.lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
                mock.patch("signal_pi_bridge.subprocess.run", side_effect=outcomes) as run:
            PiRpc._start_tmux_rpc(
                agent, ["pi", "--rpc"], "/work", "dm", Path(runtime_dir),
                "Signal Pi worker", "/opt/shawty/current/bin/register-terminal.js",
                Path(runtime_dir), 10, "Pi — Research",
                initial_topic="Restore relevant generated session names",
            )
        launch_call = next(call for call in agent._tmux.call_args_list if "new-session" in call.args)
        socket_path = str(launch_call.args[0])
        self.assertEqual(launch_call.args[1:6], ("new-session", "-d", "-P", "-F", "#{session_id}"))
        self.assertIn("/tmux.sock", socket_path)
        register = run.call_args.args[0]
        self.assertEqual(register[0], "/opt/shawty/current/bin/register-terminal.js")
        self.assertEqual(register[register.index("--socket") + 1], socket_path)
        self.assertEqual(register[register.index("--target") + 1], "dm:0.0")
        self.assertEqual(register[register.index("--pi-session-id") + 1], "durable-session-id")
        self.assertEqual(register[register.index("--group") + 1], "Pi — Research")
        self.assertEqual(register[register.index("--topic") + 1], "Restore relevant generated session names")
        captured = agent._capture_registration_artifacts.call_args
        self.assertEqual(captured.args[1], "A" * 32)
        self.assertEqual(captured.args[2]["socketPath"], socket_path)
        self.assertEqual(captured.args[2]["group"], "Research")
        stdin.write.assert_called_once()

    def test_identity_reader_drains_buffered_startup_event_before_response(self):
        stream = io.StringIO("".join(json.dumps(event) + "\n" for event in [
            {"type": "model_select"},
            {"type": "response", "id": "identity", "success": True,
             "data": {"sessionId": "persistent-session-123"}},
        ]))
        self.assertEqual(
            PiRpc._read_pi_session_identity(stream, "identity", time.monotonic() + 1),
            "persistent-session-123",
        )

    def test_identity_reader_rejects_uncorrelated_or_invalid_identity(self):
        stream = io.StringIO("".join(json.dumps(event) + "\n" for event in [
            {"type": "response", "id": "other", "success": True,
             "data": {"sessionId": "persistent-session-123"}},
            {"type": "response", "id": "identity", "success": True,
             "data": {"sessionId": "bad/value"}},
        ]))
        self.assertIsNone(PiRpc._read_pi_session_identity(stream, "identity", time.monotonic() + 1))

    def test_status_one_registrar_retry_reproves_exact_private_pane(self):
        stdin = mock.Mock()
        stdout = io.StringIO(json.dumps({
            "type": "response", "id": "shawty-session-identity", "success": True,
            "data": {"sessionId": "durable-session-id"},
        }) + "\n")
        failure = subprocess.CalledProcessError(1, ["registrar"])
        outcomes = [
            failure,
            subprocess.CompletedProcess([], 0, "A" * 32 + "\n", ""),
        ]
        agent = mock.Mock()
        agent.registration_path = None
        agent.tmux_session_id = "$7"
        agent._absolute_root.side_effect = lambda path, unused: path
        agent._tmux.side_effect = lambda unused_socket, *args, **unused_kwargs: subprocess.CompletedProcess(
            [], 0, "$7\n" if "new-session" in args else "$7\tdm\t0\t0\n", "",
        )
        agent._open_fifo.return_value = stdin
        agent._open_fifo_reader_with_keepalive.return_value = (stdout, os.open(os.devnull, os.O_WRONLY))
        with tempfile.TemporaryDirectory() as runtime_dir, \
                mock.patch("signal_pi_bridge.os.mkfifo"), \
                mock.patch("signal_pi_bridge.Path.lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
                mock.patch("signal_pi_bridge.subprocess.run", side_effect=outcomes) as run:
            PiRpc._start_tmux_rpc(
                agent, ["pi", "--rpc"], "/work", "dm", Path(runtime_dir),
                "Signal Pi worker", "/opt/shawty/current/bin/register-terminal.js",
                Path(runtime_dir), 10, "System/Automation",
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], run.call_args_list[1].args[0])
        self.assertIn("list-panes", agent._tmux.call_args.args)

    def test_tmux_close_uses_exact_session_target(self):
        agent = PiRpc.__new__(PiRpc)
        agent._close_lock = threading.Lock()
        agent._closed = False
        agent._registry_fd = None
        agent._registry_locked = False
        agent._owned_paths = {}
        agent.run_dir = Path("/private/runtime")
        agent.tmux_session = "dm"
        agent.tmux_session_id = "$7"
        agent.tmux_session_name = "dm"
        agent.tmux_socket = Path("/private/runtime/tmux.sock")
        agent.registration_path = None
        agent.registration_alias_path = None
        agent.stdin = mock.Mock()
        agent.stdout = mock.Mock()
        agent._is_owned_path = mock.Mock(return_value=True)
        agent._list_single_session = mock.Mock(return_value=["$7\tdm"])
        agent._tmux = mock.Mock()
        agent._unlink_owned_socket = mock.Mock(return_value=True)
        with mock.patch("signal_pi_bridge.Path.exists", return_value=True):
            agent.close()
        agent._tmux.assert_called_once_with(
            agent.tmux_socket, "kill-session", "-t", "$7",
            check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5,
        )
        agent.stdin.close.assert_called_once_with()
        agent.stdout.close.assert_called_once_with()


class QuotedReplyTests(unittest.TestCase):
    def test_quoted_audio_reply_targets_exact_data_message_not_quote_or_envelope(self):
        event = {
            "method": "receive",
            "params": {"envelope": {
                "sourceNumber": "+15555550123",
                # Model a transport/session timestamp belonging to a different
                # delivery.  It must not become the outbound quote target.
                "timestamp": 333,
                "dataMessage": {
                    # This is the exact inbound voice message being answered.
                    "timestamp": 222,
                    "quote": {
                        # The voice message itself quotes an earlier assistant
                        # text; that context must not become our reply target.
                        "id": 111,
                        "authorNumber": "+15555550999",
                        "text": "Earlier assistant text",
                    },
                    "attachments": [{
                        "attachmentId": "voice-message",
                        "contentType": "audio/aac",
                    }],
                },
            }},
        }

        message = extract_message(event)
        self.assertEqual(message["timestamp"], 222)
        self.assertEqual(message["quote"]["target_timestamp"], 111)
        self.assertEqual(message["source"], "+15555550123")

        rpc = mock.Mock()
        send_signal(rpc, message, "Transcribed response")
        params = rpc.send.call_args.args[0]["params"]
        self.assertEqual(params["quoteTimestamp"], 222)
        self.assertEqual(params["quoteAuthor"], "+15555550123")
        self.assertEqual(params["quoteMessage"], "")
        self.assertNotEqual(params["quoteTimestamp"], 111)
        self.assertNotEqual(params["quoteTimestamp"], 333)

    def test_extracts_bounded_quote_context(self):
        event = {
            "method": "receive",
            "params": {"envelope": {
                "sourceNumber": "+15555550123",
                "timestamp": 222,
                "dataMessage": {
                    "message": "Yes, do that",
                    "quote": {
                        "id": "111",
                        "authorNumber": "+15555550999",
                        "text": "The message being answered",
                        "attachments": [{"contentType": "image/jpeg"}],
                    },
                },
            }},
        }
        message = extract_message(event)
        self.assertEqual(message["text"], "Yes, do that")
        self.assertEqual(message["timestamp"], 222)
        self.assertEqual(message["quote"], {
            "target_timestamp": 111,
            "author": "+15555550999",
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

    def test_outbound_reply_quotes_exact_inbound_signal_identity(self):
        rpc = mock.Mock()
        send_signal(
            rpc,
            {"source": "owner", "group_id": None, "timestamp": 222, "text": "Exact inbound text"},
            "Pi reply",
        )
        request = rpc.send.call_args.args[0]
        self.assertEqual(request["params"]["quoteTimestamp"], 222)
        self.assertEqual(request["params"]["quoteAuthor"], "owner")
        self.assertEqual(request["params"]["quoteMessage"], "Exact inbound text")
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
                "sourceNumber": "+15555550123",
                "dataMessage": {"reaction": {
                    "emoji": "👍", "targetTimestamp": 123456789, "isRemove": False,
                }},
            }},
        }
        self.assertEqual(extract_reaction(event), {
            "source": "+15555550123",
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
                "sourceNumber": "+15555550123",
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

    def test_acknowledged_standalone_outbound_creates_dormant_reply_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ThreadStore(Path(temporary) / "threads.sqlite3")
            ledger = OutboundMessageLedger(10)
            correlator = SendResponseCorrelator(ledger, store)
            message = {"text": "standalone alert", "source": "owner", "group_id": None}
            correlator.register("standalone-1", message)

            self.assertTrue(correlator.handle_response({
                "id": "standalone-1", "result": {"timestamp": 456},
            }))

            routed = store.resolve_route("dm:owner", 456, "bridge-account")
            self.assertIsNotNone(routed)
            self.assertEqual(routed["status"], "idle")
            self.assertEqual(len(store.list_threads("dm:owner")), 1)
            self.assertEqual(ledger.get(456)["thread_id"], routed["id"])
            store.close()

    def test_unacknowledged_standalone_outbound_does_not_create_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ThreadStore(Path(temporary) / "threads.sqlite3")
            correlator = SendResponseCorrelator(OutboundMessageLedger(10), store)
            correlator.register("standalone-1", {
                "text": "failed alert", "source": "owner", "group_id": None,
            })

            self.assertTrue(correlator.handle_response({
                "id": "standalone-1", "error": {"message": "send failed"},
            }))

            self.assertEqual(store.list_threads("dm:owner"), [])
            store.close()

    def test_reacted_to_route_requires_exact_timestamp_and_conversation_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ThreadStore(Path(directory) / "threads.sqlite3")
            try:
                thread = store.create_thread("dm:owner")
                ledger = OutboundMessageLedger()
                correlator = SendResponseCorrelator(ledger, store)
                correlator.register("request-1", {
                    "text": "Proposal 1: deploy production.",
                    "source": "owner",
                    "group_id": None,
                    "thread_id": thread["id"],
                    "author": "pi",
                })
                self.assertTrue(correlator.handle_response({
                    "id": "request-1", "result": {"timestamp": 123},
                }))
                self.assertEqual(store.resolve_route("dm:owner", 123, "owner")["id"], thread["id"])
                self.assertIsNone(store.resolve_route("dm:owner", 124, "owner"))
                self.assertIsNone(store.resolve_route("dm:other", 123, "owner"))
                self.assertIsNotNone(ledger.get(123))
                self.assertIsNone(ledger.get(124))
            finally:
                store.close()
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

    def test_exact_thumbsup_authorizes_actionable_proposal_1_and_only_its_scope(self):
        reaction = {
            "source": "owner", "group_id": None, "emoji": "👍",
            "target_timestamp": 999, "remove": False,
        }
        proposal = (
            "Proposal 1\n"
            "Delete the obsolete production records, deploy the replacement, and send the exact draft email."
        )
        prompt = format_reaction_prompt(reaction, {"text": proposal})
        self.assertIn("Target Signal timestamp: 999", prompt)
        self.assertIn(f'"""\n{proposal}\n"""', prompt)
        self.assertIn("authorizes all and only", prompt)
        self.assertIn("the actions explicitly described in that exact message", prompt)
        self.assertIn("including explicitly described high-risk actions", prompt)
        self.assertIn("no separate approval is needed solely", prompt)
        self.assertIn("Do not infer or expand scope from any other message", prompt)
    def test_exact_thumbsup_on_non_actionable_status_remains_conversational(self):
        reaction = {
            "source": "owner", "group_id": None, "emoji": "👍",
            "target_timestamp": 1000, "remove": False,
        }
        prompt = format_reaction_prompt(
            reaction,
            {"text": "Status: Proposal 1 tests passed. No action is requested."},
        )
        self.assertIn("non-actionable status update, summary, or ordinary conversation", prompt)
        self.assertIn("conversational feedback only and authorizes nothing", prompt)
    def test_non_thumbsup_reaction_with_exact_text_is_conversational_only(self):
        reaction = {
            "source": "owner", "group_id": None, "emoji": "✅",
            "target_timestamp": 1001, "remove": False,
        }
        prompt = format_reaction_prompt(reaction, {"text": "Proposal 1: deploy production."})
        self.assertIn("Exact reacted-to outbound Pi message", prompt)
        self.assertIn("Conversational reaction only", prompt)
        self.assertIn("does not authorize any action", prompt)
    def test_added_thumbsup_without_exact_ledger_context_never_authorizes(self):
        reaction = {
            "source": "owner", "group_id": None, "emoji": "👍",
            "target_timestamp": 8675309, "remove": False,
        }
        prompt = format_reaction_prompt(reaction, None)
        self.assertIn("text is unavailable", prompt)
        self.assertIn("does not authorize any action", prompt)
        self.assertNotIn("Exact-message-bound authorization rule", prompt)
    def test_removed_thumbsup_never_authorizes(self):
        reaction = {
            "source": "owner", "group_id": None, "emoji": "👍",
            "target_timestamp": 8675309, "remove": True,
        }
        prompt = format_reaction_prompt(reaction, {"text": "Proposal 1: deploy production."})
        self.assertIn("removed their reaction", prompt)
        self.assertIn("Target Signal timestamp: 8675309", prompt)
        self.assertIn("does not authorize any action", prompt)
        self.assertNotIn("Exact-message-bound authorization rule", prompt)


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
        self.assertIn("Conversational reaction only", item["text"])

    def test_work_item_does_not_disclose_text_across_conversations(self):
        ledger = OutboundMessageLedger()
        ledger.put(44, {"text": "owner secret", "source": "owner", "group_id": None})
        reaction = {
            "source": "other", "group_id": None, "emoji": "👍",
            "target_timestamp": 44, "remove": False,
        }
        item = reaction_work_item(reaction, ledger)
        self.assertNotIn("owner secret", item["text"])
        self.assertIn("text is unavailable", item["text"])
        self.assertIn("does not authorize any action", item["text"])
        self.assertNotIn("Exact-message-bound authorization rule", item["text"])

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

    def test_per_group_sender_override_is_scoped(self):
        scoped = {"byoq": {"owner", "alex"}}
        self.assertTrue(group_sender_allowed("byoq", "alex", {"owner"}, scoped))
        self.assertFalse(group_sender_allowed("other", "alex", {"owner"}, scoped))
        self.assertTrue(reaction_routing_allowed(
            {"source": "alex", "group_id": "byoq"}, {"owner"}, True,
            {"byoq", "other"}, {"owner"}, scoped,
        ))
        self.assertFalse(reaction_routing_allowed(
            {"source": "alex", "group_id": "other"}, {"owner"}, True,
            {"byoq", "other"}, {"owner"}, scoped,
        ))


class ThreadStoreStartupTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "thread-state.json"
        self.legacy = {
            "version": 1,
            "threads": [
                {"id": "thread-one", "conversation": "group:fixture", "created_at": 100,
                 "updated_at": 200, "name": "Fixture", "cancelled": False},
                {"id": "thread-two", "conversation": "dm:fixture", "created_at": 300,
                 "updated_at": 400, "name": None, "cancelled": True},
            ],
            "routes": [["group:fixture", 201, "thread-one"], ["dm:fixture", 401, "thread-two"]],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_legacy(self):
        self.path.write_text(json.dumps(self.legacy, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(self.path, 0o600)

    def test_exact_wal_inside_transaction_failure_is_avoided(self):
        control = sqlite3.connect(Path(self.tempdir.name) / "control.sqlite3", isolation_level=None)
        control.execute("CREATE TABLE existing_state (id INTEGER PRIMARY KEY)")
        control.execute("BEGIN IMMEDIATE")
        with self.assertRaisesRegex(sqlite3.OperationalError, "within a transaction"):
            control.execute("PRAGMA journal_mode=WAL")
        control.rollback()
        control.close()

        store = ThreadStore(Path(self.tempdir.name) / "startup.sqlite3")
        self.assertEqual(store.db.execute("PRAGMA journal_mode").fetchone(), ("wal",))
        store.close()

    def test_operational_startup_error_is_not_mislabeled_as_corruption(self):
        self.path.write_bytes(b"SQLite format 3\x00")
        with mock.patch("signal_pi_bridge.sqlite3.connect") as connect:
            connect.return_value.execute.side_effect = sqlite3.OperationalError("wal transaction failure")
            with self.assertRaisesRegex(sqlite3.OperationalError, "wal transaction failure"):
                ThreadStore(self.path)

    def test_json_migration_preserves_source_hash_and_logical_records(self):
        self._write_legacy()
        source_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        store = ThreadStore(self.path)
        self.assertEqual(store.get_thread("thread-one")["pi_session"], "thread-one")
        self.assertEqual(store.get_thread("thread-two")["status"], "archived")
        self.assertEqual(store.resolve_route("group:fixture", 201, "quoted-author")["id"], "thread-one")
        store.close()
        backup = self.path.with_name(f"{self.path.name}.pre-sqlite")
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(self.path.read_bytes()[:16], b"SQLite format 3\x00")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_exact_live_shape_preserves_counts_integrity_and_sessions_on_repeat_start(self):
        self.legacy["threads"] = [
            {"id": f"thread-{index}", "conversation": f"dm:fixture-{index % 7}",
             "created_at": index + 1, "updated_at": index + 1000,
             "name": None, "cancelled": False}
            for index in range(219)
        ]
        self.legacy["routes"] = [
            [f"dm:fixture-{(index % 219) % 7}", index + 5000, f"thread-{index % 219}"]
            for index in range(965)
        ]
        self._write_legacy()
        first = ThreadStore(self.path)
        self.assertEqual(first.db.execute("SELECT count(*) FROM threads").fetchone(), (219,))
        self.assertEqual(first.db.execute("SELECT count(*) FROM routes").fetchone(), (965,))
        self.assertEqual(first.db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        sessions = dict(first.db.execute("SELECT id,pi_session FROM threads"))
        self.assertEqual(sessions, {f"thread-{index}": f"thread-{index}" for index in range(219)})
        first.close()
        second = ThreadStore(self.path)
        self.assertEqual(dict(second.db.execute("SELECT id,pi_session FROM threads")), sessions)
        self.assertEqual(second.db.execute("PRAGMA quick_check").fetchone(), ("ok",))
        second.close()

    def test_post_replace_startup_failure_restores_original_json_path(self):
        self._write_legacy()
        original = self.path.read_bytes()
        with mock.patch.object(ThreadStore, "_initialize_database",
                               side_effect=sqlite3.OperationalError("injected post-replace failure")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "post-replace"):
                ThreadStore(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.path.with_name(self.path.name + "-wal").exists())

    def test_post_replace_wrapped_database_failure_restores_original_json_path(self):
        self._write_legacy()
        original = self.path.read_bytes()
        wrapped = RuntimeError("logical thread state is missing or corrupt")
        wrapped.__cause__ = sqlite3.DatabaseError("injected integrity failure")
        with mock.patch.object(ThreadStore, "_initialize_database", side_effect=wrapped):
            with self.assertRaisesRegex(RuntimeError, "missing or corrupt"):
                ThreadStore(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.path.with_name(self.path.name + "-wal").exists())

    def test_migration_transaction_failure_preserves_original_json_path(self):
        self._write_legacy()
        original = self.path.read_bytes()
        with mock.patch.object(ThreadStore, "_create_schema", side_effect=sqlite3.OperationalError("injected")):
            with self.assertRaises(sqlite3.OperationalError):
                ThreadStore(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_already_wal_sqlite_restart_is_idempotent(self):
        store = ThreadStore(self.path)
        thread = store.create_thread("dm:fixture")
        store.add_route("dm:fixture", 501, thread["id"], None, "outbound")
        store.close()
        before = self.path.read_bytes()
        restarted = ThreadStore(self.path)
        self.assertEqual(restarted.get_thread(thread["id"])["pi_session"], thread["pi_session"])
        self.assertEqual(restarted.resolve_route("dm:fixture", 501, "author")["id"], thread["id"])
        restarted.close()
        self.assertEqual(self.path.read_bytes(), before)

    def test_harness_switch_preserves_distinct_sessions_across_restart(self):
        store = ThreadStore(self.path)
        thread = store.create_thread("group:fixture")

        # Reproduce historical state: sandbox first claimed the legacy session
        # while it was the effective default, before the group default changed.
        sandbox_session = store.harness_session(thread, "sandbox-pi", "sandbox-pi")
        self.assertEqual(sandbox_session, thread["pi_session"])
        # The durable selection sequence is host -> sandbox -> host.
        host_session = store.harness_session(thread, "host-pi", "host-pi")
        self.assertNotEqual(host_session, sandbox_session)
        self.assertEqual(store.harness_session(thread, "sandbox-pi", "host-pi"), sandbox_session)
        self.assertEqual(store.harness_session(thread, "host-pi", "host-pi"), host_session)
        store.close()

        restarted = ThreadStore(self.path)
        persisted_thread = restarted.get_thread(thread["id"])
        self.assertEqual(
            restarted.harness_session(persisted_thread, "host-pi", "host-pi"), host_session
        )
        self.assertEqual(
            restarted.harness_session(persisted_thread, "sandbox-pi", "host-pi"), sandbox_session
        )
        self.assertEqual(
            restarted.db.execute(
                "SELECT count(DISTINCT pi_session) FROM thread_harness_sessions WHERE thread_id=?",
                (thread["id"],),
            ).fetchone(),
            (2,),
        )
        restarted.close()

    def test_stale_interrupted_migration_file_cannot_replace_json(self):
        self._write_legacy()
        stale = self.path.parent / f".{self.path.name}.migrate-interrupted"
        stale.write_bytes(b"partial sqlite bytes")
        store = ThreadStore(self.path)
        self.assertEqual(store.get_thread("thread-one")["pi_session"], "thread-one")
        store.close()
        self.assertEqual(stale.read_bytes(), b"partial sqlite bytes")

    def test_partial_sqlite_schema_is_completed_without_record_loss(self):
        db = sqlite3.connect(self.path)
        db.execute("""CREATE TABLE threads (
            id TEXT PRIMARY KEY, conversation TEXT NOT NULL, pi_session TEXT NOT NULL UNIQUE,
            name TEXT, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
        row = ("existing", "dm:fixture", "durable-session", None, "idle", 10, 20)
        db.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?)", row)
        db.commit()
        db.close()
        store = ThreadStore(self.path)
        self.assertEqual(store.get_thread("existing")["pi_session"], "durable-session")
        self.assertEqual(store.db.execute("SELECT count(*) FROM routes").fetchone(), (0,))
        store.close()

    def test_corrupt_state_fails_closed_without_changing_bytes(self):
        corrupt = b'{"version":1,"threads":"invalid","routes":[]}'
        self.path.write_bytes(corrupt)
        with self.assertRaisesRegex(RuntimeError, "missing or corrupt"):
            ThreadStore(self.path)
        self.assertEqual(self.path.read_bytes(), corrupt)
        self.assertFalse(self.path.with_name(f"{self.path.name}.pre-sqlite").exists())


class TriggerPrefixTests(unittest.TestCase):
    @unittest.skip("Host deployment template is not the portable public example")
    def test_deployment_template_scopes_wally_only_to_multiperson_byoq(self):
        config = json.loads(Path(__file__).with_name("config.example.json").read_text())
        groups = config["vaults"]["groups"]
        prefixes = config["bridge"]["group_trigger_prefix_by_group_id"]

        self.assertEqual(config["bridge"]["group_trigger_prefix"], "")
        self.assertNotIn(groups["system-automation"]["signal_group_id"], prefixes)
        self.assertEqual(prefixes, {
            groups["byoq"]["signal_group_id"]: "wally",
            groups["alerts"]["signal_group_id"]: "wally",
        })

    def test_matches_wally_case_insensitively(self):
        self.assertTrue(matches_trigger_prefix("wally what is this?", "wally"))
        self.assertTrue(matches_trigger_prefix("Wally what is this?", "wally"))
        self.assertTrue(matches_trigger_prefix("WALLY what is this?", "wally"))

    def test_rejects_missing_prefix_and_allows_empty_configuration(self):
        self.assertFalse(matches_trigger_prefix("hello wally", "wally"))
        self.assertTrue(matches_trigger_prefix("any direct message", ""))

    def test_new_thread_requires_prefix(self):
        self.assertEqual(trigger_route("a new topic", "wally", False), (False, ""))
        self.assertEqual(trigger_route("Wally a new topic", "wally", False), (True, "wally"))

    def test_existing_thread_reply_does_not_require_prefix(self):
        self.assertEqual(trigger_route("continuing the thread", "wally", True), (True, ""))
        self.assertEqual(trigger_route("wally continuing", "wally", True), (True, "wally"))


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
        self.assertIn("Error: Codex error: The usage limit has been reached", message)
        self.assertNotIn("still online", message.lower())

    def test_quota_and_rate_limit_errors_have_distinct_messages(self):
        self.assertIn("Codex usage limit", format_runtime_error("insufficient_quota"))
        rate_limited = format_runtime_error("HTTP 429: Too Many Requests")
        self.assertIn("temporarily rate-limiting", rate_limited)
        self.assertIn("Error: HTTP 429: Too Many Requests", rate_limited)
        self.assertNotIn("usage resets", rate_limited)

    def test_unknown_runtime_error_includes_sanitized_provider_detail(self):
        message = format_runtime_error("provider connection reset by peer")
        self.assertIn("Error: provider connection reset by peer", message)
        self.assertNotIn("still online", message.lower())

    def test_runtime_error_redacts_credentials_and_bounds_detail(self):
        message = format_runtime_error("provider failed with secret-token-value")
        self.assertIn("Error: provider failed with [REDACTED]", message)
        self.assertNotIn("secret-token-value", message)
        self.assertLessEqual(len(format_runtime_error("x" * 1000)), 530)

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
