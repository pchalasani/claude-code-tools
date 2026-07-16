"""Focused regressions for bounded, cross-platform process observation."""

from __future__ import annotations

import ctypes
import errno
from typing import Callable

import pytest

from claude_code_tools import workflow_cli_identity_policy, workflow_processes


class _FakeNativeFunction:
    """Callable native-function stand-in with assignable ctypes metadata."""

    def __init__(
        self,
        result: int,
        callback: Callable[..., None] | None = None,
    ) -> None:
        self.argtypes: list[object] = []
        self.restype: object | None = None
        self._result = result
        self._callback = callback

    def __call__(self, *args: object) -> int:
        """Apply the configured errno side effect and return a native result."""
        if self._callback is not None:
            self._callback(*args)
        return self._result


def test_process_policy_observes_only_through_injected_provider() -> None:
    """Pure identity policy has no implicit platform-probe dependency."""
    calls: list[tuple[int, bool, float | None]] = []

    def provider(
        pid: int,
        *,
        include_legacy: bool,
        remaining_seconds: float | None,
        prior_probe: workflow_cli_identity_policy.ProcessProbe | None,
    ) -> workflow_cli_identity_policy.ProcessProbe:
        assert prior_probe is None
        calls.append((pid, include_legacy, remaining_seconds))
        return workflow_cli_identity_policy.ProcessProbe(
            "alive",
            identity="darwin:100:200",
            family="darwin",
        )

    context = workflow_cli_identity_policy.ProcessObservationContext(
        provider=provider,
        deadline=10.0,
        clock=lambda: 4.0,
    )

    assert context.classify(123, "darwin:100:200") is None
    assert calls == [(123, False, 6.0)]


def test_windows_identity_parsing_is_host_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copied Windows ticks remain a foreign strong claim on POSIX hosts."""
    monkeypatch.setattr(workflow_processes, "_IS_WINDOWS", False)

    persisted = workflow_processes.parse_persisted_identity("638881920000000000")

    assert persisted is not None
    assert persisted.kind == "strong"
    assert persisted.family == "windows"


def test_non_ascii_decimal_is_not_a_strong_windows_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only API-produced ASCII digits enter the Windows identity grammar."""
    monkeypatch.setattr(workflow_processes, "_IS_WINDOWS", True)

    persisted = workflow_processes.parse_persisted_identity("٦٣٨٨٨١")

    assert persisted is not None
    assert persisted.kind == "legacy"


def test_windows_identity_below_native_epoch_is_rejected() -> None:
    """A value the FILETIME conversion cannot emit cannot prove PID reuse."""
    minimum = workflow_processes._MIN_WINDOWS_DATETIME_TICKS

    assert workflow_processes.parse_persisted_identity(str(minimum - 1)) is None
    persisted = workflow_processes.parse_persisted_identity(str(minimum))
    assert persisted is not None
    assert persisted.kind == "strong"
    probe = workflow_processes.ProcessProbe(
        "alive",
        identity=str(minimum),
        family="windows",
    )
    assert (
        workflow_processes.observe_persisted_identity(123, "1", probe=probe)
        == "unverifiable"
    )


@pytest.mark.parametrize(
    ("token", "observed"),
    [
        (
            "linux:18446744073709551616",
            "linux:00000000-0000-0000-0000-000000000000:1",
        ),
        (
            ("linux:00000000-0000-0000-0000-000000000000:18446744073709551616"),
            "linux:00000000-0000-0000-0000-000000000000:1",
        ),
        ("darwin:18446744073709551616:0", "darwin:1:0"),
        ("3155378976000000000", "1"),
    ],
)
def test_impossible_native_identity_values_are_rejected(
    token: str,
    observed: str,
) -> None:
    """Values beyond native source ranges cannot prove PID reuse."""
    assert workflow_processes.parse_persisted_identity(token) is None
    probe = workflow_processes.ProcessProbe("alive", identity=observed)
    assert (
        workflow_processes.observe_persisted_identity(123, token, probe=probe)
        == "unverifiable"
    )


@pytest.mark.parametrize(
    "token",
    [
        "linux:18446744073709551615",
        ("linux:00000000-0000-0000-0000-000000000000:18446744073709551615"),
        "darwin:18446744073709551615:999999",
        "3155378975999999999",
    ],
)
def test_native_identity_range_boundaries_remain_valid(token: str) -> None:
    """Each family accepts the largest value its native source can emit."""
    assert workflow_processes.parse_persisted_identity(token) is not None


def test_darwin_esrch_from_libproc_proves_pid_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleared-errno ESRCH result classifies an exited Darwin process."""

    def missing_process(*_args: object) -> None:
        ctypes.set_errno(errno.ESRCH)

    class EmptyLibproc:
        """Darwin API stand-in for a PID that exited before observation."""

        proc_pidinfo = _FakeNativeFunction(0, missing_process)

    monkeypatch.setattr(workflow_processes.sys, "platform", "darwin")
    monkeypatch.setattr(
        workflow_processes.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: EmptyLibproc(),
    )

    probe = workflow_processes._darwin_process_identity(999_999_999)

    assert probe is not None
    assert probe.status == "dead"


def test_darwin_permission_failure_remains_unverifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A libproc permission error is not mistaken for process absence."""

    def hidden_process(*_args: object) -> None:
        ctypes.set_errno(errno.EPERM)

    class HiddenLibproc:
        """Darwin API stand-in for process metadata hidden by policy."""

        proc_pidinfo = _FakeNativeFunction(0, hidden_process)

    monkeypatch.setattr(workflow_processes.sys, "platform", "darwin")
    monkeypatch.setattr(
        workflow_processes.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: HiddenLibproc(),
    )

    probe = workflow_processes._darwin_process_identity(123)

    assert probe is not None
    assert probe.status == "unverifiable"
    assert probe.detail is not None
    assert "errno 1" in probe.detail


@pytest.mark.parametrize(
    ("persisted", "observed"),
    [
        (
            "linux:00000000-0000-0000-0000-000000000000:123",
            "darwin:100:200",
        ),
        ("darwin:100:200", "638881920000000000"),
        (
            "638881920000000000",
            "linux:00000000-0000-0000-0000-000000000000:123",
        ),
    ],
)
def test_cross_platform_strong_identities_are_unverifiable(
    persisted: str,
    observed: str,
) -> None:
    """Incomparable platform clocks cannot prove that a PID was reused."""
    probe = workflow_processes.ProcessProbe("alive", identity=observed)

    assert (
        workflow_processes.observe_persisted_identity(
            123,
            persisted,
            probe=probe,
        )
        == "unverifiable"
    )


def test_foreign_identity_with_dead_local_pid_is_unverifiable() -> None:
    """Local PID absence says nothing about a copied foreign process claim."""
    probe = workflow_processes.ProcessProbe("dead", family="darwin")

    assert (
        workflow_processes.observe_persisted_identity(
            123,
            "638881920000000000",
            probe=probe,
        )
        == "unverifiable"
    )


def test_observation_context_passes_remaining_budget_to_provider() -> None:
    """The aggregate deadline becomes the provider's blocking-call budget."""
    remaining: list[float | None] = []

    def bounded_provider(
        pid: int,
        *,
        include_legacy: bool,
        remaining_seconds: float | None,
        prior_probe: workflow_processes.ProcessProbe | None,
    ) -> workflow_processes.ProcessProbe:
        del pid, include_legacy, prior_probe
        remaining.append(remaining_seconds)
        return workflow_processes.ProcessProbe(
            "alive",
            identity="darwin:100:200",
        )

    context = workflow_processes.ObservationContext(
        deadline=10.0,
        probe_factory=bounded_provider,
        clock=lambda: 3.25,
    )

    assert context.classify(123, "darwin:100:200") is None
    assert remaining == [6.75]


def test_unfinished_legacy_enrichment_marks_observation_incomplete() -> None:
    """Budget exhaustion inside a provider remains visible to list callers."""

    def exhausted_provider(
        pid: int,
        *,
        include_legacy: bool,
        remaining_seconds: float | None,
        prior_probe: workflow_processes.ProcessProbe | None,
    ) -> workflow_processes.ProcessProbe:
        del pid, include_legacy, remaining_seconds, prior_probe
        return workflow_processes.ProcessProbe(
            "alive",
            identity="darwin:100:200",
            legacy_observed=False,
        )

    context = workflow_processes.ObservationContext(
        deadline=10.0,
        probe_factory=exhausted_provider,
        clock=lambda: 9.9,
    )

    assert context.classify(123, "Tue Jul 14 10:00:00 2026") == "unverifiable"
    assert context.complete is False
    assert context.skipped == 1


def test_inconclusive_native_probe_runs_legacy_dead_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead legacy probe completes and corrects an inconclusive native one."""
    native_calls: list[int] = []
    legacy_calls: list[tuple[int, float | None]] = []

    def native_probe(pid: int) -> workflow_processes.ProcessProbe:
        native_calls.append(pid)
        return workflow_processes.ProcessProbe(
            "unverifiable",
            detail="native metadata is hidden",
        )

    def legacy_probe(
        pid: int,
        *,
        remaining_seconds: float | None = None,
    ) -> workflow_processes.ProcessProbe:
        legacy_calls.append((pid, remaining_seconds))
        return workflow_processes.ProcessProbe(
            "dead",
            legacy_observed=True,
        )

    monkeypatch.setattr(workflow_processes, "_IS_WINDOWS", False)
    monkeypatch.setattr(
        workflow_processes,
        "_linux_process_identity",
        native_probe,
    )
    monkeypatch.setattr(
        workflow_processes,
        "_legacy_posix_probe",
        legacy_probe,
    )
    context = workflow_processes.ObservationContext(
        deadline=10.0,
        clock=lambda: 4.0,
    )

    assert context.classify(123, "legacy identity") == "orphaned"
    assert context.complete is True
    assert context.skipped == 0
    assert native_calls == [123]
    assert len(legacy_calls) == 1
    assert legacy_calls[0][0] == 123
    remaining = legacy_calls[0][1]
    assert remaining is not None
    assert 0 < remaining <= 6.0


def test_conclusive_native_death_completes_without_legacy_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native process absence needs no compatibility subprocess evidence."""

    def native_probe(_pid: int) -> workflow_processes.ProcessProbe:
        return workflow_processes.ProcessProbe("dead")

    def fail_legacy(
        _pid: int,
        *,
        remaining_seconds: float | None = None,
    ) -> workflow_processes.ProcessProbe:
        del remaining_seconds
        raise AssertionError("conclusive native death must not invoke ps")

    monkeypatch.setattr(workflow_processes, "_IS_WINDOWS", False)
    monkeypatch.setattr(
        workflow_processes,
        "_linux_process_identity",
        native_probe,
    )
    monkeypatch.setattr(
        workflow_processes,
        "_legacy_posix_probe",
        fail_legacy,
    )
    context = workflow_processes.ObservationContext()

    assert context.classify(123, "legacy identity") == "orphaned"
    assert context.complete is True
    assert context.skipped == 0


def test_observation_context_enriches_one_cached_native_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later legacy claim adds ps metadata without a second native probe."""
    native_calls: list[int] = []
    legacy_calls: list[int] = []
    strong_identity = "linux:00000000-0000-0000-0000-000000000000:123"

    def native_probe(pid: int) -> workflow_processes.ProcessProbe:
        native_calls.append(pid)
        return workflow_processes.ProcessProbe(
            "alive",
            identity=strong_identity,
            compatibility_identities=("linux:123",),
        )

    def legacy_probe(
        pid: int,
        *,
        remaining_seconds: float | None = None,
    ) -> workflow_processes.ProcessProbe:
        del remaining_seconds
        legacy_calls.append(pid)
        return workflow_processes.ProcessProbe(
            "alive",
            legacy_identity="Tue Jul 14 10:00:00 2026",
            legacy_observed=True,
        )

    monkeypatch.setattr(
        workflow_processes,
        "_linux_process_identity",
        native_probe,
    )
    monkeypatch.setattr(
        workflow_processes,
        "_legacy_posix_probe",
        legacy_probe,
    )
    context = workflow_processes.ObservationContext()

    assert context.classify(123, strong_identity) is None
    assert context.classify(123, "Tue Jul 14 10:00:00 2026") == "unverifiable"
    assert native_calls == [123]
    assert legacy_calls == [123]
