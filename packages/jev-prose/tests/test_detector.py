"""Validate complete editorial reports using the real localhost transport."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

import pytest

from jev_prose.backend import Backend, DetectorError
from jev_prose.detector import check, load_bank
from test_backend import Reply, running_server


def question(key: str, profile: str = 'general') -> dict[str, Any]:
    """Create a small valid question for isolated protocol tests."""
    return {
        'id': key, 'title': f'Pattern {key}', 'question': f'Is {key} present?',
        'guidance': f'Revise {key}.', 'scope': 'sentence',
        'profiles': [profile], 'sources': ['test-source'],
    }


def bank_of(*questions: dict[str, Any]) -> dict[str, Any]:
    """Make a versioned bank from explicitly selected questions."""
    return {'version': 'test-1', 'questions': list(questions)}


def successful(request: dict[str, Any], probability: float = 0.1) -> Reply:
    """Answer all supplied questions with a valid noul result."""
    return Reply({
        'model': 'jev-fixed', 'answers': {
            key: {'type': 'noul', 'noul': probability}
            for key in request['questions']
        }, 'usage': {'questions': len(request['questions'])},
    })


def test_batching_concurrency_and_complete_report() -> None:
    """Independent batches overlap, remain bounded, and cover every ID once."""
    barrier = threading.Barrier(3)

    def respond(request: dict[str, Any]) -> Reply:
        """Wait for three real HTTP requests to overlap before answering."""
        barrier.wait(timeout=5)
        return successful(request, 0.9)

    bank = bank_of(*(question(f'q-{i}') for i in range(6)))
    with running_server(respond) as service:
        report = check(
            'Original prose.', Backend(service.url, 'jev-fixed'), bank, 'digest',
            batch_size=2, workers=3, context='Background.', audience='Researchers',
            voice='Direct',
        )
        assert service.peak == 3
        assert [len(r['questions']) for r in service.requests] == [2, 2, 2]
        assert all(r['state'] == {
            'text': 'Original prose.', 'context': 'Background.',
            'audience': 'Researchers', 'voice': 'Direct',
        } for r in service.requests)
        sent = [key for r in service.requests for key in r['questions']]
        assert len(sent) == len(set(sent)) == 6
        assert report['ran'] is True and report['ok'] is True
        assert report['status'] == 'findings'
        assert report['expected'] == report['answered'] == 6
        assert report['requests'] == 3
        assert report['bank_sha256'] == 'digest'
        assert report['bank_version'] == 'test-1'
        assert report['text_sha256'] == hashlib.sha256(b'Original prose.').hexdigest()
        assert len(report['judgments']) == len(report['findings']) == 6
        assert len(report['usage']) == 3
        assert all('guidance' in item for item in report['findings'])


@pytest.mark.parametrize(('profile', 'count'), [
    ('general', 1), ('formal', 2), ('strict', 3),
])
def test_profiles_and_threshold_boundary(profile: str, count: int) -> None:
    """Profiles are additive and the probability threshold is inclusive."""
    bank = bank_of(question('a'), question('b', 'formal'), question('c', 'strict'))
    with running_server(lambda r: successful(r, 0.85)) as service:
        report = check(
            'Draft.', Backend(service.url, 'jev-fixed'), bank, 'digest',
            profile=profile, threshold=0.85,
        )
        assert report['expected'] == report['answered'] == count
        assert len(report['findings']) == count
        assert all(item['flagged'] for item in report['judgments'])


def test_unflagged_answers_are_retained_and_findings_sorted() -> None:
    """A report preserves negative judgments and ranks actionable findings."""
    def respond(request: dict[str, Any]) -> Reply:
        """Assign distinct probabilities to explicitly named test questions."""
        values = {'a': 0.849, 'b': 0.9, 'c': 1.0}
        return Reply({'model': 'jev-fixed', 'answers': {
            key: {'type': 'noul', 'noul': values[key]}
            for key in request['questions']
        }})

    with running_server(respond) as service:
        report = check('Draft.', Backend(service.url, 'jev-fixed'),
                       bank_of(question('a'), question('b'), question('c')), 'hash')
    assert [r['id'] for r in report['findings']] == ['c', 'b']
    assert [r['flagged'] for r in report['judgments']] == [False, True, True]


@pytest.mark.parametrize('answer', [
    None, [], {}, {'type': 'choice', 'noul': 0.2},
    {'type': 'noul'}, {'type': 'noul', 'noul': None},
    {'type': 'noul', 'noul': True}, {'type': 'noul', 'noul': '0.9'},
    {'type': 'noul', 'noul': -0.1}, {'type': 'noul', 'noul': 1.1},
    {'type': 'noul', 'noul': float('nan')},
    {'type': 'noul', 'noul': float('inf')},
])
def test_malformed_answer_is_not_clean(answer: Any) -> None:
    """An invalid answer invalidates the entire inference report."""
    response = {'model': 'jev-fixed', 'answers': {'a': answer}}
    with running_server(lambda r: Reply(response)) as service:
        with pytest.raises(DetectorError):
            check('Draft.', Backend(service.url, 'jev-fixed'),
                  bank_of(question('a')), 'hash')


@pytest.mark.parametrize('response', [
    {}, {'model': 'jev-fixed'}, {'model': 'jev-fixed', 'answers': []},
    {'model': 'jev-fixed', 'answers': {}},
    {'model': 'jev-fixed', 'answers': {'wrong': {'type': 'noul', 'noul': 0.1}}},
    {'model': 'jev-fixed', 'answers': {
        'a': {'type': 'noul', 'noul': 0.1}, 'extra': {'type': 'noul', 'noul': 0.1},
    }},
    {'answers': {'a': {'type': 'noul', 'noul': 0.1}}},
    {'model': '', 'answers': {'a': {'type': 'noul', 'noul': 0.1}}},
    {'model': 1, 'answers': {'a': {'type': 'noul', 'noul': 0.1}}},
])
def test_incomplete_or_unidentified_inference(response: dict[str, Any]) -> None:
    """Missing or unexpected IDs and missing model identity fail closed."""
    with running_server(lambda r: Reply(response)) as service:
        with pytest.raises(DetectorError):
            check('Draft.', Backend(service.url, 'jev-fixed'),
                  bank_of(question('a')), 'hash')


def test_mixed_models_across_batches_fail() -> None:
    """A batch that silently switches model cannot complete the report."""
    def respond(request: dict[str, Any]) -> Reply:
        """Return an answer with a batch-specific model identity."""
        reply = successful(request)
        reply.body['model'] = next(iter(request['questions']))
        return reply

    with running_server(respond) as service:
        with pytest.raises(DetectorError, match='Model changed'):
            check('Draft.', Backend(service.url, 'jev-fixed'),
                  bank_of(question('a'), question('b')), 'hash', batch_size=1)


@pytest.mark.parametrize('kwargs', [
    {'text': ''}, {'text': '   '}, {'text': 'x' * 24_001},
    {'context': 'x' * 8001}, {'audience': 'x' * 1001}, {'voice': 'x' * 1001},
    {'profile': 'unknown'}, {'threshold': 0.5}, {'threshold': 1.01},
    {'threshold': float('nan')}, {'threshold': float('inf')},
    {'batch_size': 0}, {'batch_size': 65}, {'workers': 0}, {'workers': 9},
])
def test_invalid_input_sends_no_requests(kwargs: dict[str, Any]) -> None:
    """Input validation precedes network inference."""
    with running_server(successful) as service:
        arguments = {'text': 'Draft.', **kwargs}
        with pytest.raises(DetectorError):
            check(backend=Backend(service.url, 'jev-fixed'),
                  bank=bank_of(question('a')), bank_hash='hash', **arguments)
        assert service.requests == []


def test_no_selected_questions_is_not_passing() -> None:
    """An empty profile selection is a failure, not a clean finding list."""
    with running_server(successful) as service:
        with pytest.raises(DetectorError, match='No questions selected'):
            check('Draft.', Backend(service.url, 'jev-fixed'),
                  bank_of(question('a', 'strict')), 'hash')
        assert service.requests == []


@pytest.mark.parametrize(('field', 'value'), [
    ('id', ''), ('id', 'Bad ID'), ('title', ''), ('question', '  '),
    ('guidance', None), ('scope', 'unknown'), ('profiles', []),
    ('profiles', ['unknown']), ('profiles', [1]), ('sources', []),
    ('sources', ['']), ('sources', [1]),
])
def test_invalid_question_bank_fields(
    tmp_path: Path, field: str, value: Any,
) -> None:
    """Malformed bank metadata cannot enter inference."""
    bank = bank_of(question('a'))
    bank['questions'][0][field] = value
    path = tmp_path / 'bank.json'
    path.write_text(json.dumps(bank))
    with pytest.raises(DetectorError):
        load_bank(path)


@pytest.mark.parametrize('bank', [
    [], {}, {'version': 1, 'questions': []},
    {'version': 'v1', 'questions': []}, {'version': 'v1', 'questions': [None]},
    bank_of(question('a'), question('a')),
])
def test_invalid_bank_structure(tmp_path: Path, bank: Any) -> None:
    """Require a nonempty versioned bank with unique question IDs."""
    path = tmp_path / 'bank.json'
    path.write_text(json.dumps(bank))
    with pytest.raises(DetectorError):
        load_bank(path)


def test_packaged_bank_validates_and_hashes() -> None:
    """The shipped bank is loadable and its content fingerprint is stable."""
    bank, digest = load_bank()
    repeated, repeated_digest = load_bank()
    assert bank == repeated and digest == repeated_digest
    assert len(digest) == 64
    assert len(bank['questions']) > 20


@pytest.mark.parametrize(('probability', 'exit_code', 'status'), [
    (0.1, 0, 'clean'), (0.95, 1, 'findings'), (None, 2, 'error'),
])
def test_cli_exit_codes_with_real_http(
    tmp_path: Path, probability: float | None, exit_code: int, status: str,
) -> None:
    """The actual CLI process emits complete JSON and distinct exit statuses."""
    bank_path = tmp_path / 'bank.json'
    bank_path.write_text(json.dumps(bank_of(question('a'))))

    def respond(request: dict[str, Any]) -> Reply:
        """Return either valid inference or a literal upstream error."""
        if probability is None:
            return Reply({}, status=503)
        return successful(request, probability)

    with running_server(respond) as service:
        result = subprocess.run(
            [sys.executable, '-m', 'jev_prose.cli', 'check', '--bank',
             str(bank_path), '--url', service.url],
            input='A private draft.', text=True, capture_output=True, timeout=10,
            check=False, env=os.environ.copy(),
        )
        assert len(service.requests) == 1
    assert result.returncode == exit_code, result.stderr
    report = json.loads(result.stdout)
    assert report['ran'] is True
    assert report['ok'] is (exit_code != 2)
    assert report['status'] == status
    assert 'A private draft.' not in result.stdout


def test_cli_invalid_timeout_is_structured_error(tmp_path: Path) -> None:
    """Preflight configuration failures report no inference attempt."""
    path = tmp_path / 'input.txt'
    path.write_text('A draft.')
    result = subprocess.run(
        [sys.executable, '-m', 'jev_prose.cli', 'check', str(path),
         '--timeout', 'nan'], capture_output=True, text=True, timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)['ran'] is False
    assert json.loads(result.stdout)['ok'] is False


def test_partial_failed_batch_joins_other_requests() -> None:
    """One failed batch prevents a report and all started work finishes."""
    barrier = threading.Barrier(2)
    completed: set[str] = set()
    lock = threading.Lock()

    def respond(request: dict[str, Any]) -> Reply:
        """Synchronize two requests and fail exactly one of them."""
        key = next(iter(request['questions']))
        barrier.wait(timeout=5)
        with lock:
            completed.add(key)
        return Reply({}, status=500) if key == 'a' else successful(request)

    with running_server(respond) as service:
        with pytest.raises(DetectorError):
            check('Draft.', Backend(service.url, 'jev-fixed'),
                  bank_of(question('a'), question('b')), 'hash',
                  batch_size=1, workers=2)
        assert completed == {'a', 'b'}
        assert len(service.requests) == 2


def test_invalid_json_bank_and_missing_bank(tmp_path: Path) -> None:
    """Unreadable banks fail explicitly before any inference is possible."""
    path = tmp_path / 'bad.json'
    path.write_text('{broken')
    with pytest.raises(ValueError):
        load_bank(path)
    with pytest.raises(OSError):
        load_bank(tmp_path / 'absent.json')
