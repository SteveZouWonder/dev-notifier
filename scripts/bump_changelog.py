#!/usr/bin/env python3
"""CHANGELOG archival tool.

Archives the ``## [Unreleased]`` section of CHANGELOG.md into a concrete
version section ``## [vX.Y.Z] - YYYY-MM-DD`` and restores an empty
``## [Unreleased]`` at the top. Also supports extracting the body of a given
version (or Unreleased) for injection into GitHub Release notes.

Design goals:
  - Standard library only, so it runs on any CI runner without extra deps.
  - Idempotent: ``bump`` refuses to re-archive an already-archived version.
  - Non-destructive: the rest of the file is preserved verbatim.

Usage:
  # Archive Unreleased into v1.2.3 (date defaults to today; override with --date)
  python scripts/bump_changelog.py bump --version 1.2.3

  # Preview the archived result without writing back
  python scripts/bump_changelog.py bump --version 1.2.3 --dry-run

  # Extract a version's body (without the heading); version may be
  # vX.Y.Z / X.Y.Z / unreleased
  python scripts/bump_changelog.py extract --version 1.2.3

The version may be given with or without a leading ``v``; it is normalized to
``vX.Y.Z`` internally.

@author SteveZou
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

# CHANGELOG.md lives at the repo root (the parent of this script's dir).
DEFAULT_CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

UNRELEASED_HEADING = "## [Unreleased]"
# Empty Unreleased block restored at the top after archiving (with a hint).
EMPTY_UNRELEASED_BLOCK = (
    "## [Unreleased]\n"
    "\n"
    "> Record unreleased changes for the next version here. "
    "On release they are moved under the corresponding version number.\n"
)

SEMVER_RE = re.compile(r"^v?\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?$")


def normalize_version(version: str) -> str:
    """Normalize a user-supplied version to the ``vX.Y.Z`` form and validate it."""
    version = version.strip()
    if not SEMVER_RE.match(version):
        raise ValueError(
            f"Version '{version}' is not a valid semantic version "
            f"(expected X.Y.Z, e.g. 1.0.0)"
        )
    return version if version.startswith("v") else f"v{version}"


def find_section_bounds(lines: list[str], heading_text: str) -> tuple[int, int] | None:
    """Locate a level-2 heading section's ``[start, end)`` range in ``lines``.

    Start is the heading line itself; end is the next ``## `` heading line
    (exclusive) or EOF. ``heading_text`` is e.g. ``## [Unreleased]`` or
    ``## [v1.2.3]``; matched by "stripped line starts with it" so it tolerates
    a trailing `` - date``. Returns None if not found.
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(heading_text):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return start, end


def extract_body(lines: list[str], start: int, end: int) -> str:
    """Extract a section body (drop the heading line; trim leading/trailing blanks)."""
    body = lines[start + 1 : end]
    while body and body[0].strip() == "":
        body.pop(0)
    while body and body[-1].strip() == "":
        body.pop()
    return "\n".join(body)


# Conventional Commits type -> CHANGELOG group heading mapping.
# The order here drives the output order of groups in a version section.
CC_TYPE_TO_GROUP: dict[str, str] = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "docs": "Documentation",
    "build": "Build",
    "ci": "Build",
    "revert": "Reverted",
}
# Display order of groups (stable order of CC_TYPE_TO_GROUP.values() + fallback).
GROUP_ORDER: list[str] = [
    "Added",
    "Fixed",
    "Changed",
    "Documentation",
    "Build",
    "Reverted",
    "Other",
]

# Parse a Conventional Commit subject: type(scope)!: subject
CC_SUBJECT_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$"
)

# Commits that must not enter the CHANGELOG (noise): archive commits, version
# bumps, merges, etc.
SKIP_SUBJECT_RE = re.compile(
    r"(archive CHANGELOG|bump version|Merge (branch|pull request))",
    re.IGNORECASE,
)


def _run_git(args: list[str]) -> str | None:
    """Run a git command and return stdout; return None on failure/absence."""
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout


def collect_commit_subjects(since_ref: str | None, until_ref: str | None) -> list[str]:
    """Collect non-merge commit subjects in the ``(since_ref, until_ref]`` range.

    - When ``since_ref`` is None, falls back to the full history of ``until_ref``
      (or HEAD);
    - ``until_ref`` defaults to HEAD;
    - merge and archival noise commits are filtered out.
    Returns subjects in git log's default newest-to-oldest order.
    """
    until = until_ref or "HEAD"
    rev_range = f"{since_ref}..{until}" if since_ref else until
    out = _run_git(["log", "--no-merges", "--pretty=format:%s", rev_range])
    if out is None:
        return []
    subjects: list[str] = []
    for line in out.splitlines():
        subject = line.strip()
        if not subject or SKIP_SUBJECT_RE.search(subject):
            continue
        subjects.append(subject)
    return subjects


def previous_version_ref(version: str) -> str | None:
    """Return the most recent semantic-version tag preceding ``version``.

    Returns None if there is no prior tag (caller falls back to full history).
    """
    out = _run_git(["tag", "--sort=-v:refname"])
    if out is None:
        return None
    tags = [
        t.strip()
        for t in out.splitlines()
        if re.match(r"^v\d+\.\d+\.\d+$", t.strip())
    ]
    if version in tags:
        idx = tags.index(version)
        # tags are newest-to-oldest; entries after version are older versions.
        if idx + 1 < len(tags):
            return tags[idx + 1]
        return None
    # version not yet tagged: tags is newest-to-oldest, the first is the prev one.
    return tags[0] if tags else None


def generate_body_from_git(
    version: str, since_ref: str | None, until_ref: str | None
) -> str:
    """Generate a CHANGELOG body from git commits grouped by Conventional Commits.

    ``version`` is the target version with a leading ``v``, used to infer the
    previous tag when ``since_ref`` is omitted. Returns assembled Markdown (with
    ``###`` group subheadings and list items); empty string if no commits.
    """
    if since_ref is None:
        since_ref = previous_version_ref(version)
    subjects = collect_commit_subjects(since_ref, until_ref)
    if not subjects:
        return ""

    # Aggregate by group, preserving newest-to-oldest order within each group.
    grouped: dict[str, list[str]] = {}
    for subject in subjects:
        match = CC_SUBJECT_RE.match(subject)
        if match:
            cc_type = match.group("type").lower()
            group = CC_TYPE_TO_GROUP.get(cc_type, "Other")
            text = match.group("subject").strip()
        else:
            group = "Other"
            text = subject
        grouped.setdefault(group, []).append(text)

    lines: list[str] = []
    for group in GROUP_ORDER:
        items = grouped.get(group)
        if not items:
            continue
        if lines:
            lines.append("")
        lines.append(f"### {group}")
        for item in items:
            lines.append(f"- {item}")
    return "\n".join(lines)


def cmd_extract(args: argparse.Namespace) -> int:
    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    target = args.version.strip()
    if target.lower() == "unreleased":
        heading = UNRELEASED_HEADING
    else:
        heading = f"## [{normalize_version(target)}]"

    bounds = find_section_bounds(lines, heading)
    if bounds is None:
        print(f"::error::Section {heading} not found", file=sys.stderr)
        return 1
    start, end = bounds
    body = extract_body(lines, start, end)
    # Drop the usage-hint blockquote lines (starting with '>') so they do not
    # pollute the Release Notes.
    body_lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith(">")]
    body = "\n".join(body_lines).strip()
    sys.stdout.write(body + ("\n" if body else ""))
    return 0


def cmd_bump(args: argparse.Namespace) -> int:
    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    version = normalize_version(args.version)
    date = args.date or datetime.date.today().isoformat()
    new_heading = f"## [{version}] - {date}"

    # Refuse to re-archive an already-present version (idempotency guard).
    if find_section_bounds(lines, f"## [{version}]") is not None:
        print(
            f"::error::Version {version} already exists in CHANGELOG; "
            f"refusing to re-archive",
            file=sys.stderr,
        )
        return 1

    bounds = find_section_bounds(lines, UNRELEASED_HEADING)
    if bounds is None:
        print("::error::No [Unreleased] section found in CHANGELOG", file=sys.stderr)
        return 1
    start, end = bounds

    body = extract_body(lines, start, end)
    # Drop the usage-hint blockquote lines to decide whether [Unreleased] has
    # any real releasable content.
    content_lines = [
        ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith(">")
    ]

    if content_lines:
        # Prefer human-authored [Unreleased] content (keep body, drop hint lines).
        archived_body_lines = [
            ln for ln in body.splitlines() if not ln.lstrip().startswith(">")
        ]
        source = "[Unreleased] (hand-written)"
    elif args.no_git_fallback:
        # Explicitly disabling git fallback keeps the "must fill in manually" rule.
        print(
            "::error::[Unreleased] has no archivable changes; "
            "please fill it in before releasing",
            file=sys.stderr,
        )
        return 1
    else:
        # [Unreleased] is empty: auto-generate from git commits by Conventional
        # Commits.
        generated = generate_body_from_git(version, args.since, args.until)
        if not generated.strip():
            print(
                "::error::[Unreleased] is empty and no changes could be generated "
                "from git commits (no prior commits or git unavailable). "
                "Please fill in [Unreleased] before releasing.",
                file=sys.stderr,
            )
            return 1
        archived_body_lines = generated.splitlines()
        source = "git commit history"
        print(
            f"::notice::[Unreleased] was empty; filled {version} changes from "
            f"{source}"
        )

    # Trim leading/trailing blank lines.
    while archived_body_lines and archived_body_lines[0].strip() == "":
        archived_body_lines.pop(0)
    while archived_body_lines and archived_body_lines[-1].strip() == "":
        archived_body_lines.pop()

    # Assemble the new file content:
    #   [0, start)            = content before Unreleased (title, intro, etc.)
    #   empty Unreleased block
    #   archived version section
    #   [end, ...)            = everything after the old Unreleased (history)
    before = lines[:start]
    after = lines[end:]

    new_block: list[str] = []
    new_block.extend(EMPTY_UNRELEASED_BLOCK.rstrip("\n").splitlines())
    new_block.append("")
    new_block.append(new_heading)
    new_block.append("")
    new_block.extend(archived_body_lines)
    new_block.append("")

    new_lines = before + new_block + after
    new_text = "\n".join(new_lines).rstrip("\n") + "\n"

    if args.dry_run:
        sys.stdout.write(new_text)
        return 0

    path.write_text(new_text, encoding="utf-8")
    print(f"Archived [Unreleased] into {new_heading}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CHANGELOG archival tool")
    parser.add_argument(
        "--file",
        default=str(DEFAULT_CHANGELOG),
        help="CHANGELOG path (defaults to CHANGELOG.md at the repo root)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_bump = sub.add_parser("bump", help="Archive [Unreleased] into a version section")
    p_bump.add_argument("--version", required=True, help="Version (with or without v prefix)")
    p_bump.add_argument("--date", help="Release date YYYY-MM-DD (defaults to today)")
    p_bump.add_argument(
        "--dry-run", action="store_true", help="Print the archived content only; do not write back"
    )
    p_bump.add_argument(
        "--since",
        help="Start ref (exclusive) for git auto-fill. Defaults to the previous version tag",
    )
    p_bump.add_argument(
        "--until",
        help="End ref (inclusive) for git auto-fill. Defaults to HEAD",
    )
    p_bump.add_argument(
        "--no-git-fallback",
        action="store_true",
        help="Disable git auto-fill when [Unreleased] is empty; require manual content",
    )
    p_bump.set_defaults(func=cmd_bump)

    p_extract = sub.add_parser("extract", help="Extract a version's / Unreleased body")
    p_extract.add_argument(
        "--version", required=True, help="Version number or 'unreleased'"
    )
    p_extract.set_defaults(func=cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
