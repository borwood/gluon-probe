"""Configuration files: JSON only, standard library only.

A configuration file is the JSON form of the `Configuration` object. Validation happens
in the core validator; this module just reads the file and reports diagnostics with the
field path. `python -m gluon validate <path>` wraps `validate_file`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import DiscoveryError
from .validation import configuration


def load_configuration(path: str | Path) -> dict[str, Any]:
    """Read and validate a JSON configuration file; returns the normalized configuration."""
    file = Path(path)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DiscoveryError("INVALID_CONFIG", str(file), "Configuration file not found.") from None
    except json.JSONDecodeError as error:
        raise DiscoveryError("INVALID_CONFIG", str(file), f"Not valid JSON: line {error.lineno}, column {error.colno}.") from None
    return configuration(raw)


def validate_file(path: str | Path) -> dict[str, Any]:
    """Validate without raising; returns {"ok": bool, "configuration_hash"?: str, "diagnostic"?: {...}, "summary"?: {...}}."""
    from .core import content_hash
    try:
        normalized = load_configuration(path)
    except DiscoveryError as error:
        return {"ok": False, "diagnostic": error.to_json()}
    summary = {"scopes": [{"id": s["id"], "graph": s["graph"], "terms": [t["id"] for t in s["terms"]],
                           "profiles": [p["id"] for p in s.get("profiles", [])], "snapshot": s.get("snapshot", "optional")}
                          for s in normalized["scopes"]],
               "limits": normalized["limits"]}
    return {"ok": True, "configuration_hash": content_hash(normalized), "summary": summary}
