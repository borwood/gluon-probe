"""Gluon: a read-only, deterministic discovery probe for agents inside host knowledge systems.

Distributed as ``gluon-probe``; import name ``gluon``. Experimental; no stable API yet.
The core has no runtime dependencies; ``gluon.rdflib_executor`` is an optional extra.
"""
from .core import Discovery, canonical_json, content_hash, create_discovery, diff_reports
from .loader import load_configuration, validate_file
from .schema import SCHEMA, load_schema
from .types import (CancelSignal, Candidate, Completeness, Configuration, DiscoveryError, ErrorCode, InspectReport,
                    InspectRequest, ProbeReport, ProbeRequest, Row, ScopeDefinition, SearchRecord, SelectExecutor,
                    SelectRequest, SourceUnavailableError, TermDefinition)
from .vocabularies import COMMON_LABEL_PREDICATES, standard_profiles, standard_scope, standard_terms

__version__ = "0.1.0"

__all__ = [
    "COMMON_LABEL_PREDICATES", "CancelSignal", "Candidate", "Completeness", "Configuration", "Discovery", "DiscoveryError",
    "ErrorCode", "InspectReport", "InspectRequest", "ProbeReport", "ProbeRequest", "Row", "SCHEMA", "ScopeDefinition",
    "SearchRecord", "SelectExecutor", "SelectRequest", "SourceUnavailableError", "TermDefinition", "__version__",
    "canonical_json", "content_hash", "create_discovery", "diff_reports", "load_configuration", "load_schema",
    "standard_profiles", "standard_scope", "standard_terms", "validate_file",
]
