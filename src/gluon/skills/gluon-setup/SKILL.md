---
name: gluon-setup
description: Set up Gluon (gluon-probe) inside a host application that keeps its own RDF graph - install, author a scope configuration from the host's ontology, write a guarded executor, validate, and run the first describe/probe/inspect. Use when a developer asks to add Gluon, wire a graph to agents, or author bindings.
---

# Gluon setup for a host application

Gluon is a read-only discovery probe: agents `describe` a configured vocabulary,
`probe` a term, `inspect` neighborhoods, fetch `labels`, and graduate to running
the returned SPARQL through the host's own executor. The host keeps canonical
authority; Gluon never interprets, stores or mutates. Read `README.md` and
`docs/host-integration.md` in the package before changing anything.

## 1. Install and confirm

```bash
pip install gluon-probe            # import name: gluon; no runtime dependencies
pip install "gluon-probe[rdflib]"  # only if the host will use the in-memory RDFLib reference executor
python -m gluon schema | head -40  # the protocol every call and report follows
```

## 2. Learn the host graph before writing bindings

Do not guess predicates. Ask the developer which named graph holds the data and
which store runs it, then run bounded SELECTs through the host's own tooling to
list the typing predicate, classes, label predicates, and whether facts are
stated directly or through records (reified statements). Keep notes of:

- the graph IRI and how revisions/snapshots are identified;
- label predicates actually present (`rdfs:label`, `skos:prefLabel`,
  `skos:altLabel`, `schema:name`, `dcterms:title`, or host-specific) and the
  language tags in use;
- for reified data: the record class, the predicate from record to entity, and
  the qualifier predicates (status, context, time, rank, ...).

If the host has an RDFS/OWL/SKOS ontology, start with the standards vocabulary
(`python -m gluon vocabulary`) so agents can discover the host's own classes,
properties and concepts before any host-specific term exists.

## 3. Author the configuration as JSON

Create `gluon.json` with one scope per graph. Each term is a definition plus a
binding: connected positive triple patterns rooted at `?entity`, label
predicates, optional qualifiers (one triple each, on a node the patterns bind),
optional default filters, optional languages. Rules that save rework:

- Qualifiers that may be absent must be `"required": false`; a missing value
  comes back as `null`, never as a value.
- If the graph types things both directly and via subclasses, write one term per
  convention; bindings have no UNION or property paths.
- For breadth-first search use `"limits": {"unit": "entities", "per_entity_rows": N}`.
- Add an inspection profile per record shape so one call returns a record's facts.
- Set `"snapshot": "required"` on a scope whose host pins revisions.
- Set scope-level `"labels": {"predicates": [...], "languages": [...]}` so
  `labels()` can name bare IRIs returned by inspection.

Validate after every change; the diagnostic names the exact field:

```bash
python -m gluon validate gluon.json
```

## 4. Write the executor

A callable receiving `SelectRequest(query, source, scope, graph, snapshot, signal, deadline)`
and returning an iterable of rows of JSON RDF terms. It must: select the
requested snapshot or raise `gluon.SourceUnavailableError`; enforce SELECT only,
no `SERVICE`, no `FROM`/`FROM NAMED`, `GRAPH` only for the scope graph (reuse
`gluon.rdflib_executor.check_select(query, graph)` if RDFLib is available);
check `request.signal.is_set()` between rows; keep raw errors inside. For an
in-memory RDFLib dataset use `gluon.rdflib_executor.create_local_executor`.

## 5. First contact

```python
import gluon
discovery = gluon.create_discovery(gluon.load_configuration("gluon.json"), execute)
vocabulary = discovery.describe({"scope": "<scope>"})
report = discovery.probe({"scope": "<scope>", "type": vocabulary["terms"][0]["id"], "text": "<word>", "snapshot": "<rev>"})
```

Check: `report["completeness"]`, `report["candidates"][0]["qualifiers"]`, and
that re-running `report["query"]` through the executor returns the same
entities (handoff parity). Then hand the developer `docs/host-integration.md`
section 5 for storing `report["record"]` as a selection dependency.

## 6. Report back

Summarize: scope and graph, terms written and why, qualifiers declared,
profiles, limits, executor rules enforced, validation hash
(`python -m gluon validate` prints `configuration_hash`), and anything the
graph needed that bindings could not express (record it; do not approximate).
