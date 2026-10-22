"""Query compiler.

The compiler owns the SELECT/GRAPH wrapper, literal text filter, qualifier projections and
filters, projection, ordering and the finite fetch limit (cap + 1 sentinel). It serializes
RDF terms as data and never interpolates agent text as query syntax. With a single label
predicate and no qualifiers, probe text stays byte-compatible with the original slice
(plus a label-predicate BIND); it also emits the binding fragments that `describe` exposes
for progressive SPARQL.
"""
from __future__ import annotations

import json
from typing import Any

from .types import Filter, InspectionProfile, ScopeDefinition, TermDefinition

QUOTE = chr(34)


def sparql_string(value: str) -> str:
    """Serialize a Python string as a quoted SPARQL string literal (JSON escaping is a subset)."""
    return json.dumps(value, ensure_ascii=False)


def variable_name(name: str, qualifiers: frozenset[str]) -> str:
    if name == "entity":
        return "?entity"
    return f"?q_{name}" if name in qualifiers else f"?b_{name}"


def encode(term: dict[str, Any], qualifiers: frozenset[str] = frozenset()) -> str:
    if term["type"] == "iri":
        return f"<{term['value']}>"
    if term["type"] == "variable":
        return variable_name(term["value"], qualifiers)
    value = sparql_string(term["value"])
    if term.get("language"):
        return f"{value}@{term['language']}"
    return f"{value}^^<{term['datatype']}>"


def qualifier_names(term: TermDefinition) -> frozenset[str]:
    return frozenset(q["name"] for q in term["binding"].get("qualifiers", []))


def triple_line(pattern: dict[str, Any], qualifiers: frozenset[str]) -> str:
    return f"{encode(pattern['subject'], qualifiers)} {encode(pattern['predicate'], qualifiers)} {encode(pattern['object'], qualifiers)} ."


def _filter_lines(term: TermDefinition, filters: dict[str, Filter], qualifiers: frozenset[str]) -> list[str]:
    lines: list[str] = []
    declared = {q["name"]: q for q in term["binding"].get("qualifiers", [])}
    for name in sorted(filters):
        spec = filters[name]
        var = variable_name(name, qualifiers)
        values = spec.get("values")
        unbound = spec.get("unbound", "include")
        if declared[name]["required"]:
            if values:
                lines.append(f"VALUES {var} {{ {' '.join(encode(v) for v in values)} }}")
            continue
        if values and unbound == "include":
            lines.append(f"FILTER(!BOUND({var}) || {var} IN ({', '.join(encode(v) for v in values)}))")
        elif values:
            lines.append(f"FILTER({var} IN ({', '.join(encode(v) for v in values)}))")
        elif unbound == "exclude":
            lines.append(f"FILTER(BOUND({var}))")
    return lines


def binding_fragment(term: TermDefinition, filters: dict[str, Filter] | None = None) -> str:
    """Group-graph-pattern text for one binding: required patterns, qualifiers, filters."""
    qualifiers = qualifier_names(term)
    lines = [triple_line(p, qualifiers) for p in term["binding"]["patterns"]]
    for q in term["binding"].get("qualifiers", []):
        line = triple_line(q, qualifiers)
        lines.append(line if q["required"] else f"OPTIONAL {{ {line} }}")
    lines.extend(_filter_lines(term, filters or {}, qualifiers))
    return "\n".join(lines)


def label_block(predicates: list[str], optional: bool) -> str:
    """One concrete triple pattern per label predicate, joined by UNION.

    A VALUES-bound predicate variable would force naive engines (RDFLib) to enumerate every
    triple of every candidate before filtering; concrete predicates keep the join bounded.
    """
    branches = [f"{{ ?entity <{p}> ?label . BIND(<{p}> AS ?label_predicate) }}" for p in predicates]
    block = branches[0] if len(branches) == 1 else "\nUNION\n".join(branches)
    if optional:
        return "OPTIONAL {\n" + _indent(block, 2) + "\n}"
    return block


def _indent(block: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in block.split("\n"))


def language_filter(languages: list[str] | None, variable: str = "?label") -> str | None:
    if not languages:
        return None
    tests = [f"LANG({variable}) = {QUOTE}{QUOTE}" if tag == "" else f"LANGMATCHES(LANG({variable}), {sparql_string(tag)})" for tag in languages]
    return " || ".join(tests)


def probe_body(term: TermDefinition, filters: dict[str, Filter], search: list[str] | None, iri: str | None, languages: list[str] | None) -> str:
    """Everything inside GRAPH <...> { ... } for a probe. `search` is one or more alternative expressions."""
    lines: list[str] = []
    if iri is not None:
        lines.append(f"VALUES ?entity {{ <{iri}> }}")
    lines.append(binding_fragment(term, None))
    lines.append(label_block(term["binding"]["label_predicates"], optional=iri is not None))
    if iri is None:
        lines.append("FILTER(isIRI(?entity) && isLiteral(?label))")
        tests = " || ".join(f"CONTAINS(LCASE(STR(?label)), LCASE({sparql_string(text)}))" for text in (search or []))
        lines.append(f"FILTER({tests})")
    else:
        lines.append("FILTER(isIRI(?entity))")
    condition = language_filter(languages)
    if condition:
        lines.append(f"FILTER(!BOUND(?label) || {condition})" if iri is not None else f"FILTER({condition})")
    lines.extend(_filter_lines(term, filters, qualifier_names(term)))
    return "\n".join(lines)


def labels_query(scope: ScopeDefinition, iris: list[str], predicates: list[str], languages: list[str] | None, cap: int) -> str:
    """Labels for a bounded set of IRIs over the scope's label predicates."""
    values = " ".join(f"<{i}>" for i in iris)
    branches = "\n    UNION\n    ".join(f"{{ ?iri <{p}> ?label . BIND(<{p}> AS ?predicate) }}" for p in predicates)
    condition = language_filter(languages)
    language_line = f"\n    FILTER({condition})" if condition else ""
    return (
        "SELECT DISTINCT ?iri ?predicate ?label WHERE {\n"
        f"  GRAPH <{scope['graph']}> {{\n"
        f"    VALUES ?iri {{ {values} }}\n"
        f"    {branches}\n"
        f"    FILTER(isLiteral(?label)){language_line}\n"
        "  }\n"
        "}\n"
        "ORDER BY STR(?iri) STR(?predicate) STR(?label) LANG(?label) STR(DATATYPE(?label))\n"
        f"LIMIT {cap + 1}"
    )


def probe_query(scope: ScopeDefinition, term: TermDefinition, search: list[str] | None, iri: str | None, cap: int,
                filters: dict[str, Filter], unit: str, per_entity: int) -> str:
    qualifiers = qualifier_names(term)
    qvars = [variable_name(q["name"], qualifiers) for q in term["binding"].get("qualifiers", [])]
    projection = " ".join(["?entity", "?label", "?label_predicate", *qvars])
    body = probe_body(term, filters, search, iri, term["binding"].get("languages"))
    order = ["STR(?entity)", "STR(?label)", "LANG(?label)", "STR(DATATYPE(?label))", "STR(?label_predicate)"]
    order += [f"COALESCE(STR({v}), {QUOTE}{QUOTE})" for v in qvars]
    if unit == "entities":
        inner = ("{ SELECT DISTINCT ?entity WHERE {\n" + _indent(body, 4) + "\n  } ORDER BY STR(?entity) LIMIT " + str(cap + 1) + " }")
        graph_body = inner + "\n" + body
        limit = (cap + 1) * (per_entity + 1)
    else:
        graph_body = body
        limit = cap + 1
    return (
        f"SELECT DISTINCT {projection} WHERE {{\n"
        f"  GRAPH <{scope['graph']}> {{\n"
        + _indent(graph_body, 4) + "\n"
        "  }\n"
        "}\n"
        f"ORDER BY {' '.join(order)}\n"
        f"LIMIT {limit}"
    )


def inspect_query(scope: ScopeDefinition, target: str, cap: int, direction: str | None, predicate: str | None) -> str:
    out = f"{{ <{target}> ?predicate ?neighbor . BIND({QUOTE}out{QUOTE} AS ?direction) }}"
    inc = f"{{ ?neighbor ?predicate <{target}> . BIND({QUOTE}in{QUOTE} AS ?direction) }}"
    if direction == "out":
        union = out
    elif direction == "in":
        union = inc
    else:
        union = f"{out}\n    UNION\n    {inc}"
    values = f"\n    VALUES ?predicate {{ <{predicate}> }}" if predicate else ""
    return (
        "SELECT DISTINCT ?direction ?predicate ?neighbor WHERE {\n"
        f"  GRAPH <{scope['graph']}> {{{values}\n"
        f"    {union}\n"
        "  }\n"
        "}\n"
        "ORDER BY ?direction ?predicate ?neighbor\n"
        f"LIMIT {cap + 1}"
    )


def profile_query(scope: ScopeDefinition, target: str, profile: InspectionProfile, cap: int) -> str:
    hop = f"?node <{profile['predicate']}> <{target}> ." if profile["direction"] == "in" else f"<{target}> <{profile['predicate']}> ?node ."
    values = f"    VALUES ?predicate {{ {' '.join(f'<{p}>' for p in profile['report'])} }}\n" if profile["report"] else ""
    return (
        "SELECT DISTINCT ?node ?predicate ?value WHERE {\n"
        f"  GRAPH <{scope['graph']}> {{\n"
        f"    {hop}\n"
        f"{values}"
        "    ?node ?predicate ?value .\n"
        "  }\n"
        "}\n"
        "ORDER BY ?node ?predicate ?value\n"
        f"LIMIT {cap + 1}"
    )


def fragments(scope: ScopeDefinition) -> dict[str, Any]:
    """What `describe` hands an agent so it can compose its own scoped SELECT."""
    terms: dict[str, Any] = {}
    for term in scope["terms"]:
        qualifiers = qualifier_names(term)
        bound = sorted({e["value"] for p in term["binding"]["patterns"] for e in (p["subject"], p["object"]) if e["type"] == "variable"} - {"entity"})
        terms[term["id"]] = {
            "pattern": binding_fragment(term, term["binding"].get("filters")),
            "label": label_block(term["binding"]["label_predicates"], optional=False),
            "variables": {
                "entity": "?entity",
                "label": "?label",
                "label_predicate": "?label_predicate",
                "bound": {name: variable_name(name, qualifiers) for name in bound},
                "qualifiers": {q["name"]: {"variable": variable_name(q["name"], qualifiers), "required": q["required"], "multiple": q["multiple"]}
                               for q in term["binding"].get("qualifiers", [])},
            },
        }
    first = scope["terms"][0]
    example = (
        "SELECT DISTINCT ?entity ?label WHERE {\n"
        f"  GRAPH <{scope['graph']}> {{\n"
        + _indent(binding_fragment(first, first["binding"].get("filters")), 4) + "\n"
        + _indent(label_block(first["binding"]["label_predicates"], optional=False), 4) + "\n"
        "  }\n"
        "}\n"
        "LIMIT 20"
    )
    return {
        "graph": scope["graph"],
        "rules": ["SELECT only; no SERVICE, FROM or FROM NAMED; keep the GRAPH clause for this scope.",
                  "Run queries through the host executor with the same source/scope/snapshot.",
                  "An empty result is absence of evidence in this scope and snapshot, not proof of nonexistence."],
        "terms": terms,
        "example": example,
    }
