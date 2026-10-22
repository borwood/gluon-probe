"""Reference host executor over in-memory RDFLib Datasets (optional extra).

Deliberately outside the dependency-free core. It exposes only a copy of the selected
named graph for the requested snapshot, rejects non-SELECT, ``FROM``/``FROM NAMED`` and
``SERVICE``, maps engine failures to source errors, and stops yielding when the request's
signal is set. It is a development/reference adapter, not an untrusted-query resource
sandbox and not the initial production backend. A production host must enforce the same
rules in its own executor.
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue

from .types import Row, SelectRequest, SourceUnavailableError


class QueryRejected(ValueError):
    """Raised by ``check_select`` for anything other than a plain local SELECT."""


def _algebra_nodes(value: Any) -> Iterator[CompValue]:
    if isinstance(value, CompValue):
        yield value
        for child in value.values():
            yield from _algebra_nodes(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _algebra_nodes(child)


def check_select(query: str, graph: str | None = None) -> Any:
    """Validate-only guard: parse, require SELECT, reject SERVICE and dataset clauses.

    When ``graph`` is given, every ``GRAPH`` clause must name that graph. This never
    executes anything.
    """
    try:
        prepared = prepareQuery(query)
    except Exception as error:  # noqa: BLE001 - rdflib raises assorted parser errors
        raise QueryRejected(f"SPARQL parse error: {type(error).__name__}") from None
    if prepared.algebra.name != "SelectQuery":
        raise QueryRejected("Only SELECT queries are accepted")
    graphs: set[str] = set()
    for node in _algebra_nodes(prepared.algebra):
        if node.name == "ServiceGraphPattern":
            raise QueryRejected("SERVICE is not available")
        if "datasetClause" in node and node["datasetClause"]:
            raise QueryRejected("FROM / FROM NAMED are not available")
        if node.name == "Graph" and isinstance(node.get("term"), URIRef):
            graphs.add(str(node["term"]))
    if graph is not None and graphs and graphs != {graph}:
        raise QueryRejected("Query selects a graph outside the requested scope")
    return prepared


def json_term(term: Any) -> dict[str, Any]:
    if isinstance(term, URIRef):
        return {"type": "iri", "value": str(term)}
    if isinstance(term, BNode):
        return {"type": "blank", "value": str(term)}
    if isinstance(term, Literal):
        out: dict[str, Any] = {"type": "literal", "value": str(term)}
        if term.language:
            out["language"] = term.language
        elif term.datatype is not None:
            out["datatype"] = str(term.datatype)
        return out
    raise TypeError("Unsupported RDF term")


def create_local_executor(sources: Iterable[dict[str, Any]]):
    """Build an executor over host-owned datasets.

    ``sources`` entries: ``{"source", "scope", "graph", "dataset"}`` and/or
    ``{"source", "scope", "graph", "datasets": {snapshot_id: Dataset}}``. A request naming a
    snapshot selects that dataset; a request without a snapshot uses ``dataset``. Unknown
    snapshots are unavailable, never substituted. Each query runs against a fresh copy of
    the selected graph only, so other graphs in the host dataset can never leak into results.
    """
    configured = [dict(s) for s in sources]

    def execute(request: SelectRequest) -> Iterator[Row]:
        source = next((s for s in configured if s["source"] == request.source and s["scope"] == request.scope
                       and s["graph"] == request.graph), None)
        if source is None:
            raise SourceUnavailableError()
        if request.snapshot is None:
            dataset = source.get("dataset")
        else:
            dataset = source.get("datasets", {}).get(request.snapshot) if "datasets" in source else source.get("dataset")
        if dataset is None:
            raise SourceUnavailableError()
        if request.signal.is_set():
            return iter(())
        prepared = check_select(request.query, request.graph)
        selected = Dataset()
        target = selected.graph(URIRef(source["graph"]))
        for triple in dataset.graph(URIRef(source["graph"])):
            target.add(triple)
        result = selected.query(prepared)

        def rows() -> Iterator[Row]:
            for binding in result:
                if request.signal.is_set():
                    return
                yield {str(var): json_term(term) for var, term in binding.asdict().items()}

        return rows()

    return execute
