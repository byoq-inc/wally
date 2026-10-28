import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
import group_memory_curator as curator


@pytest.mark.parametrize('raw', [None, '', '{}', 'null', 'true', '[]', '[1]',
    '["python3"]', '[""]', '["/bin/tool", null]', '["/bin/tool", ""]',
    '["/bin/tool", "\\u0000"]', json.dumps(['/bin/tool'] + ['x'] * 128)])
def test_invalid_command_never_starts_process(raw):
    env = {} if raw is None else {'PI_GROUP_MEMORY_MODEL_COMMAND': raw}
    with patch.dict('os.environ', env, clear=True), patch.object(curator.subprocess, 'run') as run:
        with pytest.raises(ValueError):
            curator.run_model('synthetic prompt')
        run.assert_not_called()


def test_constructor_cannot_bypass_validation():
    for argv in (['/bin/tool'], (), ('relative',), ('/bin/tool', False)):
        with pytest.raises(ValueError):
            curator.ModelCommand(argv)


def test_explicit_command_preserves_single_prompt_argument(tmp_path, monkeypatch):
    script = tmp_path / 'fake.py'
    script.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    monkeypatch.setenv('PI_GROUP_MEMORY_MODEL_COMMAND', json.dumps([sys.executable, str(script), 'fixed']))
    prompt = 'synthetic; $(never-execute)\nsecond line'
    assert json.loads(curator.run_model(prompt)) == ['fixed', prompt]


def test_cli_requires_explicit_storage_paths():
    result = subprocess.run([sys.executable, str(Path(curator.__file__))], capture_output=True, text=True)
    assert result.returncode == 2
    for flag in ('--config', '--state', '--main-memory'):
        assert flag in result.stderr
