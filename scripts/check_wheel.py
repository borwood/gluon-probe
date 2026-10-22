"""Build the wheel, install it into a clean virtual environment, and run it.

Steps:
1. ``python -m build --wheel`` into a task-owned temporary directory.
2. Create a fresh venv; install the wheel with ``--no-index`` (the core has no dependencies).
3. Import gluon with a fake executor and run a probe (no RDF engine).
4. Install the pinned RDFLib extra into that venv from a local wheel directory (downloaded
   through pip, which may use its cache or the network) and run the real billing example
   from copies outside the checkout.

Run from the repository root with the project interpreter:
    python scripts/check_wheel.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ["rdflib==7.1.4", "pyparsing==3.2.3"]


def run(args: list[str], cwd: Path, **env: str) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=600,
                            env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", **env})
    if result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main() -> None:
    parent = Path(tempfile.gettempdir()).resolve()
    temporary = Path(tempfile.mkdtemp(prefix="gluon-wheel-", dir=parent))
    try:
        dist = temporary / "dist"
        run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(dist)], ROOT)
        wheels = list(dist.glob("gluon_probe-*.whl"))
        assert len(wheels) == 1, wheels
        wheel = wheels[0]

        venv = temporary / "venv"
        run([sys.executable, "-m", "venv", str(venv)], temporary)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], temporary)
        metadata = run([str(python), "-m", "pip", "show", "gluon-probe"], temporary)
        requires = next((line for line in metadata.splitlines() if line.startswith("Requires:")), "Requires:")
        assert requires.strip() == "Requires:", f"core must have no runtime dependencies: {requires}"

        consumer = temporary / "consumer"
        consumer.mkdir()
        (consumer / "smoke.py").write_text(
            "import gluon, json\n"
            "config = {'scopes': [{'id': 'local', 'source': 'host', 'graph': 'urn:g', 'terms': [{'id': 'thing', 'definition': 'thing', 'binding': {'id': 'b', 'label_predicate': 'urn:label', 'patterns': [{'subject': {'type': 'variable', 'value': 'entity'}, 'predicate': {'type': 'iri', 'value': 'urn:type'}, 'object': {'type': 'iri', 'value': 'urn:Thing'}}]}}]}], 'limits': {'probe_rows': 2, 'inspect_rows': 2, 'deadline_ms': 100}}\n"
            "def execute(request):\n"
            "    yield {'entity': {'type': 'iri', 'value': 'urn:one'}, 'label': {'type': 'literal', 'value': 'name'}}\n"
            "d = gluon.create_discovery(config, execute)\n"
            "report = d.probe({'scope': 'local', 'type': 'thing', 'text': 'name'})\n"
            "assert report['candidates'][0]['iri'] == 'urn:one', report\n"
            "assert not hasattr(d, 'propose')\n"
            "assert gluon.SCHEMA['$defs']['ProbeReport']\n"
            "print(json.dumps({'ok': True, 'version': gluon.__version__}))\n",
            encoding="utf-8")
        smoke = run([str(python), str(consumer / "smoke.py")], consumer)
        assert json.loads(smoke.strip())["ok"]
        # The agent skills ship in the wheel and install into a project directory.
        script = venv / ("Scripts/gluon-probe.exe" if os.name == "nt" else "bin/gluon-probe")
        run([str(script), "skills", "--root", str(consumer)], consumer)
        for agent_dir in (".claude", ".agents"):
            for name in ("gluon-setup", "gluon-agent"):
                assert (consumer / agent_dir / "skills" / name / "SKILL.md").read_text(encoding="utf-8").startswith("---" + chr(10) + "name: " + name)

        # Install only the host's optional RDF tools, then run the real example from copies.
        links = temporary / "wheels"
        run([sys.executable, "-m", "pip", "download", "-q", "-d", str(links), *PINS], temporary)
        run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(links), *PINS], temporary)
        for relative in ["tests/__init__.py", "tests/support/__init__.py", "tests/support/billing.py", "examples/billing.py",
                         "tests/support/billing.trig"]:
            destination = consumer / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (ROOT / relative).exists():
                shutil.copyfile(ROOT / relative, destination)
            else:
                destination.write_text("", encoding="utf-8")
        output = run([str(python), str(consumer / "examples" / "billing.py")], consumer)
        reports = [json.loads(line) for line in output.strip().splitlines()]
        assert len(reports) == 2 and all(r["handoff_matches"] and r["surfaces"] == 4 for r in reports), reports
        print(f"Wheel check passed: {wheel.name}; runtime dependencies=0; clean import, fake-executor probe, "
              f"schema present, and both real RDF examples passed in an isolated environment.")
    finally:
        target = temporary.resolve()
        assert target.parent == parent and target.name.startswith("gluon-wheel-")
        shutil.rmtree(target, ignore_errors=True)


if __name__ == "__main__":
    main()
