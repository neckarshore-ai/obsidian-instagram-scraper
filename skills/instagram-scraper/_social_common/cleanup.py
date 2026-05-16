"""Delete orphan per-post `.md` files left over from previous renders (e.g. when a post's
title changed). Identifies orphans by date prefix + provenance marker, so it never touches
files this skill didn't write."""

from __future__ import annotations

import re
from pathlib import Path

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} ")


def cleanup_old_post_files(
    profile_dir: Path,
    keep_paths: set[Path],
    overview_paths: set[Path],
    scrape_source: str,
) -> list[Path]:
    """Walk `profile_dir`, delete date-prefixed `.md` files that:
    - are not in `keep_paths` (current render's per-post files), AND
    - are not in `overview_paths` (overview / themes / filtered), AND
    - contain `scrape_source` in their first 1500 chars (provenance marker).

    Returns the list of deleted paths.
    """
    deleted: list[Path] = []
    if not profile_dir.is_dir():
        return deleted
    for f in profile_dir.iterdir():
        if not f.is_file() or f.suffix != ".md":
            continue
        if f in keep_paths or f in overview_paths:
            continue
        if not _DATE_PREFIX_RE.match(f.name):
            continue
        try:
            head = f.read_text(encoding="utf-8", errors="replace")[:1500]
        except OSError:
            continue
        if scrape_source not in head:
            continue
        f.unlink()
        deleted.append(f)
    return deleted
