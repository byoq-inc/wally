import stat
import copy
import tempfile
import os
import errno
import subprocess
import shutil
import json
import hashlib
import io
import sys
import time
import unittest
import multiprocessing
import traceback
from unittest import mock
from pathlib import Path

from group_vaults import (
    EXPECTED_VAULT_IDS,
    GroupVaults,
    VaultError,
    bootstrap,
    promotion_apply,
    promotion_approve,
    promotion_preview,
    promotion_record_approval,
    promotion_sign_request,
    retrieval_command,
    safe_path,
    write_all,
)
from signal_pi_bridge import (
    LEGACY_PI_KEYS,
    main as bridge_main,
    pi_session_command,
    reject_legacy_pi_config,
    validate_proof_pi_command,
    worker_backend,
)
from migrate_logical_profile_config import migrate, transform_config
import vault_admin

def _concurrent_apply_process(root,token,requester_public,reviewer_public,barrier,results):
    barrier.wait()
    try:
        promotion_apply(Path(root),"govfiles",Path("MEMORY.md"),token,Path(requester_public),Path(reviewer_public))
        results.put("applied")
    except VaultError:
        results.put("consumed")


class DurableWriteTests(unittest.TestCase):
    def test_write_all_retries_every_eintr_and_short_write(self):
        read_fd,write_fd=os.pipe(); real_write=os.write; calls=[]
        def injected(fd,data):
            calls.append(len(data))
            if len(calls) in (1,3): raise InterruptedError(errno.EINTR,"injected")
            return real_write(fd,bytes(data[:min(2,len(data))]))
        try:
            with mock.patch("group_vaults.os.write",side_effect=injected): write_all(write_fd,b"abcdefgh")
            os.close(write_fd); write_fd=-1
            self.assertEqual(os.read(read_fd,32),b"abcdefgh")
            self.assertGreaterEqual(len(calls),6)
        finally:
            if write_fd >= 0: os.close(write_fd)
            os.close(read_fd)

    def test_write_all_rejects_zero_progress(self):
        with mock.patch("group_vaults.os.write",return_value=0), self.assertRaisesRegex(OSError,"no progress"):
            write_all(123,b"payload")

    def test_atomic_replace_short_and_eintr_never_exposes_partial_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"shared").mkdir(); target=root/"shared"/"PROMOTED.md"; target.write_bytes(b"OLD")
            from group_vaults import VaultFS
            real_write=os.write; attempts=0
            def injected(fd,data):
                nonlocal attempts
                attempts+=1
                if attempts in (1,4): raise InterruptedError(errno.EINTR,"injected")
                self.assertEqual(target.read_bytes(),b"OLD")
                return real_write(fd,bytes(data[:1]))
            with VaultFS(root) as fs, mock.patch("group_vaults.os.write",side_effect=injected):
                fs.replace(b"COMPLETE","shared","PROMOTED.md")
            self.assertEqual(target.read_bytes(),b"COMPLETE")

    def test_atomic_replace_zero_write_preserves_old_and_removes_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"shared").mkdir(); target=root/"shared"/"PROMOTED.md"; target.write_bytes(b"OLD")
            from group_vaults import VaultFS
            with VaultFS(root) as fs, mock.patch("group_vaults.os.write",return_value=0), self.assertRaises(OSError):
                fs.replace(b"NEW","shared","PROMOTED.md")
            self.assertEqual(target.read_bytes(),b"OLD")
            self.assertEqual(list((root/"shared").glob(".PROMOTED.md.tmp-*")),[])

    def test_atomic_replace_staged_fsync_failure_preserves_old(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"shared").mkdir(); target=root/"shared"/"PROMOTED.md"; target.write_bytes(b"OLD")
            from group_vaults import VaultFS
            real_fsync=os.fsync
            def injected(fd):
                if stat.S_ISREG(os.fstat(fd).st_mode): raise OSError("staged fsync crash")
                return real_fsync(fd)
            with VaultFS(root) as fs, mock.patch("group_vaults.os.fsync",side_effect=injected), self.assertRaises(OSError):
                fs.replace(b"NEW","shared","PROMOTED.md")
            self.assertEqual(target.read_bytes(),b"OLD")

    def test_atomic_replace_directory_fsync_failure_leaves_complete_new(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"shared").mkdir(); target=root/"shared"/"PROMOTED.md"; target.write_bytes(b"OLD")
            from group_vaults import VaultFS
            real_fsync=os.fsync
            def injected(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError("directory fsync crash")
                return real_fsync(fd)
            with VaultFS(root) as fs, mock.patch("group_vaults.os.fsync",side_effect=injected), self.assertRaises(OSError):
                fs.replace(b"COMPLETE","shared","PROMOTED.md")
            self.assertEqual(target.read_bytes(),b"COMPLETE")

    def test_cli_artifact_uses_checked_write_all(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"artifact.json"; real_write=os.write; attempts=0
            def injected(fd,data):
                nonlocal attempts
                attempts+=1
                if attempts==1: raise InterruptedError(errno.EINTR,"injected")
                return real_write(fd,bytes(data[:1]))
            with mock.patch("group_vaults.os.write",side_effect=injected): vault_admin.write_exclusive_artifact(path,b"complete")
            self.assertEqual(path.read_bytes(),b"complete")


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "opaque-vaults"
        bootstrap(self.root)
        self.keys = Path(self.temp.name) / "role-keys"
        self.keys.mkdir(mode=0o700)
        self.requester_private=self.keys/"requester.pem"; self.requester_public=self.keys/"requester.pub.pem"
        self.reviewer_private=self.keys/"reviewer.pem"; self.reviewer_public=self.keys/"reviewer.pub.pem"
        for private,public in ((self.requester_private,self.requester_public),(self.reviewer_private,self.reviewer_public)):
            subprocess.run(["openssl","genpkey","-algorithm","ED25519","-out",str(private)],check=True,capture_output=True)
            subprocess.run(["openssl","pkey","-in",str(private),"-pubout","-out",str(public)],check=True,capture_output=True)
        os.chown(self.reviewer_private,1001,1001); os.chown(self.reviewer_public,1001,1001)
        self.group_ids = {vault: f"private-group-id-{index}" for index, vault in enumerate(EXPECTED_VAULT_IDS)}
        self.config = {"vaults": {
            "enabled": True,
            "root": str(self.root),
            "max_context_bytes": 700,
            "max_retrieval_bytes": 300,
            "groups": {
                vault: {"signal_group_id": self.group_ids[vault], "label": f"Label {index}"}
                for index, vault in enumerate(EXPECTED_VAULT_IDS)
            },
        }}
        for vault in EXPECTED_VAULT_IDS:
            (self.root / "groups" / vault / "MEMORY.md").write_text(f"SECRET-{vault}\n", encoding="utf-8")
            skill = self.root / "groups" / vault / "skills" / "detailed"
            skill.mkdir()
            (skill / "SKILL.md").write_text(f"SKILL-SECRET-{vault}\n", encoding="utf-8")
            (self.root / "groups" / vault / "runtime" / "sessions" / "private.jsonl").write_text(
                f"SESSION-SECRET-{vault}\n", encoding="utf-8"
            )
        self.vaults = GroupVaults(self.config)

    def tearDown(self):
        self.temp.cleanup()


class IsolationTests(VaultFixture):
    def promote(self):
        _,token=promotion_preview(self.root,"govfiles",Path("MEMORY.md"),self.requester_private,self.requester_public)
        with mock.patch("group_vaults.os.geteuid",return_value=1001):
            promotion_approve(self.root,token,self.requester_public,self.reviewer_private,self.reviewer_public)
        promotion_apply(self.root,"govfiles",Path("MEMORY.md"),token,self.requester_public,self.reviewer_public)
        return token

    def test_every_group_gets_shared_and_only_its_own_details(self):
        for vault_id, group_id in self.group_ids.items():
            with self.subTest(vault_id=vault_id):
                context = self.vaults.compose_context(group_id)
                self.assertIn(f"SECRET-{vault_id}", context)
                for other in set(EXPECTED_VAULT_IDS) - {vault_id}:
                    self.assertNotIn(f"SECRET-{other}", context)

    def test_profile_context_loads_own_summary_and_optional_instructions_only(self):
        for vault in EXPECTED_VAULT_IDS:
            directory=self.root/"groups"/vault
            (directory/"SUMMARY.md").write_text(f"SUMMARY-{vault}\n")
            (directory/"INSTRUCTIONS.md").write_text(f"INSTRUCTIONS-{vault}\n")
        for vault,group_id in self.group_ids.items():
            context=self.vaults.compose_context(group_id)
            self.assertIn(f"SUMMARY-{vault}",context)
            self.assertIn(f"INSTRUCTIONS-{vault}",context)
            for other in set(EXPECTED_VAULT_IDS)-{vault}:
                self.assertNotIn(f"SUMMARY-{other}",context)
                self.assertNotIn(f"INSTRUCTIONS-{other}",context)

    def test_research_and_every_other_vault_are_bidirectionally_isolated(self):
        research_gid = self.group_ids["research"]
        research_context = self.vaults.compose_context(research_gid)
        research_skills = self.vaults.retrieve("research", "skills")
        research_session_dir = self.root / "groups" / "research" / "runtime" / "sessions"
        self.assertIn("SECRET-research", research_context)
        self.assertIn("SKILL-SECRET-research", research_skills)
        self.assertIn("SESSION-SECRET-research", (research_session_dir / "private.jsonl").read_text())
        for other in set(EXPECTED_VAULT_IDS) - {"research"}:
            with self.subTest(other=other):
                other_context = self.vaults.compose_context(self.group_ids[other])
                other_skills = self.vaults.retrieve(other, "skills")
                other_session_dir = self.root / "groups" / other / "runtime" / "sessions"
                self.assertNotIn(f"SECRET-{other}", research_context)
                self.assertNotIn(f"SKILL-SECRET-{other}", research_skills)
                self.assertNotEqual(research_session_dir, other_session_dir)
                self.assertNotIn("SECRET-research", other_context)
                self.assertNotIn("SKILL-SECRET-research", other_skills)
                self.assertNotEqual(other_session_dir, research_session_dir)

    def test_each_group_command_loads_only_shared_and_own_skills_and_sessions(self):
        extension = self.root / "sandbox.ts"
        extension.write_text("GROUP_TOOL_SANDBOX_POLICY_V1")
        for vault_id, group_id in self.group_ids.items():
            with self.subTest(vault_id=vault_id):
                skill_paths = self.vaults.skill_paths(group_id)
                session_dir = self.root / "groups" / vault_id / "runtime" / "sessions"
                command = pi_session_command(["pi", "--mode", "rpc"], {"pi_session": "s", "id": "1", "name": None}, skill_paths, session_dir=session_dir)
                loaded = [command[index + 1] for index, value in enumerate(command) if value == "--skill"]
                self.assertEqual(loaded, [str(self.root / "shared" / "skills"), str(self.root / "groups" / vault_id / "skills")])
                self.assertIn(str(session_dir), command)
                for other in set(EXPECTED_VAULT_IDS) - {vault_id}:
                    self.assertFalse(any(f"/groups/{other}/" in value for value in command))

    def test_group_extensions_are_explicit_integrity_checked_and_isolated(self):
        govfiles_extension = self.root / "groups" / "govfiles" / "extensions" / "db-tools.ts"
        govfiles_extension.write_text("export default function () {}\n", encoding="utf-8")
        digest = hashlib.sha256(govfiles_extension.read_bytes()).hexdigest()
        self.config["vaults"]["groups"]["govfiles"]["extensions"] = [
            {"path": "extensions/db-tools.ts", "sha256": digest}
        ]
        vaults = GroupVaults(self.config)
        try:
            paths = vaults.extension_paths(self.group_ids["govfiles"])
            self.assertEqual(paths, [govfiles_extension])
            self.assertEqual(vaults.extension_paths(self.group_ids["research"]), [])
            command = pi_session_command(
                ["pi", "--mode", "rpc"], {"pi_session": "s", "id": "1", "name": None},
                vaults.skill_paths(self.group_ids["govfiles"]),
                session_dir=self.root / "groups" / "govfiles" / "runtime" / "sessions",
                extension_paths=paths,
            )
            self.assertIn("--no-extensions", command)
            self.assertEqual(command[command.index("--extension") + 1], str(govfiles_extension))
            govfiles_extension.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(VaultError, "integrity mismatch"):
                vaults.extension_paths(self.group_ids["govfiles"])
        finally:
            vaults.fs.close()

    def test_group_extension_config_rejects_unscoped_or_unpinned_paths(self):
        entry = self.config["vaults"]["groups"]["govfiles"]
        for extensions in (
            ["extensions/db-tools.ts"],
            [{"path": "/tmp/db-tools.ts", "sha256": "0" * 64}],
            [{"path": "../db-tools.ts", "sha256": "0" * 64}],
            [{"path": "extensions/db-tools.ts", "sha256": "not-a-digest"}],
        ):
            with self.subTest(extensions=extensions):
                entry["extensions"] = extensions
                with self.assertRaises(VaultError):
                    GroupVaults(self.config)
        entry.pop("extensions", None)

    def test_direct_gets_only_minimized_references(self):
        context = self.vaults.compose_context(None)
        for vault in EXPECTED_VAULT_IDS:
            self.assertIn(vault, context)
            self.assertNotIn(f"SECRET-{vault}", context)

    def test_direct_research_detail_requires_explicit_scoped_retrieval(self):
        self.vaults.max_context_bytes = 2000
        default = self.vaults.compose_context(None)
        self.assertIn("research", default)
        self.assertNotIn("SECRET-research", default)
        self.assertNotIn("SKILL-SECRET-research", default)
        self.assertIn("SECRET-research", self.vaults.retrieve(*retrieval_command("/vault research memory")))
        self.assertIn("SKILL-SECRET-research", self.vaults.retrieve(*retrieval_command("/vault research skills")))

    def test_reviewed_global_promotion_reaches_all_scopes_only_after_apply(self):
        before = self.vaults.compose_context(self.group_ids["frnk"])
        self.assertNotIn("SECRET-govfiles", before)
        self.promote()
        self.assertIn("SECRET-govfiles", self.vaults.compose_context(self.group_ids["frnk"]))
        self.assertIn("SECRET-govfiles", self.vaults.compose_context(None))

    def test_unknown_group_fails_closed(self):
        with self.assertRaisesRegex(VaultError, "not mapped"):
            self.vaults.compose_context("unknown")
        with self.assertRaises(VaultError):
            self.vaults.skill_paths("unknown")

    def test_pi_command_disables_discovery_and_has_scoped_skill_paths(self):
        thread = {"pi_session": "session", "id": "123456789", "name": None}
        own = self.vaults.skill_paths(self.group_ids["frnk"])
        extension = self.root / "sandbox.ts"
        extension.write_text("GROUP_TOOL_SANDBOX_POLICY_V1")
        session_dir = self.root / "groups" / "frnk" / "runtime" / "sessions"
        command = pi_session_command(["pi", "--mode", "rpc"], thread, own, session_dir=session_dir)
        self.assertEqual(command.count("--no-skills"), 1)
        loaded = [command[index + 1] for index, value in enumerate(command) if value == "--skill"]
        self.assertEqual(loaded, [str(self.root / "shared" / "skills"), str(self.root / "groups" / "frnk" / "skills")])
        self.assertFalse(any("govfiles/skills" in value for value in loaded))
        self.assertNotIn("--no-approve",command)
        self.assertNotIn("--tools",command)

    def test_proof_wrapper_is_required_for_every_worker_kind(self):
        validated=validate_proof_pi_command({"command":["/root/.pi/agent/bin/pi","--mode","rpc"]})
        self.assertEqual(validated[0],"/root/.pi/agent/bin/pi")
        self.assertEqual(worker_backend("tmux",None),"tmux")
        self.assertEqual(worker_backend("tmux",self.group_ids["research"]),"tmux")
        with self.assertRaisesRegex(VaultError,"absolute proof-enabled wrapper"):
            validate_proof_pi_command({"command":["pi","--mode","rpc"]})
        with self.assertRaisesRegex(VaultError,"proof-enabled Pi wrapper"):
            validate_proof_pi_command({"command":["/usr/bin/pi","--mode","rpc"],
                                       "proof_wrapper_sha256":hashlib.sha256(Path("/usr/bin/pi").read_bytes()).hexdigest()})

    def test_every_enumerated_legacy_pi_key_requires_migration(self):
        for key in LEGACY_PI_KEYS:
            with self.subTest(key=key), self.assertRaisesRegex(VaultError, "requires migration"):
                reject_legacy_pi_config({key: None})

    def test_transform_removes_only_legacy_pi_keys_and_preserves_values(self):
        source = {"pi": {"command": ["/proof", "--mode", "rpc"], "other": {"x": [1, 2.0, None]}},
                  "opaque": {"unicode": "π", "flag": False}}
        for index, key in enumerate(LEGACY_PI_KEYS):
            source["pi"][key] = {"legacy": index}
        transformed = transform_config(source)
        self.assertEqual(transformed["opaque"], source["opaque"])
        self.assertEqual(transformed["pi"]["command"], source["pi"]["command"])
        self.assertEqual(transformed["pi"]["other"], source["pi"]["other"])
        self.assertTrue(all(key not in transformed["pi"] for key in LEGACY_PI_KEYS))
        self.assertTrue(all(key in source["pi"] for key in LEGACY_PI_KEYS))

    def test_migration_verify_only_then_atomic_write_with_backup(self):
        wrapper = Path(self.temp.name) / "proof-pi"
        wrapper.write_text("#!/bin/sh\n# proof-of-work/proof-of-work.ts --append-system-prompt --no-extensions\n")
        wrapper.chmod(0o700)
        extension = Path(self.temp.name) / "proof.ts"
        extension.write_text("proof")
        config = copy.deepcopy(self.config)
        config["signal"] = {"allow_groups": True, "allowed_group_ids": list(self.group_ids.values())}
        config["pi"] = {
            "command": [str(wrapper), "--mode", "rpc"],
            "proof_wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            "proof_extension": str(extension),
            "preserved": {"unicode": "π", "number": 1.0},
            **{key: index for index, key in enumerate(LEGACY_PI_KEYS)},
        }
        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        path.chmod(0o600)
        before = path.read_bytes()
        self.assertIsNone(migrate(path, verify_only=True))
        self.assertEqual(path.read_bytes(), before)
        backup = migrate(path)
        self.assertEqual(backup.read_bytes(), before)
        result = json.loads(path.read_text())
        self.assertEqual(result["pi"]["preserved"], config["pi"]["preserved"])
        self.assertTrue(all(key not in result["pi"] for key in LEGACY_PI_KEYS))

    def test_runtime_has_no_retired_helpers_or_artifact_directory(self):
        bridge = (Path(__file__).parent / "signal_pi_bridge.py").read_text()
        for retired in ("GONDOLIN_VERSION", "SandboxBinding", "validate_group_sandbox",
                        "verify_group_sandbox", "establish_sandbox_kernel_boundary",
                        "qemu-system", "PR_CAPBSET_DROP"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, bridge)
        self.assertFalse((Path(__file__).parent / "group-sandbox").exists())
        self.assertFalse((Path(__file__).parent / "pi-runtime-integrity.json").exists())

    def test_startup_rejects_legacy_fields_before_runtime_connection(self):
        config=dict(self.config)
        config["signal"]={"host":"127.0.0.1","port":1,"allow_groups":True,
                          "allowed_group_ids":list(self.group_ids.values())}
        config["pi"]={"command":["/root/.pi/agent/bin/pi","--mode","rpc"],
                      "group_sandbox_extension":"/obsolete/unavailable.ts",
                      "group_pi_runtime_root":"/obsolete/unavailable"}
        path=Path(self.temp.name)/"config.json"; path.write_text(json.dumps(config))
        with mock.patch("sys.argv",["signal_pi_bridge.py","--config",str(path)]), \
             mock.patch("signal_pi_bridge.JsonLineSocket") as socket_factory, \
             self.assertRaisesRegex(VaultError,"requires migration"):
            bridge_main()
        socket_factory.assert_not_called()

    def test_legacy_direct_command_is_unchanged_without_vault_paths(self):
        thread = {"pi_session": "session", "id": "123456789", "name": None}
        self.assertEqual(
            pi_session_command(["pi", "--mode", "rpc"], thread),
            ["pi", "--mode", "rpc", "--session-id", "session", "--name", "Signal 12345678"],
        )

    def test_context_and_retrieval_are_byte_bounded_and_deterministic(self):
        memory = self.root / "groups" / "govfiles" / "MEMORY.md"
        memory.write_text("é" * 2000, encoding="utf-8")
        first = self.vaults.compose_context(self.group_ids["govfiles"])
        self.assertEqual(first, self.vaults.compose_context(self.group_ids["govfiles"]))
        self.assertLessEqual(len(first.encode()), 700)
        self.assertLessEqual(len(self.vaults.retrieve("govfiles", "memory").encode()), 300)

class AttackTests(VaultFixture):
    def test_traversal_absolute_and_unknown_vault_are_rejected(self):
        for value in ("../outside", "/etc/passwd", "skills/../../outside"):
            with self.subTest(value=value), self.assertRaises(VaultError):
                safe_path(self.root, value)
        with self.assertRaises(VaultError):
            self.vaults.vault_dir("../govfiles")

    def test_symlink_files_and_directories_are_rejected(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("OUTSIDE SECRET", encoding="utf-8")
        memory = self.root / "groups" / "frnk" / "MEMORY.md"
        memory.unlink()
        memory.symlink_to(outside)
        with self.assertRaisesRegex(VaultError, "symlink"):
            self.vaults.retrieve("frnk", "memory")
        skill_link = self.root / "groups" / "govfiles" / "skills" / "bad"
        skill_link.symlink_to(Path(self.temp.name), target_is_directory=True)
        with self.assertRaises(VaultError):
            safe_path(self.root, "groups", "govfiles", "skills", "bad", "outside.md")

    def test_mapping_shape_duplicates_and_missing_group_fail(self):
        del self.config["vaults"]["groups"]["frnk"]
        with self.assertRaisesRegex(VaultError, "exactly"):
            GroupVaults(self.config)

    def test_root_symlink_is_rejected_before_resolution(self):
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(VaultError, "root.*symlink"):
            bootstrap(alias)

    def test_parent_swap_cannot_redirect_open_descriptor_to_sibling(self):
        original_open = __import__("os").open
        group = self.root / "groups" / "frnk"
        parked = self.root / "groups" / "frnk-parked"
        swapped = False
        def racing_open(path, *args, **kwargs):
            nonlocal swapped
            if path == "MEMORY.md" and not swapped:
                swapped = True
                group.rename(parked)
                group.symlink_to(self.root / "groups" / "govfiles", target_is_directory=True)
            return original_open(path, *args, **kwargs)
        with mock.patch("group_vaults.os.open", side_effect=racing_open):
            self.assertIn("SECRET-frnk", self.vaults.retrieve("frnk", "memory"))

    def test_final_swap_is_rejected_at_the_actual_open(self):
        original_open = __import__("os").open
        memory = self.root / "groups" / "frnk" / "MEMORY.md"
        outside = Path(self.temp.name) / "outside-race"; outside.write_text("OUTSIDE")
        swapped = False
        def racing_open(path, *args, **kwargs):
            nonlocal swapped
            if path == "MEMORY.md" and not swapped:
                swapped = True; memory.unlink(); memory.symlink_to(outside)
            return original_open(path, *args, **kwargs)
        with mock.patch("group_vaults.os.open", side_effect=racing_open), self.assertRaisesRegex(VaultError, "symlink"):
            self.vaults.retrieve("frnk", "memory")


class RetrievalAndPromotionTests(VaultFixture):
    def request(self):
        return promotion_preview(self.root,"govfiles",Path("MEMORY.md"),self.requester_private,self.requester_public)

    def approve(self,token):
        with mock.patch("group_vaults.os.geteuid",return_value=1001):
            return promotion_approve(self.root,token,self.requester_public,self.reviewer_private,self.reviewer_public)

    def apply(self,token):
        return promotion_apply(self.root,"govfiles",Path("MEMORY.md"),token,self.requester_public,self.reviewer_public)

    def test_retrieval_parser_is_exact_and_scoped(self):
        self.assertEqual(retrieval_command("/vault govfiles memory"), ("govfiles", "memory"))
        self.assertEqual(retrieval_command("/vault research skills"), ("research", "skills"))
        self.assertIsNone(retrieval_command("tell me /vault govfiles memory"))
        for invalid in ("/vault ../govfiles memory", "/vault govfiles all", "/vault govfiles memory extra",
                        "/vault  govfiles memory", "/vault govfiles memory ", "/vault govfiles memory\n", "/VAULT govfiles memory"):
            with self.assertRaises(VaultError):
                retrieval_command(invalid)

    def test_skill_retrieval_reads_only_selected_vault(self):
        for vault in ("govfiles", "frnk"):
            skill = self.root / "groups" / vault / "skills" / "local"
            skill.mkdir()
            (skill / "SKILL.md").write_text(f"SKILL-SECRET-{vault}", encoding="utf-8")
        result = self.vaults.retrieve("govfiles", "skills")
        self.assertIn("SKILL-SECRET-govfiles", result)
        self.assertNotIn("SKILL-SECRET-frnk", result)

    def test_audit_contains_safe_vault_id_not_raw_group_id(self):
        self.vaults.audit("retrieve", "govfiles", "memory")
        log = (self.root / "admin" / "audit.jsonl").read_text()
        self.assertIn('"vault_id":"govfiles"', log)
        self.assertNotIn(self.group_ids["govfiles"], log)
        self.assertEqual(stat.S_IMODE((self.root / "admin" / "audit.jsonl").stat().st_mode), 0o600)

    def test_promotion_requires_matching_preview_and_backs_up(self):
        content, token = self.request()
        self.assertIn("SECRET-govfiles", content)
        with self.assertRaises(VaultError):
            self.apply("wrong")
        self.approve(token)
        target = self.apply(token)
        self.assertIn("SECRET-govfiles", target.read_text())
        _, second_token = self.request()
        self.approve(second_token)
        self.apply(second_token)
        backups = list((self.root / "backups").glob("*PROMOTED.md"))
        self.assertTrue(backups)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaisesRegex(VaultError, "consumed"):
            self.apply(second_token)

    def test_review_token_is_validated_before_root_or_lock_access(self):
        for token in ("", "a"*31, "A"*32, "0"*33, "../"+"0"*32, None):
            with self.subTest(token=token), mock.patch("group_vaults.VaultFS",side_effect=AssertionError("root opened")), self.assertRaisesRegex(VaultError,"invalid review token"):
                promotion_apply(self.root,"govfiles",Path("MEMORY.md"),token,self.requester_public,self.reviewer_public)

    def test_one_principal_cannot_self_approve_or_forge_actor_identity(self):
        _,token=self.request()
        with self.assertRaisesRegex(VaultError,"different authenticated principals"):
            promotion_approve(self.root,token,self.requester_public,self.requester_private,self.requester_public)
        request_path=self.root/"admin"/f"request-{token}.json"
        artifact=json.loads(request_path.read_text()); artifact["requester"]="ed25519:"+"0"*64; request_path.write_text(json.dumps(artifact))
        with self.assertRaisesRegex(VaultError,"identity"):
            self.approve(token)
        _,token=self.request(); self.approve(token)
        with self.assertRaisesRegex(VaultError,"independent approval identity"):
            promotion_apply(self.root,"govfiles",Path("MEMORY.md"),token,self.requester_public,self.requester_public)
        forbidden=self.root/"admin"/"requester.pem"; forbidden.write_bytes(self.requester_private.read_bytes())
        with self.assertRaisesRegex(VaultError,"outside the vault tree"):
            promotion_preview(self.root,"govfiles",Path("MEMORY.md"),forbidden,self.requester_public)

    def test_same_os_principal_with_two_distinct_keys_is_denied(self):
        second_private=self.keys/"same-principal-second.pem"; second_public=self.keys/"same-principal-second.pub.pem"
        subprocess.run(["openssl","genpkey","-algorithm","ED25519","-out",second_private],check=True,capture_output=True)
        subprocess.run(["openssl","pkey","-in",second_private,"-pubout","-out",second_public],check=True,capture_output=True)
        _,token=self.request(); request=(self.root/"admin"/f"request-{token}.json").read_bytes()
        with self.assertRaisesRegex(VaultError,"different authenticated principals"):
            promotion_sign_request(request,self.requester_public,second_private,second_public)

    def test_detached_separate_principal_needs_no_vault_or_private_content(self):
        _,token=self.request(); request=(self.root/"admin"/f"request-{token}.json").read_bytes()
        self.assertNotIn(b"SECRET-govfiles",request)
        with self.assertRaisesRegex(VaultError,"different authenticated principals"):
            promotion_sign_request(request,self.requester_public,self.reviewer_private,self.reviewer_public)
        with mock.patch("group_vaults.os.geteuid",return_value=1001):
            approval=promotion_sign_request(request,self.requester_public,self.reviewer_private,self.reviewer_public)
        promotion_record_approval(self.root,approval,self.requester_public,self.reviewer_public)
        self.apply(token)
        audit=(self.root/"admin"/"audit.jsonl").read_text()
        self.assertIn("uid:1001/ed25519:",audit)

    def test_same_tick_backups_are_exclusive_and_distinct(self):
        with mock.patch("group_vaults.time.strftime", return_value="20260101T000000Z"):
            for _ in range(3):
                _, request_id = self.request()
                self.approve(request_id)
                self.apply(request_id)
        backups=list((self.root / "backups").glob("*PROMOTED.md"))
        self.assertEqual(len(backups), 2)
        self.assertEqual(len({p.name for p in backups}), 2)

    def test_concurrent_apply_reserves_once_and_appends_once(self):
        _,token=self.request(); self.approve(token)
        context=multiprocessing.get_context("fork"); barrier=context.Barrier(2); results=context.Queue()
        workers=[context.Process(target=_concurrent_apply_process,args=(self.root,token,self.requester_public,self.reviewer_public,barrier,results)) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        self.assertTrue(all(worker.exitcode==0 for worker in workers))
        outcomes=[results.get(timeout=1) for _ in workers]
        self.assertCountEqual(outcomes,["applied","consumed"])
        self.assertEqual((self.root/"shared"/"PROMOTED.md").read_text().count("Explicit promotion from govfiles"),1)
        audit=(self.root/"admin"/"audit.jsonl").read_text()
        self.assertEqual(audit.count('"action":"promotion-apply"'),1)

    def test_failure_after_reservation_consumes_without_mutation_or_replay(self):
        _,token=self.request(); self.approve(token)
        original=__import__("group_vaults").VaultFS.replace
        def fail_shared(fs,data,*parts,**kwargs):
            if parts==("shared","PROMOTED.md"): raise OSError("injected before replace")
            return original(fs,data,*parts,**kwargs)
        with mock.patch("group_vaults.VaultFS.replace",new=fail_shared), self.assertRaises(OSError): self.apply(token)
        self.assertFalse((self.root/"shared"/"PROMOTED.md").exists())
        with self.assertRaisesRegex(VaultError,"consumed"): self.apply(token)

    def test_failure_before_replace_preserves_existing_generation(self):
        _,first=self.request(); self.approve(first); self.apply(first)
        before=(self.root/"shared"/"PROMOTED.md").read_bytes()
        _,token=self.request(); self.approve(token)
        with mock.patch("group_vaults.VaultFS.replace",side_effect=OSError("crash before replace")), self.assertRaises(OSError): self.apply(token)
        self.assertEqual((self.root/"shared"/"PROMOTED.md").read_bytes(),before)
        with self.assertRaisesRegex(VaultError,"consumed"): self.apply(token)

    def test_failure_after_replace_before_audit_leaves_complete_generation_and_denies_replay(self):
        _,token=self.request(); self.approve(token)
        original=__import__("group_vaults")._admin_audit
        def fail_apply_audit(fs,action,*args,**kwargs):
            if action=="promotion-apply": raise OSError("crash before success audit")
            return original(fs,action,*args,**kwargs)
        with mock.patch("group_vaults._admin_audit",new=fail_apply_audit), self.assertRaises(OSError): self.apply(token)
        promoted=(self.root/"shared"/"PROMOTED.md").read_text()
        self.assertEqual(promoted.count("Explicit promotion from govfiles"),1)
        self.assertIn("SECRET-govfiles",promoted)
        self.assertNotIn('"action":"promotion-apply"',(self.root/"admin"/"audit.jsonl").read_text())
        with self.assertRaisesRegex(VaultError,"consumed"): self.apply(token)

    def test_detached_cli_export_review_record_apply_with_distinct_identity(self):
        request=Path(self.temp.name)/"request.json"; approval=Path(self.temp.name)/"approval.json"
        def cli(arguments,uid=0):
            with mock.patch.object(sys,"argv",["vault_admin.py",*arguments]), \
                 mock.patch("group_vaults.os.geteuid",return_value=uid), \
                 mock.patch("sys.stdout",new_callable=io.StringIO) as output:
                self.assertEqual(vault_admin.main(),0); return output.getvalue()
        preview=cli(["promote","--root",str(self.root),"--vault","govfiles","--preview",
                     "--request-artifact",str(request),"--requester-private-key",str(self.requester_private),
                     "--requester-public-key",str(self.requester_public)])
        token=preview.split("REQUEST_ID=",1)[1].splitlines()[0]
        self.assertNotIn(b"SECRET-govfiles",request.read_bytes())
        # The reviewer command has no vault argument and fails if it attempts vault access.
        with mock.patch("group_vaults.VaultFS",side_effect=AssertionError("reviewer touched vault")):
            cli(["promote","--approve","--request-artifact",str(request),"--approval-artifact",str(approval),
                 "--requester-public-key",str(self.requester_public),"--reviewer-private-key",str(self.reviewer_private),
                 "--reviewer-public-key",str(self.reviewer_public)],uid=1001)
        cli(["promote","--root",str(self.root),"--record-approval","--approval-artifact",str(approval),
             "--requester-public-key",str(self.requester_public),"--reviewer-public-key",str(self.reviewer_public)])
        cli(["promote","--root",str(self.root),"--vault","govfiles","--apply","--review-token",token,
             "--requester-public-key",str(self.requester_public),"--reviewer-public-key",str(self.reviewer_public)])
        self.assertEqual((self.root/"shared"/"PROMOTED.md").read_text().count("SECRET-govfiles"),1)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "vaults"

    def tearDown(self):
        self.temp.cleanup()

    def test_bootstrap_is_idempotent_private_and_non_destructive(self):
        source = self.base / "source.md"
        source.write_text("SOURCE REMAINS", encoding="utf-8")
        first = bootstrap(self.root, {"frnk": source})
        second = bootstrap(self.root, {"frnk": source})
        self.assertTrue(first)
        self.assertEqual(second, [])
        self.assertEqual(source.read_text(), "SOURCE REMAINS")
        self.assertTrue(source.exists())
        for directory in [self.root, self.root / "shared", *(self.root / "groups" / v for v in EXPECTED_VAULT_IDS)]:
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for vault in EXPECTED_VAULT_IDS:
            memory = self.root / "groups" / vault / "MEMORY.md"
            summary = self.root / "groups" / vault / "SUMMARY.md"
            self.assertEqual(stat.S_IMODE(memory.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(summary.stat().st_mode), 0o600)
            self.assertNotIn("SOURCE REMAINS", summary.read_text())

    def test_bootstrap_never_overwrites_existing_memory(self):
        bootstrap(self.root)
        memory = self.root / "groups" / "govfiles" / "MEMORY.md"
        memory.write_text("KEEP EXISTING", encoding="utf-8")
        source = self.base / "new.md"
        source.write_text("DO NOT COPY", encoding="utf-8")
        bootstrap(self.root, {"govfiles": source})
        self.assertEqual(memory.read_text(), "KEEP EXISTING")

    def test_research_bootstrap_preserves_all_existing_private_artifacts(self):
        bootstrap(self.root)
        research = self.root / "groups" / "research"
        memory = research / "MEMORY.md"
        summary = research / "SUMMARY.md"
        skill = research / "skills" / "existing" / "SKILL.md"
        session = research / "runtime" / "sessions" / "existing.jsonl"
        skill.parent.mkdir()
        memory.write_text("KEEP MEMORY")
        summary.write_text("KEEP SUMMARY")
        skill.write_text("KEEP SKILL")
        session.write_text("KEEP SESSION")
        self.assertEqual(bootstrap(self.root), [])
        self.assertEqual(memory.read_text(), "KEEP MEMORY")
        self.assertEqual(summary.read_text(), "KEEP SUMMARY")
        self.assertEqual(skill.read_text(), "KEEP SKILL")
        self.assertEqual(session.read_text(), "KEEP SESSION")
        self.assertFalse((self.root / "shared" / "PROMOTED.md").exists())


if __name__ == "__main__":
    unittest.main()
