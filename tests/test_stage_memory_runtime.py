import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('stage_memory_runtime', ROOT/'scripts/stage-memory-runtime.py')
staging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(staging)


def test_stage_only_allowlisted_sources_and_import_from_new_location(tmp_path):
    dest = tmp_path/'release'
    hashes = staging.stage(ROOT, dest)
    assert len(hashes) == 18
    assert {str(p.relative_to(dest)) for p in dest.rglob('*') if p.is_file()} == set(hashes)|{'SOURCE-MANIFEST.json'}
    env = {'PATH': os.defpath, 'HOME': str(tmp_path), 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': os.pathsep.join(str(dest/x) for x in ('bridge', 'entity', 'curator'))}
    subprocess.run([sys.executable, '-c', 'import signal_pi_bridge, signal_pi_relay, signal_current_delivery; import entity_memory.cli, group_memory_curator'], env=env, cwd=tmp_path, check=True)
    subprocess.run([sys.executable, '-m', 'entity_memory.cli', '--help'], env=env, cwd=tmp_path, check=True, capture_output=True)
    result = subprocess.run([sys.executable, str(dest/'curator/group_memory_curator.py')], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2 and '--config' in result.stderr


def test_staged_curator_end_to_end_without_live_credentials(tmp_path):
    dest = tmp_path/'release'
    staging.stage(ROOT, dest)
    root = tmp_path/'vaults'; group=root/'groups'/'alpha'
    (group/'runtime/sessions').mkdir(parents=True)
    (group/'MEMORY.md').write_text('# Fixture\n\nExisting\n')
    (group/'SUMMARY.md').write_text('Summary\n')
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'vaults':{'enabled':True,'root':str(root),'groups':{'alpha':{}}}}))
    fake=tmp_path/'fake.py'
    fake.write_text('import json\nprint(json.dumps({"memory":"Existing", "summary":"Summary", "promotions":[], "consistency_actions":[]}))\n')
    env={'PATH':os.defpath,'HOME':str(tmp_path),'PYTHONDONTWRITEBYTECODE':'1',
         'PI_GROUP_MEMORY_MODEL_COMMAND':json.dumps([sys.executable,str(fake)])}
    state=tmp_path/'state/state.json'
    run=subprocess.run([sys.executable,str(dest/'curator/group_memory_curator.py'), '--config',str(config), '--state',str(state),'--main-memory',str(tmp_path/'unused.md')], env=env,cwd=tmp_path,capture_output=True,text=True,check=True)
    assert json.loads(run.stdout)['audited_vaults']==1 and state.exists()
    assert '[recorded: unknown]' in (group/'MEMORY.md').read_text()
    assert not (tmp_path/'unused.md').exists()


def test_existing_destination_and_symlink_are_not_overwritten(tmp_path):
    dest=tmp_path/'existing';dest.mkdir();marker=dest/'keep';marker.write_text('unchanged')
    with pytest.raises(FileExistsError):staging.stage(ROOT,dest)
    alias=tmp_path/'alias';alias.symlink_to(dest,target_is_directory=True)
    with pytest.raises(FileExistsError):staging.stage(ROOT,alias)
    assert marker.read_text()=='unchanged'
    with pytest.raises(ValueError):staging.stage(ROOT,alias/'nested')


def test_private_files_excluded_and_symlink_source_rejected(tmp_path):
    source=tmp_path/'source';source.mkdir()
    for rel,_ in staging.FILES:
        p=source/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# synthetic source\n')
    (source/'auth.json').write_text('SYNTHETIC_PRIVATE_SENTINEL')
    dest=tmp_path/'release';staging.stage(source,dest)
    assert not any(b'SYNTHETIC_PRIVATE_SENTINEL' in p.read_bytes() for p in dest.rglob('*') if p.is_file())
    first=source/staging.FILES[0][0];first.unlink();first.symlink_to(source/'auth.json')
    with pytest.raises(ValueError):staging.stage(source,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()
