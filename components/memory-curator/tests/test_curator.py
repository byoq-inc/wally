import importlib.util
import json
from pathlib import Path
import os
import sys

SPEC=importlib.util.spec_from_file_location("curator",Path(__file__).parents[1]/"group_memory_curator.py")
mod=importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = mod; SPEC.loader.exec_module(mod)


def test_extract_excludes_tools_and_cleans_signal_wrapper(tmp_path):
    session=tmp_path/"x.jsonl"
    events=[
      {"type":"message","message":{"role":"user","content":[{"type":"text","text":"prefix\nSignal message from +1:\nRemember blue"}]}},
      {"type":"message","message":{"role":"toolResult","content":[{"type":"text","text":"SECRET TOOL"}]}},
      {"type":"message","message":{"role":"assistant","content":[{"type":"thinking","thinking":"hidden"},{"type":"text","text":"Noted"}]}},
    ]
    session.write_text("".join(json.dumps(x)+"\n" for x in events))
    text,offsets=mod.extract_new_messages(tmp_path,{})
    assert text=="USER: Remember blue\n\nASSISTANT: Noted"
    assert "SECRET" not in text and offsets["x.jsonl"]==session.stat().st_size
    assert mod.extract_new_messages(tmp_path,offsets)[0]==""


def test_existing_memory_is_consistency_audited_without_new_messages(tmp_path, monkeypatch):
    root = tmp_path / "vaults"
    group = root / "groups" / "alpha"
    (group / "runtime" / "sessions").mkdir(parents=True)
    (group / "MEMORY.md").write_text("# Private memory\n\n- Exactly eight vaults exist.\n- Only seven vaults exist.\n")
    (group / "SUMMARY.md").write_text("Tracks memory operations.\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vaults": {"enabled": True, "root": str(root), "groups": {"alpha": {}}}}))
    main = tmp_path / "main.md"
    main.write_text("# Main\n")
    fake = tmp_path / "fake.py"
    fake.write_text('''import json\nprint(json.dumps({"memory":"# Private memory\\n\\n- [source: 7f7dcbc12755e607,4d0155dc2c6e0bfb] Eight vaults are configured; the recorded name inventory needs reconciliation.","summary":"Tracks memory operations.","promotions":[],"consistency_actions":["Reconciled conflicting vault counts without guessing the missing identity."]}))\n''')
    monkeypatch.setenv("PI_GROUP_MEMORY_MODEL_COMMAND", json.dumps([sys.executable, str(fake)]))

    result = mod.curate(config, tmp_path / "state" / "state.json", main)

    assert result["audited_vaults"] == 1
    assert result["consistency_actions"] == 1
    assert "Only seven vaults exist" not in (group / "MEMORY.md").read_text()
    assert "needs reconciliation" in (group / "MEMORY.md").read_text()


def test_oversized_output_is_compacted_once_before_writing(tmp_path, monkeypatch):
    root = tmp_path / "vaults"
    group = root / "groups" / "alpha"
    (group / "runtime" / "sessions").mkdir(parents=True)
    (group / "MEMORY.md").write_text("# alpha\n\nOriginal\n")
    (group / "SUMMARY.md").write_text("Original summary\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vaults": {"enabled": True, "root": str(root), "groups": {"alpha": {}}}}))
    main = tmp_path / "main.md"
    main.write_text("# Main\n")
    fake = tmp_path / "fake.py"
    fake.write_text('''import json,sys\np=sys.argv[-1]\nif "COMPACT OVERSIZED CURATION" in p: memory="# alpha\\n\\n[source: 88d759ea02cef4b8] Compacted"\nelse: memory="# alpha\\n\\n" + ("x" * 40000)\nprint(json.dumps({"memory":memory,"summary":"Original summary","promotions":[],"consistency_actions":[]}))\n''')
    monkeypatch.setenv("PI_GROUP_MEMORY_MODEL_COMMAND", json.dumps([sys.executable, str(fake)]))

    result = mod.curate(config, tmp_path / "state" / "state.json", main)

    assert result["vaults"] == ["alpha"]
    result_text = (group / "MEMORY.md").read_text()
    assert '[recorded: unknown] [updated:' in result_text
    assert result_text.endswith(' Compacted\n')


def test_oversized_output_fails_before_any_vault_is_written(tmp_path, monkeypatch):
    root = tmp_path / "vaults"
    for vault_id in ("alpha", "beta"):
        group = root / "groups" / vault_id
        (group / "runtime" / "sessions").mkdir(parents=True)
        (group / "MEMORY.md").write_text(f"# {vault_id}\n\nOriginal\n")
        (group / "SUMMARY.md").write_text("Original summary\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vaults": {"enabled": True, "root": str(root), "groups": {"alpha": {}, "beta": {}}}}))
    main = tmp_path / "main.md"
    main.write_text("# Main\n")
    fake = tmp_path / "fake.py"
    fake.write_text('''import json,sys\np=sys.argv[-1]\nif "Vault: beta" in p or "COMPACT OVERSIZED CURATION" in p: memory="# beta\\n\\n" + ("x" * 40000)\nelse: memory="# alpha\\n\\n[source: 88d759ea02cef4b8] Changed"\nprint(json.dumps({"memory":memory,"summary":"Original summary","promotions":[],"consistency_actions":[]}))\n''')
    monkeypatch.setenv("PI_GROUP_MEMORY_MODEL_COMMAND", json.dumps([sys.executable, str(fake)]))

    try:
        mod.curate(config, tmp_path / "state" / "state.json", main)
    except RuntimeError as error:
        assert "exceeds limit" in str(error)
    else:
        raise AssertionError("oversized output was accepted")

    assert (root / "groups" / "alpha" / "MEMORY.md").read_text() == "# alpha\n\nOriginal\n"


def test_end_to_end_curates_group_and_global_memory(tmp_path,monkeypatch):
    root=tmp_path/"vaults"; group=root/"groups"/"alpha"; sessions=group/"runtime"/"sessions"
    sessions.mkdir(parents=True)
    (group/"MEMORY.md").write_text("# Private memory\n\nOld\n")
    (group/"SUMMARY.md").write_text("Old summary\n")
    (sessions/"s.jsonl").write_text(json.dumps({"type":"message","message":{"role":"user","content":"Durable decision"}})+"\n")
    config=tmp_path/"config.json"; config.write_text(json.dumps({"vaults":{"enabled":True,"root":str(root),"groups":{"alpha":{}}}}))
    main=tmp_path/"main.md"; main.write_text("# Main\n")
    fake=tmp_path/"fake.py"; fake.write_text('''import json,sys\np=sys.argv[-1]\nif "CANDIDATES:" in p: print(json.dumps({"memory":"# Main\\n\\n[new] General lesson"}))\nelif "(none; perform the scheduled consistency audit" in p: print(json.dumps({"memory":"# Private memory\\n\\nDurable decision","summary":"Handles durable work.","promotions":[],"consistency_actions":[]}))\nelse: print(json.dumps({"memory":"# Private memory\\n\\n[new] Durable decision","summary":"[new] Handles durable work.","promotions":["General lesson"],"consistency_actions":[]}))\n''')
    monkeypatch.setenv("PI_GROUP_MEMORY_MODEL_COMMAND",json.dumps([sys.executable,str(fake)]))
    state=tmp_path/"state"/"state.json"
    result=mod.curate(config,state,main)
    assert result=={"status":"completed","vaults":["alpha"],"promotion_candidates":1,"main_updated":True,"audited_vaults":1,"consistency_actions":0}
    assert "Durable decision" in (group/"MEMORY.md").read_text()
    assert "General lesson" in main.read_text()
    before=(group/"MEMORY.md").read_text()
    assert mod.curate(config,state,main)["vaults"]==[]
    assert (group/"MEMORY.md").read_text()==before
