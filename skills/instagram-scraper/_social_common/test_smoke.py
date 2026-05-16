#!/usr/bin/env python3
"""Smoke tests for `_social_common` pure helpers.

Run: `python3 ~/.claude/skills/_social_common/test_smoke.py`
Exit code: 0 on success, 1 on any failed assertion (with the first failure printed).

Coverage rule: every pure helper exported from `__init__.py` has at least one happy-path
assertion and at least one edge-case assertion. Stdlib only — no pytest dependency.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the package importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _social_common import (  # noqa: E402
    content_preview,
    extract_json_object,
    fmt_batch_log_ts,
    fmt_date_iso,
    fmt_int,
    fmt_int_yaml_str,
    fmt_property_ts,
    md_escape_pipe,
    sanitize_tag,
    slugify_for_filename,
    truncate_at_word,
    url_encode_link,
    yaml_quote,
)


def _check(label: str, actual, expected) -> None:
    if actual != expected:
        sys.stderr.write(
            f"FAIL [{label}]:\n  expected: {expected!r}\n  actual:   {actual!r}\n"
        )
        sys.exit(1)


def _check_truthy(label: str, actual) -> None:
    if not actual:
        sys.stderr.write(f"FAIL [{label}]: expected truthy, got {actual!r}\n")
        sys.exit(1)


def main() -> int:
    # fmt_int — German thousand-separator + sentinel
    _check("fmt_int happy", fmt_int(1234567), "1.234.567")
    _check("fmt_int zero", fmt_int(0), "0")
    _check("fmt_int None sentinel", fmt_int(None), "—")
    _check("fmt_int garbage sentinel", fmt_int("abc"), "—")

    # fmt_int_yaml_str — quoted version + sentinel
    _check("fmt_int_yaml_str happy", fmt_int_yaml_str(1234), '"1.234"')
    _check("fmt_int_yaml_str None", fmt_int_yaml_str(None), '""')

    # yaml_quote — escape, newline-collapse
    _check("yaml_quote plain", yaml_quote("hello"), '"hello"')
    _check("yaml_quote with quote", yaml_quote('he"llo'), '"he\\"llo"')
    _check("yaml_quote multiline collapses", yaml_quote("a\nb"), '"a b"')
    _check("yaml_quote None coerces", yaml_quote(None), '""')

    # md_escape_pipe — table-cell safety
    _check("md_escape_pipe happy", md_escape_pipe("a|b|c"), "a\\|b\\|c")
    _check("md_escape_pipe noop", md_escape_pipe("nopipe"), "nopipe")

    # url_encode_link — file-name safety
    _check("url_encode_link space", url_encode_link("foo bar.md"), "foo%20bar.md")
    _check("url_encode_link emdash", url_encode_link("a — b"), "a%20%E2%80%94%20b")

    # content_preview — strips #/@, truncates, returns "" on None
    _check("content_preview None", content_preview(None), "")
    _check(
        "content_preview strips hashtag/mention",
        content_preview("Hello #world @bob there"),
        "Hello there",
    )
    _check_truthy(
        "content_preview truncates with ellipsis",
        content_preview("a" * 100, width=10).endswith("…"),
    )

    # sanitize_tag — Obsidian-safe content tag
    _check("sanitize_tag spaces", sanitize_tag("Foo Bar Baz"), "Foo-Bar-Baz")
    _check("sanitize_tag empty", sanitize_tag(""), "")
    _check("sanitize_tag dots+slashes", sanitize_tag("a.b/c"), "a-b-c")

    # slugify_for_filename — no FS-unsafe chars
    _check("slugify happy", slugify_for_filename("hello world"), "hello world")
    _check("slugify strips slashes", slugify_for_filename("foo/bar:baz"), "foo bar baz")
    _check("slugify empty", slugify_for_filename(""), "")
    _check("slugify trailing dot", slugify_for_filename("name."), "name")

    # truncate_at_word — keeps word boundary
    _check("truncate noop", truncate_at_word("short", 100), "short")
    _check_truthy(
        "truncate word boundary",
        truncate_at_word("hello world foo bar", 10).endswith("…"),
    )

    # extract_json_object — defensive parser
    _check("extract bare json", extract_json_object('{"a": 1}'), {"a": 1})
    _check(
        "extract fenced json (uses re — would NameError on legacy local copies)",
        extract_json_object('```json\n{"x": 2}\n```'),
        {"x": 2},
    )
    _check(
        "extract json with leading prose",
        extract_json_object('Sure, here:\n{"k": "v"}'),
        {"k": "v"},
    )
    _check("extract empty", extract_json_object(""), None)
    _check("extract garbage", extract_json_object("hello world"), None)

    # fmt_property_ts — Obsidian Property timestamp format
    _check(
        "fmt_property_ts",
        fmt_property_ts(datetime(2026, 4, 26, 10, 30, tzinfo=timezone.utc)),
        "2026-04-26 10:30",
    )

    # fmt_date_iso — ISO / int / Twitter-style / fallback
    _check("fmt_date_iso ISO", fmt_date_iso("2026-04-26T10:30:00Z"), "2026-04-26")
    _check("fmt_date_iso None sentinel", fmt_date_iso(None), "—")
    _check("fmt_date_iso ms epoch", fmt_date_iso(1745654400000), "2025-04-26")

    # fmt_batch_log_ts — UTC + Berlin local
    _check(
        "fmt_batch_log_ts CEST (April = UTC+2)",
        fmt_batch_log_ts(datetime(2026, 4, 26, 8, 9, tzinfo=timezone.utc)),
        "2026-04-26 08:09 UTC (10:09 Berlin)",
    )
    _check(
        "fmt_batch_log_ts CET (December = UTC+1)",
        fmt_batch_log_ts(datetime(2026, 12, 15, 8, 9, tzinfo=timezone.utc)),
        "2026-12-15 08:09 UTC (09:09 Berlin)",
    )
    _check(
        "fmt_batch_log_ts naive UTC assumption",
        fmt_batch_log_ts(datetime(2026, 4, 26, 8, 9)),
        "2026-04-26 08:09 UTC (10:09 Berlin)",
    )

    print("OK — all smoke assertions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
