"""Discovery core: bounded collection and the describe/probe/inspect/labels operations.

Synchronous executor protocol: the host returns an iterable of rows; the core
checks the deadline and the caller's cancellation between rows and after executor setup,
sets the executor's signal, and closes the iterator. It cannot preempt a synchronous
store that never yields; late output is rejected once control returns.

The host contract adds per-request snapshot identity, qualifier projection and grouping,
configured and narrowed filters, alias predicates and match basis, the embedded search
record with replay and diff, limit units and literal caps, inspection profiles, batch
inspection, membership probes by IRI, the `labels` operation and multiple search
expressions. Nothing here attaches meaning to a qualifier name or value.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any, Iterator

from .compiler import fragments, inspect_query, labels_query, probe_query, profile_query
from .types import (CancelSignal, Configuration, DiscoveryError, InspectReport, ProbeReport, Row,
                    ScopeDefinition, SelectExecutor, SelectRequest, SourceUnavailableError, TermDefinition)
from .validation import choice, configuration, fail, iri, keys, positive, rdf, record, request_filters, text

RECORD_VERSION = 2
_INTERRUPTS = ("DEADLINE_EXCEEDED", "CANCELLED")
_OPTIONS = ["snapshot", "limit", "deadline_ms", "signal"]


def canonical_json(value: Any) -> str:
    """Deterministic JSON for reports, records and diagnostics (sorted keys, compact, UTF-8 text)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _close(iterator: Iterator[Any] | None) -> None:
    close = getattr(iterator, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - never let host cleanup failure replace the real outcome
        pass


def collect(execute: SelectExecutor, scope: ScopeDefinition, query: str, cap: int,
            deadline_ms: int, external: CancelSignal | None, snapshot: str | None) -> list[Row]:
    """Bound setup and every pull; release the source even when it arrives late."""
    if external is not None and external.is_set():
        fail("CANCELLED")
    signal = CancelSignal(parent=external)
    expires = time.monotonic() + deadline_ms / 1000.0

    def interruption() -> str | None:
        if external is not None and external.is_set():
            return "CANCELLED"
        if time.monotonic() >= expires:
            return "DEADLINE_EXCEEDED"
        return None

    iterator: Iterator[Any] | None = None
    try:
        iterable = execute(SelectRequest(query, scope["source"], scope["id"], scope["graph"], signal, expires, snapshot))
        code = interruption()
        if iterable is None or not hasattr(iterable, "__iter__"):
            raise TypeError("executor must return an iterable of rows")
        iterator = iter(iterable)
        if code:
            raise DiscoveryError(code)
        rows: list[Row] = []
        while len(rows) <= cap:
            try:
                item = next(iterator)
            except StopIteration:
                break
            code = interruption()
            if code:
                raise DiscoveryError(code)
            rows.append(item)
        return rows
    except DiscoveryError as error:
        if error.code in _INTERRUPTS:
            raise
        # An executor must not speak the core's diagnostic language; treat as a source failure.
        raise DiscoveryError(interruption() or "SOURCE_ERROR") from None
    except SourceUnavailableError:
        raise DiscoveryError(interruption() or "SOURCE_UNAVAILABLE") from None
    except Exception:  # noqa: BLE001 - only the marker above is preserved; never expose host messages or causes
        raise DiscoveryError(interruption() or "SOURCE_ERROR") from None
    finally:
        signal.set()
        _close(iterator)


def _clip(term: dict[str, Any], literal_chars: int | None) -> dict[str, Any]:
    if literal_chars is None or term.get("type") != "literal" or len(term["value"]) <= literal_chars:
        return term
    return {**term, "value": term["value"][:literal_chars], "truncated": True, "length": len(term["value"])}


def _blank(term: dict[str, Any], scope: ScopeDefinition) -> dict[str, Any]:
    return {**term, "scope": scope["id"]} if term["type"] == "blank" else term


def _dedupe(items: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    if not any(canonical_json(existing) == canonical_json(candidate) for existing in items):
        items.append(candidate)


class Discovery:
    """Deterministic in-process library; the application owns all graph access."""

    __slots__ = ("_config", "_execute", "_scopes", "_hash")

    def __init__(self, config: Configuration, execute: SelectExecutor) -> None:
        self._config = configuration(config)
        if not callable(execute):
            fail("INVALID_CONFIG", "executor", "Expected a callable executor.")
        self._execute = execute
        self._scopes = {scope["id"]: scope for scope in self._config["scopes"]}
        self._hash = content_hash(self._config)

    # --- helpers -------------------------------------------------------------

    @property
    def configuration_hash(self) -> str:
        return self._hash

    def _scope(self, value: Any) -> ScopeDefinition:
        scope_id = text(value, "INVALID_REQUEST", "scope")
        scope = self._scopes.get(scope_id)
        if scope is None:
            fail("UNKNOWN_SCOPE", "scope", None, sorted(self._scopes))
        return scope  # type: ignore[return-value]

    def _term(self, scope: ScopeDefinition, value: Any) -> TermDefinition:
        term_id = text(value, "INVALID_REQUEST", "type")
        term = next((t for t in scope["terms"] if t["id"] == term_id), None)
        if term is None:
            fail("UNKNOWN_TERM", "type", None, [t["id"] for t in scope["terms"]])
        return term  # type: ignore[return-value]

    def _options(self, scope: ScopeDefinition, value: dict[str, Any], cap: int) -> tuple[int, int, CancelSignal | None, str | None]:
        deadline = self._config["limits"]["deadline_ms"]
        limit = cap if value.get("limit") is None else positive(value["limit"], "INVALID_REQUEST", "limit", cap)
        deadline_ms = deadline if value.get("deadline_ms") is None else positive(value["deadline_ms"], "INVALID_REQUEST", "deadline_ms", deadline)
        signal = value.get("signal")
        if signal is not None and not isinstance(signal, CancelSignal):
            fail("INVALID_REQUEST", "signal", "Expected a gluon.CancelSignal.")
        snapshot = None if value.get("snapshot") is None else text(value["snapshot"], "INVALID_REQUEST", "snapshot")
        if snapshot is None and scope.get("snapshot") == "required":
            fail("INVALID_REQUEST", "snapshot", "This scope requires a snapshot identity on every request.")
        return limit, deadline_ms, signal, snapshot

    def _record(self, operation: str, scope: ScopeDefinition, snapshot: str | None, request: dict[str, Any], query: str,
                term: TermDefinition | None = None) -> dict[str, Any]:
        from . import __version__  # local import avoids a cycle at package load
        out: dict[str, Any] = {"version": RECORD_VERSION, "gluon": __version__, "operation": operation,
                               "configuration_hash": self._hash, "scope": scope["id"], "graph": scope["graph"],
                               "snapshot": snapshot, "request": request, "query": query}
        if term is not None:
            out["binding_hash"] = content_hash(term)
        return out

    def _report(self, scope: ScopeDefinition, snapshot: str | None, query: str, cap: int, count: int) -> dict[str, Any]:
        return {"source": scope["source"], "scope": scope["id"], "graph": scope["graph"], "snapshot": snapshot, "query": query,
                "completeness": {"complete": count <= cap, "truncated": count > cap, "unit": "rows", "row_limit": cap,
                                 "fetch_limit": cap + 1, "fetched_rows": count}}

    def _label_source(self, scope: ScopeDefinition) -> tuple[list[str], list[str] | None]:
        spec = scope.get("labels")
        if spec:
            return list(spec["predicates"]), spec.get("languages")
        predicates: list[str] = []
        for term in scope["terms"]:
            for predicate in term["binding"]["label_predicates"]:
                if predicate not in predicates:
                    predicates.append(predicate)
        return predicates, None

    # --- operations ----------------------------------------------------------

    def describe(self, request: dict[str, Any]) -> dict[str, Any]:
        value = record(request, "INVALID_REQUEST", "request")
        keys(value, ["scope"], [], "INVALID_REQUEST", "")
        scope = self._scope(value["scope"])
        description = copy.deepcopy(scope)
        description["sparql"] = fragments(scope)  # type: ignore[typeddict-unknown-key]
        return description  # type: ignore[return-value]

    def probe(self, request: dict[str, Any]) -> ProbeReport:
        value = record(request, "INVALID_REQUEST", "request")
        keys(value, ["scope", "type"], ["text", "iri", "filters", *_OPTIONS], "INVALID_REQUEST", "")
        scope = self._scope(value["scope"])
        term = self._term(scope, value["type"])
        if ("text" in value) == ("iri" in value):
            fail("INVALID_REQUEST", "text", "Provide exactly one of text or iri.")
        texts: list[str] | None = None
        if "text" in value:
            raw_text = value["text"]
            items = raw_text if isinstance(raw_text, list) else [raw_text]
            if not items or len(items) > 20:
                fail("INVALID_REQUEST", "text", "Provide 1 to 20 search expressions.")
            texts = [text(t, "INVALID_REQUEST", f"text[{i}]" if isinstance(raw_text, list) else "text") for i, t in enumerate(items)]
            if len({t.lower() for t in texts}) != len(texts):
                fail("INVALID_REQUEST", "text", "Search expressions must be distinct.")
        target = iri(value["iri"], "INVALID_REQUEST", "iri") if "iri" in value else None
        filters = request_filters(value.get("filters"), "filters", term)
        limits = self._config["limits"]
        limit, deadline_ms, signal, snapshot = self._options(scope, value, limits["probe_rows"])
        unit = limits["unit"]
        per_entity = limits.get("per_entity_rows", limit)
        query = probe_query(scope, term, texts, target, limit, filters, unit, per_entity)
        outer_cap = (limit + 1) * (per_entity + 1) - 1 if unit == "entities" else limit
        rows = collect(self._execute, scope, query, outer_cap, deadline_ms, signal, snapshot)
        declared = term["binding"].get("qualifiers", [])
        qnames = [q["name"] for q in declared]
        expand = [q["name"] for q in declared if q["multiple"] == "expand"]
        collect_names = [q["name"] for q in declared if q["multiple"] == "collect"]
        literal_chars = limits.get("literal_chars")
        checked: list[dict[str, Any]] = []
        for index, raw in enumerate(rows):
            row = record(raw, "INVALID_RESULT", f"rows[{index}]")
            keys(row, ["entity"], ["label", "label_predicate", *[f"q_{n}" for n in qnames]], "INVALID_RESULT", f"rows[{index}]")
            entity = rdf(row["entity"], "INVALID_RESULT", f"rows[{index}].entity")
            if entity["type"] != "iri":
                fail("INVALID_RESULT")
            label = None
            predicate = None
            if "label" in row:
                label = rdf(row["label"], "INVALID_RESULT", f"rows[{index}].label")
                if label["type"] != "literal":
                    fail("INVALID_RESULT")
                predicates = term["binding"]["label_predicates"]
                if "label_predicate" in row:
                    predicate = rdf(row["label_predicate"], "INVALID_RESULT", f"rows[{index}].label_predicate")
                    if predicate["type"] != "iri" or predicate["value"] not in predicates:
                        fail("INVALID_RESULT")
                elif len(predicates) == 1:
                    predicate = {"type": "iri", "value": predicates[0]}  # a host may omit the BIND for a single predicate
                else:
                    fail("INVALID_RESULT")
            qualifiers: dict[str, Any] = {}
            for name in qnames:
                key = f"q_{name}"
                qualifiers[name] = None if key not in row else _blank(rdf(row[key], "INVALID_RESULT", f"rows[{index}].{key}", blanks=True), scope)
            checked.append({"entity": entity["value"], "label": label, "predicate": predicate["value"] if predicate else None, "qualifiers": qualifiers})
        entity_order: list[str] = []
        for row in checked:
            if row["entity"] not in entity_order:
                entity_order.append(row["entity"])
        fetched_entities = len(entity_order)
        if unit == "entities":
            kept_entities = set(entity_order[:limit])
            considered = [r for r in checked if r["entity"] in kept_entities]
            rows_exhausted = len(rows) > outer_cap
        else:
            considered = checked[:limit]
            rows_exhausted = False
        combinations: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in considered:
            key = canonical_json([row["entity"], [row["qualifiers"][n] for n in expand]])
            combo = combinations.get(key)
            if combo is None:
                combo = {"iri": row["entity"], "labels": [], "matches": [],
                         "qualifiers": {n: (row["qualifiers"][n] if n in expand else []) for n in qnames},
                         "inspect": {"scope": scope["id"], "iri": row["entity"]}}
                combinations[key] = combo
                order.append(key)
            if row["label"] is not None:
                clipped = _clip(row["label"], literal_chars)
                _dedupe(combo["labels"], clipped)
                if texts is not None:
                    lowered = row["label"]["value"].lower()
                    for expression in texts:
                        if expression.lower() in lowered:
                            kind = "exact" if lowered == expression.lower() else "substring"
                            _dedupe(combo["matches"], {"predicate": row["predicate"], "kind": kind, "text": expression, "label": clipped})
                else:
                    _dedupe(combo["matches"], {"predicate": row["predicate"], "kind": "iri", "label": clipped})
            for name in collect_names:
                value_ = row["qualifiers"][name]
                if value_ is not None:
                    _dedupe(combo["qualifiers"][name], value_)
        for combo in combinations.values():
            for name in collect_names:
                combo["qualifiers"][name].sort(key=canonical_json)
            if target is not None and not combo["matches"]:
                combo["matches"].append({"predicate": None, "kind": "iri"})
        candidates = [combinations[k] for k in order]
        truncated_entities: list[str] = []
        if unit == "entities":
            per: dict[str, int] = {}
            kept: list[dict[str, Any]] = []
            for combo in candidates:
                per[combo["iri"]] = per.get(combo["iri"], 0) + 1
                if per[combo["iri"]] <= per_entity:
                    kept.append(combo)
                elif combo["iri"] not in truncated_entities:
                    truncated_entities.append(combo["iri"])
            candidates = kept
        report = self._report(scope, snapshot, query, limit if unit == "rows" else outer_cap, len(rows))
        if unit == "entities":
            report["completeness"] = {
                "complete": fetched_entities <= limit and not truncated_entities and not rows_exhausted,
                "truncated": fetched_entities > limit or bool(truncated_entities) or rows_exhausted,
                "unit": "entities", "row_limit": outer_cap, "fetch_limit": outer_cap + 1, "fetched_rows": len(rows),
                "entity_limit": limit, "fetched_entities": fetched_entities, "per_entity_limit": per_entity,
                "truncated_entities": truncated_entities,
            }
        normalized_request: dict[str, Any] = {"scope": scope["id"], "type": term["id"], "limit": limit}
        if texts is not None:
            normalized_request["text"] = texts if isinstance(value["text"], list) else texts[0]
        if target is not None:
            normalized_request["iri"] = target
        if value.get("filters") is not None:
            normalized_request["filters"] = copy.deepcopy(value["filters"])
        matching: dict[str, Any] = {"strategy": "literal-substring" if texts is not None else "iri",
                                    "label_predicates": list(term["binding"]["label_predicates"]),
                                    "languages": term["binding"].get("languages")}
        if texts is not None:
            matching["texts"] = texts
        else:
            matching["iri"] = target
        return {  # type: ignore[return-value]
            **report,
            "record": self._record("probe", scope, snapshot, normalized_request, query, term),
            "outcome": "matched" if candidates else "no_match",
            "term": {"id": term["id"], "binding_id": term["binding"]["id"], "binding_hash": content_hash(term)},
            "matching": matching,
            "filters": filters,
            "candidates": candidates,
        }

    def inspect(self, request: dict[str, Any]) -> InspectReport:
        value = record(request, "INVALID_REQUEST", "request")
        keys(value, ["scope", "iri"], ["profile", "direction", "predicate", *_OPTIONS], "INVALID_REQUEST", "")
        scope = self._scope(value["scope"])
        target = iri(value["iri"], "INVALID_REQUEST", "iri")
        limits = self._config["limits"]
        limit, deadline_ms, signal, snapshot = self._options(scope, value, limits["inspect_rows"])
        literal_chars = limits.get("literal_chars")
        profile = None
        if value.get("profile") is not None:
            if "direction" in value or "predicate" in value:
                fail("INVALID_REQUEST", "profile", "A profile cannot be combined with direction or predicate filters.")
            profiles = {p["id"]: p for p in scope.get("profiles", [])}
            profile_id = text(value["profile"], "INVALID_REQUEST", "profile")
            if profile_id not in profiles:
                fail("INVALID_REQUEST", "profile", "Unknown inspection profile for this scope.", sorted(profiles))
            profile = profiles[profile_id]
        direction = None if value.get("direction") is None else choice(value["direction"], ["in", "out"], "INVALID_REQUEST", "direction")
        predicate = None if value.get("predicate") is None else iri(value["predicate"], "INVALID_REQUEST", "predicate")
        normalized_request: dict[str, Any] = {"scope": scope["id"], "iri": target, "limit": limit}
        statements: list[dict[str, Any]] = []
        filters: dict[str, Any] = {}
        if profile is not None:
            query = profile_query(scope, target, profile, limit)
            rows = collect(self._execute, scope, query, limit, deadline_ms, signal, snapshot)
            for index, raw in enumerate(rows):
                row = record(raw, "INVALID_RESULT", f"rows[{index}]")
                keys(row, ["node", "predicate", "value"], [], "INVALID_RESULT", f"rows[{index}]")
                node = rdf(row["node"], "INVALID_RESULT", f"rows[{index}].node", blanks=True)
                pred = rdf(row["predicate"], "INVALID_RESULT", f"rows[{index}].predicate")
                val = rdf(row["value"], "INVALID_RESULT", f"rows[{index}].value", blanks=True)
                if node["type"] == "literal" or pred["type"] != "iri":
                    fail("INVALID_RESULT")
                statements.append({"node": _blank(node, scope), "predicate": pred["value"], "value": _blank(_clip(val, literal_chars), scope)})
            normalized_request["profile"] = profile["id"]
        else:
            query = inspect_query(scope, target, limit, direction, predicate)
            rows = collect(self._execute, scope, query, limit, deadline_ms, signal, snapshot)
            for index, raw in enumerate(rows):
                row = record(raw, "INVALID_RESULT", f"rows[{index}]")
                keys(row, ["direction", "predicate", "neighbor"], [], "INVALID_RESULT", f"rows[{index}]")
                d = rdf(row["direction"], "INVALID_RESULT", f"rows[{index}].direction")
                pred = rdf(row["predicate"], "INVALID_RESULT", f"rows[{index}].predicate")
                neighbor = rdf(row["neighbor"], "INVALID_RESULT", f"rows[{index}].neighbor", blanks=True)
                if d["type"] != "literal" or d["value"] not in ("in", "out") or pred["type"] != "iri":
                    fail("INVALID_RESULT")
                if direction is not None and d["value"] != direction:
                    fail("INVALID_RESULT")
                if predicate is not None and pred["value"] != predicate:
                    fail("INVALID_RESULT")
                statements.append({"direction": d["value"], "predicate": pred["value"], "neighbor": _blank(_clip(neighbor, literal_chars), scope)})
            if direction is not None:
                filters["direction"] = direction
            if predicate is not None:
                filters["predicate"] = predicate
            normalized_request.update(filters)
        return {  # type: ignore[return-value]
            **self._report(scope, snapshot, query, limit, len(rows)),
            "record": self._record("inspect", scope, snapshot, normalized_request, query),
            "iri": target,
            "profile": None if profile is None else profile["id"],
            "filters": filters,
            "statements": statements[:limit],
        }

    def _iris(self, value: dict[str, Any]) -> list[str]:
        iris = value["iris"]
        batch = self._config["limits"]["inspect_batch"]
        if not isinstance(iris, list) or not iris:
            fail("INVALID_REQUEST", "iris", "Expected a non-empty list of IRIs.")
        if len(iris) > batch:
            fail("INVALID_REQUEST", "iris", f"At most {batch} IRIs per batch.")
        targets = [iri(v, "INVALID_REQUEST", f"iris[{i}]") for i, v in enumerate(iris)]
        if len(set(targets)) != len(targets):
            fail("INVALID_REQUEST", "iris", "IRIs must be unique.")
        return targets

    def inspect_many(self, request: dict[str, Any]) -> dict[str, Any]:
        """Bounded batch of single-IRI inspections sharing one scope and options; one query per IRI."""
        value = record(request, "INVALID_REQUEST", "request")
        keys(value, ["scope", "iris"], ["profile", "direction", "predicate", *_OPTIONS], "INVALID_REQUEST", "")
        scope = self._scope(value["scope"])
        targets = self._iris(value)
        shared = {k: v for k, v in value.items() if k != "iris"}
        reports = [self.inspect({**shared, "iri": target}) for target in targets]
        return {"scope": scope["id"], "snapshot": reports[0]["snapshot"], "reports": reports}

    def labels(self, request: dict[str, Any]) -> dict[str, Any]:
        """Labels for a bounded set of IRIs over the scope's label predicates, one query."""
        value = record(request, "INVALID_REQUEST", "request")
        keys(value, ["scope", "iris"], _OPTIONS, "INVALID_REQUEST", "")
        scope = self._scope(value["scope"])
        targets = self._iris(value)
        limits = self._config["limits"]
        limit, deadline_ms, signal, snapshot = self._options(scope, value, limits["inspect_rows"])
        predicates, languages = self._label_source(scope)
        query = labels_query(scope, targets, predicates, languages, limit)
        rows = collect(self._execute, scope, query, limit, deadline_ms, signal, snapshot)
        literal_chars = limits.get("literal_chars")
        grouped: dict[str, list[dict[str, Any]]] = {target: [] for target in targets}
        for index, raw in enumerate(rows[:limit]):
            row = record(raw, "INVALID_RESULT", f"rows[{index}]")
            keys(row, ["iri", "predicate", "label"], [], "INVALID_RESULT", f"rows[{index}]")
            subject = rdf(row["iri"], "INVALID_RESULT", f"rows[{index}].iri")
            predicate = rdf(row["predicate"], "INVALID_RESULT", f"rows[{index}].predicate")
            label = rdf(row["label"], "INVALID_RESULT", f"rows[{index}].label")
            if subject["type"] != "iri" or subject["value"] not in grouped or predicate["type"] != "iri" \
                    or predicate["value"] not in predicates or label["type"] != "literal":
                fail("INVALID_RESULT")
            _dedupe(grouped[subject["value"]], {"predicate": predicate["value"], "label": _clip(label, literal_chars)})
        normalized_request = {"scope": scope["id"], "iris": targets, "limit": limit}
        return {
            **self._report(scope, snapshot, query, limit, len(rows)),
            "record": self._record("labels", scope, snapshot, normalized_request, query),
            "label_predicates": predicates,
            "languages": languages,
            "entities": [{"iri": target, "labels": grouped[target]} for target in targets],
        }

    def replay(self, search_record: dict[str, Any], snapshot: str | None = None, **options: Any) -> dict[str, Any]:
        """Re-execute a stored search record, optionally against another snapshot."""
        rec = record(search_record, "INVALID_REQUEST", "record")
        keys(rec, ["version", "gluon", "operation", "configuration_hash", "scope", "graph", "snapshot", "request", "query"],
             ["binding_hash"], "INVALID_REQUEST", "record")
        if rec["version"] != RECORD_VERSION:
            fail("INVALID_REQUEST", "record.version", f"Unsupported record version; this library writes version {RECORD_VERSION}.")
        if rec["configuration_hash"] != self._hash:
            fail("INVALID_REQUEST", "record.configuration_hash", "The record was produced under a different configuration.")
        request = dict(record(rec["request"], "INVALID_REQUEST", "record.request"))
        request["snapshot"] = rec["snapshot"] if snapshot is None else snapshot
        request.update(options)
        operation = choice(rec["operation"], ["probe", "inspect", "labels"], "INVALID_REQUEST", "record.operation")
        return getattr(self, operation)(request)  # type: ignore[no-any-return]


def _match_keys(report: dict[str, Any]) -> dict[str, Any]:
    if "candidates" in report:
        return {canonical_json([c["iri"], c["qualifiers"]]): {"iri": c["iri"], "qualifiers": c["qualifiers"]} for c in report["candidates"]}
    if "entities" in report:
        return {canonical_json([e["iri"], l]): {"iri": e["iri"], **l} for e in report["entities"] for l in e["labels"]}
    return {canonical_json(s): s for s in report["statements"]}


def diff_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Pure set difference of matches between two reports of the same operation."""
    a, b = _match_keys(before), _match_keys(after)
    return {
        "before": {"snapshot": before.get("snapshot"), "complete": before["completeness"]["complete"]},
        "after": {"snapshot": after.get("snapshot"), "complete": after["completeness"]["complete"]},
        "same_query": before["query"] == after["query"],
        "added": [b[k] for k in sorted(b) if k not in a],
        "removed": [a[k] for k in sorted(a) if k not in b],
    }


def create_discovery(config: Configuration, execute: SelectExecutor) -> Discovery:
    return Discovery(config, execute)
