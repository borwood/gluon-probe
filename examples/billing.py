"""Billing walkthrough.

Describe terms, probe ``surface``, reuse ``cli-ui``, follow inspection routes to policy
evidence, then execute the returned SELECT directly through the host executor and show
that the handoff reproduces the probe. Requires the ``rdflib`` extra.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gluon import CancelSignal, SelectRequest, create_discovery  # noqa: E402
from tests.support.billing import EX, fixture_host  # noqa: E402


def main() -> None:
    host = fixture_host()
    discovery = create_discovery(host.config, host.execute)
    for scope in [s["id"] for s in host.config["scopes"]]:
        vocabulary = discovery.describe({"scope": scope})
        broad = discovery.probe({"scope": scope, "type": next(t["id"] for t in vocabulary["terms"] if t["id"] == "surface"), "text": "billing"})
        cli_term = next(t for t in vocabulary["terms"] if t["id"] == "cli-ui")
        cli = discovery.probe({"scope": scope, "type": cli_term["id"], "text": "billing"})
        assert len(broad["candidates"]) == 4
        assert [c["iri"] for c in cli["candidates"]] == [EX + "BillingCli", EX + "BillingHybrid"]
        surface = discovery.inspect(cli["candidates"][0]["inspect"])
        runner = next(s for s in surface["statements"] if s["direction"] == "out" and s["predicate"] == EX + "uses")["neighbor"]["value"]
        component = discovery.inspect({"scope": scope, "iri": runner})
        policy_iri = next(s for s in component["statements"] if s["direction"] == "out" and s["predicate"] == EX + "policy")["neighbor"]["value"]
        policy = discovery.inspect({"scope": scope, "iri": policy_iri})
        assert any(s["predicate"] == EX + "retainsSelectionAcross" and s["neighbor"]["value"] == EX + "PeriodChange" for s in policy["statements"])
        assert any(s["predicate"] == EX + "evidenceStatus" and s["neighbor"]["value"] == EX + "Verified" for s in policy["statements"])
        # A host can use exactly the same SELECT. It returns cap+1 rows: trim, then deduplicate.
        request = SelectRequest(cli["query"], cli["source"], scope, cli["graph"], CancelSignal(), math.inf)
        rows = list(host.execute(request))
        handed_off = list(dict.fromkeys(r["entity"]["value"] for r in rows[: cli["completeness"]["row_limit"]]))
        assert handed_off == [c["iri"] for c in cli["candidates"]]
        print(json.dumps({"scope": scope, "surfaces": len(broad["candidates"]), "cli": handed_off,
                          "evidence": "Graph records retention with Verified status; implementation behavior is not established.",
                          "handoff_matches": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
