"""Host-contract tests on a domain-neutral reified fixture.

No host-specific namespace, predicate or qualifier name appears here; the core never branches on
a qualifier's name or value, and the plain twin graph reproduces the plain-slice behavior unchanged.
"""
from __future__ import annotations

import json
import unittest

from gluon import DiscoveryError, SCHEMA, canonical_json, content_hash, create_discovery, diff_reports
from gluon.rdflib_executor import QueryRejected, check_select
from tests.support.billing import billing_configuration, fixture_host
from tests.support.registry import GRAPH, NS, RegistryHost, iri, pattern, registry_configuration, var


def ids(report):
    return [c["iri"] for c in report["candidates"]]


def combos(report):
    return [(c["iri"].removeprefix(NS), {k: (v["value"].removeprefix(NS) if isinstance(v, dict) else v if v is None else [t["value"].removeprefix(NS) for t in v])
                                          for k, v in c["qualifiers"].items()}) for c in report["candidates"]]


class HostContractTests(unittest.TestCase):
    def assertCode(self, code, fn, *args, **kwargs):
        with self.assertRaises(DiscoveryError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def setUp(self):
        self.host = RegistryHost()
        self.gluon = create_discovery(self.host.config, self.host.execute)

    # --- snapshot pinning --------------------------------------------------

    def test_snapshot_is_echoed_and_never_substituted(self):
        one = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        two = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s2"})
        self.assertEqual(one["snapshot"], "s1")
        self.assertEqual(two["snapshot"], "s2")
        self.assertEqual(one["record"]["snapshot"], "s1")
        self.assertIn(NS + "Epsilon", ids(two))
        self.assertNotIn(NS + "Epsilon", ids(one))
        self.assertCode("SOURCE_UNAVAILABLE", self.gluon.probe, {"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s9"})
        # Hosts that pin snapshots make an unpinned request unavailable rather than guessing "latest".
        self.assertCode("SOURCE_UNAVAILABLE", self.gluon.probe, {"scope": "registry", "type": "gadget", "text": "gadget"})

    # --- qualifier projection ---------------------------------------------

    def test_one_entity_under_two_qualifier_values_is_two_combinations(self):
        report = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "alpha", "snapshot": "s1"})
        self.assertEqual(combos(report), [
            ("Alpha", {"status": "live", "era": "old", "tag": ["blue", "green"]}),
            ("Alpha", {"status": "retired", "era": "old", "tag": []}),
        ])
        self.assertEqual(len({c["iri"] for c in report["candidates"]}), 1)
        self.assertTrue(all(c["inspect"] == {"scope": "registry", "iri": NS + "Alpha"} for c in report["candidates"]))

    def test_unbound_optional_qualifier_is_null_never_a_value(self):
        report = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "beta", "snapshot": "s1"})
        self.assertEqual(combos(report), [("Beta", {"status": None, "era": "new", "tag": []})])
        self.assertIsNone(report["candidates"][0]["qualifiers"]["status"])

    def test_plain_graph_without_qualifiers_reproduces_w009_shape(self):
        report = self.gluon.probe({"scope": "plain", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        self.assertEqual(ids(report), [NS + "Alpha", NS + "Beta", NS + "Gamma"])
        self.assertTrue(all(c["qualifiers"] == {} for c in report["candidates"]))
        self.assertEqual(report["completeness"], {"complete": True, "truncated": False, "unit": "rows", "row_limit": 20, "fetch_limit": 21, "fetched_rows": 3})
        self.assertNotIn("OPTIONAL", report["query"])
        billing = fixture_host()
        before = create_discovery(billing.config, billing.execute).probe({"scope": "class-model", "type": "cli-ui", "text": "billing"})
        self.assertEqual(ids(before), ["https://example.org/gluon/BillingCli", "https://example.org/gluon/BillingHybrid"])

    def test_core_source_contains_no_fixture_or_host_vocabulary(self):
        from pathlib import Path
        import gluon
        source = "".join(p.read_text(encoding="utf-8") for p in Path(gluon.__file__).parent.glob("*.py"))
        for word in ["status", "standing", "treatment", "registry", "era", "Gadget"]:
            self.assertNotRegex(source, rf"[\"']{word}[\"']", f"core must not know the name {word!r}")

    # --- filters ------------------------------------------------------------

    def test_configured_filter_is_reported_and_caller_can_only_narrow(self):
        configured = self.gluon.probe({"scope": "registry", "type": "live-gadget", "text": "gadget", "snapshot": "s1"})
        self.assertEqual(configured["filters"], {"status": {"values": [iri("live"), iri("retired")], "unbound": "include"}})
        self.assertIn("FILTER(!BOUND(?q_status) || ?q_status IN (", configured["query"])
        self.assertEqual([c for c in combos(configured)], [
            ("Alpha", {"status": "live"}), ("Alpha", {"status": "retired"}), ("Beta", {"status": None}), ("Gamma", {"status": "live"})])
        narrowed = self.gluon.probe({"scope": "registry", "type": "live-gadget", "text": "gadget", "snapshot": "s1",
                                     "filters": {"status": {"values": [iri("live")], "unbound": "exclude"}}})
        self.assertEqual(combos(narrowed), [("Alpha", {"status": "live"}), ("Gamma", {"status": "live"})])
        self.assertEqual(narrowed["filters"]["status"], {"values": [iri("live")], "unbound": "exclude"})
        self.assertIn("FILTER(?q_status IN (<urn:example:registry:live>))", narrowed["query"])
        self.assertEqual(narrowed["record"]["request"]["filters"], {"status": {"values": [iri("live")], "unbound": "exclude"}})
        widen = self.assertCode("INVALID_REQUEST", self.gluon.probe, {"scope": "registry", "type": "live-gadget", "text": "gadget", "snapshot": "s1",
                                                                      "filters": {"status": {"values": [iri("draft")]}}})
        self.assertEqual(widen.path, "filters.status.values[0]")
        undeclared = self.assertCode("INVALID_REQUEST", self.gluon.probe, {"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1",
                                                                           "filters": {"nope": {"values": [iri("x")]}}})
        self.assertEqual(undeclared.options, ["era", "status", "tag"])

    def test_unfiltered_probe_returns_every_value_uninterpreted(self):
        report = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        self.assertEqual(report["filters"], {})
        statuses = {json.dumps(c["qualifiers"]["status"], sort_keys=True) for c in report["candidates"]}
        self.assertEqual(len(statuses), 3, "live, retired and null are all present and distinct")
        exclude_unbound = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1", "filters": {"status": {"unbound": "exclude"}}})
        self.assertNotIn(NS + "Beta", ids(exclude_unbound))
        self.assertIn("FILTER(BOUND(?q_status))", exclude_unbound["query"])
        required_filter = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1", "filters": {"era": {"values": [iri("new")]}}})
        self.assertIn("VALUES ?q_era { <urn:example:registry:new> }", required_filter["query"])
        self.assertEqual(sorted(set(ids(required_filter))), [NS + "Beta", NS + "Gamma"])

    # --- alias matching ----------------------------------------------------

    def test_alias_only_match_reports_predicate_and_kind(self):
        report = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gizmo", "snapshot": "s1"})
        self.assertEqual(ids(report), [NS + "Gamma"])
        self.assertEqual(report["candidates"][0]["matches"], [{"predicate": NS + "alias", "kind": "substring", "text": "gizmo", "label": {"type": "literal", "value": "gizmo gadget", "datatype": "http://www.w3.org/2001/XMLSchema#string"}}])
        exact = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "GIZMO GADGET", "snapshot": "s1"})
        self.assertEqual(exact["candidates"][0]["matches"][0]["kind"], "exact")
        self.assertEqual(report["matching"]["label_predicates"], [NS + "name", NS + "alias"])
        self.assertIn("{ ?entity <urn:example:registry:alias> ?label . BIND(<urn:example:registry:alias> AS ?label_predicate) }", report["query"])
        self.assertIn("UNION", report["query"])

    def test_language_filter_is_configuration_and_reported(self):
        config = registry_configuration()
        config["scopes"][0]["terms"][0]["binding"]["languages"] = ["de"]
        gluon = create_discovery(config, self.host.execute)
        report = gluon.probe({"scope": "registry", "type": "gadget", "text": "alpha", "snapshot": "s1"})
        self.assertEqual(report["matching"]["languages"], ["de"])
        self.assertTrue(all(label["language"] == "de" for c in report["candidates"] for label in c["labels"]))
        self.assertEqual(ids(self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gerät", "snapshot": "s1"})), [NS + "Alpha", NS + "Alpha"])

    # --- search record, replay, diff ---------------------------------------

    def test_record_replays_byte_identically_and_diffs_across_snapshots(self):
        first = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        record = json.loads(canonical_json(first["record"]))
        self.assertEqual(record["configuration_hash"], self.gluon.configuration_hash)
        self.assertEqual(record["binding_hash"], first["term"]["binding_hash"])
        self.assertEqual(set(first["term"]), {"id", "binding_id", "binding_hash"})
        self.assertEqual(record["version"], 2)
        again = self.gluon.replay(record)
        self.assertEqual(canonical_json(again), canonical_json(first))
        later = self.gluon.replay(record, snapshot="s2")
        self.assertEqual(later["snapshot"], "s2")
        diff = diff_reports(first, later)
        self.assertTrue(diff["same_query"])
        self.assertEqual([d["iri"] for d in diff["added"]], [NS + "Epsilon"])
        self.assertEqual([(d["iri"], d["qualifiers"]["status"]["value"]) for d in diff["removed"]], [(NS + "Alpha", NS + "retired")])
        other = create_discovery(registry_configuration(unit="entities"), self.host.execute)
        rejected = self.assertCode("INVALID_REQUEST", other.replay, record)
        self.assertEqual(rejected.path, "record.configuration_hash")
        inspect = self.gluon.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1", "profile": "records"})
        self.assertEqual(canonical_json(self.gluon.replay(inspect["record"])), canonical_json(inspect))
        self.assertEqual(diff_reports(inspect, self.gluon.replay(inspect["record"], snapshot="s2"))["removed"],
                         [{"node": iri("r2"), "predicate": NS + "kind", "value": iri("Gadget")}])

    def test_same_input_gives_byte_identical_json(self):
        request = {"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"}
        a = canonical_json(self.gluon.probe(request))
        b = canonical_json(create_discovery(registry_configuration(), RegistryHost().execute).probe(request))
        self.assertEqual(a, b)
        self.assertNotIn("time", a.lower().replace("times", ""))

    # --- completeness states -----------------------------

    def test_states_stay_distinct(self):
        empty = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "zzz", "snapshot": "s1"})
        self.assertEqual((empty["outcome"], empty["completeness"]["complete"]), ("no_match", True))
        truncated = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1", "limit": 1})
        self.assertEqual((truncated["completeness"]["complete"], truncated["completeness"]["truncated"]), (False, True))
        self.assertCode("UNKNOWN_SCOPE", self.gluon.probe, {"scope": "nope", "type": "gadget", "text": "x"})
        self.assertCode("SOURCE_UNAVAILABLE", self.gluon.probe, {"scope": "registry", "type": "gadget", "text": "x", "snapshot": "missing"})
        def failing(request):
            raise RuntimeError("engine down")
        self.assertCode("SOURCE_ERROR", create_discovery(self.host.config, failing).probe, {"scope": "registry", "type": "gadget", "text": "x", "snapshot": "s1"})

    # --- indirect patterns ---------------------------------------------------

    def test_candidates_reached_through_an_intermediate_node(self):
        description = self.gluon.describe({"scope": "registry"})
        fragment = description["sparql"]["terms"]["gadget"]["pattern"]
        self.assertEqual(fragment.split("\n"), [
            "?b_rec <urn:example:registry:about> ?entity .",
            "?b_rec <urn:example:registry:kind> <urn:example:registry:Gadget> .",
            "OPTIONAL { ?b_rec <urn:example:registry:status> ?q_status . }",
            "?b_rec <urn:example:registry:era> ?q_era .",
            "OPTIONAL { ?b_rec <urn:example:registry:tag> ?q_tag . }",
        ])
        report = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        self.assertNotIn(NS + "Delta", ids(report), "Delta's record is kind Other")
        config = registry_configuration()
        config["scopes"][0]["terms"][0]["binding"]["qualifiers"].append(
            {"name": "loose", "subject": var("elsewhere"), "predicate": iri("p"), "object": var("loose")})
        error = self.assertCode("INVALID_CONFIG", create_discovery, config, self.host.execute)
        self.assertEqual(error.options, ["entity", "rec"])
        config = registry_configuration()
        config["scopes"][0]["terms"][0]["binding"]["qualifiers"].append(
            {"name": "rec", "subject": var("rec"), "predicate": iri("p"), "object": var("x")})
        self.assertCode("INVALID_CONFIG", create_discovery, config, self.host.execute)
        config = registry_configuration()
        config["scopes"][0]["terms"][0]["binding"]["filters"] = {"status": {"values": [iri("live")], "unbound": "include"}}
        config["scopes"][0]["terms"][0]["binding"]["qualifiers"][0]["required"] = True
        self.assertEqual(self.assertCode("INVALID_CONFIG", create_discovery, config, self.host.execute).path,
                         "configuration.scopes[0].terms[0].binding.filters.status.unbound")

    # --- progressive SPARQL --------------------------------------------------

    def test_describe_fragments_compose_into_a_working_scoped_select(self):
        description = self.gluon.describe({"scope": "registry"})
        sparql = description["sparql"]
        self.assertEqual(sparql["graph"], GRAPH)
        term = sparql["terms"]["gadget"]
        self.assertEqual(term["variables"]["qualifiers"]["status"], {"variable": "?q_status", "required": False, "multiple": "expand"})
        self.assertEqual(term["variables"]["bound"], {"rec": "?b_rec"})
        # An agent joins a probe result to a qualifier the probe did not return (the record's tags for the live Alpha record).
        composed = ("SELECT ?entity ?q_tag WHERE { GRAPH <" + GRAPH + "> {\n" + term["pattern"] + "\n"
                    "VALUES ?entity { <" + NS + "Alpha> } VALUES ?q_status { <" + NS + "live> } } } ORDER BY ?q_tag")
        check_select(composed, GRAPH)
        from gluon import CancelSignal, SelectRequest
        import math
        rows = list(self.host.execute(SelectRequest(composed, "registry", "registry", GRAPH, CancelSignal(), math.inf, "s1")))
        self.assertEqual([r["q_tag"]["value"] for r in rows], [NS + "blue", NS + "green"])
        check_select(sparql["example"], GRAPH)
        for bad in ["SELECT * WHERE { GRAPH <urn:other> { ?s ?p ?o } }", "SELECT * FROM <" + GRAPH + "> WHERE { ?s ?p ?o }",
                    "INSERT DATA { <urn:a> <urn:b> <urn:c> }", "SELECT * WHERE { SERVICE <https://x.invalid/> { ?s ?p ?o } }"]:
            with self.assertRaises(QueryRejected):
                check_select(bad, GRAPH)

    # --- limits by entity, literal cap -----------------------------------

    def test_entity_unit_limits_count_distinct_entities_and_cap_combinations(self):
        gluon = create_discovery(registry_configuration(unit="entities", per_entity_rows=1), self.host.execute)
        report = gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1", "limit": 2})
        completeness = report["completeness"]
        self.assertEqual(completeness["unit"], "entities")
        self.assertEqual((completeness["entity_limit"], completeness["fetched_entities"], completeness["per_entity_limit"]), (2, 3, 1))
        self.assertEqual(sorted(set(ids(report))), [NS + "Alpha", NS + "Beta"])
        self.assertEqual(completeness["truncated_entities"], [NS + "Alpha"])
        self.assertFalse(completeness["complete"])
        self.assertIn("{ SELECT DISTINCT ?entity WHERE {", report["query"])
        full = gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        self.assertTrue(full["completeness"]["complete"] is False and full["completeness"]["truncated_entities"] == [NS + "Alpha"])
        roomy = create_discovery(registry_configuration(unit="entities"), self.host.execute).probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        self.assertTrue(roomy["completeness"]["complete"])
        self.assertEqual(roomy["completeness"]["fetched_entities"], 3)

    def test_literal_cap_marks_truncated_literals(self):
        gluon = create_discovery(registry_configuration(literal_chars=5), self.host.execute)
        report = gluon.probe({"scope": "registry", "type": "gadget", "text": "beta", "snapshot": "s1"})
        label = report["candidates"][0]["labels"][0]
        self.assertEqual((label["value"], label["truncated"], label["length"]), ("Beta ", True, 11))
        inspect = gluon.inspect({"scope": "registry", "iri": NS + "Beta", "snapshot": "s1", "direction": "out", "predicate": NS + "name"})
        self.assertTrue(inspect["statements"][0]["neighbor"]["truncated"])

    # --- inspection profiles, batch, filters; membership by IRI -------

    def test_inspection_profile_returns_record_facts_in_one_call(self):
        plain = self.gluon.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1"})
        self.assertEqual({s["predicate"] for s in plain["statements"] if s["direction"] == "in"}, {NS + "about"})
        profiled = self.gluon.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1", "profile": "records"})
        self.assertEqual(profiled["profile"], "records")
        self.assertEqual(sorted((s["node"]["value"].removeprefix(NS), s["predicate"].removeprefix(NS), s["value"]["value"].removeprefix(NS)) for s in profiled["statements"]), [
            ("r1", "era", "old"), ("r1", "kind", "Gadget"), ("r1", "status", "live"),
            ("r2", "era", "old"), ("r2", "kind", "Gadget"), ("r2", "status", "retired")])
        everything = self.gluon.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1", "profile": "records-all"})
        self.assertIn(NS + "tag", {s["predicate"] for s in everything["statements"]})
        unknown = self.assertCode("INVALID_REQUEST", self.gluon.inspect, {"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1", "profile": "nope"})
        self.assertEqual(unknown.options, ["records", "records-all"])
        self.assertCode("INVALID_REQUEST", self.gluon.inspect, {"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1", "profile": "records", "direction": "in"})

    def test_batch_inspection_and_direction_predicate_filters(self):
        batch = self.gluon.inspect_many({"scope": "registry", "iris": [NS + "Alpha", NS + "Beta"], "snapshot": "s1", "direction": "out", "predicate": NS + "name"})
        self.assertEqual(batch["snapshot"], "s1")
        self.assertEqual([r["iri"] for r in batch["reports"]], [NS + "Alpha", NS + "Beta"])
        self.assertEqual(batch["reports"][0]["filters"], {"direction": "out", "predicate": NS + "name"})
        self.assertEqual({s["direction"] for r in batch["reports"] for s in r["statements"]}, {"out"})
        self.assertEqual({s["predicate"] for r in batch["reports"] for s in r["statements"]}, {NS + "name"})
        self.assertIn("VALUES ?predicate { <urn:example:registry:name> }", batch["reports"][0]["query"])
        self.assertNotIn("UNION", batch["reports"][0]["query"])
        too_many = self.assertCode("INVALID_REQUEST", self.gluon.inspect_many, {"scope": "registry", "iris": [NS + f"E{i}" for i in range(11)], "snapshot": "s1"})
        self.assertEqual(too_many.path, "iris")
        self.assertCode("INVALID_REQUEST", self.gluon.inspect_many, {"scope": "registry", "iris": [NS + "Alpha", NS + "Alpha"], "snapshot": "s1"})

    def test_membership_probe_by_iri(self):
        member = self.gluon.probe({"scope": "registry", "type": "gadget", "iri": NS + "Alpha", "snapshot": "s1"})
        self.assertEqual(member["matching"]["strategy"], "iri")
        self.assertEqual(member["outcome"], "matched")
        self.assertEqual(len(member["candidates"]), 2, "two record combinations")
        self.assertIn("VALUES ?entity { <urn:example:registry:Alpha> }", member["query"])
        self.assertEqual(member["candidates"][0]["matches"][0]["kind"], "iri")
        non_member = self.gluon.probe({"scope": "registry", "type": "gadget", "iri": NS + "Delta", "snapshot": "s1"})
        self.assertEqual((non_member["outcome"], non_member["completeness"]["complete"]), ("no_match", True))
        self.assertCode("INVALID_REQUEST", self.gluon.probe, {"scope": "registry", "type": "gadget", "iri": NS + "Alpha", "text": "x", "snapshot": "s1"})
        self.assertCode("INVALID_REQUEST", self.gluon.probe, {"scope": "registry", "type": "gadget", "snapshot": "s1"})

    # --- schema stays honest ----------------------------------------------------

    def test_shipped_schema_matches_host_contract_reports(self):
        defs = SCHEMA["$defs"]
        probe = self.gluon.probe({"scope": "registry", "type": "live-gadget", "text": "gadget", "snapshot": "s1", "filters": {"status": {"values": [iri("live")]}}})
        self.assertEqual(set(probe), set(defs["ProbeReport"]["properties"]))
        self.assertEqual(set(probe["record"]), set(defs["SearchRecord"]["properties"]))
        self.assertEqual(set(probe["candidates"][0]), set(defs["Candidate"]["properties"]))
        self.assertEqual(set(probe["candidates"][0]["matches"][0]), set(defs["Match"]["properties"]))
        entity_mode = create_discovery(registry_configuration(unit="entities"), self.host.execute).probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        self.assertEqual(set(entity_mode["completeness"]), set(defs["Completeness"]["properties"]))
        description = self.gluon.describe({"scope": "registry"})
        self.assertLessEqual(set(description), set(defs["ScopeDescription"]["properties"]))
        self.assertTrue({"id", "source", "graph", "terms", "profiles", "sparql"} <= set(description))
        self.assertEqual(set(description["sparql"]), set(defs["ScopeDescription"]["properties"]["sparql"]["properties"]))
        self.assertEqual(set(description["terms"][0]["binding"]["qualifiers"][0]), set(defs["Qualifier"]["properties"]))
        self.assertEqual(set(description["profiles"][0]), set(defs["InspectionProfile"]["properties"]))
        inspect = self.gluon.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1", "profile": "records"})
        self.assertEqual(set(inspect), set(defs["InspectReport"]["properties"]))
        self.assertEqual(set(inspect["statements"][0]), set(defs["ProfileStatement"]["properties"]))
        diff = diff_reports(inspect, inspect)
        self.assertEqual(set(diff), set(defs["Diff"]["properties"]))
        self.assertEqual(set(registry_configuration(unit="entities", literal_chars=3)["limits"]) - set(defs["Limits"]["properties"]), set())


if __name__ == "__main__":
    unittest.main()
