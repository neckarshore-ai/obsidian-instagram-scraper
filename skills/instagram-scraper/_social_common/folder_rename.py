"""Rename `<base>/<handle>/` → `<base>/<handle> — <essence>/` after the LLM essence is set."""

from __future__ import annotations

import json
import sys
from pathlib import Path


FOLDER_ESSENCE_SEPARATOR = " — "  # em-dash with spaces; matches the user's prose style


def rename_folder_with_essence(profile_dir: Path, raw_path: Path, handle: str) -> Path:
    """If `_essence` is set in the JSON envelope and the folder is still on its bare-handle
    path, rename to `<handle> — <essence>` (idempotent). Returns the (possibly new) raw_path
    so callers can update tracking lists.

    Errors are logged to stderr but don't raise — folder rename is best-effort.
    """
    try:
        envelope = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"WARN: cannot read JSON for folder-rename: {exc}\n")
        return raw_path

    essence = (envelope.get("_essence") or "").strip()
    if not essence:
        return raw_path

    if profile_dir.name != handle:
        return raw_path  # already renamed (or user renamed it manually)

    parent = profile_dir.parent
    target = parent / f"{handle}{FOLDER_ESSENCE_SEPARATOR}{essence}"
    if target.exists():
        sys.stderr.write(f"WARN: target folder already exists, skipping rename: {target.name}\n")
        return raw_path
    try:
        profile_dir.rename(target)
    except OSError as exc:
        sys.stderr.write(f"WARN: folder rename failed for {handle}: {exc}\n")
        return raw_path

    sys.stderr.write(f"INFO: renamed folder to '{target.name}'\n")
    return target / raw_path.name
