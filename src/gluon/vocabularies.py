"""One opt-in, standards-based vocabulary.

Terms bound to RDFS, OWL and SKOS so a host can let agents discover its *own* ontology
terms (classes, properties, concepts, schemes) before any host-specific binding exists.
The host adds these term definitions to a scope; nothing here is applied by default, and
no domain concept appears. Label predicates default to the common five; a host may
override them and the accepted languages.
"""
from __future__ import annotations

from typing import Any

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDF_PROPERTY = "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
SCHEMA_ORG = "http://schema.org/"
DCTERMS = "http://purl.org/dc/terms/"

COMMON_LABEL_PREDICATES = [RDFS + "label", SKOS + "prefLabel", SKOS + "altLabel", SCHEMA_ORG + "name", DCTERMS + "title"]

_TERMS: list[tuple[str, str, str]] = [
    # (term id, definition, class IRI the entity is an instance of)
    ("class", "An OWL class (owl:Class).", OWL + "Class"),
    ("rdfs-class", "An RDFS class (rdfs:Class); many ontologies use owl:Class instead, so probe both.", RDFS + "Class"),
    ("object-property", "An OWL object property (owl:ObjectProperty).", OWL + "ObjectProperty"),
    ("datatype-property", "An OWL datatype property (owl:DatatypeProperty).", OWL + "DatatypeProperty"),
    ("property", "An RDF property (rdf:Property); OWL properties are often typed this way too.", RDF_PROPERTY),
    ("concept", "A SKOS concept (skos:Concept).", SKOS + "Concept"),
    ("concept-scheme", "A SKOS concept scheme (skos:ConceptScheme).", SKOS + "ConceptScheme"),
    ("individual", "An OWL named individual (owl:NamedIndividual).", OWL + "NamedIndividual"),
]


def standard_terms(label_predicates: list[str] | None = None, languages: list[str] | None = None,
                   prefix: str = "standard") -> list[dict[str, Any]]:
    """Term definitions for the standards vocabulary; add them to a scope's `terms`.

    `prefix` namespaces the binding ids so they cannot collide with host bindings.
    """
    predicates = list(label_predicates or COMMON_LABEL_PREDICATES)
    terms: list[dict[str, Any]] = []
    for term_id, definition, class_iri in _TERMS:
        binding: dict[str, Any] = {
            "id": f"{prefix}/{term_id}",
            "label_predicates": predicates,
            "patterns": [{"subject": {"type": "variable", "value": "entity"}, "predicate": {"type": "iri", "value": RDF_TYPE},
                          "object": {"type": "iri", "value": class_iri}}],
        }
        if languages:
            binding["languages"] = list(languages)
        terms.append({"id": term_id, "definition": definition, "binding": binding})
    return terms


def standard_profiles() -> list[dict[str, Any]]:
    """Inspection profiles that follow RDFS and SKOS structure; add them to a scope's `profiles`."""
    return [
        {"id": "subclasses", "direction": "in", "predicate": RDFS + "subClassOf", "report": [RDFS + "label", SKOS + "prefLabel"]},
        {"id": "superclasses", "direction": "out", "predicate": RDFS + "subClassOf", "report": [RDFS + "label", SKOS + "prefLabel"]},
        {"id": "narrower", "direction": "out", "predicate": SKOS + "narrower", "report": [SKOS + "prefLabel", SKOS + "altLabel"]},
        {"id": "broader", "direction": "out", "predicate": SKOS + "broader", "report": [SKOS + "prefLabel", SKOS + "altLabel"]},
        {"id": "domain-range", "direction": "out", "predicate": RDFS + "domain", "report": [RDFS + "label"]},
    ]


def standard_scope(scope_id: str, source: str, graph: str, label_predicates: list[str] | None = None,
                   languages: list[str] | None = None) -> dict[str, Any]:
    """A complete scope definition using only the standards vocabulary; a convenience for first contact."""
    predicates = list(label_predicates or COMMON_LABEL_PREDICATES)
    scope: dict[str, Any] = {"id": scope_id, "source": source, "graph": graph,
                             "terms": standard_terms(predicates, languages), "profiles": standard_profiles(),
                             "labels": {"predicates": predicates}}
    if languages:
        scope["labels"]["languages"] = list(languages)
    return scope
