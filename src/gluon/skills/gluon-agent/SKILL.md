---
name: gluon-agent
description: Use a Gluon discovery probe correctly when answering questions over a host's RDF graph - describe, probe, inspect, labels, then write your own scoped SPARQL from the fragments. Use when a task says to use Gluon, gluon-probe, or an MCP tool built on it.
---

# Working through Gluon

Gluon reports what was searched, under which scope, snapshot and limits, and
how complete the search was. It never decides what a match means; you do.

## Order of work

1. `describe(scope)` once. It costs no graph access. Note the term ids, their
   definitions, declared qualifiers (`required` or optional, `expand` or
   `collect`), any default `filters`, the `profiles`, and `sparql.terms[...]`
   fragments for later.
2. `probe(scope, type, text=[...])` with a term id and one or more short
   substrings (aliases are searched too). Or `probe(..., iri=...)` to check
   whether a known entity satisfies a term.
3. `inspect(scope, iri)` for a neighborhood, or `inspect(..., profile=...)` to
   get a record's facts in one call; `direction`/`predicate` narrow it.
4. `labels(scope, iris=[...])` to name bare IRIs you were shown.
5. Graduate: compose a SELECT from `describe.sparql.terms[<term>].pattern`
   inside `GRAPH <describe.sparql.graph> { ... }` and run it through the host's
   executor. Keep the GRAPH clause; never use `SERVICE`, `FROM` or updates.

## Reading reports honestly

- `outcome: "no_match"` with `completeness.complete: true` means no evidence in
  this scope and snapshot. It is not proof that something does not exist.
- `completeness.truncated: true` means narrow the text, add a filter, lower
  the ambition, or page by writing your own query. Do not summarize a truncated
  result as complete. In entities mode read `truncated_entities`.
- Each candidate is one combination of entity plus expand-mode qualifier
  values. The same IRI can appear several times with different qualifiers;
  that is data, not duplication. `null` means the qualifier was not recorded.
- `matches` tell you which label predicate and which of your search expressions
  matched, and whether exactly or as a substring. An alias match is not an
  identity claim.
- `filters` in a report are the filters that were applied. If a filter hid
  something you need, narrow differently; you cannot widen a host filter.
- Always pass the `snapshot` you were given, and quote it when you report.

## Errors

`DiscoveryError.code` is stable: `INVALID_REQUEST`, `UNKNOWN_SCOPE` and
`UNKNOWN_TERM` carry `path`, `detail` and valid `options` for your own input;
fix and retry once. `SOURCE_UNAVAILABLE`, `SOURCE_ERROR`, `DEADLINE_EXCEEDED`
and `CANCELLED` are not empty results; say so rather than concluding absence.

## What to hand back

Entity IRIs with their labels, the qualifier values you relied on, the
`completeness` of each search, and the `record` of any search a host should
remember. Let the host record what the match means.
