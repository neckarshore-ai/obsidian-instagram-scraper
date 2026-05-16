"""Timestamp helpers for render scripts: scrape-time recovery and per-file `created`/`modified`
preservation across re-renders."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


_BERLIN_TZ = ZoneInfo("Europe/Berlin")


# ---------- formatting ----------------------------------------------------------------------

def fmt_property_ts(ts: datetime) -> str:
    """Property-block timestamp: `YYYY-MM-DD HH:MM`."""
    return ts.strftime("%Y-%m-%d %H:%M")


def fmt_batch_log_ts(ts: datetime) -> str:
    """Batch-log section header timestamp: `YYYY-MM-DD HH:MM UTC (HH:MM Berlin)`.

    The local-time display uses Europe/Berlin (auto-handles CET / CEST). Naive datetimes
    are assumed to be UTC.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    utc_part = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    berlin_part = ts.astimezone(_BERLIN_TZ).strftime("%H:%M Berlin")
    return f"{utc_part} ({berlin_part})"


def fmt_date_iso(value) -> str:
    """Coerce common date inputs (ISO-8601 string, ms-since-epoch int, Twitter-style string)
    to `YYYY-MM-DD`. Returns `—` on missing input; falls back to the raw value's first 10
    chars if all parsers fail."""
    if not value:
        return "—"
    if isinstance(value, (int, float)):
        try:
            ts_seconds = value / 1000.0 if value > 1e12 else value
            return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            return str(value)
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d")
    except ValueError:
        return s[:10]


# ---------- scrape-time recovery -------------------------------------------------------------

def derive_scrape_timestamp(source_path: Path | None, envelope: dict | None = None) -> datetime:
    """Prefer the `_scraped_at` field written by scrape_profile.py at initial scrape time —
    that survives later mutations of the JSON (polish, themes, essence). Fall back to the
    JSON file's mtime, then to `now()`."""
    if isinstance(envelope, dict):
        raw = envelope.get("_scraped_at")
        if isinstance(raw, str) and raw.strip():
            try:
                return datetime.fromisoformat(raw.strip())
            except ValueError:
                pass
    if source_path is not None and source_path.exists():
        return datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc)
    return datetime.now(timezone.utc)


# ---------- per-file `created` preservation --------------------------------------------------

_EXISTING_CREATED_RE = re.compile(r"^created:\s*(.+?)\s*$")


def read_existing_created(path: Path) -> str | None:
    """Read the `created:` value from an existing rendered MD's frontmatter. Returns None if
    file missing, no frontmatter, or no `created:` field."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    for line in text[4:end].splitlines():
        m = _EXISTING_CREATED_RE.match(line)
        if m:
            return m.group(1).strip()
    return None


def resolve_timestamps(output_path: Path) -> tuple[str, str]:
    """Returns (created_str, modified_str). `modified` is now; `created` is preserved if the
    file already exists (so re-renders don't change the original create date)."""
    modified_str = fmt_property_ts(datetime.now(timezone.utc))
    existing = read_existing_created(output_path)
    return (existing or modified_str), modified_str
