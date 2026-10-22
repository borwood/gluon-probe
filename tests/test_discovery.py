"""Discovery tests on the billing fixture: describe, probe, inspect, handoff and diagnostics."""
from __future__ import annotations

import json
import math
import unittest

from rdflib import BNode, Dataset, Literal, URIRef

from gluon import CancelSignal, DiscoveryError, SCHEMA, SourceUnavailableError, canonical_json, create_discovery
from gluon.rdflib_executor import create_local_executor, json_term
from gluon.types import SelectRequest
from tests.support.billing import (EX, ENTITY, LABEL, MODELS, RDF_TYPE, billing_configuration, clone, fixture_host,
                                   literal, named, triple, variable)

ALL = sorted(EX + s for s in ["BillingCli", "BillingHybrid", "BillingUnclassified", "BillingWeb"])
CLI = [EX + "BillingCli", EX + "BillingHybrid"]
U = URIRef


def ids(report) -> list[str]:
    return sorted(c["iri"] for c in report["candidates"])


def canonical(values) -> list[str]:
    return sorted(canonical_json(v) for v in values)


def raw(execute, report, **overrides) -> list[dict]:
    fields = {"query": report["query"], "source": report["source"], "scope": report["scope"], "graph": report["graph"], **overrides}
    request = SelectRequest(fields["query"], fields["source"], fields["scope"], fields["graph"], CancelSignal(), math.inf)
    return list(execute(request))


class DiscoveryTests(unittest.TestCase):
    def assertCode(self, code, fn, *args, **kwargs):
        with self.assertRaises(DiscoveryError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_describe_probe_and_reuse_discovered_term(self):
        """Describe, probe and reuse the discovered term in both models."""
        host = fixture_host()
        queries = []
        gluon = create_discovery(host.config, lambda request: (queries.append(request.query), host.execute(request))[1])
        for scope in MODELS:
            with self.subTest(scope=scope):
                before = len(queries)
                description = gluon.describe({"scope": scope})
                self.assertEqual(len(queries), before)
                self.assertEqual(len(description["terms"]), 3)
                broad = gluon.probe({"scope": scope, "type": "surface", "text": "BiLLinG"})
                self.assertEqual(ids(broad), ALL)
                self.assertEqual(len(queries), before + 1, "no sibling-mode queries")
                self.assertFalse(any("known_modes" in c for c in broad["candidates"]))
                refined = next(t for t in description["terms"] if t["id"] == "cli-ui")
                self.assertEqual(ids(gluon.probe({"scope": scope, "type": refined["id"], "text": "billing"})), CLI)
                self.assertEqual(ids(gluon.probe({"scope": scope, "type": "web-ui", "text": "billing"})), [EX + "BillingHybrid", EX + "BillingWeb"])
                self.assertEqual(broad["term"]["binding_id"], scope + "/surface")
                self.assertEqual(broad["graph"], EX + scope)
                self.assertTrue(all(c["labels"] and c["inspect"] == {"scope": scope, "iri": c["iri"]} for c in broad["candidates"]))

    def test_explicit_exclusive_binding_and_unknown_modes(self):
        """An explicit exclusive binding; unknown modes never become exclusive."""
        host = fixture_host()
        for scope in host.config["scopes"]:
            term = clone(next(t for t in scope["terms"] if t["id"] == "cli-ui"))
            term["id"] = "exclusive-cli"
            term["binding"]["id"] += "/exclusive"
            term["binding"]["patterns"].append(triple(ENTITY, EX + "onlyMode", named(EX + "Cli")))
            scope["terms"].append(term)
        gluon = create_discovery(host.config, host.execute)
        for scope in MODELS:
            with self.subTest(scope=scope):
                self.assertEqual(len(gluon.describe({"scope": scope})["terms"]), 4)
                self.assertEqual(ids(gluon.probe({"scope": scope, "type": "exclusive-cli", "text": "billing"})), [EX + "BillingCli"])
                empty = gluon.probe({"scope": scope, "type": "surface", "text": "mobile"})
                self.assertEqual(empty["outcome"], "no_match")
                self.assertTrue(empty["completeness"]["complete"])
                unknown = gluon.inspect({"scope": scope, "iri": EX + "BillingUnclassified"})
                self.assertFalse(any(s["predicate"] == EX + "onlyMode" for s in unknown["statements"]))

    def test_unrelated_namespace_uses_configuration_only(self):
        """An unrelated namespace and label predicate work through configuration alone."""
        ns = "urn:library:"
        dataset = Dataset()
        graph = dataset.graph(U(ns + "graph"))
        graph.add((U(ns + "edition"), U(ns + "category"), U(ns + "Book")))
        graph.add((U(ns + "edition"), U(ns + "title"), Literal("River Atlas")))
        config = {"scopes": [{"id": "catalog", "source": "local-catalog", "graph": ns + "graph", "terms": [{
            "id": "volume", "definition": "A catalog volume.", "binding": {"id": "book-binding", "label_predicate": ns + "title",
                                                                           "patterns": [triple(ENTITY, ns + "category", named(ns + "Book"))]}}]}],
            "limits": {"probe_rows": 10, "inspect_rows": 10, "deadline_ms": 10_000}}
        gluon = create_discovery(config, create_local_executor([{"scope": "catalog", "source": "local-catalog", "graph": ns + "graph", "dataset": dataset}]))
        self.assertEqual(gluon.describe({"scope": "catalog"})["terms"][0]["id"], "volume")
        self.assertEqual(ids(gluon.probe({"scope": "catalog", "type": "volume", "text": "river"})), [ns + "edition"])

    def test_inspect_exact_neighborhoods_and_retain_host_graph(self):
        """Exact neighborhoods, evidence traversal, and the host graph is unchanged."""
        host = fixture_host()
        snapshot = host.snapshot()
        gluon = create_discovery(host.config, host.execute)
        for scope in MODELS:
            graph = host.dataset.graph(U(EX + scope))
            for target in [*ALL, EX + "BillingRunner", EX + "SelectionPolicy", EX + "uses", EX + "Surface", EX + "Missing"]:
                with self.subTest(scope=scope, target=target):
                    expected = [{"direction": "out", "predicate": str(p), "neighbor": json_term(o)} for p, o in graph.predicate_objects(U(target))]
                    expected += [{"direction": "in", "predicate": str(p), "neighbor": json_term(s)} for s, p in graph.subject_predicates(U(target))]
                    expected = [{**e, "neighbor": ({**e["neighbor"], "scope": scope} if e["neighbor"]["type"] == "blank" else e["neighbor"])} for e in expected]
                    self.assertEqual(canonical(gluon.inspect({"scope": scope, "iri": target})["statements"]), canonical(_normalize(expected)))
            for target in CLI:
                surface = gluon.inspect({"scope": scope, "iri": target})
                runner = next(s for s in surface["statements"] if s["direction"] == "out" and s["predicate"] == EX + "uses")["neighbor"]["value"]
                component = gluon.inspect({"scope": scope, "iri": runner})
                policy = next(s for s in component["statements"] if s["direction"] == "out" and s["predicate"] == EX + "policy")["neighbor"]["value"]
                evidence = gluon.inspect({"scope": scope, "iri": policy})
                self.assertTrue(any(s["predicate"] == EX + "retainsSelectionAcross" and s["neighbor"]["value"] == EX + "PeriodChange" for s in evidence["statements"]))
                self.assertTrue(any(s["predicate"] == EX + "evidenceStatus" and s["neighbor"]["value"] == EX + "Verified" for s in evidence["statements"]))
                self.assertNotIn("verified", evidence)
        self.assertEqual(host.snapshot(), snapshot)

    def test_graph_isolation_and_source_changing_sparql_rejected(self):
        """Graph isolation; source-changing SPARQL is rejected by the reference adapter."""
        host = fixture_host()
        other = host.dataset.graph(U("urn:other"))
        other.add((U(EX + "Decoy"), U(RDF_TYPE), U(EX + "Surface")))
        other.add((U(EX + "Decoy"), U(LABEL), Literal("Billing decoy")))
        gluon = create_discovery(host.config, host.execute)
        for scope in MODELS:
            with self.subTest(scope=scope):
                report = gluon.probe({"scope": scope, "type": "surface", "text": "billing"})
                self.assertEqual(ids(report), ALL)
                # The reference adapter copies only the selected graph and, rejects a query naming another graph.
                self.assertEqual(raw(host.execute, report, query="SELECT * WHERE { ?s ?p ?o }"), [])
                for query in ["SELECT * WHERE { GRAPH <urn:other> { ?s ?p ?o } }",
                              "SELECT * FROM <file:///missing.trig> WHERE { ?s ?p ?o }",
                              "SELECT * WHERE { SERVICE <https://example.invalid/> { ?s ?p ?o } }",
                              "INSERT DATA { <urn:a> <urn:b> <urn:c> }"]:
                    with self.assertRaises(Exception):
                        raw(host.execute, report, query=query)
                with self.assertRaises(SourceUnavailableError):
                    raw(host.execute, report, graph="urn:other")

    def test_complete_query_handoff_reproduces_results(self):
        """The complete handed-off query reproduces probe and inspection results."""
        host = fixture_host()
        gluon = create_discovery(host.config, host.execute)
        for scope in MODELS:
            with self.subTest(scope=scope):
                probe = gluon.probe({"scope": scope, "type": "cli-ui", "text": "billing"})
                self.assertIn(f"GRAPH <{EX + scope}>", probe["query"])
                rows = raw(host.execute, probe)
                handed = sorted({r["entity"]["value"] for r in rows[: probe["completeness"]["row_limit"]]})
                self.assertEqual(handed, ids(probe))
                inspected = gluon.inspect(probe["candidates"][0]["inspect"])
                statements = [{"direction": r["direction"]["value"], "predicate": r["predicate"]["value"], "neighbor": r["neighbor"]} for r in raw(host.execute, inspected)]
                self.assertEqual(canonical(_normalize(statements)), canonical(inspected["statements"]))
                self.assertEqual(json.loads(canonical_json(inspected)), inspected)

    def test_reference_adapter_stops_on_host_cancellation(self):
        """The reference adapter stops on host cancellation."""
        host = fixture_host()
        result = create_discovery(host.config, host.execute).probe({"scope": "class-model", "type": "surface", "text": "billing"})
        signal = CancelSignal()
        iterator = iter(host.execute(SelectRequest(result["query"], result["source"], result["scope"], result["graph"], signal, math.inf)))
        self.assertIsNotNone(next(iterator))
        signal.set()
        self.assertEqual(list(iterator), [])

    def test_special_characters_remain_literal_values(self):
        """Quoted, control, Unicode and query-looking data remain literal values."""
        host = fixture_host()
        scope = "class-model"
        graph = host.dataset.graph(U(EX + scope))
        labels = ['quote " slash \\ line\n tab\t back\b form\f nul\u0000', "Été 東京 😀",
                  '")) } } UNION { GRAPH <urn:other> { ?s ?p ?o } } #', "SERVICE FROM DELETE INSERT"]
        for i, label in enumerate(labels):
            graph.add((U(EX + f"Special{i}"), U(RDF_TYPE), U(EX + "Surface")))
            graph.add((U(EX + f"Special{i}"), U(LABEL), Literal(label)))
        gluon = create_discovery(host.config, host.execute)
        for i, label in enumerate(labels):
            with self.subTest(label=label[:12]):
                self.assertEqual(ids(gluon.probe({"scope": scope, "type": "surface", "text": label})), [EX + f"Special{i}"])
        for target in ["relative", "urn:x> } UNION {", "urn:x\n", "urn:x\\y", "urn:x\ud800"]:
            self.assertCode("INVALID_REQUEST", gluon.inspect, {"scope": scope, "iri": target})
        self.assertCode("INVALID_REQUEST", gluon.probe, {"scope": scope, "type": "surface", "text": "\ud800"})

    def test_typed_language_blank_and_variable_collisions(self):
        """Typed and language literals, blank neighbors and variable collisions."""
        host = fixture_host()
        scope = "class-model"
        graph = host.dataset.graph(U(EX + scope))
        target = EX + "BillingCli"
        xsd_int = "http://www.w3.org/2001/XMLSchema#integer"
        graph.add((U(target), U(EX + "number"), Literal("42", datatype=U(xsd_int))))
        graph.add((U(target), U(EX + "caption"), Literal("été", lang="fr")))
        graph.add((U(target), U(EX + "anonymous"), BNode("opaque")))
        host.config["scopes"][0]["terms"].append({"id": "typed", "definition": "Typed constants and variable renaming.", "binding": {
            "id": "typed-binding", "label_predicate": LABEL, "patterns": [
                triple(ENTITY, EX + "number", literal("42", datatype=xsd_int)),
                triple(ENTITY, EX + "caption", literal("été", language="FR")),
                triple(ENTITY, EX + "uses", variable("label")),
            ]}})
        gluon = create_discovery(host.config, host.execute)
        self.assertEqual(ids(gluon.probe({"scope": scope, "type": "typed", "text": "billing"})), [target])
        result = gluon.inspect({"scope": scope, "iri": target})
        self.assertTrue(any(s["neighbor"]["type"] == "literal" and s["neighbor"].get("language") == "fr" for s in result["statements"]))
        self.assertTrue(any(s["neighbor"]["type"] == "literal" and s["neighbor"].get("datatype", "").endswith("#integer") for s in result["statements"]))
        self.assertTrue(any(s["neighbor"]["type"] == "blank" and s["neighbor"]["scope"] == scope for s in result["statements"]))
        self.assertCode("INVALID_REQUEST", gluon.inspect, {"scope": scope, "iri": {"type": "blank", "value": "opaque"}})

    def test_deduplicate_candidates_and_duplicate_rich_results(self):
        """Candidates are deduplicated; duplicate-rich results are bounded explicitly."""
        host = fixture_host()
        graph = host.dataset.graph(U(EX + "pattern-model"))
        graph.add((U(EX + "BillingHybrid"), U(EX + "exposes"), U(EX + "SecondCli")))
        graph.add((U(EX + "SecondCli"), U(EX + "mode"), U(EX + "Cli")))
        graph.add((U(EX + "BillingCli"), U(LABEL), Literal("billing additional label")))
        gluon = create_discovery(host.config, host.execute)
        full = gluon.probe({"scope": "pattern-model", "type": "cli-ui", "text": "billing"})
        self.assertEqual(ids(full), CLI)
        self.assertEqual(len(next(c for c in full["candidates"] if c["iri"] == EX + "BillingCli")["labels"]), 2)
        capped = gluon.probe({"scope": "pattern-model", "type": "cli-ui", "text": "billing", "limit": 1})
        self.assertEqual(len(capped["candidates"]), 1)
        self.assertEqual(capped["completeness"], {"complete": False, "truncated": True, "unit": "rows", "row_limit": 1, "fetch_limit": 2, "fetched_rows": 2})

    def test_exact_cap_and_cap_plus_one_boundaries(self):
        """Exact cap and cap+1 boundaries for probe and inspection."""
        for count in (20, 21, 24):
            with self.subTest(probe_entities=count):
                host = fixture_host()
                graph = host.dataset.graph(U(EX + "class-model"))
                for i in range(4, count):
                    graph.add((U(EX + f"Extra{i}"), U(RDF_TYPE), U(EX + "Surface")))
                    graph.add((U(EX + f"Extra{i}"), U(LABEL), Literal("billing extra")))
                result = create_discovery(host.config, host.execute).probe({"scope": "class-model", "type": "surface", "text": "billing"})
                self.assertEqual(len(result["candidates"]), 20)
                self.assertEqual(result["completeness"]["truncated"], count > 20)
                self.assertEqual(result["completeness"]["fetched_rows"], min(count, 21))
        for count in (50, 51, 54):
            with self.subTest(inspect_edges=count):
                host = fixture_host()
                graph = host.dataset.graph(U(EX + "class-model"))
                for i in range(count):
                    graph.add((U(EX + "Large"), U(EX + "edge"), U(EX + f"N{i}")))
                result = create_discovery(host.config, host.execute).inspect({"scope": "class-model", "iri": EX + "Large"})
                self.assertEqual(len(result["statements"]), 50)
                self.assertEqual(result["completeness"]["truncated"], count > 50)
                self.assertEqual(result["completeness"]["fetched_rows"], min(count, 51))

    def test_malformed_configuration_rejected(self):
        """Malformed, disconnected, duplicate or executable configuration is rejected."""
        def set_path(c, *path, value):
            target = c
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        mutations = {
            "duplicate scope": lambda c: c["scopes"].append(clone(c["scopes"][0])),
            "duplicate term": lambda c: c["scopes"][0]["terms"].append(clone(c["scopes"][0]["terms"][0])),
            "disconnected pattern": lambda c: c["scopes"][0]["terms"][0]["binding"]["patterns"].append(triple(variable("other"), EX + "unrelated", named("urn:disconnected"))),
            "union operator": lambda c: c["scopes"][0]["terms"][0]["binding"]["patterns"].append({"union": []}),
            "variable predicate": lambda c: set_path(c, "scopes", 0, "terms", 0, "binding", "patterns", 0, "predicate", value=variable("predicate")),
            "literal subject": lambda c: set_path(c, "scopes", 0, "terms", 0, "binding", "patterns", 0, "subject", value=literal("bad subject")),
            "injected variable name": lambda c: set_path(c, "scopes", 0, "terms", 0, "binding", "patterns", 0, "subject", value=variable("entity) } #")),
            "injected iri": lambda c: set_path(c, "scopes", 0, "terms", 0, "binding", "patterns", 0, "object", value=named("urn:x> } #")),
            "raw query in binding": lambda c: set_path(c, "scopes", 0, "terms", 0, "binding", "query", value="SELECT * WHERE {}"),
            "executable resolver": lambda c: set_path(c, "scopes", 0, "terms", 0, "binding", "resolve", value=lambda: []),
            "bad graph iri": lambda c: set_path(c, "scopes", 0, "graph", value="urn:bad graph"),
            "empty patterns": lambda c: set_path(c, "scopes", 0, "terms", 0, "binding", "patterns", value=[]),
            "infinite deadline": lambda c: set_path(c, "limits", "deadline_ms", value=math.inf),
            "zero rows": lambda c: set_path(c, "limits", "probe_rows", value=0),
            "oversized rows": lambda c: set_path(c, "limits", "inspect_rows", value=2**53 - 1),
            "boolean rows": lambda c: set_path(c, "limits", "probe_rows", value=True),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                config = billing_configuration()
                mutate(config)
                error = self.assertCode("INVALID_CONFIG", create_discovery, config, lambda r: [])
                self.assertTrue(error.path, "diagnostic names a path")

    def test_invalid_requests_fail_before_source_access(self):
        """Invalid requests fail before any source access."""
        calls = []
        def execute(request):
            calls.append(request)
            raise RuntimeError()
        gluon = create_discovery(billing_configuration(), execute)
        valid = {"scope": "class-model", "type": "surface", "text": "billing"}
        self.assertCode("UNKNOWN_SCOPE", gluon.describe, {"scope": "unknown"})
        self.assertCode("UNKNOWN_SCOPE", gluon.probe, {**valid, "scope": "unknown"})
        unknown = self.assertCode("UNKNOWN_TERM", gluon.probe, {**valid, "type": "unknown"})
        self.assertEqual(unknown.options, ["surface", "cli-ui", "web-ui"])
        for changed in [{"mode": "propose"}, {"nodes": []}, {"graph": EX + "pattern-model"}, {"text": ["a", "A"]}, {"text": []},
                        {"limit": 0}, {"limit": 21}, {"deadline_ms": 10_001}, {"deadline_ms": math.nan}, {"limit": 1.5},
                        {"text": ""}, {"signal": {}}, {"limit": True}]:
            with self.subTest(changed=changed):
                error = self.assertCode("INVALID_REQUEST", gluon.probe, {**valid, **changed})
                self.assertTrue(error.path)
        self.assertEqual(calls, [])

    def test_caller_mutations_do_not_alter_configuration(self):
        """Caller mutations do not alter the captured configuration."""
        host = fixture_host()
        gluon = create_discovery(host.config, host.execute)
        host.config["scopes"][0]["graph"] = "urn:other"
        view = gluon.describe({"scope": "class-model"})
        view["terms"][0]["binding"]["patterns"].clear()
        self.assertEqual(len(gluon.describe({"scope": "class-model"})["terms"][0]["binding"]["patterns"]), 1)
        self.assertEqual(ids(gluon.probe({"scope": "class-model", "type": "surface", "text": "billing"})), ALL)

    def test_source_errors_never_become_empty_match_or_leak(self):
        """Source errors never become an empty match or leak details."""
        request = {"scope": "class-model", "type": "surface", "text": "billing"}
        for error, code in [(RuntimeError("credential=secret"), "SOURCE_ERROR"), (SourceUnavailableError("credential=secret"), "SOURCE_UNAVAILABLE")]:
            with self.subTest(code=code):
                def execute(_request, error=error):
                    raise error
                caught = self.assertCode(code, create_discovery(billing_configuration(), execute).probe, request)
                self.assertNotIn("secret", canonical_json(caught.to_json()))
                self.assertIsNone(caught.__cause__)
                self.assertTrue(caught.__suppress_context__)
        def bad_rows(_request):
            yield {"entity": named(EX + "Bad"), "label": named(EX + "NotALiteral")}
        self.assertCode("INVALID_RESULT", create_discovery(billing_configuration(), bad_rows).probe, request)

    def test_shipped_schema_matches_real_reports(self):
        """The packaged schema names exactly the keys reports and diagnostics carry."""
        host = fixture_host()
        gluon = create_discovery(host.config, host.execute)
        probe = gluon.probe({"scope": "class-model", "type": "cli-ui", "text": "billing"})
        inspect = gluon.inspect(probe["candidates"][0]["inspect"])
        defs = SCHEMA["$defs"]
        self.assertEqual(set(probe), set(defs["ProbeReport"]["properties"]))
        self.assertEqual(set(inspect), set(defs["InspectReport"]["properties"]))
        self.assertEqual(set(probe["completeness"]), set(defs["Completeness"]["required"]))
        self.assertEqual(set(probe["candidates"][0]), set(defs["Candidate"]["properties"]))
        self.assertEqual(set(host.config), set(defs["Configuration"]["properties"]))
        self.assertLessEqual(set(host.config["scopes"][0]["terms"][0]["binding"]), set(defs["Binding"]["properties"]))
        self.assertEqual(set(probe["record"]), set(defs["SearchRecord"]["properties"]))
        error = self.assertCode("UNKNOWN_TERM", gluon.probe, {"scope": "class-model", "type": "nope", "text": "x"})
        self.assertTrue(set(error.to_json()) <= set(defs["Diagnostic"]["properties"]))
        json.dumps(SCHEMA)


def _normalize(statements):
    """Executor rows carry rdflib's literal form; normalize as the core does for comparison."""
    out = []
    for s in statements:
        n = dict(s["neighbor"])
        if n["type"] == "literal":
            if n.get("language"):
                n["language"] = n["language"].lower()
                n["datatype"] = "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"
            elif "datatype" not in n:
                n["datatype"] = "http://www.w3.org/2001/XMLSchema#string"
        out.append({**s, "neighbor": n})
    return out


if __name__ == "__main__":
    unittest.main()
