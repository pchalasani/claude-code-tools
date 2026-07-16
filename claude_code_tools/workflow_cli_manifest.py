"""Neutral immutable version-1 workflow persistence manifest."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class V1ObjectManifest:
    """Deeply immutable field policy for one persisted version-1 object."""

    required_strings: tuple[str, ...] = ()
    required_integers: tuple[str, ...] = ()
    optional_strings: tuple[str, ...] = ()
    optional_integers: tuple[str, ...] = ()
    optional_booleans: tuple[str, ...] = ()
    timestamp_fields: tuple[str, ...] = ()
    additional_fields: tuple[str, ...] = ()
    null_projection_fields: frozenset[str] = frozenset()

    @property
    def projection_fields(self) -> frozenset[str]:
        """Return every field retained by the observational projection."""
        return frozenset(
            self.required_strings
            + self.required_integers
            + self.optional_strings
            + self.optional_integers
            + self.optional_booleans
            + self.additional_fields
        )


STEP_V1_MANIFEST = V1ObjectManifest(
    required_strings=("fingerprint", "id", "label", "startedAt", "status"),
    required_integers=("attempt",),
    optional_strings=("completedAt", "error", "threadId", "workerStartedAt"),
    optional_integers=("workerPid",),
    timestamp_fields=("startedAt", "completedAt"),
    additional_fields=("result",),
    null_projection_fields=frozenset({"result"}),
)
STATE_V1_MANIFEST = V1ObjectManifest(
    required_strings=(
        "createdAt",
        "cwd",
        "runId",
        "status",
        "updatedAt",
        "workflowHash",
        "workflowPath",
    ),
    required_integers=("concurrency", "version"),
    optional_strings=(
        "completedAt",
        "engineStartedAt",
        "error",
        "pidStartedAt",
        "runnerStartedAt",
        "startedAt",
        "terminalFingerprint",
    ),
    optional_integers=(
        "agentInvocations",
        "defaultAgentTimeoutMs",
        "enginePid",
        "maxAgentInvocations",
        "maxRuntimeMs",
        "pid",
    ),
    optional_booleans=("cleanupPending",),
    timestamp_fields=(
        "completedAt",
        "createdAt",
        "runnerStartedAt",
        "startedAt",
        "updatedAt",
    ),
    additional_fields=("result", "steps"),
    null_projection_fields=frozenset({"result"}),
)
CALLBACK_V1_MANIFEST = V1ObjectManifest(
    required_strings=(
        "createdAt",
        "endpoint",
        "runId",
        "status",
        "threadId",
        "updatedAt",
    ),
    required_integers=("attempts", "timeoutMs", "version"),
    optional_strings=(
        "clientUserMessageId",
        "deadlineAt",
        "deliveredAt",
        "error",
        "lastAttemptAt",
        "notifierStartedAt",
        "terminalCompletedAt",
        "terminalFingerprint",
        "terminalStatus",
        "turnId",
    ),
    optional_integers=("notifierPid",),
    timestamp_fields=(
        "createdAt",
        "deadlineAt",
        "deliveredAt",
        "lastAttemptAt",
        "terminalCompletedAt",
        "updatedAt",
    ),
)
