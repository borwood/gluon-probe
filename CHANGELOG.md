# Changelog

All notable changes to `gluon-probe` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow the
rule in the README's Versioning section. The package version, the schema `$id`
and the release tag (`v<version>`) always agree.

## [0.1.0] - 2026-10-22

First public release. Experimental: report and search-record shapes are the
compatibility surface; Python call signatures may still change before 1.0.

### Added

- Read-only discovery operations over a host-supplied executor: `describe`,
  `probe` (text or IRI), `inspect` (plain, filtered, or by profile),
  `inspect_many`, `labels`, `replay` and `diff_reports`.
- Host contract: per-request snapshot identity (optionally required per scope),
  declared qualifiers with required/optional and expand/collect handling,
  configured filters that callers may only narrow, alias predicates with match
  basis, row or entity limit units with per-entity caps, literal length caps,
  inspection profiles, an embedded replayable search record (version 2).
- Progressive SPARQL: `describe` returns binding fragments and an example query;
  the reference RDFLib executor offers a validate-only `check_select` guard.
- Opt-in standards vocabulary bound to RDFS, OWL and SKOS, with profiles.
- JSON configuration loader and `python -m gluon validate|schema|vocabulary|skills`
  (also the `gluon-probe` console script).
- Agent skills `gluon-setup` and `gluon-agent`, shipped in the package and
  installable into `.claude/skills` and `.agents/skills`.
- Shipped protocol schema (`gluon.SCHEMA`), host integration note, examples,
  tests, and a clean-environment wheel check.

[0.1.0]: https://github.com/borwood/gluon-probe/releases/tag/v0.1.0
