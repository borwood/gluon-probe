# Host integration note for agent-knowledge systems

**Status:** Advisory. This note describes how a host application
that keeps its own versioned semantic graph can embed Gluon. It implements
nothing in any host; adoption, including by the first intended host, is that
host's decision under its own protocol. The executable
companion is [examples/host_integration.py](../examples/host_integration.py),
kept consistent with the shipped schema by `tests/test_host_integration_example.py`.
The library surface itself is documented in the [library README](../README.md).

## 1. Division of responsibility

| The host owns | Gluon does |
| --- | --- |
| Canonical graph, revisions, snapshot selection, publication | Echoes the snapshot you pass; never resolves "latest" |
| Vocabulary and bindings as configuration | Validates them, compiles scoped SELECTs, reports what it searched |
| Executor: store access, credentials, query guard, hard stops | Calls your executor with a complete query and a cooperative signal |
| Interpretation: identity, equivalence, relevance, acceptance | Reports candidates, qualifiers, match basis and completeness as data |
| Storing search records and recording resolution outcomes | Embeds a replayable record in every report; replays and diffs on request |
| Agent orchestration and policy | Ships a machine-readable protocol (`gluon.SCHEMA`) for your agent tooling |

Everything host-specific (your predicates, statuses, contexts, revision ids) is
configuration or opaque data. If you find yourself wanting Gluon to understand a
value, that is a sign the interpretation belongs in your host.

## 2. Configure a scope

A scope is one named graph in one source, with the terms agents may use.

```python
configuration = {
    "scopes": [{
        "id": "knowledge", "source": "my-store", "graph": "urn:my:graph",
        "terms": [{
            "id": "assertion-about",
            "definition": "Entity that some assertion record is about; qualifiers are the record's status and context.",
            "binding": {
                "id": "knowledge/assertion-about",
                "label_predicates": ["http://www.w3.org/2000/01/rdf-schema#label", "http://www.w3.org/2004/02/skos/core#altLabel"],
                "languages": ["en", ""],
                "patterns": [
                    {"subject": {"type": "variable", "value": "rec"}, "predicate": {"type": "iri", "value": "urn:my:about"},
                     "object": {"type": "variable", "value": "entity"}},
                ],
                "qualifiers": [
                    {"name": "status", "subject": {"type": "variable", "value": "rec"}, "predicate": {"type": "iri", "value": "urn:my:status"},
                     "object": {"type": "variable", "value": "status"}, "required": False},
                    {"name": "context", "subject": {"type": "variable", "value": "rec"}, "predicate": {"type": "iri", "value": "urn:my:context"},
                     "object": {"type": "variable", "value": "context"}, "required": False},
                ],
                "filters": {"status": {"unbound": "include"}},
            },
        }],
        "profiles": [{"id": "records", "direction": "in", "predicate": "urn:my:about",
                      "report": ["urn:my:predicate", "urn:my:object", "urn:my:status", "urn:my:context"]}],
    }],
    "limits": {"probe_rows": 20, "inspect_rows": 50, "deadline_ms": 10_000, "unit": "entities", "per_entity_rows": 5},
}
```

Guidance:

- **Reified data.** Root the binding at the record and reach `?entity` through
  it. Qualifiers attach to the record (or to a node the record binds, such
  as a context). Declare a qualifier `required: False` whenever a record may
  lack it; an unbound value comes back as `null`, never as a value.
- **Default filters.** If ordinary search must *disclose* rejected or withdrawn
  content rather than hide it, do not configure a filter; agents can narrow per
  request and the applied filter is always reported. If policy requires hiding,
  configure `values` and callers cannot widen them.
- **Limit unit.** Prior-knowledge search usually wants breadth across entities:
  use `unit: "entities"` with a `per_entity_rows` cap, and read
  `completeness.truncated_entities`.
- **Typing conventions.** If your graph types entities both directly and via
  subclasses, configure one term per convention; bindings have no `UNION` or
  property paths. Agents compose the union from `describe` fragments (section 6).
- **Profiles.** Add one profile per record shape so an agent gets the facts a
  record states in one call instead of a hop per record.
- **Snapshot discipline.** Set `"snapshot": "required"` on every scope whose
  host pins revisions; Gluon then rejects unpinned requests before any source
  access instead of leaving it to the executor.
- **Labels.** Set `"labels": {"predicates": [...], "languages": [...]}` so the
  `labels` operation can name the bare IRIs inspection returns.
- **First contact.** If the host graph has an RDFS, OWL or SKOS ontology, add
  `gluon.standard_terms()` and `gluon.standard_profiles()` (or start from
  `gluon.standard_scope(...)`) so agents can discover the host's own classes,
  properties and concepts before host-specific terms exist.
- **Files.** Keep the configuration as JSON and run `python -m gluon validate
  gluon.json` in CI; the verdict names the field path of any problem and the
  configuration hash that search records will carry.

## 3. Supply an executor

```python
from gluon import CancelSignal, SelectRequest, SourceUnavailableError

def execute(request: SelectRequest):
    dataset = snapshots.get(request.snapshot)          # the host selects the revision
    if dataset is None or request.graph != "urn:my:graph":
        raise SourceUnavailableError()                 # never substitute another revision
    guard(request.query, request.graph)                # SELECT only; no SERVICE, FROM, foreign GRAPH
    for row in run(dataset, request.query):            # rows of JSON RDF terms
        if request.signal.is_set():
            return
        yield row
```

Rules the executor must enforce, whether or not it uses Gluon's reference
adapter: SELECT only; no `SERVICE`, `FROM` or `FROM NAMED`; `GRAPH` clauses only
for the requested scope graph; the requested snapshot or unavailable. The
reference `gluon.rdflib_executor.create_local_executor` does all of this over
in-memory RDFLib datasets and exposes `check_select(query, graph)` as a
validate-only guard you can reuse for agent-authored queries. Keep raw backend
errors inside the executor; Gluon reports them as `SOURCE_ERROR` without detail.

Gluon is synchronous and checks the deadline between rows. A store that blocks
before its first row cannot be preempted; if you need a hard stop, run the
executor in a worker thread or subprocess and make `request.signal` abort it.

## 4. Expose the protocol to agents

`gluon.SCHEMA` is JSON Schema (draft 2020-12) for every request and report plus a
`protocol` block: the step order, the handoff recipe and the diagnostic contract.
Derive your tool definitions from it rather than hand-writing them. Tell agents:

- a `no_match` with `complete: true` is absence of evidence in this scope and
  snapshot, not proof of nonexistence;
- `labels(scope, iris)` names IRIs they were shown; `text` may be a list of
  alternative expressions and each match says which one hit;
- `qualifiers` values are your data; `null` means not recorded in this snapshot;
- `completeness.truncated` means narrow or page, not "that is all";
- every report's `query` can be re-run through the host executor unchanged, and
  `describe(...)["sparql"]` gives the fragments to write new ones.

## 5. Store search records as selection dependencies

Every probe and inspect report embeds `record`: Gluon version, operation,
configuration hash, binding hash, scope, graph, snapshot, normalized request and
complete query. A host that must retain search selections, including empty ones,
stores that record next to what its agent concluded:

```python
report = discovery.probe({"scope": "knowledge", "type": "assertion-about", "text": "retention", "snapshot": baseline})
dependency = {
    "record": report["record"],                                 # replayable identity
    "result_hash": gluon.content_hash(report["candidates"]),    # what was seen
    "outcome": agent_resolution,                                # same identity / alias / related / no prior: host-owned
}
```

When a new baseline is selected:

```python
later = discovery.replay(dependency["record"], snapshot=new_baseline)
delta = gluon.diff_reports(previous_report, later)   # added / removed candidate combinations
```

`replay` refuses a record whose `configuration_hash` differs from the running
configuration, so a binding change is a visible migration event rather than a
silent reinterpretation. If you keep a built-in retrieval alongside Gluon, have
it emit the same record shape (`SCHEMA["$defs"]["SearchRecord"]`); stored
dependencies then survive swapping one for the other.

## 6. Agent-authored SPARQL

Agents graduate from probes to their own queries using `describe` output:
`sparql.terms[<term>].pattern` (the binding as a group graph pattern with its
qualifier variables), `sparql.terms[<term>].label`, `sparql.graph` and
`sparql.example`. Run those queries through the same executor, under the same
guard and snapshot. Gluon never executes agent text itself.

## 7. Operational notes

- Keep `deadline_ms` honest for your store; the reference RDFLib adapter runs
  the Wikidata gate queries in 0.1 to 8 seconds on 7,000 triples, which is a
  reference-adapter characteristic, not a product measurement.
- `limits.literal_chars` protects agent context from long descriptions; cut
  literals are marked `truncated` with their original `length`.
- `inspect_many` issues one query per IRI, bounded by `limits.inspect_batch`.
- Reports are canonical JSON when serialized with `gluon.canonical_json`; the
  same input and snapshot give byte-identical output, which makes result hashes
  meaningful.

## 8. Known gaps and what is not offered

Recorded gaps: no `UNION` or property paths in bindings (write one term per
convention; agents compose the union from fragments); no label-language
fallback beyond the configured filter; truthy and full-statement conventions
must be chosen per term. Not offered by design: ranking, confidence,
equivalence or relevance judgments, remote adapters, storage, models,
proposals, or any host-specific behavior.
