"""Pure-function formatting helpers shared across social-scraper render scripts.

Output is byte-identical to the per-skill copies these were extracted from. Any change here
ripples to all 3 skills — verify against backed-up reference renders.
"""

from __future__ import annotations

import re
import urllib.parse


# ---------- numeric / yaml -------------------------------------------------------------------

def fmt_int(value) -> str:
    """German-style thousand separator (`.`). Sentinel `—` on bad input."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "—"
    return f"{n:,}".replace(",", ".")


def fmt_int_yaml_str(value) -> str:
    """Same formatting as fmt_int, wrapped in YAML double-quotes (Properties expect a string)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return '""'
    formatted = f"{n:,}".replace(",", ".")
    return f'"{formatted}"'


def yaml_quote(text: str) -> str:
    """Escape a string for safe inclusion as a YAML double-quoted scalar."""
    cleaned = (text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
    return f'"{cleaned}"'


# ---------- markdown / inline ---------------------------------------------------------------

def md_escape_pipe(text: str) -> str:
    """Escape `|` for use inside a Markdown table cell."""
    return text.replace("|", "\\|")


def url_encode_link(text: str) -> str:
    """URL-encode a path/file-name for use inside `[label](URL)`."""
    return urllib.parse.quote(text)


_HASHTAG_MENTION_RE = re.compile(r"(?:(?<=\s)|^)[#@][\w.]+")


def content_preview(content: str | None, width: int = 60) -> str:
    """One-line preview, hashtags/mentions stripped (so they don't render as Obsidian
    auto-tags inside table cells), truncated at `width` with an ellipsis."""
    if not content:
        return ""
    cleaned = _HASHTAG_MENTION_RE.sub("", content)
    one_line = " ".join(cleaned.split())
    if len(one_line) > width:
        return one_line[: width - 1].rstrip() + "…"
    return one_line


# ---------- text shaping --------------------------------------------------------------------

def truncate_at_word(text: str, max_len: int) -> str:
    """Hard truncate at word boundary, append `…` when truncation happens."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut.rstrip(",;:- ") + "…"


# ---------- filename / tag sanitizers --------------------------------------------------------

_FS_REPLACE = re.compile(r"[/\\:*?<>|\"]+")
_WS_COLLAPSE = re.compile(r"\s+")
_TRAILING_DOTS = re.compile(r"\.+$")

_TAG_SANITIZE_REPLACE_RE = re.compile(r"[\s./\\]+")
_TAG_SANITIZE_STRIP_RE = re.compile(r"[^\w\-/]")
_TAG_SANITIZE_COLLAPSE_RE = re.compile(r"-+")


def sanitize_tag(raw: str) -> str:
    """Coerce raw text to an Obsidian-safe content tag. Empty → empty."""
    if not raw:
        return ""
    s = _TAG_SANITIZE_REPLACE_RE.sub("-", raw.strip())
    s = _TAG_SANITIZE_STRIP_RE.sub("", s)
    s = _TAG_SANITIZE_COLLAPSE_RE.sub("-", s).strip("-_")
    return s


def slugify_for_filename(text: str) -> str:
    """Turn a string into a filesystem-safe filename fragment. Empty → empty."""
    if not text:
        return ""
    s = _FS_REPLACE.sub(" ", text)
    s = _WS_COLLAPSE.sub(" ", s).strip()
    s = _TRAILING_DOTS.sub("", s)
    return s
