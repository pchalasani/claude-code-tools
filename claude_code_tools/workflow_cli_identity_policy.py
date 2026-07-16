"""Pure identity policy shared by workflow queries and native observers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Callable, Literal, Protocol

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ABBREVIATED_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8}~[A-Za-z0-9._-]{8}$")
MAX_SUPPORTED_PID = (1 << 31) - 1
MAX_LINUX_START_TICKS = (1 << 64) - 1
MAX_DARWIN_START_SECONDS = (1 << 64) - 1
DOTNET_FILETIME_OFFSET = 504_911_232_000_000_000
MIN_WINDOWS_DATETIME_TICKS = DOTNET_FILETIME_OFFSET
MAX_WINDOWS_DATETIME_TICKS = 3_155_378_975_999_999_999
_LINUX_IDENTITY = re.compile(
    r"linux:[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}:[0-9]+"
)
_LINUX_COMPATIBILITY_IDENTITY = re.compile(r"linux:[0-9]+")
_DARWIN_IDENTITY = re.compile(r"darwin:[0-9]+:[0-9]+")

ProcessStatus = Literal["alive", "dead", "unverifiable"]
ProcessObservation = Literal["orphaned", "stale", "unverifiable"] | None
IdentityKind = Literal["strong", "compatibility", "legacy"]
IdentityFamily = Literal["linux", "darwin", "windows", "legacy"]


def abbreviate_run_id(run_id: str) -> str:
    """Return the canonical compact representation of a run identifier."""
    if len(run_id) <= 17:
        return run_id
    return f"{run_id[:8]}~{run_id[-8:]}"


def display_run_id(
    run_id: str,
    abbreviated_id: str,
    *,
    ambiguous: bool,
) -> str:
    """Choose an actionable full or abbreviated identifier for display."""
    if ambiguous or not ABBREVIATED_RUN_ID_PATTERN.fullmatch(abbreviated_id):
        return run_id
    return abbreviated_id


def colliding_abbreviations(
    identities: Iterable[tuple[str, str]],
) -> frozenset[str]:
    """Return abbreviations that refer to multiple distinct full IDs."""
    owners: dict[str, set[str]] = {}
    for run_id, abbreviated_id in identities:
        owners.setdefault(abbreviated_id, set()).add(run_id)
    return frozenset(
        abbreviated_id for abbreviated_id, run_ids in owners.items() if len(run_ids) > 1
    )


class RunResolutionKind(Enum):
    """Typed outcome of resolving user-provided run identity text."""

    FOUND = "found"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class RunResolution:
    """Result of resolving a full or canonical abbreviated run ID."""

    kind: RunResolutionKind
    requested_id: str
    directory: Path | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcessProbe:
    """Raw process evidence returned by an injected native provider."""

    status: ProcessStatus
    identity: str | None = None
    legacy_identity: str | None = None
    compatibility_identities: tuple[str, ...] = ()
    detail: str | None = None
    legacy_observed: bool = False
    family: IdentityFamily | None = None


@dataclass(frozen=True)
class PersistedProcessIdentity:
    """A validated process identity token read from durable state."""

    token: str
    kind: IdentityKind
    family: IdentityFamily


class ProcessProbeProvider(Protocol):
    """Typed boundary between pure policy and native process observation."""

    def __call__(
        self,
        pid: int,
        *,
        include_legacy: bool,
        remaining_seconds: float | None,
        prior_probe: ProcessProbe | None,
    ) -> ProcessProbe:
        """Return or enrich one PID generation within the remaining budget."""
        ...


def parse_bounded_decimal(
    value: str,
    *,
    maximum: int | None = None,
) -> int | None:
    """Parse a short positive-decimal component without huge integers."""
    if not value or len(value) > 20 or not value.isascii() or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if maximum is None or parsed <= maximum else None


def parse_persisted_identity(token: str) -> PersistedProcessIdentity | None:
    """Parse the process-token grammar shared by workflow observations."""
    if token.startswith("linux:"):
        if _LINUX_IDENTITY.fullmatch(token):
            ticks = parse_bounded_decimal(
                token.rsplit(":", 1)[1],
                maximum=MAX_LINUX_START_TICKS,
            )
            kind: IdentityKind = "strong"
        elif _LINUX_COMPATIBILITY_IDENTITY.fullmatch(token):
            ticks = parse_bounded_decimal(
                token.removeprefix("linux:"),
                maximum=MAX_LINUX_START_TICKS,
            )
            kind = "compatibility"
        else:
            return None
        if ticks is None or ticks <= 0:
            return None
        return PersistedProcessIdentity(token, kind, "linux")
    if token.startswith("darwin:"):
        if not _DARWIN_IDENTITY.fullmatch(token):
            return None
        raw_seconds, raw_microseconds = token.split(":")[1:]
        seconds = parse_bounded_decimal(
            raw_seconds,
            maximum=MAX_DARWIN_START_SECONDS,
        )
        microseconds = parse_bounded_decimal(
            raw_microseconds,
            maximum=999_999,
        )
        if seconds is None or seconds <= 0 or microseconds is None:
            return None
        return PersistedProcessIdentity(token, "strong", "darwin")
    if token.isascii() and token.isdigit():
        ticks = parse_bounded_decimal(
            token,
            maximum=MAX_WINDOWS_DATETIME_TICKS,
        )
        if ticks is None or ticks < MIN_WINDOWS_DATETIME_TICKS:
            return None
        return PersistedProcessIdentity(token, "strong", "windows")
    return PersistedProcessIdentity(token, "legacy", "legacy")


def parse_persisted_identity_claim(
    pid: int,
    token: str,
) -> PersistedProcessIdentity | None:
    """Parse a complete bounded PID and persisted-identity claim."""
    if pid <= 0 or pid > MAX_SUPPORTED_PID:
        return None
    return parse_persisted_identity(token)


def compare_persisted_identity(
    persisted: PersistedProcessIdentity,
    probe: ProcessProbe,
) -> ProcessObservation:
    """Compare one parsed persisted claim with one raw process snapshot."""
    observed_identity = (
        parse_persisted_identity(probe.identity) if probe.identity is not None else None
    )
    observed_family = probe.family
    if observed_family is None and observed_identity is not None:
        observed_family = observed_identity.family
    if (
        persisted.family != "legacy"
        and observed_family is not None
        and observed_family != persisted.family
    ):
        return "unverifiable"
    if probe.status == "dead":
        return "orphaned"
    if probe.status == "unverifiable":
        return "unverifiable"
    if probe.identity == persisted.token:
        return "unverifiable" if persisted.kind == "compatibility" else None
    compatibility = set(probe.compatibility_identities)
    if probe.legacy_identity is not None:
        compatibility.add(probe.legacy_identity)
    if persisted.token in compatibility:
        return "unverifiable"
    comparable_compatibility = any(
        parsed is not None and parsed.family == persisted.family
        for token in probe.compatibility_identities
        if (parsed := parse_persisted_identity(token)) is not None
    )
    if persisted.kind == "compatibility" and comparable_compatibility:
        return "stale"
    if (
        persisted.kind == "strong"
        and observed_identity is not None
        and observed_identity.kind == "strong"
        and observed_identity.family == persisted.family
    ):
        return "stale"
    return "unverifiable"


def compare_process_claim(
    pid: int,
    token: str,
    probe: ProcessProbe,
) -> ProcessObservation:
    """Validate and compare one durable claim against supplied evidence."""
    persisted = parse_persisted_identity_claim(pid, token)
    if persisted is None:
        return "unverifiable"
    return compare_persisted_identity(persisted, probe)


@dataclass
class ProcessObservationContext:
    """Cache process evidence acquired only through an injected provider."""

    provider: ProcessProbeProvider
    deadline: float | None = None
    clock: Callable[[], float] = monotonic
    skipped: int = 0
    _probes: dict[int, ProcessProbe] | None = None

    def __post_init__(self) -> None:
        """Create private mutable cache state for this observation."""
        if self._probes is None:
            self._probes = {}

    @property
    def complete(self) -> bool:
        """Return whether every valid requested claim was probed."""
        return self.skipped == 0

    def classify(self, pid: int, token: str) -> ProcessObservation:
        """Classify one persisted ownership claim without side effects."""
        persisted = parse_persisted_identity_claim(pid, token)
        if persisted is None:
            return "unverifiable"
        include_legacy = persisted.kind == "legacy"
        assert self._probes is not None
        probe = self._probes.get(pid)
        needs_probe = probe is None
        needs_enrichment = (
            probe is not None and include_legacy and not probe.legacy_observed
        )
        if needs_probe or needs_enrichment:
            remaining = self._remaining_seconds()
            if remaining is not None and remaining <= 0:
                self.skipped += 1
                return "unverifiable"
            probe = self.provider(
                pid,
                include_legacy=include_legacy,
                remaining_seconds=remaining,
                prior_probe=probe,
            )
            self._probes[pid] = probe
            if include_legacy and not probe.legacy_observed:
                self.skipped += 1
        return compare_persisted_identity(persisted, probe)

    def _remaining_seconds(self) -> float | None:
        """Return the nonnegative process-observation budget remainder."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - self.clock())
