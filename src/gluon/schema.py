"""Machine-readable description of the calls and results.

``schema.json`` is shipped inside the package. It is hand-maintained and checked by the
test suite against real reports, so the shipped document cannot drift from behavior.
"""
from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_schema() -> dict[str, Any]:
    with resources.files(__package__).joinpath("schema.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


SCHEMA: dict[str, Any] = load_schema()
