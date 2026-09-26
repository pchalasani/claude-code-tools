"""Exercise portable skill installation in isolated subprocess environments."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_backend import running_server
from test_detector import successful

REPO_ROOT = Path(__file__).resolve().parents[3]


def isolated_environment(root: Path, overrides: bool = True) -> dict[str, str]:
    """Use a private home, agent roots, and an absolute package import path."""
    environment = os.environ.copy()
    environment['HOME'] = str(root / 'home')
    environment['PYTHONPATH'] = str(REPO_ROOT / 'packages/jev-prose/src')
    environment.pop('CODEX_HOME', None)
    environment.pop('CLAUDE_CONFIG_DIR', None)
    if overrides:
        environment['CODEX_HOME'] = str(root / 'custom-codex')
        environment['CLAUDE_CONFIG_DIR'] = str(root / 'custom-claude')
    return environment


def invoke(
    root: Path, *arguments: str, overrides: bool = True, text: str = '',
) -> subprocess.CompletedProcess[str]:
    """Run the actual CLI away from the checkout without changing global state."""
    cwd = root / 'unrelated-project'
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, '-m', 'jev_prose.cli', *arguments], cwd=cwd,
        env=isolated_environment(root, overrides), input=text, capture_output=True,
        text=True, timeout=15, check=False,
    )


def skill_paths(root: Path, overrides: bool = True) -> dict[str, Path]:
    """Return expected paths under private default or configured agent roots."""
    roots = {'claude': root / 'custom-claude', 'codex': root / 'custom-codex'}
    if not overrides:
        roots = {'claude': root / 'home/.claude', 'codex': root / 'home/.codex'}
    return {name: path / 'skills/jev-prose/SKILL.md' for name, path in roots.items()}


def test_bundled_skill_matches_plugin() -> None:
    """The package and plugin must teach identical writing-loop instructions."""
    bundled = files('jev_prose').joinpath('SKILL.md').read_bytes()
    plugin = REPO_ROOT / 'plugins/writing/skills/jev-prose/SKILL.md'
    assert bundled == plugin.read_bytes()
    assert bundled.startswith(b'---\n')


@pytest.mark.parametrize('overrides', [False, True])
@pytest.mark.parametrize('target', ['claude', 'codex', 'both'])
def test_install_target_and_repeat(
    tmp_path: Path, overrides: bool, target: str,
) -> None:
    """Each target respects agent roots and accepts identical existing content."""
    paths = skill_paths(tmp_path, overrides)
    expected = set(paths) if target == 'both' else {target}
    content = files('jev_prose').joinpath('SKILL.md').read_bytes()
    for attempt in range(2):
        result = invoke(tmp_path, 'install-skill', '--target', target,
                        overrides=overrides)
        assert result.returncode == 0, (attempt, result.stdout, result.stderr)
        report = json.loads(result.stdout)
        assert report['ran'] is True and report['ok'] is True
        assert set(report['installed']) == {str(paths[name]) for name in expected}
        for name, path in paths.items():
            if name in expected:
                assert path.read_bytes() == content
            else:
                assert not path.exists()


@pytest.mark.parametrize('conflict_target', ['claude', 'codex'])
@pytest.mark.parametrize('other_exists', [False, True])
def test_conflict_preserves_both_targets(
    tmp_path: Path, conflict_target: str, other_exists: bool,
) -> None:
    """Preflight detects either conflict without creating or rewriting the other."""
    paths = skill_paths(tmp_path)
    conflict = paths[conflict_target]
    other = paths['codex' if conflict_target == 'claude' else 'claude']
    conflict.parent.mkdir(parents=True)
    conflict.write_bytes(b'Existing personal skill.\n')
    if other_exists:
        other.parent.mkdir(parents=True)
        other.write_bytes(files('jev_prose').joinpath('SKILL.md').read_bytes())
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
        for path in paths.values()
    }
    result = invoke(tmp_path, 'install-skill')
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report['ok'] is False and report['status'] == 'error'
    assert '--force' in report['error']
    for path, original in before.items():
        if original is None:
            assert not path.exists()
        else:
            assert (path.read_bytes(), path.stat().st_mtime_ns) == original


def test_force_replaces_different_skills(tmp_path: Path) -> None:
    """Explicit force replaces both existing skills with the bundled version."""
    paths = skill_paths(tmp_path)
    for name, path in paths.items():
        path.parent.mkdir(parents=True)
        path.write_text(f'Previous {name} skill.')
    result = invoke(tmp_path, 'install-skill', '--force')
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report['ran'] is True and report['ok'] is True
    content = files('jev_prose').joinpath('SKILL.md').read_bytes()
    assert all(path.read_bytes() == content for path in paths.values())


def test_bank_resources_available_away_from_checkout(tmp_path: Path) -> None:
    """The questions command loads its bundled resource independently of cwd."""
    result = invoke(tmp_path, 'questions')
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    resource = files('jev_prose').joinpath('questions.json').read_bytes()
    bank = json.loads(resource)
    assert report['ran'] is True and report['ok'] is True
    assert report['questions'] == bank['questions']
    assert report['version'] == bank['version']
    assert report['bank_sha256'] == hashlib.sha256(resource).hexdigest()


@pytest.mark.parametrize(('text', 'arguments'), [
    ('', []), ('Draft.', ['--threshold', 'nan']),
    ('Draft.', ['--workers', '0']), ('Draft.', ['--batch-size', '65']),
])
def test_validation_failure_did_not_run_inference(
    tmp_path: Path, text: str, arguments: list[str],
) -> None:
    """A resolved backend alone must not mark a failed validation as attempted."""
    with running_server(successful) as service:
        result = invoke(tmp_path, 'check', '--url', service.url, *arguments,
                        text=text)
        assert service.requests == []
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report['ran'] is False and report['ok'] is False
    assert report['status'] == 'error'
