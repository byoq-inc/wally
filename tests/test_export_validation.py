from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'pi/extensions/agora-harness-learning/core.test.ts'


def validate(tmp_path, path, content):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    script = tmp_path/'scripts/validate-export.sh'; script.parent.mkdir()
    script.write_bytes((ROOT/'scripts/validate-export.sh').read_bytes())
    target = tmp_path/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content)
    return subprocess.run(['bash', str(script)], cwd=tmp_path, capture_output=True, text=True)


def test_exact_reviewed_synthetic_fixture_is_allowed(tmp_path):
    value = 'not-a-real-' + 'api-key-value'
    result = validate(tmp_path, FIXTURE, 'const apiKey = '+repr(value)+';')
    assert result.returncode == 0, result.stderr


def test_fixture_allowlist_does_not_permit_other_values(tmp_path):
    result = validate(tmp_path, FIXTURE, 'const apiKey = '+repr('x'*32)+';')
    assert result.returncode == 1 and 'generic assigned secret' in result.stderr


def test_fixture_allowlist_does_not_cover_production_files(tmp_path):
    value = 'not-a-real-' + 'api-key-value'
    result = validate(tmp_path, 'module.ts', 'const apiKey = '+repr(value)+';')
    assert result.returncode == 1


def test_reserved_example_domain_allowed(tmp_path):
    assert validate(tmp_path, 'fixture.txt', 'operator'+'@'+'example.net').returncode == 0


def test_nonexample_contact_still_rejected(tmp_path):
    result = validate(tmp_path, 'fixture.txt', 'operator'+'@'+'private.invalid')
    assert result.returncode == 1 and 'email address' in result.stderr


def test_token_rule_not_suppressed_in_fixture_file(tmp_path):
    result = validate(tmp_path, FIXTURE, 'gh'+'p_'+'x'*30)
    assert result.returncode == 1 and 'GitHub token' in result.stderr


def test_exact_synthetic_bridge_phone_allowed(tmp_path):
    assert validate(tmp_path, 'pi/signal-bridge/test_signal_pi_bridge.py', '+'+'15555550123').returncode == 0


def test_phone_exception_does_not_allow_other_identities(tmp_path):
    assert validate(tmp_path, 'pi/signal-bridge/test_signal_pi_bridge.py', '+'+'15555550124').returncode == 1


def test_phone_exception_is_not_global(tmp_path):
    assert validate(tmp_path, 'module.py', '+'+'15555550123').returncode == 1
