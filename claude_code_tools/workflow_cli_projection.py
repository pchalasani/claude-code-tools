"""Bounded streaming projection for hostile workflow-store JSON."""

from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, cast

from claude_code_tools.workflow_cli_manifest import (
    CALLBACK_V1_MANIFEST,
    STATE_V1_MANIFEST,
    STEP_V1_MANIFEST,
)

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_STATE_JSON_BYTES = 16 * 1024 * 1024 * 1024
MAX_PROJECTED_JSON_NODES = 250_000
READ_CHUNK_BYTES = 64 * 1024
PROJECTION_CHUNK_BYTES = 64 * 1024


class ProjectionBudget(Protocol):
    """Aggregate limits consumed by a sequence of projections."""

    @property
    def remaining_bytes(self) -> int:
        """Return raw bytes remaining in the observation."""
        ...

    def charge(self, byte_count: int) -> None:
        """Charge raw bytes read from the store."""

    def charge_nodes(self, node_count: int = 1) -> None:
        """Charge source JSON nodes parsed from the store."""

    def charge_retained(self, byte_count: int) -> None:
        """Charge bytes retained after projection."""


@dataclass(frozen=True)
class ProjectionSpec:
    """Describe keys retained from one JSON object.

    ``fields=None`` retains every key. Child specs constrain nested objects;
    ``wildcard_child`` constrains values under arbitrary keys, such as step IDs.
    Fields without a child spec retain their complete value.
    """

    fields: frozenset[str] | None = None
    children: tuple[tuple[str, ProjectionSpec], ...] = ()
    wildcard_child: ProjectionSpec | None = None
    null_fields: frozenset[str] = frozenset()

    def includes(self, key: str) -> bool:
        """Return whether an object member belongs in the projection."""
        return self.fields is None or key in self.fields

    def child(self, key: str) -> ProjectionSpec | None:
        """Return the projection for a retained child value, if any."""
        for child_key, child_spec in self.children:
            if key == child_key:
                return child_spec
        return self.wildcard_child


STEP_PROJECTION = ProjectionSpec(
    fields=STEP_V1_MANIFEST.projection_fields,
    null_fields=STEP_V1_MANIFEST.null_projection_fields,
)
STEPS_PROJECTION = ProjectionSpec(wildcard_child=STEP_PROJECTION)
STATE_PROJECTION = ProjectionSpec(
    fields=STATE_V1_MANIFEST.projection_fields,
    children=(("steps", STEPS_PROJECTION),),
    null_fields=STATE_V1_MANIFEST.null_projection_fields,
)
CALLBACK_PROJECTION = ProjectionSpec(fields=CALLBACK_V1_MANIFEST.projection_fields)
FULL_PROJECTION = ProjectionSpec()


def _reject_constant(value: str) -> None:
    """Reject Python's non-standard NaN and Infinity extensions."""
    raise ValueError(f"invalid JSON constant {value!r}")


class CharacterStream:
    """Incrementally decode UTF-8 while accounting for raw bytes read."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        maximum_bytes: int,
        budget: ProjectionBudget | None,
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._budget = budget
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._buffer = ""
        self._position = 0
        self._raw_bytes = 0
        self._finished = False

    def _fill(self) -> bool:
        """Decode another nonempty character chunk, if one remains."""
        while self._position >= len(self._buffer) and not self._finished:
            read_size = READ_CHUNK_BYTES
            if self._budget is not None:
                read_size = min(
                    read_size,
                    max(1, self._budget.remaining_bytes + 1),
                )
            raw = self._stream.read(read_size)
            if raw:
                self._raw_bytes += len(raw)
                if self._raw_bytes > self._maximum_bytes:
                    raise ValueError(f"JSON file exceeds {self._maximum_bytes} bytes")
                if self._budget is not None:
                    self._budget.charge(len(raw))
                self._buffer = self._decoder.decode(raw, final=False)
                self._position = 0
            else:
                self._buffer = self._decoder.decode(b"", final=True)
                self._position = 0
                self._finished = True
        return self._position < len(self._buffer)

    def peek(self) -> str | None:
        """Return the next decoded character without consuming it."""
        return self._buffer[self._position] if self._fill() else None

    def take(self) -> str:
        """Consume and return one decoded character."""
        value = self.peek()
        if value is None:
            raise ValueError("unexpected end of JSON input")
        self._position += 1
        return value

    def take_plain_string_chunk(self) -> str:
        """Consume a maximal string segment without quote, escape, or control."""
        if not self._fill():
            return ""
        start = self._position
        end = len(self._buffer)
        while self._position < end:
            value = self._buffer[self._position]
            if value in {'"', "\\"} or ord(value) < 0x20:
                break
            self._position += 1
        return self._buffer[start : self._position]

    def take_whitespace_chunk(self) -> str:
        """Consume a maximal decoded JSON-whitespace segment."""
        if not self._fill():
            return ""
        start = self._position
        end = len(self._buffer)
        while self._position < end and self._buffer[self._position] in " \t\r\n":
            self._position += 1
        return self._buffer[start : self._position]


class ProjectionWriter:
    """Collect projected JSON text in bounded allocation chunks."""

    def __init__(self, maximum_bytes: int) -> None:
        self._maximum_bytes = maximum_bytes
        self._byte_count = 0
        self._chunks: list[bytes] = []
        self._pending = bytearray()

    @property
    def byte_count(self) -> int:
        """Return encoded bytes retained so far."""
        return self._byte_count

    def _flush(self) -> None:
        if self._pending:
            self._chunks.append(bytes(self._pending))
            self._pending.clear()

    def append(self, value: str) -> None:
        """Append text unless the retained-data cap would be exceeded."""
        encoded = value.encode("utf-8")
        next_count = self._byte_count + len(encoded)
        if next_count > self._maximum_bytes:
            raise ValueError(
                "projected JSON object exceeds the retained-data limit of "
                f"{self._maximum_bytes} bytes"
            )
        self._byte_count = next_count
        position = 0
        while position < len(encoded):
            available = PROJECTION_CHUNK_BYTES - len(self._pending)
            end = min(position + available, len(encoded))
            self._pending.extend(encoded[position:end])
            position = end
            if len(self._pending) == PROJECTION_CHUNK_BYTES:
                self._flush()

    def value(self) -> str:
        """Return the completed projected JSON text."""
        self._flush()
        return b"".join(self._chunks).decode("utf-8")


@dataclass
class LocalNodeBudget:
    """Bound source structure when no aggregate observation budget exists."""

    maximum_nodes: int = MAX_PROJECTED_JSON_NODES
    consumed_nodes: int = 0

    def charge_nodes(self, node_count: int = 1) -> None:
        """Account for source keys and values before materialization."""
        self.consumed_nodes += node_count
        if self.consumed_nodes > self.maximum_nodes:
            raise ValueError(
                f"JSON input exceeds the structural limit of {self.maximum_nodes} nodes"
            )


class Parser:
    """Validate all input while materializing only an explicit projection."""

    def __init__(
        self,
        source: CharacterStream,
        writer: ProjectionWriter,
        nodes: ProjectionBudget | LocalNodeBudget,
    ) -> None:
        self.source = source
        self.writer = writer
        self.nodes = nodes

    def whitespace(self, destination: ProjectionWriter | None) -> None:
        """Consume JSON whitespace and optionally retain it."""
        while value := self.source.take_whitespace_chunk():
            if destination is not None:
                destination.append(value)

    def expect(self, expected: str) -> None:
        """Consume one required punctuation character."""
        observed = self.source.take()
        if observed != expected:
            raise ValueError(f"expected {expected!r}, got {observed!r}")

    def string(
        self,
        destination: ProjectionWriter | None,
        *,
        capture: bool = False,
    ) -> str | None:
        """Parse a string, scanning discarded plain text one chunk at a time."""
        self.expect('"')
        captured = ProjectionWriter(MAX_JSON_BYTES) if capture else None
        if destination is not None:
            destination.append('"')
        if captured is not None:
            captured.append('"')
        while True:
            chunk = self.source.take_plain_string_chunk()
            if chunk:
                if destination is not None:
                    destination.append(chunk)
                if captured is not None:
                    captured.append(chunk)
            value = self.source.take()
            if ord(value) < 0x20:
                raise ValueError("unescaped control character in JSON string")
            if destination is not None:
                destination.append(value)
            if captured is not None:
                captured.append(value)
            if value == '"':
                break
            if value != "\\":
                continue
            escaped = self.source.take()
            if escaped not in '"\\/bfnrtu':
                raise ValueError(f"invalid JSON escape {escaped!r}")
            if destination is not None:
                destination.append(escaped)
            if captured is not None:
                captured.append(escaped)
            if escaped == "u":
                for _ in range(4):
                    digit = self.source.take()
                    if digit not in "0123456789abcdefABCDEF":
                        raise ValueError("invalid Unicode escape in JSON string")
                    if destination is not None:
                        destination.append(digit)
                    if captured is not None:
                        captured.append(digit)
        if captured is None:
            return None
        decoded: Any = json.loads(
            captured.value(),
            parse_constant=_reject_constant,
        )
        return cast(str, decoded)

    def scalar(self, destination: ProjectionWriter | None) -> None:
        """Parse a JSON number, boolean, or null token."""
        pieces: list[str] = []
        while (value := self.source.peek()) is not None and value not in " \t\r\n,]}":
            pieces.append(self.source.take())
            if len(pieces) > 4_096:
                raise ValueError("JSON scalar token exceeds 4096 characters")
        token = "".join(pieces)
        if not token:
            raise ValueError("expected a JSON value")
        json.loads(token, parse_constant=_reject_constant)
        if destination is not None:
            destination.append(token)

    def array(
        self,
        destination: ProjectionWriter | None,
        spec: ProjectionSpec | None,
    ) -> None:
        """Parse an array, retaining it only when requested."""
        self.expect("[")
        if destination is not None:
            destination.append("[")
        self.whitespace(destination)
        if self.source.peek() == "]":
            self.source.take()
            if destination is not None:
                destination.append("]")
            return
        while True:
            self.value(destination, spec)
            self.whitespace(destination)
            separator = self.source.take()
            if separator == "]":
                if destination is not None:
                    destination.append("]")
                return
            if separator != ",":
                raise ValueError(f"expected ',' or ']', got {separator!r}")
            if destination is not None:
                destination.append(",")
            self.whitespace(destination)

    def object(
        self,
        destination: ProjectionWriter | None,
        spec: ProjectionSpec | None,
    ) -> None:
        """Parse an object and retain only keys selected by ``spec``."""
        self.expect("{")
        if destination is not None:
            destination.append("{")
        self.whitespace(None)
        if self.source.peek() == "}":
            self.source.take()
            if destination is not None:
                destination.append("}")
            return
        first_retained = True
        while True:
            self.nodes.charge_nodes()
            key = self.string(None, capture=True)
            assert key is not None
            self.whitespace(None)
            self.expect(":")
            self.whitespace(None)
            retain = destination is not None and (spec is None or spec.includes(key))
            child_spec = None if spec is None else spec.child(key)
            if retain:
                assert destination is not None
                if not first_retained:
                    destination.append(",")
                destination.append(json.dumps(key, ensure_ascii=True))
                destination.append(":")
                if spec is not None and key in spec.null_fields:
                    self.value(None, child_spec)
                    destination.append("null")
                else:
                    self.value(destination, child_spec)
                first_retained = False
            else:
                self.value(None, child_spec)
            self.whitespace(None)
            separator = self.source.take()
            if separator == "}":
                if destination is not None:
                    destination.append("}")
                return
            if separator != ",":
                raise ValueError(f"expected ',' or '}}', got {separator!r}")
            self.whitespace(None)

    def value(
        self,
        destination: ProjectionWriter | None,
        spec: ProjectionSpec | None,
    ) -> None:
        """Parse one complete JSON value."""
        self.nodes.charge_nodes()
        value = self.source.peek()
        if value == "{":
            self.object(destination, spec)
        elif value == "[":
            self.array(destination, spec)
        elif value == '"':
            self.string(destination)
        else:
            self.scalar(destination)


def project_mapping(
    stream: BinaryIO,
    *,
    maximum_input_bytes: int,
    budget: ProjectionBudget | None,
    spec: ProjectionSpec = FULL_PROJECTION,
) -> dict[str, object]:
    """Decode a bounded object after applying an explicit field projection."""
    source = CharacterStream(
        stream,
        maximum_bytes=maximum_input_bytes,
        budget=budget,
    )
    destination = ProjectionWriter(MAX_JSON_BYTES)
    nodes: ProjectionBudget | LocalNodeBudget = budget or LocalNodeBudget()
    parser = Parser(source, destination, nodes)
    parser.whitespace(None)
    parser.value(destination, spec)
    parser.whitespace(None)
    if source.peek() is not None:
        raise ValueError("extra data after JSON object")
    if budget is not None:
        budget.charge_retained(destination.byte_count)
    value: Any = json.loads(
        destination.value(),
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def project_state_mapping(
    stream: BinaryIO,
    *,
    budget: ProjectionBudget | None,
) -> dict[str, object]:
    """Project a durable state while discarding results and unknown fields."""
    return project_mapping(
        stream,
        maximum_input_bytes=MAX_STATE_JSON_BYTES,
        budget=budget,
        spec=STATE_PROJECTION,
    )


def project_callback_mapping(
    stream: BinaryIO,
    *,
    budget: ProjectionBudget | None,
) -> dict[str, object]:
    """Project callback metadata to its version-1 observation fields."""
    return project_mapping(
        stream,
        maximum_input_bytes=MAX_JSON_BYTES,
        budget=budget,
        spec=CALLBACK_PROJECTION,
    )
