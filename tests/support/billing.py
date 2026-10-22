"""Fixture configuration for tests and the example; never part of the library's vocabulary or logic.

Reads the small synthetic billing TriG fixture beside this module.
"""
from __future__ import annotations

import copy
from pathlib import Path

from rdflib import Dataset

from gluon.rdflib_executor import create_local_executor

FIXTURE = Path(__file__).resolve().parent / "billing.trig"
EX = "https://example.org/gluon/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
MODELS = ("class-model", "pattern-model")


def named(value: str) -> dict: return {"type": "iri", "value": value}
def variable(value: str) -> dict: return {"type": "variable", "value": value}
def literal(value: str, datatype: str | None = None, language: str | None = None) -> dict:
    out = {"type": "literal", "value": value}
    if datatype: out["datatype"] = datatype
    if language: out["language"] = language
    return out
def triple(subject: dict, predicate: str, obj: dict) -> dict:
    return {"subject": subject, "predicate": named(predicate), "object": obj}


ENTITY = variable("entity")


def billing_configuration() -> dict:
    scopes = []
    for model in MODELS:
        broad = triple(ENTITY, RDF_TYPE, named(EX + "Surface"))

        def mode(name: str, model=model) -> list[dict]:
            if model == "class-model":
                return [triple(ENTITY, RDF_TYPE, named(EX + name + "Surface"))]
            return [triple(ENTITY, EX + "exposes", variable("channel")), triple(variable("channel"), EX + "mode", named(EX + name))]

        def term(term_id: str, definition: str, patterns: list[dict]) -> dict:
            return {"id": term_id, "definition": definition,
                    "binding": {"id": f"{model}/{term_id}", "label_predicate": LABEL, "patterns": patterns}}

        scopes.append({"id": model, "source": "fixture", "graph": EX + model, "terms": [
            term("surface", "Interaction surface; mode is not established by this binding.", [broad]),
            term("cli-ui", "Surface with CLI capability, including shared surfaces.", [broad, *mode("Cli")]),
            term("web-ui", "Surface with web capability, including shared surfaces.", [broad, *mode("Web")]),
        ]})
    return {"scopes": scopes, "limits": {"probe_rows": 20, "inspect_rows": 50, "deadline_ms": 10_000}}


class FixtureHost:
    """Host-owned dataset plus a reference executor; tests mutate ``dataset`` to vary data."""

    def __init__(self) -> None:
        self.dataset = Dataset()
        self.dataset.parse(FIXTURE, format="trig")
        self.config = billing_configuration()
        self.execute = create_local_executor(
            [{"source": s["source"], "scope": s["id"], "graph": s["graph"], "dataset": self.dataset} for s in self.config["scopes"]]
        )

    def snapshot(self) -> set:
        return set(self.dataset.quads((None, None, None, None)))


def fixture_host() -> FixtureHost:
    return FixtureHost()


def clone(value):
    return copy.deepcopy(value)
