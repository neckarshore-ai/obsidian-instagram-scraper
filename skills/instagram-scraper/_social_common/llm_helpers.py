"""Defensive parsers for LLM output."""

from __future__ import annotations

import json
import re


def extract_json_object(text: str) -> dict | None:
    """Extract the first JSON object from `text`, tolerating ```json fences and stray prose
    around the object. Returns None on parse failure.
    """
    if not text:
        return None
    fence = re.match(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    text = text.strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start == -1:
            return None
        text = text[start:]
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[: i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None
