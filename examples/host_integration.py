"""Executable companion to docs/host-integration.md.

A minimal host: two snapshots of a reified graph, a configuration with qualifiers and a
profile, a guarded executor, a stored search record as a selection dependency, replay
against a new snapshot, a diff, and an agent-authored query under the same guard.
Runs on the domain-neutral registry fixture from tests/support/registry.py; nothing here
is specific to any host.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gluon import SCHEMA, CancelSignal, SelectRequest, canonical_json, content_hash, create_discovery, diff_reports  # noqa: E402
from gluon.rdflib_executor import check_select  # noqa: E402
from tests.support.registry import GRAPH, NS, RegistryHost  # noqa: E402


def main() -> dict:
    host = RegistryHost(unit="entities", per_entity_rows=5)
    discovery = create_discovery(host.config, host.execute)

    # 1. The agent learns the vocabulary; no graph access happens here.
    description = discovery.describe({"scope": "registry"})
    assert set(description["sparql"]["terms"]) == {"gadget", "live-gadget"}

    # 2. A probe under the host's current baseline; the host stores the record as a dependency.
    baseline = "s1"
    report = discovery.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": baseline})
    dependency = {
        "record": report["record"],
        "result_hash": content_hash(report["candidates"]),
        "outcome": "host-recorded resolution goes here; Gluon does not decide",
    }
    assert set(dependency["record"]) == set(SCHEMA["$defs"]["SearchRecord"]["properties"])
    stored = json.loads(canonical_json(dependency))  # what a host would persist

    # 3. The host selects a new baseline and recomputes the dependency.
    later = discovery.replay(stored["record"], snapshot="s2")
    same = discovery.replay(stored["record"])
    assert canonical_json(same) == canonical_json(report), "same snapshot replays byte for byte"
    delta = diff_reports(report, later)
    added = sorted(d["iri"].removeprefix(NS) for d in delta["added"])
    removed = sorted((d["iri"].removeprefix(NS), d["qualifiers"]["status"]["value"].removeprefix(NS)) for d in delta["removed"])
    assert added == ["Epsilon"] and removed == [("Alpha", "retired")]

    # 4. A profile returns record-level facts in one call.
    facts = discovery.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s2", "profile": "records"})
    assert any(s["predicate"] == NS + "kind" and s["value"]["value"] == NS + "Other" for s in facts["statements"])

    # 5. The agent writes its own query from describe fragments; the host guards and runs it.
    fragment = description["sparql"]["terms"]["gadget"]["pattern"]
    composed = ("SELECT ?entity (COUNT(?q_tag) AS ?tags) WHERE { GRAPH <" + GRAPH + "> {\n" + fragment + "\n} } GROUP BY ?entity ORDER BY ?entity")
    check_select(composed, GRAPH)
    rows = list(host.execute(SelectRequest(composed, "registry", "registry", GRAPH, CancelSignal(), math.inf, "s2")))
    counted = {r["entity"]["value"].removeprefix(NS): int(r["tags"]["value"]) for r in rows}
    assert counted["Alpha"] == 2 and counted["Epsilon"] == 1

    summary = {"baseline": baseline, "candidates": len(report["candidates"]), "truncated": report["completeness"]["truncated"],
               "replayed_on": later["snapshot"], "added": added, "removed": removed, "configuration_hash": discovery.configuration_hash,
               "agent_query_rows": len(rows)}
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    main()
