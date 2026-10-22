"""Configuration, request and result validation.

Every rejection is a ``DiscoveryError`` whose ``path`` names the offending field so a
caller can fix its own input in one step. Nothing here touches a graph. Qualifier and
filter names/values are validated as RDF data only; no name or value has meaning here.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .types import Configuration, DiscoveryError, ErrorCode, Filter, InspectionProfile, Qualifier, ScopeDefinition, TermDefinition, TriplePattern

XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
LANG_STRING = "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"
MAX_SAFE_INTEGER = 2**53 - 1

_B = chr(92)  # backslash, kept out of literal escape sequences in this source
_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")
_SCHEME = re.compile("^[A-Za-z][A-Za-z0-9+.-]*:")
_IRI_FORBIDDEN = re.compile("[" + _B + "s<>" + chr(34) + "{}|^`" + _B + _B + "".join(chr(c) for c in range(0, 33)) + chr(127) + "]")
_LANG = re.compile("^[A-Za-z]+(?:-[A-Za-z0-9]+)*$")
_VARIABLE = re.compile("^[A-Za-z_][A-Za-z0-9_]*$")


def fail(code: ErrorCode, path: str | None = None, detail: str | None = None, options: list[str] | None = None) -> None:
    raise DiscoveryError(code, path, detail, options) from None


def record(value: Any, code: ErrorCode, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        fail(code, path, "Expected an object.")
    return value


def keys(value: dict[str, Any], required: list[str], optional: list[str], code: ErrorCode, path: str) -> None:
    for key in required:
        if key not in value:
            fail(code, f"{path}.{key}" if path else key, "Required field is missing.")
    allowed = set(required) | set(optional)
    for key in value:
        if not isinstance(key, str) or key not in allowed:
            fail(code, f"{path}.{key}" if path else str(key),
                 f"Unsupported field. Allowed: {', '.join(sorted(allowed))}.")


def text(value: Any, code: ErrorCode, path: str, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        fail(code, path, "Expected a string.")
    if nonempty and not value.strip():
        fail(code, path, "Expected a non-blank string.")
    if _SURROGATE.search(value):
        fail(code, path, "Lone surrogate code points are not allowed.")
    return value


def iri(value: Any, code: ErrorCode, path: str) -> str:
    result = text(value, code, path)
    if not _SCHEME.match(result) or _IRI_FORBIDDEN.search(result):
        fail(code, path, "Expected an absolute IRI without whitespace, angle brackets, quotes or control characters.")
    return result


def positive(value: Any, code: ErrorCode, path: str, maximum: int = MAX_SAFE_INTEGER - 1) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        fail(code, path, f"Expected an integer between 1 and {maximum}.")
    return value


def boolean(value: Any, code: ErrorCode, path: str) -> bool:
    if type(value) is not bool:
        fail(code, path, "Expected true or false.")
    return value


def choice(value: Any, options: list[str], code: ErrorCode, path: str) -> str:
    if not isinstance(value, str) or value not in options:
        fail(code, path, "Unsupported value.", options)
    return value


def language(value: Any, code: ErrorCode, path: str, allow_empty: bool = False) -> str:
    tag = text(value, code, path, nonempty=not allow_empty)
    if tag == "" and allow_empty:
        return ""
    if not _LANG.match(tag):
        fail(code, path, "Expected a BCP 47 language tag.")
    return tag.lower()


def rdf(value: Any, code: ErrorCode, path: str, variables: bool = False, blanks: bool = False) -> dict[str, Any]:
    item = record(value, code, path)
    kind = item.get("type")
    if kind == "literal":
        keys(item, ["type", "value"], ["datatype", "language"], code, path)
        result: dict[str, Any] = {"type": "literal", "value": text(item["value"], code, f"{path}.value", nonempty=False)}
        if "language" in item and item["language"] is not None:
            lang = text(item["language"], code, f"{path}.language")
            if not _LANG.match(lang):
                fail(code, f"{path}.language", "Expected a BCP 47 language tag.")
            if "datatype" in item and item["datatype"] is not None and item["datatype"] != LANG_STRING:
                fail(code, f"{path}.datatype", "A language-tagged literal must use rdf:langString or omit datatype.")
            result["language"] = lang.lower()
            result["datatype"] = LANG_STRING
        else:
            datatype = item.get("datatype")
            result["datatype"] = XSD_STRING if datatype is None else iri(datatype, code, f"{path}.datatype")
            if result["datatype"] == LANG_STRING:
                fail(code, f"{path}.datatype", "rdf:langString requires a language tag.")
        return result
    keys(item, ["type", "value"], [], code, path)
    if kind == "iri":
        return {"type": "iri", "value": iri(item["value"], code, f"{path}.value")}
    if kind == "variable" and variables:
        name = text(item["value"], code, f"{path}.value")
        if not _VARIABLE.match(name):
            fail(code, f"{path}.value", "Variable names must match [A-Za-z_][A-Za-z0-9_]*.")
        return {"type": "variable", "value": name}
    if kind == "blank" and blanks:
        return {"type": "blank", "value": text(item["value"], code, f"{path}.value")}
    allowed = ["iri", "literal"] + (["variable"] if variables else []) + (["blank"] if blanks else [])
    fail(code, f"{path}.type", "Unsupported RDF term type.", allowed)
    raise AssertionError("unreachable")


def _node_key(node: dict[str, Any]) -> str:
    return f"{node['type']}:{node['value']}:{node.get('datatype', '')}:{node.get('language', '')}"


def _triple(raw: Any, code: ErrorCode, p: str) -> TriplePattern:
    triple = record(raw, code, p)
    keys(triple, ["subject", "predicate", "object"], [], code, p)
    subject = rdf(triple["subject"], code, f"{p}.subject", variables=True)
    predicate = rdf(triple["predicate"], code, f"{p}.predicate")
    obj = rdf(triple["object"], code, f"{p}.object", variables=True)
    if subject["type"] not in ("iri", "variable"):
        fail(code, f"{p}.subject", "Subjects must be IRIs or variables.")
    if predicate["type"] != "iri":
        fail(code, f"{p}.predicate", "Predicates must be IRIs.")
    return {"subject": subject, "predicate": predicate, "object": obj}  # type: ignore[typeddict-item]


def _bound_variables(patterns: list[TriplePattern]) -> set[str]:
    names = {"entity"}
    for t in patterns:
        for end in (t["subject"], t["object"]):
            if end["type"] == "variable":
                names.add(end["value"])
    return names


def _filter(raw: Any, code: ErrorCode, p: str, qualifiers: dict[str, Qualifier], name: str) -> Filter:
    item = record(raw, code, p)
    keys(item, [], ["values", "unbound"], code, p)
    if name not in qualifiers:
        fail(code, p, "Filter names an undeclared qualifier.", sorted(qualifiers))
    result: Filter = {}
    if "values" in item:
        values = item["values"]
        if not isinstance(values, list) or not values:
            fail(code, f"{p}.values", "Expected a non-empty list of RDF constants.")
        result["values"] = [rdf(v, code, f"{p}.values[{i}]") for i, v in enumerate(values)]  # type: ignore[typeddict-item]
    if "unbound" in item:
        if qualifiers[name]["required"]:
            fail(code, f"{p}.unbound", "An unbound policy applies only to optional qualifiers.")
        result["unbound"] = choice(item["unbound"], ["include", "exclude"], code, f"{p}.unbound")  # type: ignore[typeddict-item]
    if not result:
        fail(code, p, "A filter needs values and/or an unbound policy.")
    return result


def _term(value: Any, path: str) -> TermDefinition:
    code: ErrorCode = "INVALID_CONFIG"
    item = record(value, code, path)
    keys(item, ["id", "definition", "binding"], [], code, path)
    binding = record(item["binding"], code, f"{path}.binding")
    bp = f"{path}.binding"
    keys(binding, ["id", "patterns"], ["label_predicate", "label_predicates", "qualifiers", "filters", "languages"], code, bp)
    if ("label_predicate" in binding) == ("label_predicates" in binding):
        fail(code, bp, "Provide exactly one of label_predicate or label_predicates.")
    if "label_predicate" in binding:
        predicates = [iri(binding["label_predicate"], code, f"{bp}.label_predicate")]
    else:
        raw_list = binding["label_predicates"]
        if not isinstance(raw_list, list) or not raw_list:
            fail(code, f"{bp}.label_predicates", "Expected a non-empty list of IRIs.")
        predicates = [iri(v, code, f"{bp}.label_predicates[{i}]") for i, v in enumerate(raw_list)]
        if len(set(predicates)) != len(predicates):
            fail(code, f"{bp}.label_predicates", "Label predicates must be unique.")
    raw_patterns = binding["patterns"]
    if not isinstance(raw_patterns, list) or not raw_patterns:
        fail(code, f"{bp}.patterns", "Expected a non-empty list of triple patterns.")
    patterns = [_triple(raw, code, f"{bp}.patterns[{i}]") for i, raw in enumerate(raw_patterns)]
    # Connected pattern graph rooted at the candidate; shared predicates alone do not connect nodes.
    reached = {_node_key({"type": "variable", "value": "entity"})}
    pending = list(patterns)
    while pending:
        before = len(pending)
        for triple in list(pending):
            left, right = _node_key(triple["subject"]), _node_key(triple["object"])
            if left in reached or right in reached:
                reached.add(left)
                reached.add(right)
                pending.remove(triple)
        if len(pending) == before:
            fail(code, f"{bp}.patterns", "Patterns must form one connected graph rooted at ?entity.")
    bound = _bound_variables(patterns)
    out_binding: dict[str, Any] = {"id": text(binding["id"], code, f"{bp}.id"), "label_predicates": predicates, "patterns": patterns}
    qualifiers: dict[str, Qualifier] = {}
    if "qualifiers" in binding:
        raw_q = binding["qualifiers"]
        if not isinstance(raw_q, list):
            fail(code, f"{bp}.qualifiers", "Expected a list of qualifier declarations.")
        declared: list[Qualifier] = []
        for i, raw in enumerate(raw_q):
            qp = f"{bp}.qualifiers[{i}]"
            q = record(raw, code, qp)
            keys(q, ["name", "subject", "predicate", "object"], ["required", "multiple"], code, qp)
            name = text(q["name"], code, f"{qp}.name")
            if not _VARIABLE.match(name):
                fail(code, f"{qp}.name", "Qualifier names must match [A-Za-z_][A-Za-z0-9_]*.")
            if name in qualifiers:
                fail(code, f"{qp}.name", "Duplicate qualifier name.")
            if name in bound:
                fail(code, f"{qp}.name", "Qualifier names must not reuse a variable bound by the binding patterns.")
            triple = _triple({"subject": q["subject"], "predicate": q["predicate"], "object": q["object"]}, code, qp)
            ends = [triple["subject"], triple["object"]]
            is_q = [e["type"] == "variable" and e["value"] == name for e in ends]
            if sum(is_q) != 1:
                fail(code, qp, "Exactly one of subject/object must be the qualifier's own variable.")
            other = ends[1] if is_q[0] else ends[0]
            if other["type"] == "variable" and other["value"] not in bound:
                fail(code, qp, "The qualifier pattern must attach to ?entity, a constant, or a variable bound by the binding patterns.",
                     sorted(bound))
            declared.append({  # type: ignore[typeddict-item]
                "name": name, "subject": triple["subject"], "predicate": triple["predicate"], "object": triple["object"],
                "required": boolean(q.get("required", True), code, f"{qp}.required"),
                "multiple": choice(q.get("multiple", "expand"), ["expand", "collect"], code, f"{qp}.multiple"),
            })
            qualifiers[name] = declared[-1]
        out_binding["qualifiers"] = declared
    if "filters" in binding:
        raw_f = record(binding["filters"], code, f"{bp}.filters")
        out_binding["filters"] = {name: _filter(raw, code, f"{bp}.filters.{name}", qualifiers, text(name, code, f"{bp}.filters"))
                                  for name, raw in raw_f.items()}
    if "languages" in binding:
        raw_l = binding["languages"]
        if not isinstance(raw_l, list) or not raw_l:
            fail(code, f"{bp}.languages", "Expected a non-empty list of language tags; use an empty string for untagged literals.")
        out_binding["languages"] = [language(v, code, f"{bp}.languages[{i}]", allow_empty=True) for i, v in enumerate(raw_l)]
    return {"id": text(item["id"], code, f"{path}.id"), "definition": text(item["definition"], code, f"{path}.definition"),
            "binding": out_binding}  # type: ignore[typeddict-item]


def _profile(raw: Any, p: str) -> InspectionProfile:
    code: ErrorCode = "INVALID_CONFIG"
    item = record(raw, code, p)
    keys(item, ["id", "direction", "predicate"], ["report"], code, p)
    report = item.get("report", [])
    if not isinstance(report, list):
        fail(code, f"{p}.report", "Expected a list of property IRIs (empty for all properties).")
    reported = [iri(v, code, f"{p}.report[{i}]") for i, v in enumerate(report)]
    if len(set(reported)) != len(reported):
        fail(code, f"{p}.report", "Reported properties must be unique.")
    return {"id": text(item["id"], code, f"{p}.id"), "direction": choice(item["direction"], ["in", "out"], code, f"{p}.direction"),  # type: ignore[typeddict-item]
            "predicate": iri(item["predicate"], code, f"{p}.predicate"), "report": reported}


def configuration(value: Any) -> Configuration:
    """Validate and deep-copy a configuration; the result is independent of caller mutation."""
    code: ErrorCode = "INVALID_CONFIG"
    data = record(value, code, "configuration")
    keys(data, ["scopes", "limits"], [], code, "configuration")
    limits = record(data["limits"], code, "configuration.limits")
    keys(limits, ["probe_rows", "inspect_rows", "deadline_ms"], ["unit", "per_entity_rows", "literal_chars", "inspect_batch"], code, "configuration.limits")
    raw_scopes = data["scopes"]
    if not isinstance(raw_scopes, list) or not raw_scopes:
        fail(code, "configuration.scopes", "Expected a non-empty list of scopes.")
    ids: set[str] = set()
    scopes: list[ScopeDefinition] = []
    for index, raw in enumerate(raw_scopes):
        p = f"configuration.scopes[{index}]"
        scope = record(raw, code, p)
        keys(scope, ["id", "source", "graph", "terms"], ["profiles", "snapshot", "labels"], code, p)
        scope_id = text(scope["id"], code, f"{p}.id")
        if scope_id in ids:
            fail(code, f"{p}.id", "Duplicate scope id.")
        ids.add(scope_id)
        raw_terms = scope["terms"]
        if not isinstance(raw_terms, list) or not raw_terms:
            fail(code, f"{p}.terms", "Expected a non-empty list of terms.")
        terms = [_term(t, f"{p}.terms[{i}]") for i, t in enumerate(raw_terms)]
        if len({t["id"] for t in terms}) != len(terms):
            fail(code, f"{p}.terms", "Term ids must be unique within a scope.")
        if len({t["binding"]["id"] for t in terms}) != len(terms):
            fail(code, f"{p}.terms", "Binding ids must be unique within a scope.")
        out: dict[str, Any] = {"id": scope_id, "source": text(scope["source"], code, f"{p}.source"),
                               "graph": iri(scope["graph"], code, f"{p}.graph"), "terms": terms}
        if "profiles" in scope:
            raw_profiles = scope["profiles"]
            if not isinstance(raw_profiles, list):
                fail(code, f"{p}.profiles", "Expected a list of inspection profiles.")
            profiles = [_profile(r, f"{p}.profiles[{i}]") for i, r in enumerate(raw_profiles)]
            if len({pr["id"] for pr in profiles}) != len(profiles):
                fail(code, f"{p}.profiles", "Profile ids must be unique within a scope.")
            out["profiles"] = profiles
        if "snapshot" in scope:
            out["snapshot"] = choice(scope["snapshot"], ["required", "optional"], code, f"{p}.snapshot")
        if "labels" in scope:
            lp = f"{p}.labels"
            spec = record(scope["labels"], code, lp)
            keys(spec, ["predicates"], ["languages"], code, lp)
            raw_predicates = spec["predicates"]
            if not isinstance(raw_predicates, list) or not raw_predicates:
                fail(code, f"{lp}.predicates", "Expected a non-empty list of IRIs.")
            predicates = [iri(v, code, f"{lp}.predicates[{i}]") for i, v in enumerate(raw_predicates)]
            if len(set(predicates)) != len(predicates):
                fail(code, f"{lp}.predicates", "Label predicates must be unique.")
            labels: dict[str, Any] = {"predicates": predicates}
            if "languages" in spec:
                raw_l = spec["languages"]
                if not isinstance(raw_l, list) or not raw_l:
                    fail(code, f"{lp}.languages", "Expected a non-empty list of language tags; use an empty string for untagged literals.")
                labels["languages"] = [language(v, code, f"{lp}.languages[{i}]", allow_empty=True) for i, v in enumerate(raw_l)]
            out["labels"] = labels
        scopes.append(out)  # type: ignore[arg-type]
    probe_rows = positive(limits["probe_rows"], code, "configuration.limits.probe_rows")
    out_limits: dict[str, Any] = {
        "probe_rows": probe_rows,
        "inspect_rows": positive(limits["inspect_rows"], code, "configuration.limits.inspect_rows"),
        "deadline_ms": positive(limits["deadline_ms"], code, "configuration.limits.deadline_ms", 2_147_483_647),
        "unit": choice(limits.get("unit", "rows"), ["rows", "entities"], code, "configuration.limits.unit"),
    }
    if "per_entity_rows" in limits:
        out_limits["per_entity_rows"] = positive(limits["per_entity_rows"], code, "configuration.limits.per_entity_rows")
    elif out_limits["unit"] == "entities":
        out_limits["per_entity_rows"] = probe_rows
    if "literal_chars" in limits:
        out_limits["literal_chars"] = positive(limits["literal_chars"], code, "configuration.limits.literal_chars")
    out_limits["inspect_batch"] = positive(limits.get("inspect_batch", 10), code, "configuration.limits.inspect_batch", 1000)
    return copy.deepcopy({"scopes": scopes, "limits": out_limits})  # type: ignore[return-value]


def request_filters(raw: Any, path: str, term: TermDefinition) -> dict[str, Filter]:
    """Merge caller filters into configured filters; callers may only narrow."""
    code: ErrorCode = "INVALID_REQUEST"
    qualifiers = {q["name"]: q for q in term["binding"].get("qualifiers", [])}
    configured: dict[str, Any] = copy.deepcopy(term["binding"].get("filters", {}))
    if raw is None:
        return configured
    item = record(raw, code, path)
    for name, spec in item.items():
        name = text(name, code, path)
        if name not in qualifiers:
            fail(code, f"{path}.{name}", "Filter names an undeclared qualifier.", sorted(qualifiers))
        narrow = _filter(spec, code, f"{path}.{name}", qualifiers, name)
        base = configured.get(name, {})
        merged: dict[str, Any] = dict(base)
        if "values" in narrow:
            if "values" in base:
                allowed = {_node_key(v) for v in base["values"]}
                for i, v in enumerate(narrow["values"]):
                    if _node_key(v) not in allowed:
                        fail(code, f"{path}.{name}.values[{i}]", "Callers may only narrow the configured filter values.")
            merged["values"] = narrow["values"]
        if "unbound" in narrow:
            if base.get("unbound") == "exclude" and narrow["unbound"] == "include":
                fail(code, f"{path}.{name}.unbound", "Callers may not re-include unbound values the host excludes.")
            merged["unbound"] = narrow["unbound"]
        configured[name] = merged
    return configured
