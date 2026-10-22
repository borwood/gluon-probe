"""Domain-neutral reified fixture for the host-contract tests.

Facts about entities live in *records*, each pointing at its entity and carrying opaque
qualifiers. The vocabulary is deliberately not any particular host's:
``status``, ``era``, ``tag``. A plain twin graph types entities directly with no qualifiers.
Two snapshots exist so replay and diff can be exercised. Nothing here is a Gluon default.

Snapshot "s1":
  Alpha: record r1 (kind Gadget, status live, era old, tags blue+green), record r2 (kind Gadget, status retired, era old)
  Beta:  record r3 (kind Gadget, no status, era new)                     -> optional qualifier unbound
  Gamma: record r4 (kind Gadget, status live, era new); matches "gadget" by alias only ("gizmo gadget")
  Delta: record r5 (kind Other) -> not a gadget
Snapshot "s2": r2 withdrawn (status -> retired stays but kind changed to Other), new Epsilon r6 (kind Gadget, status live, era new)
"""
from __future__ import annotations

from rdflib import Dataset, Literal, URIRef

from gluon.rdflib_executor import create_local_executor

NS = "urn:example:registry:"
GRAPH = NS + "graph"
PLAIN_GRAPH = NS + "plain"
U = URIRef


def _t(s: str, p: str, o) -> tuple:
    return (U(NS + s), U(NS + p), o if isinstance(o, Literal) else U(NS + o))


def _records(graph, rows):
    for rec, entity, kind, status, era, tags in rows:
        graph.add(_t(rec, "about", entity))
        graph.add(_t(rec, "kind", kind))
        if status:
            graph.add(_t(rec, "status", status))
        graph.add(_t(rec, "era", era))
        for tag in tags:
            graph.add(_t(rec, "tag", tag))


def _labels(graph):
    graph.add(_t("Alpha", "name", Literal("Alpha gadget")))
    graph.add(_t("Beta", "name", Literal("Beta gadget")))
    graph.add(_t("Gamma", "name", Literal("Third device")))
    graph.add(_t("Gamma", "alias", Literal("gizmo gadget")))
    graph.add(_t("Delta", "name", Literal("Delta gadget")))
    graph.add(_t("Epsilon", "name", Literal("Epsilon gadget")))
    graph.add(_t("Alpha", "name", Literal("Alpha Gerät", lang="de")))


def snapshot_one() -> Dataset:
    ds = Dataset()
    g = ds.graph(U(GRAPH))
    _labels(g)
    _records(g, [
        ("r1", "Alpha", "Gadget", "live", "old", ["blue", "green"]),
        ("r2", "Alpha", "Gadget", "retired", "old", []),
        ("r3", "Beta", "Gadget", None, "new", []),
        ("r4", "Gamma", "Gadget", "live", "new", ["blue"]),
        ("r5", "Delta", "Other", "live", "new", []),
    ])
    plain = ds.graph(U(PLAIN_GRAPH))
    for name in ("Alpha", "Beta", "Gamma"):
        plain.add(_t(name, "type", "Gadget"))
    plain.add(_t("Alpha", "name", Literal("Alpha gadget")))
    plain.add(_t("Beta", "name", Literal("Beta gadget")))
    plain.add(_t("Gamma", "name", Literal("Gamma gadget")))
    plain.add(_t("Alpha", "uses", "Runner"))
    plain.add(_t("Runner", "name", Literal("Shared runner")))
    return ds


def snapshot_two() -> Dataset:
    ds = snapshot_one()
    g = ds.graph(U(GRAPH))
    g.remove(_t("r2", "kind", "Gadget"))
    g.add(_t("r2", "kind", "Other"))
    _records(g, [("r6", "Epsilon", "Gadget", "live", "new", ["green"])])
    return ds


def iri(local: str) -> dict:
    return {"type": "iri", "value": NS + local}


def var(name: str) -> dict:
    return {"type": "variable", "value": name}


def pattern(s, p: str, o) -> dict:
    return {"subject": s, "predicate": iri(p), "object": o}


def registry_configuration(unit: str = "rows", **limits) -> dict:
    gadget = {
        "id": "gadget", "definition": "Entity that some record classifies as a Gadget; records carry opaque qualifiers.",
        "binding": {
            "id": "registry/gadget",
            "label_predicates": [NS + "name", NS + "alias"],
            "patterns": [pattern(var("rec"), "about", var("entity")), pattern(var("rec"), "kind", iri("Gadget"))],
            "qualifiers": [
                {"name": "status", "subject": var("rec"), "predicate": iri("status"), "object": var("status"), "required": False},
                {"name": "era", "subject": var("rec"), "predicate": iri("era"), "object": var("era")},
                {"name": "tag", "subject": var("rec"), "predicate": iri("tag"), "object": var("tag"), "required": False, "multiple": "collect"},
            ],
        },
    }
    live_only = {
        "id": "live-gadget", "definition": "Gadget records restricted by a configured status filter.",
        "binding": {
            "id": "registry/live-gadget",
            "label_predicates": [NS + "name", NS + "alias"],
            "patterns": [pattern(var("rec"), "about", var("entity")), pattern(var("rec"), "kind", iri("Gadget"))],
            "qualifiers": [{"name": "status", "subject": var("rec"), "predicate": iri("status"), "object": var("status"), "required": False}],
            "filters": {"status": {"values": [iri("live"), iri("retired")], "unbound": "include"}},
        },
    }
    plain = {
        "id": "gadget", "definition": "Directly typed gadget; no qualifiers.",
        "binding": {"id": "plain/gadget", "label_predicate": NS + "name", "patterns": [pattern(var("entity"), "type", iri("Gadget"))]},
    }
    return {
        "scopes": [
            {"id": "registry", "source": "registry", "graph": GRAPH, "terms": [gadget, live_only],
             "profiles": [{"id": "records", "direction": "in", "predicate": NS + "about", "report": [NS + "kind", NS + "status", NS + "era"]},
                          {"id": "records-all", "direction": "in", "predicate": NS + "about", "report": []}]},
            {"id": "plain", "source": "registry", "graph": PLAIN_GRAPH, "terms": [plain]},
        ],
        "limits": {"probe_rows": 20, "inspect_rows": 50, "deadline_ms": 10_000, "unit": unit, **limits},
    }


class RegistryHost:
    def __init__(self, unit: str = "rows", **limits) -> None:
        self.snapshots = {"s1": snapshot_one(), "s2": snapshot_two()}
        self.config = registry_configuration(unit, **limits)
        self.execute = create_local_executor([
            {"source": "registry", "scope": "registry", "graph": GRAPH, "datasets": self.snapshots},
            {"source": "registry", "scope": "plain", "graph": PLAIN_GRAPH, "datasets": self.snapshots},
        ])
