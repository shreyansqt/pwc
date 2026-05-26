#!/usr/bin/env python3
"""Idempotently splice PWC's marked section into a workspace CLAUDE.md.

The section (claude-md-section.md) is bounded by <!-- PWC:START --> / <!-- PWC:END
--> markers. On install: if the target has those markers, replace what's between
them (so re-installing updates the section in place); otherwise append the section,
creating the file if it doesn't exist. Existing CLAUDE.md content is never touched.

Usage:  claude_md.py --target <path-to-CLAUDE.md>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

START = "<!-- PWC:START"
END = "<!-- PWC:END -->"
_SECTION = Path(__file__).resolve().parent.parent / "claude-md-section.md"


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_md.py", description=__doc__)
    p.add_argument("--target", required=True, help="path to the workspace CLAUDE.md")
    args = p.parse_args(argv)

    section = _SECTION.read_text().strip("\n")
    target = Path(args.target)
    existing = target.read_text() if target.exists() else ""

    if START in existing and END in existing:
        # Replace the managed block in place, leaving everything else as-is.
        pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
        updated = pattern.sub(section, existing)
        action = "updated"
    elif existing.strip():
        updated = existing.rstrip("\n") + "\n\n" + section + "\n"
        action = "appended"
    else:
        updated = section + "\n"
        action = "created"

    target.write_text(updated)
    print(f"pwc: CLAUDE.md section {action} in {target}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
