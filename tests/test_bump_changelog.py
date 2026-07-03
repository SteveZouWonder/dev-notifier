"""Tests for scripts/bump_changelog.py — version normalization, section parsing,
git-based body generation, archival (bump), and release-note assembly.

Git interactions are mocked so tests are hermetic and deterministic.

@author SteveZou
"""
import argparse

import pytest

import bump_changelog as bc


# ---------------------------------------------------------------------------
# normalize_version
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1.2.3", "v1.2.3"),
    ("v1.2.3", "v1.2.3"),
    ("1.0.0-rc.1", "v1.0.0-rc.1"),
])
def test_normalize_version_valid(raw, expected):
    assert bc.normalize_version(raw) == expected


@pytest.mark.parametrize("raw", ["1.2", "abc", "", "v1"])
def test_normalize_version_invalid(raw):
    with pytest.raises(ValueError):
        bc.normalize_version(raw)


# ---------------------------------------------------------------------------
# find_section_bounds / extract_body
# ---------------------------------------------------------------------------

SAMPLE = [
    "# Changelog",
    "",
    "## [Unreleased]",
    "",
    "### Added",
    "- new thing",
    "",
    "## [v1.0.0] - 2024-01-01",
    "",
    "- first release",
]


def test_find_section_bounds_unreleased():
    bounds = bc.find_section_bounds(SAMPLE, "## [Unreleased]")
    assert bounds == (2, 7)


def test_find_section_bounds_versioned_tolerates_date():
    bounds = bc.find_section_bounds(SAMPLE, "## [v1.0.0]")
    assert bounds == (7, len(SAMPLE))


def test_find_section_bounds_missing():
    assert bc.find_section_bounds(SAMPLE, "## [v9.9.9]") is None


def test_extract_body_trims_blanks():
    start, end = bc.find_section_bounds(SAMPLE, "## [Unreleased]")
    body = bc.extract_body(SAMPLE, start, end)
    assert body == "### Added\n- new thing"


# ---------------------------------------------------------------------------
# git-based body generation (Conventional Commits)
# ---------------------------------------------------------------------------

def test_generate_body_groups_by_type(monkeypatch):
    subjects = [
        "feat: add polling",
        "fix(ui): correct icon",
        "docs: update readme",
        "chore: bump deps",       # CC type, unmapped -> Other (subject stripped)
        "random no-prefix commit",  # non-CC -> Other, kept verbatim
    ]
    monkeypatch.setattr(bc, "collect_commit_subjects", lambda s, u: subjects)
    monkeypatch.setattr(bc, "previous_version_ref", lambda v: None)

    body = bc.generate_body_from_git("v1.1.0", None, None)
    assert "### Added" in body
    assert "- add polling" in body
    assert "### Fixed" in body
    assert "- correct icon" in body
    assert "### Documentation" in body
    assert "### Other" in body
    assert "- bump deps" in body            # CC type stripped to subject
    assert "- random no-prefix commit" in body  # non-CC kept verbatim


def test_generate_body_empty_when_no_commits(monkeypatch):
    monkeypatch.setattr(bc, "collect_commit_subjects", lambda s, u: [])
    monkeypatch.setattr(bc, "previous_version_ref", lambda v: None)
    assert bc.generate_body_from_git("v1.1.0", None, None) == ""


def test_collect_commit_subjects_filters_noise(monkeypatch):
    log_output = (
        "feat: real feature\n"
        "docs: archive CHANGELOG [Unreleased] into v1.0.0\n"
        "Merge pull request #5\n"
        "fix: another\n"
    )
    monkeypatch.setattr(bc, "_run_git", lambda args: log_output)
    subjects = bc.collect_commit_subjects("v0.9.0", "HEAD")
    assert subjects == ["feat: real feature", "fix: another"]


def test_collect_commit_subjects_git_unavailable(monkeypatch):
    monkeypatch.setattr(bc, "_run_git", lambda args: None)
    assert bc.collect_commit_subjects(None, None) == []


def test_collect_commit_subjects_uses_range_and_full_history(monkeypatch):
    seen = {}

    def fake_run_git(args):
        seen["args"] = args
        return "feat: x\n"

    monkeypatch.setattr(bc, "_run_git", fake_run_git)
    bc.collect_commit_subjects("v1.0.0", "v1.1.0")
    assert "v1.0.0..v1.1.0" in seen["args"]
    # No since ref -> full history of until (HEAD default).
    bc.collect_commit_subjects(None, None)
    assert "HEAD" in seen["args"]


# ---------------------------------------------------------------------------
# _run_git
# ---------------------------------------------------------------------------

def test_run_git_returns_stdout(monkeypatch):
    class Fake:
        stdout = "output\n"

    monkeypatch.setattr(bc.subprocess, "run", lambda *a, **k: Fake())
    assert bc._run_git(["log"]) == "output\n"


def test_run_git_returns_none_on_error(monkeypatch):
    def boom(*a, **k):
        raise bc.subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(bc.subprocess, "run", boom)
    assert bc._run_git(["log"]) is None


def test_run_git_returns_none_when_git_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(bc.subprocess, "run", boom)
    assert bc._run_git(["log"]) is None


# ---------------------------------------------------------------------------
# previous_version_ref
# ---------------------------------------------------------------------------

def test_previous_version_ref_untagged_version(monkeypatch):
    # version not yet tagged -> newest existing tag is the previous one.
    monkeypatch.setattr(bc, "_run_git", lambda args: "v1.2.0\nv1.1.0\nv1.0.0\n")
    assert bc.previous_version_ref("v1.3.0") == "v1.2.0"


def test_previous_version_ref_tagged_version(monkeypatch):
    # version already tagged -> the tag right after it (older) is returned.
    monkeypatch.setattr(bc, "_run_git", lambda args: "v1.2.0\nv1.1.0\nv1.0.0\n")
    assert bc.previous_version_ref("v1.1.0") == "v1.0.0"


def test_previous_version_ref_oldest_has_no_prior(monkeypatch):
    monkeypatch.setattr(bc, "_run_git", lambda args: "v1.0.0\n")
    assert bc.previous_version_ref("v1.0.0") is None


def test_previous_version_ref_no_tags(monkeypatch):
    monkeypatch.setattr(bc, "_run_git", lambda args: "")
    assert bc.previous_version_ref("v1.0.0") is None


def test_previous_version_ref_git_unavailable(monkeypatch):
    monkeypatch.setattr(bc, "_run_git", lambda args: None)
    assert bc.previous_version_ref("v1.0.0") is None


# ---------------------------------------------------------------------------
# extract_version_body / build_release_notes
# ---------------------------------------------------------------------------

def test_extract_version_body_strips_hint_blockquotes():
    lines = [
        "## [Unreleased]",
        "",
        "> hint line",
        "- actual change",
    ]
    assert bc.extract_version_body(lines, "unreleased") == "- actual change"


def test_build_release_notes_with_changes():
    lines = ["## [v1.4.0] - 2024-05-01", "", "### Added", "- cool feature"]
    notes, had = bc.build_release_notes(lines, "1.4.0", "acme/app", "v1.3.0")
    assert had is True
    assert "- cool feature" in notes
    assert "compare/v1.3.0...v1.4.0" in notes
    assert "DevNotifier-1.4.0.dmg" in notes


def test_build_release_notes_placeholder_when_empty():
    lines = ["# Changelog", ""]
    notes, had = bc.build_release_notes(lines, "1.4.0", "acme/app", None)
    assert had is False
    assert bc.RELEASE_NOTES_PLACEHOLDER in notes


# ---------------------------------------------------------------------------
# cmd_bump (archival) — via files in tmp_path
# ---------------------------------------------------------------------------

def _write_changelog(tmp_path, body):
    p = tmp_path / "CHANGELOG.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_cmd_bump_archives_handwritten(tmp_path):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Added\n- shiny\n\n"
        "## [v1.0.0] - 2024-01-01\n\n- init\n"
    ))
    ns = _bump_ns(str(changelog), "1.1.0", date="2024-06-01")
    assert bc.cmd_bump(ns) == 0

    text = changelog.read_text(encoding="utf-8")
    assert "## [v1.1.0] - 2024-06-01" in text
    assert "- shiny" in text
    # Empty Unreleased restored at the top.
    assert "## [Unreleased]" in text
    assert text.index("## [Unreleased]") < text.index("## [v1.1.0]")


def test_cmd_bump_trims_surrounding_blank_lines(tmp_path):
    # Blank lines wrapping the Unreleased content exercise the trim loops so the
    # archived section has no leading/trailing blank lines.
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n"
        "## [Unreleased]\n"
        "\n"
        "\n"
        "### Added\n- wrapped\n"
        "\n"
        "\n"
        "## [v1.0.0] - 2024-01-01\n\n- init\n"
    ))
    ns = _bump_ns(str(changelog), "1.1.0", date="2024-06-01")
    assert bc.cmd_bump(ns) == 0
    text = changelog.read_text(encoding="utf-8")
    # The archived heading is immediately followed by the content, no blank gap
    # beyond the single separator line.
    assert "## [v1.1.0] - 2024-06-01\n\n### Added\n- wrapped\n" in text


def test_cmd_bump_refuses_existing_version(tmp_path):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [Unreleased]\n\n## [v1.1.0] - 2024-01-01\n\n- x\n"
    ))
    ns = _bump_ns(str(changelog), "1.1.0")
    assert bc.cmd_bump(ns) == 1  # idempotency guard


def test_cmd_bump_dry_run_does_not_write(tmp_path, capsys):
    original = (
        "# Changelog\n\n## [Unreleased]\n\n### Added\n- x\n"
    )
    changelog = _write_changelog(tmp_path, original)
    ns = _bump_ns(str(changelog), "1.1.0", date="2024-06-01", dry_run=True)
    assert bc.cmd_bump(ns) == 0
    # File unchanged on disk; new content printed to stdout.
    assert changelog.read_text(encoding="utf-8") == original
    assert "## [v1.1.0]" in capsys.readouterr().out


def test_cmd_bump_empty_unreleased_no_git_fallback(tmp_path):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [Unreleased]\n\n"
        "> hint only\n"
    ))
    ns = _bump_ns(str(changelog), "1.1.0", no_git_fallback=True)
    assert bc.cmd_bump(ns) == 1  # nothing archivable, git fallback disabled


def _bump_ns(file, version, date=None, dry_run=False, since=None, until=None,
             no_git_fallback=False):
    """Build an argparse-like namespace for cmd_bump without the parser."""
    return argparse.Namespace(
        file=file, version=version, date=date, dry_run=dry_run,
        since=since, until=until, no_git_fallback=no_git_fallback,
    )


# ---------------------------------------------------------------------------
# CLI entrypoint smoke test
# ---------------------------------------------------------------------------

def test_main_extract_end_to_end(tmp_path):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [Unreleased]\n\n- pending change\n"
    ))
    rc = bc.main(["--file", str(changelog), "extract", "--version", "unreleased"])
    assert rc == 0


def test_main_extract_missing_quiet(tmp_path):
    changelog = _write_changelog(tmp_path, "# Changelog\n")
    rc = bc.main(["--file", str(changelog), "extract",
                  "--version", "9.9.9", "--quiet"])
    assert rc == 0  # quiet -> missing section is not an error


def test_cmd_extract_missing_section_errors(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, "# Changelog\n")
    ns = argparse.Namespace(file=str(changelog), version="9.9.9", quiet=False)
    assert bc.cmd_extract(ns) == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_cmd_extract_missing_unreleased_errors(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, "# Changelog\n")
    ns = argparse.Namespace(file=str(changelog), version="unreleased", quiet=False)
    assert bc.cmd_extract(ns) == 1
    assert "[Unreleased]" in capsys.readouterr().err


def test_cmd_extract_prints_body(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [Unreleased]\n\n- a change\n"
    ))
    ns = argparse.Namespace(file=str(changelog), version="unreleased", quiet=False)
    assert bc.cmd_extract(ns) == 0
    assert "- a change" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_release_notes
# ---------------------------------------------------------------------------

def test_cmd_release_notes_with_changes(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [v1.4.0] - 2024-05-01\n\n- shipped\n"
    ))
    ns = argparse.Namespace(file=str(changelog), version="1.4.0",
                            repo="acme/app", prev="v1.3.0")
    assert bc.cmd_release_notes(ns) == 0
    out = capsys.readouterr().out
    assert "- shipped" in out
    assert "compare/v1.3.0...v1.4.0" in out


def test_cmd_release_notes_warns_on_placeholder(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, "# Changelog\n")
    ns = argparse.Namespace(file=str(changelog), version="1.4.0",
                            repo="acme/app", prev="")
    assert bc.cmd_release_notes(ns) == 0
    captured = capsys.readouterr()
    assert bc.RELEASE_NOTES_PLACEHOLDER in captured.out
    assert "::warning::" in captured.err


# ---------------------------------------------------------------------------
# cmd_bump git auto-fill path
# ---------------------------------------------------------------------------

def test_cmd_bump_autofills_from_git(tmp_path, monkeypatch, capsys):
    # [Unreleased] empty (hint only) -> body generated from git commits.
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [Unreleased]\n\n> hint only\n"
    ))
    monkeypatch.setattr(bc, "generate_body_from_git",
                        lambda v, s, u: "### Added\n- auto feature")
    ns = _bump_ns(str(changelog), "1.1.0", date="2024-06-01")
    assert bc.cmd_bump(ns) == 0
    text = changelog.read_text(encoding="utf-8")
    assert "- auto feature" in text
    assert "## [v1.1.0] - 2024-06-01" in text


def test_cmd_bump_autofill_trims_blank_lines(tmp_path, monkeypatch):
    # A generated body padded with blank lines exercises the trim loops.
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [Unreleased]\n\n> hint only\n"
    ))
    monkeypatch.setattr(bc, "generate_body_from_git",
                        lambda v, s, u: "\n\n### Added\n- padded\n\n\n")
    ns = _bump_ns(str(changelog), "1.1.0", date="2024-06-01")
    assert bc.cmd_bump(ns) == 0
    text = changelog.read_text(encoding="utf-8")
    assert "## [v1.1.0] - 2024-06-01\n\n### Added\n- padded\n" in text


def test_cmd_bump_git_autofill_empty_errors(tmp_path, monkeypatch):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [Unreleased]\n\n> hint only\n"
    ))
    monkeypatch.setattr(bc, "generate_body_from_git", lambda v, s, u: "")
    ns = _bump_ns(str(changelog), "1.1.0")
    assert bc.cmd_bump(ns) == 1  # nothing to archive from git either


def test_cmd_bump_missing_unreleased_errors(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, "# Changelog\n\n## [v1.0.0] - x\n\n- a\n")
    ns = _bump_ns(str(changelog), "1.1.0")
    assert bc.cmd_bump(ns) == 1
    assert "No [Unreleased]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() dispatch + error handling
# ---------------------------------------------------------------------------

def test_main_bump_invalid_version_returns_error(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, "# Changelog\n\n## [Unreleased]\n\n- x\n")
    # Invalid semver -> normalize_version raises ValueError -> main returns 1.
    rc = bc.main(["--file", str(changelog), "bump", "--version", "not-a-version"])
    assert rc == 1
    assert "::error::" in capsys.readouterr().err


def test_main_release_notes_end_to_end(tmp_path, capsys):
    changelog = _write_changelog(tmp_path, (
        "# Changelog\n\n## [v2.0.0] - 2024-07-01\n\n- big release\n"
    ))
    rc = bc.main(["--file", str(changelog), "release-notes",
                  "--version", "2.0.0", "--repo", "acme/app", "--prev", "v1.0.0"])
    assert rc == 0
    assert "- big release" in capsys.readouterr().out
