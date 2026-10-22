# Gluon (Python library, distributed as `gluon-probe`)

**Status:** Experimental, pre-release. Gluon is a read-only, deterministic
discovery probe for agents inside host knowledge systems that keep their own RDF
graph: `describe` a configured vocabulary, `probe` a term, `inspect` an entity's
neighborhood, fetch `labels` for IRIs, then graduate to running the returned
complete SPARQL through the host's own executor. Gluon reports; it never
interprets, proposes, decides, stores or mutates. The host contract (snapshot
pinning, opaque qualifiers, configured filters, alias matching, replayable
search records) was validated on synthetic reified fixtures and on a pinned
Wikidata subset, and a cold agent given only `describe` output composed correct
scoped queries in a recorded trial. Report and record shapes are the
compatibility surface; Python call signatures may still change before 1.0.
Guidance for embedding Gluon is in the
[host integration note](docs/host-integration.md); the agent skills
[gluon-setup](src/gluon/skills/gluon-setup/SKILL.md) and
[gluon-agent](src/gluon/skills/gluon-agent/SKILL.md) cover assisted setup and
correct use, and ship inside the package.

**For agents:** `uvx gluon-probe skills` (or `pipx run gluon-probe skills`, or
`pip install gluon-probe && python -m gluon skills`) writes both skills into the
current project for Claude Code (`.claude/skills`) and Codex (`.agents/skills`);
add `--target claude` or `--target codex` for one.
**For agents (from the repository, standard tools only):**
`mkdir -p .claude/skills && curl -fsSL https://github.com/borwood/gluon-probe/archive/refs/heads/main.tar.gz | tar -xz --strip-components=4 --exclude='__init__.py' -C .claude/skills gluon-probe-main/src/gluon/skills`
(use `.agents/skills` for Codex).

## Run and reproduce

Verified on Windows with Python **3.13.7**, RDFLib **7.1.4**, pyparsing
**3.2.3**, setuptools **84.2.1**, build **1.6.1**: all tests, both examples and
the wheel check pass. Minimum declared Python is 3.11; other versions and
platforms are unverified. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe examples\billing.py
.\.venv\Scripts\python.exe scripts\check_wheel.py
```

On POSIX use `.venv/bin/python`. Installing pins needs package-index access or a
populated pip cache; tests and the examples then use only local data. The wheel
check builds the wheel, installs it into a fresh virtual environment with
`--no-index`, runs a fake-executor probe, then installs the pinned RDFLib extra
from a local wheel directory and runs the real example from copies outside the
checkout. No model account, remote graph or database service is needed.

## Package and host responsibilities

The core (`src/gluon/`) has **zero runtime dependencies**: `types.py` (JSON RDF
terms, requests, reports, diagnostics), `validation.py`, `compiler.py`,
`core.py` (bounded collection, `describe`/`probe`/`inspect`/`inspect_many`/`labels`,
`replay`, `diff_reports`), `loader.py` (JSON configuration files),
`vocabularies.py` (the opt-in standards vocabulary), `schema.json`
(machine-readable calls and results, shipped in the wheel), `skills/` (the two
agent skills, shipped in the wheel) and `__main__.py`
(`python -m gluon validate|schema|vocabulary|skills`, also installed as the
`gluon-probe` console script; development commands only, no service). The
optional extra `gluon.rdflib_executor` is a reference host executor over an
in-memory RDFLib `Dataset`: it exposes only a copy of the selected named graph,
parses and rejects non-SELECT, `FROM`/`FROM NAMED` and `SERVICE`, offers a
validate-only `check_select` guard, and stops yielding when the request's signal
is set. It is a development/reference adapter, not a production backend or a
sandbox.

The application supplies an executor and keeps canonical data, authentication
and connection details. The executor is a callable receiving a `SelectRequest`
(`query`, `source`, `scope`, `graph`, `snapshot`, `signal`, `deadline`) and
returning an iterable of rows, each mapping selected variable names to JSON RDF
terms. It must honor the scope and the opaque `snapshot` (never substituting
another revision), and check `request.signal.is_set()` between rows. The core
does not load sources, own a graph database, or verify a dishonest executor.
The reference executor accepts `{"dataset": ...}` for unpinned hosts or
`{"datasets": {snapshot_id: Dataset}}` for pinned ones; an unknown or missing
snapshot is unavailable, not guessed.

## Configuration and calls

```python
from gluon import create_discovery

configuration = {
    "scopes": [{
        "id": "catalog", "source": "host-catalog", "graph": "urn:catalog:graph",
        "terms": [{
            "id": "volume", "definition": "Catalog volume",
            "binding": {
                "id": "catalog-volume", "label_predicate": "urn:catalog:title",
                "patterns": [{
                    "subject": {"type": "variable", "value": "entity"},
                    "predicate": {"type": "iri", "value": "urn:catalog:category"},
                    "object": {"type": "iri", "value": "urn:catalog:Book"},
                }],
            },
        }],
    }],
    "limits": {"probe_rows": 20, "inspect_rows": 50, "deadline_ms": 10_000},
}

discovery = create_discovery(configuration, execute)   # execute: the host's SelectExecutor
vocabulary = discovery.describe({"scope": "catalog"})
result = discovery.probe({"scope": "catalog", "type": vocabulary["terms"][0]["id"], "text": "river"})
if result["candidates"]:
    neighborhood = discovery.inspect(result["candidates"][0]["inspect"])
```

`entity` is the distinguished candidate variable; other variables are renamed
internally (`?b_<name>` for binding variables, `?q_<name>` for qualifiers).
Bindings are connected positive conjunctions of triple patterns rooted at
`?entity`, including patterns that reach the candidate through an intermediate
node such as a reified statement. Subjects are IRIs or variables, predicates
IRIs, objects IRIs, variables or literals. Constants are serialized as RDF data
with escaped strings and language/datatype annotations. Configuration is
validated and deep-copied at construction; returned descriptions and caller
mutations cannot alter it. Fixture configurations live in
`tests/support/billing.py` (a small synthetic
[TriG fixture](tests/support/billing.trig)) and `tests/support/registry.py`
(a domain-neutral reified graph with two snapshots).

Term discovery is independent of graph matches. Probe uses exactly one explicit
binding and either one or more case-insensitive substring expressions over the
configured label predicates (`text` may be a string or a list of up to 20
alternatives; each match reports which expression hit) or one entity IRI
(membership check, compiled as a `VALUES` constraint). It does not query
siblings, infer hierarchy, classify unqueried modes or compute confidence.

Configuration can live in a JSON file: `gluon.load_configuration(path)` returns
the validated configuration and `python -m gluon validate path` prints a verdict
with the field path of any problem and the configuration hash. A scope may set
`"snapshot": "required"` so unpinned requests are rejected before any source
access, and `"labels": {"predicates": [...], "languages": [...]}` to drive the
`labels` operation (default: the union of the scope's term label predicates).

**Opt-in standards vocabulary.** `gluon.standard_terms()`, `standard_profiles()`
and `standard_scope(id, source, graph)` return configuration data bound to RDFS,
OWL and SKOS (classes, properties, concepts, schemes, individuals; subclass and
broader/narrower profiles) with the five common label predicates, so agents can
discover a host's own ontology terms before any host-specific binding exists.
Nothing is applied unless the host adds it to a scope. `python -m gluon vocabulary`
prints it.

### Host contract

Everything below is configuration. The core recognizes no predicate, qualifier
name or value, and a binding that declares none of it behaves exactly like the
plain describe/probe/inspect slice.

- **Snapshot.** Any request may carry an opaque `snapshot` string. It is echoed
  in the report and record and passed to the executor. Snapshot identity is per
  request, never bound at construction, so the configuration hash and the data
  identity stay independent.
- **Qualifiers.** A binding may declare `qualifiers`: each is one triple pattern
  attached to `?entity`, a constant, or a variable the binding patterns bind;
  exactly one end is the qualifier's own variable. `required` (default true)
  compiles to a plain pattern; `false` compiles to a single `OPTIONAL` pattern
  and an unbound value is reported as `null`, meaning not recorded in this
  snapshot, never as a value or as false. `multiple` is `expand` (one candidate
  combination per value, the default) or `collect` (a sorted list on one
  combination). Candidates are combinations of entity plus expand-mode qualifier
  values and are never merged.
- **Filters.** A binding may set `filters` per qualifier: `values` (a list of
  RDF constants) and, for optional qualifiers, `unbound` (`include`, the default,
  or `exclude`). A request may pass `filters` that only narrow: values must be a
  subset of configured values, and `exclude` cannot be turned back into
  `include`. Every filter applied appears in `report["filters"]`, in the record
  and in the query text.
- **Alias predicates.** `label_predicates` lists one or more label or alias
  predicates (`label_predicate` remains accepted for one); they compile to one
  concrete pattern each, joined by `UNION`, so naive engines stay bounded. Each
  candidate's `matches` names the predicate and whether the match was `exact`,
  `substring` or by `iri`. Optional `languages` restricts label language tags; an
  empty string admits untagged literals.
- **Search record.** Every report embeds `record`: version, Gluon version,
  operation, configuration hash, binding hash, scope, graph, snapshot, the
  normalized request and the complete query. `discovery.replay(record)`
  re-executes it under the same configuration hash and reproduces the report
  byte for byte on the same snapshot; `replay(record, snapshot="other")` runs it
  elsewhere and `gluon.diff_reports(before, after)` lists added and removed
  candidate combinations or statements. A record from a different configuration
  is rejected.
- **Limit units.** `limits.unit` is `rows` (default) or `entities`: the query
  wraps a distinct-entity subquery bounded by `probe_rows`, and
  `per_entity_rows` caps qualifier combinations per entity. Completeness then
  carries `entity_limit`, `fetched_entities`, `per_entity_limit` and
  `truncated_entities`; the outer row bound is `(entities + 1) * (per_entity + 1)`
  and is reported as `row_limit`. Optional `limits.literal_chars` cuts long
  literals and marks them `truncated` with the original `length`.
- **Inspection profiles and filters.** A scope may declare `profiles`: follow
  one `in` or `out` `predicate` from the entity, then report the listed
  properties of the reached node (all when `report` is empty). `inspect` takes
  `profile`, or `direction` and `predicate` filters (reported under `filters`),
  and `inspect_many` runs a bounded batch (`limits.inspect_batch`, default 10)
  with one query per IRI. `labels(scope, iris)` names a bounded set of IRIs in one
  query over the scope's label predicates.
- **Progressive SPARQL.** `describe` returns `sparql`: the scope graph, rules,
  and for each term its group-graph-pattern `pattern`, `label` block and
  variable names, plus an `example` SELECT. An agent composes its own query from
  these and runs it through the host executor; the reference adapter's
  `check_select(query, graph)` is a validate-only guard that rejects non-SELECT,
  `FROM`, `SERVICE` and foreign `GRAPH` clauses without executing anything.

### Results and query handoff

Reports are plain dicts; `gluon.canonical_json` serializes them deterministically
(sorted keys, compact, no timing), so the same input and snapshot give
byte-identical JSON. Probe returns candidate combinations with IRI, labels,
`matches`, `qualifiers` and an `inspect` route, plus a compact `term` reference
(id, binding id, binding hash; the definition is in `describe`), the `matching`
basis and applied `filters`. Inspection returns incoming and outgoing `statements`
(or `node`/`predicate`/`value` rows under a profile). Both carry `source`,
`scope`, `graph`, `snapshot`, the complete executed `query`, `completeness` and
the `record`. IRIs are absolute; literals retain `datatype` and `language`;
blank nodes carry their `scope` and are not accepted as inspection targets.

The SELECT always contains `GRAPH <...>` and full IRIs, and in rows mode fetches
**row_limit + 1** rows to detect truncation. For parity, take at most
`row_limit` rows, group probe rows by `?entity` plus the expand-mode qualifier
variables, deduplicate labels and collect collect-mode values; inspection rows
map directly to statements. Duplicate-rich rows may yield fewer candidates than
the cap; no exact total or pagination is promised. See `examples/billing.py`,
`tests/test_host_contract.py` and `gluon.SCHEMA["protocol"]`.

Row limits and the deadline are required host configuration; callers can only
lower them with `limit` and `deadline_ms`, and may pass a `gluon.CancelSignal`
as `signal`. The core checks the deadline and cancellation after executor setup
and between rows, sets the executor's signal, and closes the iterator. **It
cannot preempt a synchronous store** that never yields; late output is rejected
once control returns. A host needing a hard stop runs its executor in a thread
or subprocess.

### Diagnostics

All failures are `gluon.DiscoveryError` with a stable `.code`: `INVALID_CONFIG`,
`INVALID_REQUEST`, `UNKNOWN_SCOPE`, `UNKNOWN_TERM`, `SOURCE_UNAVAILABLE`,
`SOURCE_ERROR`, `INVALID_RESULT`, `DEADLINE_EXCEEDED`, `CANCELLED`. For the
caller's own input the error names the field `.path` and a reason `.detail`;
`UNKNOWN_SCOPE`/`UNKNOWN_TERM` list valid `.options`. Source-side failures carry
none of that: a host may raise `gluon.SourceUnavailableError` as a marker, and
every other executor exception becomes a generic `SOURCE_ERROR` with no message,
cause or credentials. `.to_json()` matches `SCHEMA["$defs"]["Diagnostic"]`.

An empty complete probe has `outcome == "no_match"`; a failed query never
becomes that result. Truncation is explicit metadata. Unsupported request
fields, operators, unknown bindings, scopes, qualifiers or profiles, filters
that widen, and attempts to raise host caps are rejected before any source
access. There is no proposal or raw-query API in the core.

## Verification and limits

| Behavior | Maintained verification |
| --- | --- |
| Describe, probe, refinement, inspection, graph isolation, RDF encoding, diagnostics, shipped schema | `tests/test_discovery.py` on the billing fixture: both graph models, a separately configured namespace, explicit refinement/exclusivity, exact neighborhoods, scoped decoys, raw-query parity |
| Bounded collection and cancellation | `tests/test_lifecycle.py`: cap+1 sentinel, late pulls, late setup, caller cancellation (in-band and from a thread), partial failures, synchronous overrun, impostor diagnostics |
| Host contract | `tests/test_host_contract.py` on the reified registry fixture: two snapshots, one entity under two qualifier values, unbound optional qualifier, plain-graph twin, configured and narrowed filters, alias-only match, byte-identical replay and cross-snapshot diff, entity-unit truncation, profiles, batch inspection, guard rejections, and a check that no fixture vocabulary appears in core source |
| Pre-release additions | `tests/test_public_readiness.py`: compact term and single `filters` key, snapshot-required scopes, the `labels` operation, multiple search expressions, JSON loader and `python -m gluon validate|schema|vocabulary`, the standards vocabulary on a small OWL/SKOS graph |
| Host integration example | `examples/host_integration.py` and its test: stored record as a selection dependency, byte-identical replay, cross-snapshot diff, profile facts, guarded agent-authored query; record keys checked against the shipped schema |
| Packaging | `scripts/check_wheel.py`: wheel with no runtime dependencies, clean import, schema present, both real RDF examples in an isolated environment |

Additional validation on a pinned Wikidata subset (two typing conventions,
alias-only matches, qualified version statements with rank filters, a
304-statement hub, branching inspection and profiles, handoff parity, replay)
and a recorded cold-agent trial (12 of 12 attempts composed a correct scoped
query from `describe` output alone) are kept with the private project records;
their fixtures and traces are not part of this tree.

**Known limits:** optional qualifiers are restricted to one triple pattern on
an already-bound node (no nested optionals, unions, property paths or negation;
write one term per typing convention and compose the `UNION` from `describe`
fragments); no label-language fallback beyond the configured filter; in
entities mode the outer row bound can exhaust before every kept entity's
combinations are seen and is then reported as truncation; a blank-node
intermediate is reported with its scope but cannot be inspected; `inspect_many`
issues one query per IRI. No remote backend, multi-node shape, hierarchy or
inference, ranking, planner, service or stable Python API is implemented. No
efficiency claim is made.
