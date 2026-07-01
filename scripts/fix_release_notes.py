#!/usr/bin/env python3
"""Backfill/repair GitHub Release notes from CHANGELOG.md.

Older releases were published with a placeholder "What's changed" section
because release notes were generated before the CHANGELOG was archived. This
tool rebuilds the notes for one or more existing releases from the current
CHANGELOG (which now contains the archived entries) and updates them via the
``gh`` CLI, using the exact same formatting as the release workflow (both share
``bump_changelog.build_release_notes``).

Usage:
  # Preview what every release's notes would become (no changes made)
  python scripts/fix_release_notes.py --all --dry-run

  # Update specific releases
  python scripts/fix_release_notes.py --version 1.2.0 --version 1.3.0

  # Update all releases found via `gh release list`
  python scripts/fix_release_notes.py --all

Requires the GitHub CLI (`gh`) to be installed and authenticated with write
access to the repository.

@author SteveZou
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Reuse the shared notes builder so output matches the release workflow exactly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bump_changelog as bc  # noqa: E402

DEFAULT_CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )


def _repo_slug() -> str | None:
    """Return owner/name for the current repo via gh, or None on failure."""
    r = _gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _list_release_versions() -> list[str]:
    """Return released versions (without a leading v), newest first, via gh."""
    r = _gh(["release", "list", "--json", "tagName", "--jq", ".[].tagName"])
    if r.returncode != 0:
        # Older gh may not support --json for release list; fall back to plain.
        r = _gh(["release", "list"])
        tags = [ln.split("\t")[2] for ln in r.stdout.splitlines() if "\t" in ln]
    else:
        tags = [t.strip() for t in r.stdout.splitlines() if t.strip()]
    versions = []
    for t in tags:
        t = t.strip()
        if TAG_RE.match(t):
            versions.append(t.lstrip("v"))
    return versions


def _previous_tag(version: str, all_versions: list[str]) -> str | None:
    """Pick the tag ordered right before v{version} (semantic sort)."""
    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    ordered = sorted({*all_versions, version}, key=key)
    idx = ordered.index(version)
    if idx == 0:
        return None
    return f"v{ordered[idx - 1]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill GitHub Release notes")
    parser.add_argument(
        "--file", default=str(DEFAULT_CHANGELOG), help="CHANGELOG path"
    )
    parser.add_argument(
        "--version", action="append", default=[],
        help="Version to fix (repeatable); omit with --all for every release",
    )
    parser.add_argument(
        "--all", action="store_true", help="Fix all releases found via gh"
    )
    parser.add_argument(
        "--repo", help="owner/name (defaults to the current repo via gh)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print notes; do not update"
    )
    args = parser.parse_args(argv)

    lines = Path(args.file).read_text(encoding="utf-8").splitlines()

    repo = args.repo or _repo_slug()
    if not repo:
        print("::error::Could not determine repo; pass --repo owner/name",
              file=sys.stderr)
        return 1

    all_versions = _list_release_versions()
    if args.all:
        targets = all_versions
    else:
        targets = [v.strip().lstrip("v") for v in args.version]
    if not targets:
        print("::error::No versions given. Use --version X.Y.Z or --all",
              file=sys.stderr)
        return 1

    rc = 0
    for version in targets:
        prev = _previous_tag(version, all_versions)
        notes, had_changes = bc.build_release_notes(lines, version, repo, prev)
        if not had_changes:
            print(f"WARN v{version}: no CHANGELOG entries; skipping to avoid "
                  f"overwriting with a placeholder", file=sys.stderr)
            continue
        if args.dry_run:
            print(f"===== v{version} (dry-run) =====")
            print(notes)
            continue
        r = _gh(["release", "edit", f"v{version}", "--notes", notes])
        if r.returncode != 0:
            print(f"::error::Failed to update v{version}: {r.stderr.strip()}",
                  file=sys.stderr)
            rc = 1
        else:
            print(f"Updated release notes for v{version}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
