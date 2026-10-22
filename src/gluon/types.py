"""Experimental types and diagnostics. RDF values are plain JSON dicts; no store dependency.

The host contract adds per-request snapshot identity, declared qualifiers and filters,
alias predicates, limit units, inspection profiles and the embedded search record. Names
follow Python conventions (snake_case keys).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Iterable, Literal as L, TypedDict, NotRequired

# --- RDF terms (JSON shape) -------------------------------------------------

class Iri(TypedDict):
    type: L["iri"]
    value: str

class Literal(TypedDict):
    type: L["literal"]
    value: str
    datatype: NotRequired[str]
    language: NotRequired[str]
    truncated: NotRequired[bool]
    length: NotRequired[int]

class Variable(TypedDict):
    type: L["variable"]
    value: str

class Blank(TypedDict):
    type: L["blank"]
    value: str

class ScopedBlank(Blank):
    scope: str

RdfTerm = Iri | Literal | Blank

# --- Configuration ------------------------------------------------------------

class TriplePattern(TypedDict):
    subject: Iri | Variable
    predicate: Iri
    object: Iri | Variable | Literal

class Qualifier(TypedDict):
    """An opaque named projection attached to a node the binding already binds."""
    name: str
    subject: Iri | Variable
    predicate: Iri
    object: Iri | Variable | Literal
    required: bool
    multiple: L["expand", "collect"]

class Filter(TypedDict, total=False):
    """Restrict a qualifier to listed values; `unbound` applies to optional qualifiers."""
    values: list[Iri | Literal]
    unbound: L["include", "exclude"]

class Binding(TypedDict):
    id: str
    label_predicates: list[str]
    patterns: list[TriplePattern]
    qualifiers: NotRequired[list[Qualifier]]
    filters: NotRequired[dict[str, Filter]]
    languages: NotRequired[list[str]]

class TermDefinition(TypedDict):
    id: str
    definition: str
    binding: Binding

class InspectionProfile(TypedDict):
    """Follow one configured property from the entity, then report properties of the reached node."""
    id: str
    direction: L["in", "out"]
    predicate: str
    report: list[str]

class ScopeDefinition(TypedDict):
    id: str
    source: str
    graph: str
    terms: list[TermDefinition]
    profiles: NotRequired[list[InspectionProfile]]

class Limits(TypedDict):
    probe_rows: int
    inspect_rows: int
    deadline_ms: int
    unit: NotRequired[L["rows", "entities"]]
    per_entity_rows: NotRequired[int]
    literal_chars: NotRequired[int]
    inspect_batch: NotRequired[int]

class Configuration(TypedDict):
    scopes: list[ScopeDefinition]
    limits: Limits

# --- Executor protocol --------------------------------------------------------

Row = dict[str, RdfTerm]


class CancelSignal:
    """Cooperative cancellation flag shared between the core, the caller and the executor.

    The core sets the signal it hands to the executor when a deadline passes, the caller
    cancels, or the call finishes. A host checks ``is_set()`` between rows and should stop
    yielding; the core cannot preempt a synchronous store that never checks.
    """

    __slots__ = ("_event", "_parent")

    def __init__(self, parent: "CancelSignal | None" = None) -> None:
        self._event = threading.Event()
        self._parent = parent

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set() or (self._parent is not None and self._parent.is_set())

    @property
    def cancelled(self) -> bool:
        return self.is_set()


class SelectRequest:
    """Complete read-only SELECT handed to the host executor, with scope identity and cancellation.

    ``snapshot`` is the caller's opaque revision identity or ``None``; the host selects
    that revision and must never substitute another.
    """

    __slots__ = ("query", "source", "scope", "graph", "signal", "deadline", "snapshot")

    def __init__(self, query: str, source: str, scope: str, graph: str, signal: CancelSignal, deadline: float,
                 snapshot: str | None = None) -> None:
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "graph", graph)
        object.__setattr__(self, "signal", signal)
        # time.monotonic() value after which the core stops accepting rows
        object.__setattr__(self, "deadline", deadline)
        object.__setattr__(self, "snapshot", snapshot)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("SelectRequest is read-only")

    def __repr__(self) -> str:
        return f"SelectRequest(source={self.source!r}, scope={self.scope!r}, graph={self.graph!r}, snapshot={self.snapshot!r})"


# Must honor scope, snapshot and cancellation, and release resources when the iterator is closed.
SelectExecutor = Callable[[SelectRequest], Iterable[Row]]

# --- Requests and reports -----------------------------------------------------

class CallOptions(TypedDict, total=False):
    snapshot: str
    limit: int
    deadline_ms: int
    signal: CancelSignal

class ProbeRequest(CallOptions):
    scope: str
    type: str
    text: NotRequired[str]
    iri: NotRequired[str]
    filters: NotRequired[dict[str, Filter]]

class InspectRequest(CallOptions):
    scope: str
    iri: str
    profile: NotRequired[str]
    direction: NotRequired[L["in", "out"]]
    predicate: NotRequired[str]

class InspectManyRequest(CallOptions):
    scope: str
    iris: list[str]
    profile: NotRequired[str]
    direction: NotRequired[L["in", "out"]]
    predicate: NotRequired[str]

class Completeness(TypedDict):
    complete: bool
    truncated: bool
    unit: L["rows", "entities"]
    row_limit: int
    fetch_limit: int
    fetched_rows: int
    entity_limit: NotRequired[int]
    fetched_entities: NotRequired[int]
    per_entity_limit: NotRequired[int]
    truncated_entities: NotRequired[list[str]]

class SearchRecord(TypedDict):
    """Self-contained, replayable identity of one search."""
    version: int
    gluon: str
    operation: L["probe", "inspect"]
    configuration_hash: str
    binding_hash: NotRequired[str]
    scope: str
    graph: str
    snapshot: str | None
    request: dict[str, Any]
    query: str

class QueryReport(TypedDict):
    source: str
    scope: str
    graph: str
    snapshot: str | None
    query: str
    completeness: Completeness
    record: SearchRecord

class InspectRoute(TypedDict):
    scope: str
    iri: str

class Match(TypedDict):
    predicate: str
    kind: L["exact", "substring", "iri"]
    label: NotRequired[Literal]

class Candidate(TypedDict):
    iri: str
    labels: list[Literal]
    matches: list[Match]
    qualifiers: dict[str, Any]
    inspect: InspectRoute

class Matching(TypedDict):
    strategy: L["literal-substring", "iri"]
    text: NotRequired[str]
    iri: NotRequired[str]
    label_predicates: list[str]
    languages: list[str] | None

class ProbeReport(QueryReport):
    outcome: L["matched", "no_match"]
    term: TermDefinition
    matching: Matching
    filters: dict[str, Any]
    candidates: list[Candidate]

class Statement(TypedDict):
    direction: L["in", "out"]
    predicate: str
    neighbor: Iri | Literal | ScopedBlank

class ProfileStatement(TypedDict):
    node: Iri | ScopedBlank
    predicate: str
    value: Iri | Literal | ScopedBlank

class InspectReport(QueryReport):
    iri: str
    profile: str | None
    filter: dict[str, Any] | None
    statements: list[Statement] | list[ProfileStatement]

# --- Diagnostics --------------------------------------------------------------

ErrorCode = L[
    "INVALID_CONFIG", "INVALID_REQUEST", "UNKNOWN_SCOPE", "UNKNOWN_TERM",
    "SOURCE_UNAVAILABLE", "SOURCE_ERROR", "INVALID_RESULT", "DEADLINE_EXCEEDED", "CANCELLED",
]

MESSAGES: dict[str, str] = {
    "INVALID_CONFIG": "Invalid or unsupported configuration.",
    "INVALID_REQUEST": "Invalid or unsupported request.",
    "UNKNOWN_SCOPE": "Scope is not configured for this instance.",
    "UNKNOWN_TERM": "Term is not configured in this scope.",
    "SOURCE_UNAVAILABLE": "The configured source is unavailable.",
    "SOURCE_ERROR": "The source could not execute the query.",
    "INVALID_RESULT": "The source returned an invalid result.",
    "DEADLINE_EXCEEDED": "The query deadline was exceeded.",
    "CANCELLED": "The request was cancelled.",
}


class DiscoveryError(Exception):
    """Stable diagnostic codes.

    ``path``/``detail`` describe the caller's own input (configuration or request) and
    ``options`` lists valid alternatives where known. Source-side failures carry none of
    these: no raw backend error, message or cause is ever propagated.
    """

    def __init__(self, code: ErrorCode, path: str | None = None, detail: str | None = None,
                 options: list[str] | None = None) -> None:
        if code in ("SOURCE_ERROR", "SOURCE_UNAVAILABLE", "INVALID_RESULT"):
            path = detail = options = None
        message = MESSAGES[code]
        if path:
            message += f" At '{path}'."
        if detail:
            message += f" {detail}"
        if options:
            message += " Valid options: " + ", ".join(options) + "."
        super().__init__(message)
        self.code: ErrorCode = code
        self.path = path
        self.detail = detail
        self.options = list(options) if options else None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.path:
            out["path"] = self.path
        if self.detail:
            out["detail"] = self.detail
        if self.options:
            out["options"] = self.options
        return out


class SourceUnavailableError(Exception):
    """A host executor may raise this marker; its message and cause are never propagated."""
