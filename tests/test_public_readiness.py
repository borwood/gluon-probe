"""Pre-release set: compact term, one filters key, snapshot-required scopes, the labels
operation, multiple search expressions, the JSON loader and validate command, and the opt-in
standards vocabulary."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rdflib import Dataset, Literal, URIRef

from gluon import (SCHEMA, DiscoveryError, canonical_json, create_discovery, diff_reports, load_configuration,
                   standard_profiles, standard_scope, standard_terms, validate_file)
from gluon.rdflib_executor import create_local_executor
from tests.support.registry import NS, RegistryHost, registry_configuration

U = URIRef
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


class PublicReadinessTests(unittest.TestCase):
    def assertCode(self, code, fn, *args, **kwargs):
        with self.assertRaises(DiscoveryError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def setUp(self):
        self.host = RegistryHost()
        self.gluon = create_discovery(self.host.config, self.host.execute)

    # --- A, C: report shapes ----------------------------------------------------

    def test_compact_term_and_single_filters_key(self):
        probe = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})
        self.assertEqual(set(probe["term"]), {"id", "binding_id", "binding_hash"})
        self.assertEqual(probe["term"]["binding_hash"], probe["record"]["binding_hash"])
        self.assertNotIn("patterns", canonical_json(probe["term"]))
        plain = self.gluon.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1"})
        self.assertEqual(plain["filters"], {})
        filtered = self.gluon.inspect({"scope": "registry", "iri": NS + "Alpha", "snapshot": "s1", "direction": "in"})
        self.assertEqual(filtered["filters"], {"direction": "in"})
        self.assertEqual(probe["record"]["version"], 2)

    # --- D: snapshot required by configuration -----------------------------------

    def test_scope_can_require_a_snapshot(self):
        config = registry_configuration()
        config["scopes"][0]["snapshot"] = "required"
        gluon = create_discovery(config, self.host.execute)
        error = self.assertCode("INVALID_REQUEST", gluon.probe, {"scope": "registry", "type": "gadget", "text": "gadget"})
        self.assertEqual(error.path, "snapshot")
        self.assertCode("INVALID_REQUEST", gluon.inspect, {"scope": "registry", "iri": NS + "Alpha"})
        self.assertCode("INVALID_REQUEST", gluon.labels, {"scope": "registry", "iris": [NS + "Alpha"]})
        self.assertEqual(gluon.probe({"scope": "registry", "type": "gadget", "text": "gadget", "snapshot": "s1"})["snapshot"], "s1")
        self.assertEqual(gluon.describe({"scope": "registry"})["snapshot"], "required")
        config["scopes"][0]["snapshot"] = "sometimes"
        self.assertCode("INVALID_CONFIG", create_discovery, config, self.host.execute)

    # --- E: labels operation --------------------------------------------------------

    def test_labels_operation_uses_scope_label_predicates(self):
        report = self.gluon.labels({"scope": "registry", "iris": [NS + "Gamma", NS + "Alpha", NS + "Nobody"], "snapshot": "s1"})
        by_iri = {e["iri"].removeprefix(NS): e["labels"] for e in report["entities"]}
        self.assertEqual([e["iri"].removeprefix(NS) for e in report["entities"]], ["Gamma", "Alpha", "Nobody"], "caller order preserved")
        self.assertEqual({(l["predicate"].removeprefix(NS), l["label"]["value"]) for l in by_iri["Gamma"]}, {("name", "Third device"), ("alias", "gizmo gadget")})
        self.assertEqual({l["label"]["value"] for l in by_iri["Alpha"]}, {"Alpha gadget", "Alpha Gerät"})
        self.assertEqual(by_iri["Nobody"], [])
        self.assertEqual(report["label_predicates"], [NS + "name", NS + "alias"], "union of term label predicates when the scope declares none")
        self.assertTrue(report["completeness"]["complete"])
        self.assertEqual(report["record"]["operation"], "labels")
        self.assertEqual(canonical_json(self.gluon.replay(report["record"])), canonical_json(report))
        config = registry_configuration()
        config["scopes"][0]["labels"] = {"predicates": [NS + "name"], "languages": ["de"]}
        german = create_discovery(config, self.host.execute).labels({"scope": "registry", "iris": [NS + "Alpha", NS + "Gamma"], "snapshot": "s1"})
        self.assertEqual([[l["label"]["value"] for l in e["labels"]] for e in german["entities"]], [["Alpha Gerät"], []])
        self.assertEqual(german["languages"], ["de"])
        self.assertEqual(set(report), set(SCHEMA["$defs"]["LabelsReport"]["properties"]))
        self.assertCode("INVALID_REQUEST", self.gluon.labels, {"scope": "registry", "iris": [], "snapshot": "s1"})
        self.assertCode("INVALID_REQUEST", self.gluon.labels, {"scope": "registry", "iris": [NS + f"E{i}" for i in range(11)], "snapshot": "s1"})
        later = self.gluon.labels({"scope": "registry", "iris": [NS + "Gamma", NS + "Alpha", NS + "Nobody"], "snapshot": "s2"})
        self.assertEqual(diff_reports(report, later)["added"], [])

    # --- J: multiple search expressions --------------------------------------------

    def test_multiple_search_expressions(self):
        report = self.gluon.probe({"scope": "registry", "type": "gadget", "text": ["alpha", "GIZMO"], "snapshot": "s1"})
        self.assertEqual(sorted({c["iri"].removeprefix(NS) for c in report["candidates"]}), ["Alpha", "Gamma"])
        self.assertEqual(report["matching"]["texts"], ["alpha", "GIZMO"])
        self.assertIn("|| CONTAINS(LCASE(STR(?label)), LCASE(\"GIZMO\"))", report["query"])
        gamma = next(c for c in report["candidates"] if c["iri"] == NS + "Gamma")
        self.assertEqual([(m["text"], m["predicate"].removeprefix(NS), m["kind"]) for m in gamma["matches"]], [("GIZMO", "alias", "substring")])
        self.assertEqual(report["record"]["request"]["text"], ["alpha", "GIZMO"])
        self.assertEqual(canonical_json(self.gluon.replay(report["record"])), canonical_json(report))
        single = self.gluon.probe({"scope": "registry", "type": "gadget", "text": "alpha", "snapshot": "s1"})
        self.assertEqual(single["matching"]["texts"], ["alpha"])
        self.assertEqual(single["record"]["request"]["text"], "alpha")
        self.assertCode("INVALID_REQUEST", self.gluon.probe, {"scope": "registry", "type": "gadget", "text": ["a", "A"], "snapshot": "s1"})
        self.assertCode("INVALID_REQUEST", self.gluon.probe, {"scope": "registry", "type": "gadget", "text": [], "snapshot": "s1"})
        self.assertCode("INVALID_REQUEST", self.gluon.probe, {"scope": "registry", "type": "gadget", "text": ["ok", ""], "snapshot": "s1"})
        self.assertEqual(set(gamma["matches"][0]), set(SCHEMA["$defs"]["Match"]["properties"]))

    # --- I: loader and validate command ---------------------------------------------

    def test_json_loader_and_validate_command(self):
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good.json"
            good.write_text(json.dumps(registry_configuration()), encoding="utf-8")
            loaded = load_configuration(good)
            self.assertEqual(create_discovery(loaded, self.host.execute).configuration_hash, self.gluon.configuration_hash)
            verdict = validate_file(good)
            self.assertTrue(verdict["ok"])
            self.assertEqual(verdict["configuration_hash"], self.gluon.configuration_hash)
            self.assertEqual(verdict["summary"]["scopes"][0]["terms"], ["gadget", "live-gadget"])
            bad = Path(directory) / "bad.json"
            config = registry_configuration()
            config["scopes"][0]["terms"][0]["binding"]["patterns"] = []
            bad.write_text(json.dumps(config), encoding="utf-8")
            verdict = validate_file(bad)
            self.assertFalse(verdict["ok"])
            self.assertEqual(verdict["diagnostic"]["path"], "configuration.scopes[0].terms[0].binding.patterns")
            broken = Path(directory) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertIn("line 1", validate_file(broken)["diagnostic"]["detail"])
            self.assertEqual(validate_file(Path(directory) / "missing.json")["diagnostic"]["code"], "INVALID_CONFIG")
            ok = subprocess.run([sys.executable, "-m", "gluon", "validate", str(good)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(ok.returncode, 0)
            self.assertTrue(json.loads(ok.stdout)["ok"])
            failed = subprocess.run([sys.executable, "-m", "gluon", "validate", str(bad)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(failed.returncode, 1)
            schema = subprocess.run([sys.executable, "-m", "gluon", "schema"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(json.loads(schema.stdout)["$id"], SCHEMA["$id"])

    # --- skills ship in the package and install into a project -------------------------

    def test_skills_install_from_the_package(self):
        from gluon.skills import SKILL_NAMES, install_skills, skill_text
        self.assertEqual(SKILL_NAMES, ("gluon-setup", "gluon-agent"))
        for name in SKILL_NAMES:
            self.assertTrue(skill_text(name).startswith("---" + chr(10) + "name: " + name + chr(10)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            written = install_skills(root)
            self.assertEqual(sorted(p.relative_to(root).as_posix() for p in written),
                             sorted(f"{d}/skills/{n}/SKILL.md" for d in (".claude", ".agents") for n in SKILL_NAMES))
            with self.assertRaises(FileExistsError):
                install_skills(root)
            with self.assertRaises(ValueError):
                install_skills(root, target="cursor")
            only = install_skills(root / "codex-only", target="codex")
            self.assertEqual({p.parts[-4] for p in only}, {".agents"})
            again = subprocess.run([sys.executable, "-m", "gluon", "skills", "--root", str(root)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(again.returncode, 1)
            self.assertIn("--force", again.stderr)
            forced = subprocess.run([sys.executable, "-m", "gluon", "skills", "--root", str(root), "--target", "claude", "--force"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(forced.returncode, 0)
            self.assertEqual(len(forced.stdout.strip().splitlines()), 2)

    # --- M: opt-in standards vocabulary ------------------------------------------------

    def test_standards_vocabulary_discovers_a_hosts_own_ontology(self):
        graph_iri = "urn:example:ontology"
        dataset = Dataset()
        g = dataset.graph(U(graph_iri))
        ex = "urn:example:onto:"
        for local, kind, label in [("Vehicle", OWL + "Class", "Vehicle"), ("Car", OWL + "Class", "Car"), ("hasWheel", OWL + "ObjectProperty", "has wheel"),
                                   ("colour", OWL + "DatatypeProperty", "colour"), ("Red", SKOS + "Concept", "red"), ("Colours", SKOS + "ConceptScheme", "colours")]:
            g.add((U(ex + local), U(RDF_TYPE), U(kind)))
            g.add((U(ex + local), U(RDFS + "label" if kind.startswith(OWL) else SKOS + "prefLabel"), Literal(label, lang="en")))
        g.add((U(ex + "Car"), U(RDFS + "subClassOf"), U(ex + "Vehicle")))
        g.add((U(ex + "Red"), U(SKOS + "altLabel"), Literal("crimson", lang="en")))
        g.add((U(ex + "Red"), U(SKOS + "broader"), U(ex + "Colour")))
        g.add((U(ex + "Colour"), U(SKOS + "prefLabel"), Literal("colour", lang="en")))
        scope = standard_scope("onto", "host", graph_iri, languages=["en"])
        config = {"scopes": [scope], "limits": {"probe_rows": 20, "inspect_rows": 50, "deadline_ms": 10_000}}
        execute = create_local_executor([{"source": "host", "scope": "onto", "graph": graph_iri, "dataset": dataset}])
        gluon = create_discovery(config, execute)
        description = gluon.describe({"scope": "onto"})
        self.assertEqual([t["id"] for t in description["terms"]], [t["id"] for t in standard_terms()])
        self.assertEqual(len(standard_terms()), 8)
        self.assertEqual({c["iri"] for c in gluon.probe({"scope": "onto", "type": "class", "text": ["vehicle", "car"]})["candidates"]}, {ex + "Vehicle", ex + "Car"})
        self.assertEqual([c["iri"] for c in gluon.probe({"scope": "onto", "type": "object-property", "text": "wheel"})["candidates"]], [ex + "hasWheel"])
        red = gluon.probe({"scope": "onto", "type": "concept", "text": "crimson"})
        self.assertEqual(red["candidates"][0]["matches"][0]["predicate"], SKOS + "altLabel")
        self.assertEqual(gluon.probe({"scope": "onto", "type": "rdfs-class", "text": "vehicle"})["outcome"], "no_match", "owl:Class is not rdfs:Class without inference")
        subclasses = gluon.inspect({"scope": "onto", "iri": ex + "Vehicle", "profile": "subclasses"})
        self.assertEqual([(s["node"]["value"], s["value"]["value"]) for s in subclasses["statements"]], [(ex + "Car", "Car")])
        broader = gluon.inspect({"scope": "onto", "iri": ex + "Red", "profile": "broader"})
        self.assertEqual([s["value"]["value"] for s in broader["statements"]], ["colour"])
        labels = gluon.labels({"scope": "onto", "iris": [ex + "Red", ex + "hasWheel"]})
        self.assertEqual({l["label"]["value"] for l in labels["entities"][0]["labels"]}, {"red", "crimson"})
        self.assertTrue(all(b["id"].startswith("standard/") for b in (t["binding"] for t in standard_terms())))
        self.assertEqual([p["id"] for p in standard_profiles()], ["subclasses", "superclasses", "narrower", "broader", "domain-range"])
        vocabulary = subprocess.run([sys.executable, "-m", "gluon", "vocabulary", "--prefix", "std"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(vocabulary.stdout)["terms"][0]["binding"]["id"], "std/class")
        # Application independence: the vocabulary knows only W3C namespaces.
        source = Path(__import__("gluon").vocabularies.__file__).read_text(encoding="utf-8")
        self.assertNotIn("registry", source)
        self.assertNotIn("urn:example", source)


if __name__ == "__main__":
    unittest.main()
