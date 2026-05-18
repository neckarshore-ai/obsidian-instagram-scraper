#!/usr/bin/env python3
"""Scrape Instagram profiles + their latest posts via the Apify actor `apify/instagram-scraper`.

Reads APIFY_API_TOKEN from the environment, accepts one or more usernames,
calls the Apify actor synchronously, and writes one raw.json per profile.

Stdout: a JSON summary with succeeded/failed lists for downstream consumers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _social_common.tokens import get_apify_token
from _social_common.folder_rename import rename_folder_with_essence, FOLDER_ESSENCE_SEPARATOR
from _social_common.timestamps import fmt_batch_log_ts
try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: 'requests' is not installed. Run:\n"
        "  pip install -r " + str(Path(__file__).parent.parent / "requirements.txt") + "\n"
    )
    sys.exit(2)

APIFY_ENDPOINT = (
    "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"
)
DEFAULT_POSTS_LIMIT = 12
DEFAULT_TIMEOUT_SECONDS = 300
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,30}$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--usernames",
        help="Comma-separated list of usernames or profile URLs (e.g. 'natgeo,@nasa').",
    )
    src.add_argument(
        "--input-file",
        type=Path,
        help="Path to a text file with one username/URL per line.",
    )
    p.add_argument(
        "--posts-limit",
        type=int,
        default=DEFAULT_POSTS_LIMIT,
        help=f"How many recent posts per profile (default: {DEFAULT_POSTS_LIMIT}).",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output base directory. Default: ./data/instagram/<YYYY-MM-DD>/",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    p.add_argument(
        "--transcribe",
        action="store_true",
        help=(
            "After scraping, run scripts/transcribe_videos.py on each profile's JSON "
            "to fill in `transcript` fields for every Reel (productType=clips). "
            "Requires whisper-cli + ffmpeg installed."
        ),
    )
    p.add_argument(
        "--whisper-model",
        default="medium.en",
        help=(
            "Whisper model name passed to transcribe_videos.py (default: medium.en). "
            "Resolves to ~/.local/share/whisper-cpp/models/ggml-<MODEL>.bin"
        ),
    )
    return p.parse_args()


def normalize_username(raw: str) -> str | None:
    """Accept '@user', 'user', 'https://instagram.com/user/', etc. Returns lowercase username or None."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    # Strip URL prefix if present
    m = re.match(
        r"^(?:https?://)?(?:www\.)?instagram\.com/([^/?#]+)/?",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        s = m.group(1)
    s = s.lstrip("@").strip("/")
    s = s.lower()
    if not USERNAME_RE.match(s):
        return None
    return s


def collect_usernames(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    """Returns (valid, rejected_raw)."""
    raw_items: list[str] = []
    if args.usernames:
        # Split on comma, semicolon, whitespace, newline
        raw_items = [x for x in re.split(r"[,;\s\n]+", args.usernames) if x.strip()]
    else:
        text = args.input_file.read_text(encoding="utf-8")
        raw_items = [x for x in re.split(r"[,;\s\n]+", text) if x.strip()]

    seen: set[str] = set()
    valid: list[str] = []
    rejected: list[str] = []
    for raw in raw_items:
        norm = normalize_username(raw)
        if norm is None:
            rejected.append(raw)
            continue
        if norm in seen:
            continue
        seen.add(norm)
        valid.append(norm)
    return valid, rejected



def call_apify(usernames: list[str], posts_limit: int, token: str, timeout: int) -> list[dict]:
    """Call the Apify Instagram Scraper actor synchronously and return the dataset items.

    Uses an Authorization: Bearer header so the token never appears in URLs, server access
    logs, or stack traces.
    """
    payload = {
        "directUrls": [f"https://www.instagram.com/{u}/" for u in usernames],
        "resultsType": "details",
        "resultsLimit": posts_limit,
        "addParentData": False,
    }
    resp = requests.post(
        APIFY_ENDPOINT,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 401:
        sys.stderr.write("ERROR: Apify rejected the token (401). Verify APIFY_API_TOKEN.\n")
        sys.exit(3)
    if resp.status_code == 402:
        sys.stderr.write(
            "ERROR: Apify returned 402 (payment required). "
            "Check your credit balance at https://console.apify.com/billing.\n"
        )
        sys.exit(3)
    if resp.status_code == 403:
        # Permission scope issue — show Apify's body so the user can see what's missing
        body = resp.text[:600] if resp.text else "(empty body)"
        sys.stderr.write(
            "ERROR: Apify returned 403 Forbidden — the token authenticated but lacks the "
            "required permissions.\n"
            "Most common cause: the token was created without 'Allow this token to access "
            "default run storages' enabled, OR the Storages: Read account-level permission "
            "is missing.\n"
            f"Apify response body: {body}\n"
        )
        sys.exit(3)
    if not resp.ok:
        # Generic error — never echo the URL (would contain query-string token in old call style)
        body = resp.text[:600] if resp.text else "(empty body)"
        sys.stderr.write(
            f"ERROR: Apify returned HTTP {resp.status_code}.\n"
            f"Response body: {body}\n"
        )
        sys.exit(3)
    data = resp.json()
    if not isinstance(data, list):
        sys.stderr.write(f"ERROR: Unexpected Apify response shape: {type(data).__name__}\n")
        sys.exit(3)
    return data


BATCH_LOG_NAME = "Instagram scraper batch.md"
BATCH_LOG_HEADER = """\
---
title: "Instagram scraper batch log"
description: "Run-by-run history of every Instagram scrape (newest entry at the bottom)."
status: active
tags:
  - Instagram
  - Inbox
  - Log
source: apify/instagram-scraper
---

# Instagram scraper batch log

Each scrape appends a section below. The entries are append-only — Obsidian sorts them by
file order, so the most recent run lives at the bottom.

"""


def append_run_to_batch_log(
    out_dir: Path,
    *,
    scraped_at: datetime,
    requested: list[str],
    succeeded: list[str],
    failed: list[dict],
    transcribe: bool,
    transcription_results: list[dict],
    posts_limit: int,
) -> None:
    """Append one run section to <out-dir>/Instagram scraper batch.md. Creates the file with
    a header on first run."""
    log_path = out_dir / BATCH_LOG_NAME
    if not log_path.exists():
        log_path.write_text(BATCH_LOG_HEADER, encoding="utf-8")

    transcribed_count = sum(int(r.get("transcribed", 0) or 0) for r in transcription_results)
    transcription_failures = sum(len(r.get("failed", []) or []) for r in transcription_results)

    ts = fmt_batch_log_ts(scraped_at)
    profile_links: list[str] = []
    for u in succeeded:
        target = f"{u}/_{u} overview.md"
        # Spaces in path → percent-encode
        encoded = target.replace(" ", "%20")
        profile_links.append(f"[@{u}]({encoded})")

    section_lines: list[str] = []
    section_lines.append(f"## {ts} — {len(succeeded)} profile(s) scraped")
    section_lines.append("")
    section_lines.append(f"- **Requested:** {', '.join('@' + u for u in requested) or '—'}")
    if profile_links:
        section_lines.append(f"- **Succeeded:** {', '.join(profile_links)}")
    if failed:
        formatted = ", ".join(f"@{f.get('username','?')} ({f.get('reason','?')})" for f in failed)
        section_lines.append(f"- **Failed:** {formatted}")
    section_lines.append(f"- **Posts-limit (requested):** {posts_limit}")
    if transcribe:
        section_lines.append(
            f"- **Reels transcribed:** {transcribed_count}"
            + (f" · failed: {transcription_failures}" if transcription_failures else "")
        )
    else:
        section_lines.append("- **Transcripts:** skipped (no `--transcribe` flag)")
    section_lines.append("")

    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(section_lines))
        f.write("\n")




def index_by_username(items: list[dict]) -> dict[str, dict]:
    """Map each result item to its username (lowercased). Items lacking a username are skipped."""
    out: dict[str, dict] = {}
    for item in items:
        u = (item.get("username") or "").strip().lower()
        if not u:
            # Apify sometimes returns an `error` item for unreachable profiles —
            # try to recover the username from `inputUrl`.
            input_url = item.get("inputUrl") or item.get("url") or ""
            m = re.search(r"instagram\.com/([^/?#]+)/?", input_url, flags=re.IGNORECASE)
            if m:
                u = m.group(1).lower()
        if u:
            out[u] = item
    return out


def main() -> int:
    args = parse_args()
    usernames, rejected = collect_usernames(args)

    if rejected:
        sys.stderr.write(f"WARN: ignoring {len(rejected)} unparseable input(s): {rejected}\n")
    if not usernames:
        sys.stderr.write("ERROR: no valid usernames to scrape.\n")
        return 2

    token = get_apify_token()

    out_dir = args.out_dir
    if out_dir is None:
        vault = os.environ.get("OBSIDIAN_VAULT_PATH")
        if not vault:
            sys.stderr.write(
                "ERROR: --out-dir not provided and OBSIDIAN_VAULT_PATH is not set.\n"
                "Either pass --out-dir <path> or export OBSIDIAN_VAULT_PATH=/path/to/vault.\n"
            )
            return 3
        out_dir = Path(vault) / "Instagram Scraper"
    out_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sys.stderr.write(
        f"INFO: scraping {len(usernames)} profile(s) with up to {args.posts_limit} posts each. "
        f"Out: {out_dir} (filenames prefixed with {today})\n"
    )

    items = call_apify(usernames, args.posts_limit, token, args.timeout)
    indexed = index_by_username(items)

    succeeded: list[str] = []
    failed: list[dict] = []
    written: list[str] = []
    transcription_results: list[dict] = []

    for username in usernames:
        item = indexed.get(username)
        if item is None:
            failed.append({"username": username, "reason": "no_data_returned"})
            continue
        # Apify marks errors with an `error` field
        if item.get("error"):
            failed.append({"username": username, "reason": item.get("error")})
            continue

        # v3 layout: <out-dir>/<username>/_<username> overview.json — leading underscore
        # so Obsidian sorts it to the top of the profile folder; no date in the filename
        # so each scrape overwrites the same overview file. Per-scrape history lives
        # in the batch log (see below), not in proliferating overview files.
        profile_dir = out_dir / username
        profile_dir.mkdir(parents=True, exist_ok=True)
        raw_path = profile_dir / f"_{username} overview.json"
        item["_scraped_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        raw_path.write_text(
            json.dumps(item, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        succeeded.append(username)

        if args.transcribe:
            sys.stderr.write(f"INFO: transcribing Reels for @{username} ...\n")
            transcribe_script = Path(__file__).parent / "transcribe_videos.py"
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(transcribe_script),
                        "--input", str(raw_path),
                        "--model", args.whisper_model,
                        "--skip-existing",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    sys.stderr.write(
                        f"WARN: transcription exited {proc.returncode} for @{username}\n"
                        f"{proc.stderr}\n"
                    )
                    transcription_results.append({"username": username, "ok": False, "exit_code": proc.returncode})
                else:
                    try:
                        result = json.loads(proc.stdout)
                    except json.JSONDecodeError:
                        result = {"raw_stdout": proc.stdout[:500]}
                    transcription_results.append({"username": username, "ok": True, **result})
            except FileNotFoundError as exc:
                sys.stderr.write(f"WARN: could not run transcribe_videos.py: {exc}\n")
                transcription_results.append({"username": username, "ok": False, "reason": str(exc)})

            # Chain Anthropic-Haiku polish step. Skips internally if ANTHROPIC_API_KEY isn't
            # set — never blocks the scrape pipeline. Produces description_polished and
            # content_polished fields for every Reel that has a transcript.
            polish_script = Path(__file__).parent / "polish_post.py"
            sys.stderr.write(f"INFO: polishing transcripts for @{username} ...\n")
            try:
                proc = subprocess.run(
                    [sys.executable, str(polish_script), "--input", str(raw_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    sys.stderr.write(
                        f"WARN: polish exited {proc.returncode} for @{username}\n{proc.stderr}\n"
                    )
                else:
                    sys.stderr.write(proc.stderr)
            except FileNotFoundError as exc:
                sys.stderr.write(f"WARN: could not run polish_post.py: {exc}\n")

        # Chain Anthropic-Haiku essence step. Profile-level (not per-Reel), so it runs for
        # every scrape regardless of --transcribe. Idempotent: skips if `_essence` is already
        # in the JSON. After essence is generated, rename the profile folder to
        # `<username> — <essence>` so the Obsidian sidebar shows what each account is about.
        essence_script = Path(__file__).parent / "essence_profile.py"
        try:
            proc = subprocess.run(
                [sys.executable, str(essence_script), "--input", str(raw_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                sys.stderr.write(
                    f"WARN: essence exited {proc.returncode} for @{username}\n{proc.stderr}\n"
                )
            else:
                sys.stderr.write(proc.stderr)
        except FileNotFoundError as exc:
            sys.stderr.write(f"WARN: could not run essence_profile.py: {exc}\n")

        raw_path = rename_folder_with_essence(profile_dir, raw_path, username)
        written.append(str(raw_path))

    # Append a row to the platform-level batch log (Instagram scraper batch.md).
    append_run_to_batch_log(
        out_dir,
        scraped_at=datetime.now(timezone.utc),
        requested=usernames,
        succeeded=succeeded,
        failed=failed,
        transcribe=args.transcribe,
        transcription_results=transcription_results,
        posts_limit=args.posts_limit,
    )

    summary = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "out_dir": str(out_dir),
        "requested": usernames,
        "rejected_input": rejected,
        "transcription_results": transcription_results if args.transcribe else None,
        "succeeded": succeeded,
        "failed": failed,
        "files": written,
        "posts_limit": args.posts_limit,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
