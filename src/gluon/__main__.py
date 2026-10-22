"""`python -m gluon` — development commands only; no runtime service.

  python -m gluon validate <configuration.json>   validate a configuration file, print a JSON verdict
  python -m gluon schema                          print the shipped protocol schema
  python -m gluon vocabulary [--prefix P]         print the standards vocabulary terms and profiles as JSON
  python -m gluon skills [--root DIR] [--target claude|codex|both] [--force]
                                                  write the gluon-setup and gluon-agent skills into DIR/.claude/skills and/or DIR/.agents/skills
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gluon", description="Gluon development commands (no service is started).")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate a JSON configuration file")
    validate.add_argument("path")
    sub.add_parser("schema", help="print the shipped protocol schema")
    vocabulary = sub.add_parser("vocabulary", help="print the standards vocabulary as JSON")
    vocabulary.add_argument("--prefix", default="standard")
    skills = sub.add_parser("skills", help="write the agent skills into a project (.claude/skills and/or .agents/skills)")
    skills.add_argument("--root", default=".", help="project root (default: current directory)")
    skills.add_argument("--target", choices=["claude", "codex", "both"], default="both", help="which agent directories to write (default: both)")
    skills.add_argument("--force", action="store_true", help="overwrite existing skill files")
    args = parser.parse_args(argv)
    if args.command == "skills":
        from .skills import install_skills
        try:
            for path in install_skills(args.root, target=args.target, force=args.force):
                print(path)
        except FileExistsError as error:
            print(str(error), file=sys.stderr)
            return 1
        return 0
    if args.command == "validate":
        from .loader import validate_file
        verdict = validate_file(args.path)
        print(json.dumps(verdict, indent=2, ensure_ascii=False))
        return 0 if verdict["ok"] else 1
    if args.command == "schema":
        from .schema import SCHEMA
        print(json.dumps(SCHEMA, indent=2, ensure_ascii=False))
        return 0
    from .vocabularies import standard_profiles, standard_terms
    print(json.dumps({"terms": standard_terms(prefix=args.prefix), "profiles": standard_profiles()}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
