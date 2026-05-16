#!/usr/bin/env python3
"""Render an Instagram profile JSON into Obsidian-friendly markdown.

Output layout (per profile):
  <profile-folder>/
  ├── _<username> overview.md                  # profile card + posts index (overwrites on re-scrape)
  ├── _<username> overview.json                # the input JSON (kept here as raw)
  ├── <post-date> <title-slug>.md              # one file per latest post, slug derived from caption
  └── ...

Two modes:
  --input <overview.json>          # render one profile (overview + per-post files)
  --batch-dir <platform-folder>    # render every <username>/_<*>overview.json in it,
                                     plus a comparative summary at the platform-folder root
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _social_common.render_helpers import (
    fmt_int, fmt_int_yaml_str, yaml_quote, md_escape_pipe, url_encode_link,
    content_preview, sanitize_tag, slugify_for_filename, truncate_at_word,
)
from _social_common.timestamps import (
    fmt_property_ts, fmt_date_iso, derive_scrape_timestamp,
    read_existing_created, resolve_timestamps,
)
from _social_common.cleanup import cleanup_old_post_files
# ---------- tunables --------------------------------------------------------------------------

CAPTION_PREVIEW_CHARS = 60
TITLE_MAX_CHARS = 80
DESCRIPTION_MAX_CHARS = 125          # halved per user request: 50% shorter, neutral tone
DESCRIPTION_FALLBACK_MAX_CHARS = 125  # used when no LLM-polished version is available
SCRAPE_SOURCE = "apify/instagram-scraper"
PROFILE_TAGS = ("Instagram", "Overview")
POST_TAGS_BASE = ("Instagram",)  # always present; per-post content tags from polish_post.py append to this
SUMMARY_TAGS = ("Instagram", "Summary")
REEL_PRODUCT_TYPE = "clips"

# Filesystem-illegal or unfriendly characters → replaced or stripped in title-slugs.
# Common engagement-bait prefixes we strip to get a usable title.
_BAIT_PREFIX_RE = re.compile(
    r"""^(
        comment\s+["'“”‘’]?\w+["'“”‘’]?\s+(to|for|and|then|so)\s+
        | dm\s+["'“”‘’]?\w+["'“”‘’]?\s+(to|for|and)\s+
        | link\s+in\s+bio\s+(to|for|and)\s+
        | tag\s+a\s+friend\s+(who|that)\s+
        | swipe\s+(up|right|left|across|to)\s+
        | save\s+this\s+(post\s+)?(if|so|to|for)\s+
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_LEADING_EMOJI_RE = re.compile(r"^[☀-➿\U0001F300-\U0001FAFF✀-➿\s]+")
# Re-declared here because used by per-skill body renderers. Same patterns as in
# _social_common.render_helpers; keep in sync.
_WS_COLLAPSE = re.compile(r"\s+")
_HASHTAG_MENTION_RE = re.compile(r"(?:(?<=\s)|^)[#@][\w.]+")
# `#hashtag` or `@mention` Instagram-style tokens. Obsidian auto-renders any `#word` outside
# of code-spans as a clickable tag chip — so caption text in the overview table must be
# scrubbed of these (the explicit `## Hashtags / Mentions` section in post .md uses
# backtick-wrapped `#tag` to escape the auto-tag rendering).


# ---------- argument parsing -----------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", type=Path, help="Path to a single profile overview.json")
    g.add_argument(
        "--batch-dir",
        type=Path,
        help="Platform folder containing one <username>/ subfolder per profile",
    )
    return p.parse_args()


# ---------- low-level formatting helpers ------------------------------------------------------

def caption_preview(caption: str | None, width: int = CAPTION_PREVIEW_CHARS) -> str:
    if not caption:
        return ""
    # Strip #hashtag and @mention tokens before rendering — Obsidian would otherwise turn
    # every `#word` in the table into an auto-tag chip, polluting the vault's tag namespace
    # and visually breaking the comparative overview.
    cleaned = _HASHTAG_MENTION_RE.sub("", caption)
    one_line = " ".join(cleaned.split())
    if len(one_line) > width:
        return one_line[: width - 1].rstrip() + "…"
    return one_line




def engagement_score(post: dict) -> int:
    likes = post.get("likesCount") or 0
    comments = post.get("commentsCount") or 0
    try:
        return int(likes) + 2 * int(comments)
    except (TypeError, ValueError):
        return 0


def get_posts(profile: dict) -> list[dict]:
    for key in ("latestPosts", "posts", "topPosts"):
        v = profile.get(key)
        if isinstance(v, list):
            return v
    return []


def _parse_post_timestamp(post: dict) -> datetime | None:
    raw = post.get("timestamp") or post.get("takenAtTimestamp")
    if raw is None:
        return None
    s = str(raw)
    # Apify timestamps look like "2026-04-22T02:42:23.000Z" — fromisoformat needs +00:00
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def dedupe_caption_variants(posts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop the lower-engagement variants when the same caption appears multiple times.
    Returns (kept, dropped). Posts without a caption are always kept (can't be matched).

    Includes both same-day A/B-test pairs (creator posts a variant minutes apart) and
    cross-day reposts (creator re-publishes the same caption days/weeks later) — both are
    duplicates from the vault's point of view. Highest engagement_score wins; ties prefer
    the later timestamp."""
    by_caption: dict[str, list[dict]] = {}
    no_caption: list[dict] = []
    for p in posts:
        cap = (p.get("caption") or "").strip()
        if not cap:
            no_caption.append(p)
            continue
        by_caption.setdefault(cap, []).append(p)

    kept: list[dict] = list(no_caption)
    dropped: list[dict] = []

    for cap, group in by_caption.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        winner = max(
            group,
            key=lambda p: (
                engagement_score(p),
                _parse_post_timestamp(p) or datetime.min.replace(tzinfo=timezone.utc),
            ),
        )
        kept.append(winner)
        for p in group:
            if p is not winner:
                dropped.append(p)
    return kept, dropped


def is_reel(post: dict) -> bool:
    return post.get("productType") == REEL_PRODUCT_TYPE


# ---------- title / description / content / TL;DR derivation ----------------------------------

def derive_title(caption: str | None, post_date: str, type_label: str) -> str:
    """Title from caption: strip engagement-bait prefix + leading emojis, take first sentence,
    cap at TITLE_MAX_CHARS at a word boundary. Fall back to a date+type label when there's
    no caption to extract from."""
    if not caption:
        return f"{post_date} · {type_label}"
    text = caption.strip()
    text = _LEADING_EMOJI_RE.sub("", text).strip()
    text = _BAIT_PREFIX_RE.sub("", text).strip()
    # First sentence — split on . ! ? when followed by whitespace/end, OR on newline.
    # The `(?=\s|$)` lookahead prevents splitting on `.` between digits like "GPT 5.5",
    # version numbers ("v1.2"), or decimals — all common in tech-product captions.
    text = re.split(r"[.!?](?=\s|$)|\n", text, maxsplit=1)[0].strip()
    text = _WS_COLLAPSE.sub(" ", text)
    if not text:
        return f"{post_date} · {type_label}"
    # Capitalize the first letter, leave the rest of the casing alone (preserves brand names)
    text = text[0].upper() + text[1:]
    text = truncate_at_word(text, TITLE_MAX_CHARS)
    return text




def derive_description(post: dict) -> str:
    """Prefer the LLM-polished description (set by `polish_post.py` when an
    `ANTHROPIC_API_KEY` is available). Fall back to a raw truncation of the transcript, or
    the caption when no transcript exists. Always returns a single-line string ≤
    DESCRIPTION_MAX_CHARS."""
    polished = (post.get("description_polished") or "").strip()
    if polished:
        return polished
    source = (post.get("transcript") or post.get("caption") or "").strip()
    if not source:
        return ""
    text = " ".join(source.split())
    if len(text) > DESCRIPTION_FALLBACK_MAX_CHARS:
        text = text[:DESCRIPTION_FALLBACK_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def split_sentences(text: str) -> list[str]:
    """Whisper outputs reasonable punctuation, so split on .!? followed by whitespace/end."""
    cleaned = _WS_COLLAPSE.sub(" ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [p.strip() for p in parts if p.strip()]


def format_content(transcript: str | None) -> str:
    """Paragraph-format the transcript: ~3 sentences per paragraph, capitalized starts. Pure
    heuristic — no LLM rewrite. The point is readability over a wall of text."""
    if not transcript:
        return ""
    sentences = split_sentences(transcript)
    if not sentences:
        return ""
    paragraphs: list[str] = []
    for i in range(0, len(sentences), 3):
        chunk = " ".join(sentences[i:i + 3]).strip()
        if chunk:
            paragraphs.append(chunk[0].upper() + chunk[1:])
    return "\n\n".join(paragraphs)


# ---------- filename helpers ------------------------------------------------------------------

def extract_hhmm(timestamp_value) -> str | None:
    """Pull `HH-MM` (filename-safe) out of an ISO timestamp string. Returns None when the
    value is missing or unparseable."""
    if not timestamp_value:
        return None
    s = str(timestamp_value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.strftime("%H-%M")


def compute_post_stems(posts: list[dict], reserved: set[str]) -> list[str]:
    """Three-pass, symmetric filename derivation:
      1. Compute base stem per post (`<date> <title>`).
      2. For each base-stem collision group, try `(HH-MM)` suffixes. If HH-MM uniquely
         distinguishes every post in the group, all get `(HH-MM)`. If two posts in the same
         group share a minute, the WHOLE group falls back to `(<shortcode>)` instead — never
         a mix of `(HH-MM)` and `(HH-MM) (shortcode)`, which produces asymmetric, ugly
         filenames.
      3. The reserved-set check is a safety net for the rare case where a post stem still
         clashes with the overview filename or another already-claimed name.
    """
    base_stems: list[str] = []
    for post in posts:
        post_date = fmt_date_iso(post.get("timestamp") or post.get("takenAtTimestamp"))
        type_label = "Reel" if is_reel(post) else (post.get("type") or "Post")
        title = derive_title(post.get("caption"), post_date, type_label)
        slug = slugify_for_filename(title)
        base = (
            f"{post_date} {slug}".strip()
            if slug
            else f"{post_date} {post.get('shortCode') or 'post'}"
        )
        base_stems.append(base)

    # Group post indices by their base stem
    groups: dict[str, list[int]] = {}
    for i, base in enumerate(base_stems):
        groups.setdefault(base, []).append(i)

    # Decide a per-post suffix policy. Default: no suffix. For collision groups, prefer
    # HH-MM; fall back to shortcode for the whole group when minutes collide internally.
    suffix_for: dict[int, str] = {}
    for base, indices in groups.items():
        if len(indices) <= 1:
            continue
        hhmms = [extract_hhmm(posts[i].get("timestamp") or posts[i].get("takenAtTimestamp")) for i in indices]
        if all(hhmms) and len(set(hhmms)) == len(hhmms):
            for i, hhmm in zip(indices, hhmms):
                suffix_for[i] = f"({hhmm})"
        else:
            for i in indices:
                sc = posts[i].get("shortCode") or "x"
                suffix_for[i] = f"({sc})"

    final: list[str] = []
    used = set(reserved)
    for i, base in enumerate(base_stems):
        suffix = suffix_for.get(i)
        stem = f"{base} {suffix}" if suffix else base
        if stem in used:
            sc = posts[i].get("shortCode") or "x"
            stem = f"{stem} ({sc})"
        used.add(stem)
        final.append(stem)
    return final


def overview_filename_stem(username: str) -> str:
    """`_<username> overview` — leading underscore so Obsidian sorts it to the top of the
    profile folder. No date in the name; each scrape overwrites the same overview file."""
    return f"_{username} overview"


# ---------- frontmatter blocks ----------------------------------------------------------------

def frontmatter_overview(
    profile: dict, scraped_at: datetime, created_str: str, modified_str: str
) -> str:
    """Property order mirrors the per-post frontmatter (title-first, identifier, metrics,
    timestamps, provenance, tags) for visual consistency in Obsidian Properties."""
    username = profile.get("username") or "unknown"
    biography = profile.get("biography") or ""
    description_preview = " ".join(biography.split())[:DESCRIPTION_FALLBACK_MAX_CHARS]
    scraped_str = fmt_property_ts(scraped_at)

    lines = ["---"]
    lines.append(f"title: {yaml_quote('@' + username + ' overview')}")
    lines.append(f"description: {yaml_quote(description_preview)}")
    lines.append("status: draft")
    lines.append(f"username: {yaml_quote(username)}")
    lines.append(f"followers: {fmt_int_yaml_str(profile.get('followersCount') or 0)}")
    lines.append(f"following: {fmt_int_yaml_str(profile.get('followsCount') or 0)}")
    lines.append(f"posts_total: {fmt_int_yaml_str(profile.get('postsCount') or 0)}")
    lines.append(f"verified: {str(bool(profile.get('verified'))).lower()}")
    lines.append(f"private: {str(bool(profile.get('private'))).lower()}")
    lines.append(f"scraped_at: {scraped_str}")
    lines.append(f"source: {SCRAPE_SOURCE}")
    lines.append(f"created: {created_str}")
    lines.append(f"modified: {modified_str}")
    lines.append("tags:")
    for tag in PROFILE_TAGS:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def frontmatter_post(
    post: dict,
    profile: dict,
    scraped_at: datetime,
    derived_title: str,
    created_str: str,
    modified_str: str,
) -> str:
    """Property order: title, description, status, username, likes, comments, video_views,
    post_date, scraped_at, post_type, shortcode, source, created, modified, tags.

    `has_transcript` and `transcript_model` are intentionally NOT in the YAML — the
    Transcript section in the body already shows whether transcription happened and which
    model produced it, so duplicating those facts in Properties just adds noise."""
    username = profile.get("username") or "unknown"
    post_date = fmt_date_iso(post.get("timestamp") or post.get("takenAtTimestamp"))
    shortcode = post.get("shortCode") or "unknown"
    is_reel_flag = is_reel(post)
    description_text = derive_description(post)
    scraped_str = fmt_property_ts(scraped_at)

    # Tag composition: platform-base ("Instagram") + LLM-derived content tags from
    # polish_post.py. The `Reel`/`Post` type tag is intentionally dropped — that distinction
    # is already in the byline and adds zero filtering value (every post here is one or the
    # other). Each content tag is run through `sanitize_tag` to make it Obsidian-safe
    # (`GPT-5.5` → `GPT-5-5` since Obsidian truncates tags at `.`).
    raw_content_tags = post.get("content_tags") or []
    sanitized = [sanitize_tag(t) for t in raw_content_tags if isinstance(t, str)]
    content_tags = [t for t in sanitized if t]
    # De-duplicate while preserving order (a content tag might equal "Instagram" — drop it)
    seen: set[str] = set()
    tags: list[str] = []
    for t in list(POST_TAGS_BASE) + content_tags:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(t)

    # likes / comments / video_views / post_type intentionally omitted from Properties:
    # the visible byline already shows likes ❤ · comments 💬 · views 👁, and the post type
    # is encoded in the `tags:` array (Reel / Post). Removing them from Properties keeps the
    # Obsidian Properties panel scannable instead of buried under stats.
    lines = ["---"]
    lines.append(f"title: {yaml_quote(derived_title)}")
    lines.append(f"description: {yaml_quote(description_text)}")
    lines.append("status: draft")
    lines.append(f"username: {yaml_quote(username)}")
    lines.append(f"post_date: {post_date}")
    lines.append(f"scraped_at: {scraped_str}")
    lines.append(f"shortcode: {yaml_quote(shortcode)}")
    lines.append(f"source: {SCRAPE_SOURCE}")
    lines.append(f"created: {created_str}")
    lines.append(f"modified: {modified_str}")
    lines.append("tags:")
    for tag in tags:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def frontmatter_summary(
    count: int, scraped_at: datetime, created_str: str, modified_str: str
) -> str:
    scraped_str = fmt_property_ts(scraped_at)
    lines = ["---"]
    lines.append(f"title: {yaml_quote(f'Instagram batch summary ({count} profiles)')}")
    lines.append(f"description: {yaml_quote(f'Comparative table for {count} Instagram profiles scraped on {scraped_str}')}")
    lines.append(f"created: {created_str}")
    lines.append(f"modified: {modified_str}")
    lines.append("status: draft")
    lines.append("tags:")
    for tag in SUMMARY_TAGS:
        lines.append(f"  - {tag}")
    lines.append(f"scraped_at: {scraped_str}")
    lines.append(f"source: {SCRAPE_SOURCE}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# ---------- body renderers --------------------------------------------------------------------

def render_overview_body(
    profile: dict,
    post_files: list[tuple[dict, Path]],
    dropped: list[dict] | None = None,
) -> str:
    username = profile.get("username") or "unknown"
    full_name = profile.get("fullName") or ""
    biography = profile.get("biography") or ""
    followers = profile.get("followersCount")
    follows = profile.get("followsCount")
    posts_count = profile.get("postsCount")
    verified = bool(profile.get("verified"))
    private = bool(profile.get("private"))
    is_business = bool(profile.get("isBusinessAccount"))
    business_cat = profile.get("businessCategoryName") or ""
    external_url = profile.get("externalUrl") or ""
    profile_url = profile.get("url") or f"https://www.instagram.com/{username}/"
    profile_pic = profile.get("profilePicUrl") or profile.get("profilePicUrlHD") or ""

    badges: list[str] = []
    if verified:
        badges.append("✅ Verified")
    if private:
        badges.append("🔒 Private")
    if is_business:
        badges.append("💼 Business" + (f" — {business_cat}" if business_cat else ""))
    badge_line = " · ".join(badges) if badges else "—"

    lines: list[str] = []
    lines.append(f"# @{username}")
    if full_name:
        lines.append("")
        lines.append(f"**{full_name}**")
    lines.append("")
    lines.append(f"**Status:** {badge_line}")
    lines.append("")
    lines.append(f"[Open profile]({profile_url})")
    if profile_pic:
        lines.append(f" · [Profile picture]({profile_pic})")
    lines.append("")

    lines.append("## Stats")
    lines.append("")
    lines.append("| # | Metric | Value |")
    lines.append("|---|---|---|")
    lines.append(f"| 1 | Followers | {fmt_int(followers)} |")
    lines.append(f"| 2 | Following | {fmt_int(follows)} |")
    lines.append(f"| 3 | Posts (total) | {fmt_int(posts_count)} |")
    lines.append(f"| 4 | Posts loaded | {fmt_int(len(post_files))} |")
    lines.append("")

    if biography:
        lines.append("## Bio")
        lines.append("")
        lines.append("> " + biography.replace("\n", "\n> "))
        lines.append("")

    if external_url:
        lines.append(f"**External link:** {external_url}")
        lines.append("")

    ranked = sorted(post_files, key=lambda pf: engagement_score(pf[0]), reverse=True)
    top = ranked[:3]
    if top:
        lines.append("## Top 3 posts (by likes + 2×comments)")
        lines.append("")
        lines.append("| # | Date | Type | Likes | Comments | Score | Caption | Note |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, (post, path) in enumerate(top, start=1):
            lines.append(
                "| {idx} | {date} | {ptype} | {likes} | {comments} | {score} | {caption} | [{name}]({link}) |".format(
                    idx=i,
                    date=fmt_date_iso(post.get("timestamp")),
                    ptype="Reel" if is_reel(post) else (post.get("type") or "—"),
                    likes=fmt_int(post.get("likesCount")),
                    comments=fmt_int(post.get("commentsCount")),
                    score=fmt_int(engagement_score(post)),
                    caption=md_escape_pipe(caption_preview(post.get("caption"))),
                    name=path.stem,
                    link=url_encode_link(path.name),
                )
            )
        lines.append("")

    lines.append(f"## All loaded posts ({len(post_files)})")
    lines.append("")
    lines.append("| # | Date | Type | Likes | Comments | Caption | Note |")
    lines.append("|---|---|---|---|---|---|---|")
    chronological = sorted(post_files, key=lambda pf: pf[0].get("timestamp") or "", reverse=True)
    for i, (post, path) in enumerate(chronological, start=1):
        lines.append(
            "| {idx} | {date} | {ptype} | {likes} | {comments} | {caption} | [{name}]({link}) |".format(
                idx=i,
                date=fmt_date_iso(post.get("timestamp")),
                ptype="Reel" if is_reel(post) else (post.get("type") or "—"),
                likes=fmt_int(post.get("likesCount")),
                comments=fmt_int(post.get("commentsCount")),
                caption=md_escape_pipe(caption_preview(post.get("caption"))),
                name=path.stem,
                link=url_encode_link(path.name),
            )
        )
    lines.append("")

    # Caption-duplicate audit trail. Maps each dropped post to its surviving twin (same caption,
    # higher engagement) so the user can see what was deduped and why.
    if dropped:
        # Build caption → kept-post lookup so each dropped row can link to its winner
        kept_by_caption: dict[str, tuple[dict, Path]] = {}
        for post, path in post_files:
            cap = (post.get("caption") or "").strip()
            if cap and cap not in kept_by_caption:
                kept_by_caption[cap] = (post, path)
        lines.append(f"## Removed duplicates ({len(dropped)})")
        lines.append("")
        lines.append(
            "_Posts whose caption matched a higher-engagement variant — kept the winner "
            "(highest likes + 2× comments), dropped the rest. The dedupe runs on every "
            "render; the originals are still in the JSON for re-scraping/auditing._"
        )
        lines.append("")
        lines.append("| # | Date | Type | Likes | Comments | Score | Caption | Replaced by |")
        lines.append("|---|---|---|---|---|---|---|---|")
        dropped_chronological = sorted(dropped, key=lambda p: p.get("timestamp") or "", reverse=True)
        for i, post in enumerate(dropped_chronological, start=1):
            cap = (post.get("caption") or "").strip()
            winner = kept_by_caption.get(cap)
            if winner is not None:
                _, winner_path = winner
                replaced_by = f"[{winner_path.stem}]({url_encode_link(winner_path.name)})"
            else:
                replaced_by = "—"
            lines.append(
                "| {idx} | {date} | {ptype} | {likes} | {comments} | {score} | {caption} | {replaced} |".format(
                    idx=i,
                    date=fmt_date_iso(post.get("timestamp")),
                    ptype="Reel" if is_reel(post) else (post.get("type") or "—"),
                    likes=fmt_int(post.get("likesCount")),
                    comments=fmt_int(post.get("commentsCount")),
                    score=fmt_int(engagement_score(post)),
                    caption=md_escape_pipe(caption_preview(post.get("caption"))),
                    replaced=replaced_by,
                )
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_Scraped via Apify `apify/instagram-scraper`. "
        f"Rendered: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_"
    )
    lines.append("")
    return "\n".join(lines)


def render_post_body(post: dict, profile: dict, overview_path: Path) -> str:
    username = profile.get("username") or "unknown"
    post_date = fmt_date_iso(post.get("timestamp") or post.get("takenAtTimestamp"))
    type_label = "Reel" if is_reel(post) else (post.get("type") or "Post")
    caption = post.get("caption") or ""
    likes = post.get("likesCount")
    comments = post.get("commentsCount")
    views = post.get("videoViewCount")
    post_url = post.get("url") or f"https://www.instagram.com/p/{post.get('shortCode', '')}/"

    transcript = post.get("transcript")
    transcript_model = post.get("transcript_model") or "?"
    transcribed_at = post.get("transcribed_at") or "?"

    polished_content = (post.get("content_polished") or "").strip()
    polished_content_model = post.get("content_polished_model") or ""

    hashtags = post.get("hashtags") or []
    mentions = post.get("mentions") or []

    derived_title = derive_title(caption, post_date, type_label)

    lines: list[str] = []
    lines.append(f"# {derived_title}")
    lines.append("")

    # Single italic byline: meta + stats + nav links in one scannable line.
    byline_parts = [username, post_date, type_label]
    if likes is not None:
        byline_parts.append(f"{fmt_int(likes)} ❤")
    if comments is not None:
        byline_parts.append(f"{fmt_int(comments)} 💬")
    if is_reel(post) and views is not None:
        byline_parts.append(f"{fmt_int(views)} 👁")
    byline_parts.append(f"[Open]({post_url})")
    byline_parts.append(f"[← Profile]({url_encode_link(overview_path.name)})")
    lines.append("_" + " · ".join(byline_parts) + "_")
    lines.append("")

    if not is_reel(post) and caption.strip():
        # Non-Reel posts (Image, Sidecar, etc.) carry their substance in the caption itself,
        # not in a spoken transcript. Render it verbatim as `## Caption`. For Reels we
        # intentionally omit this section because the polished `## Content` is a rewrite
        # of the spoken substance and the caption is usually redundant scaffolding.
        lines.append("## Caption")
        lines.append("")
        lines.append(caption.rstrip())
        lines.append("")

    if is_reel(post):
        lines.append("## Content")
        lines.append("")
        if polished_content:
            lines.append(
                f"_Neutral briefing rewritten by Anthropic Claude "
                f"(`{polished_content_model}`) from the raw transcript._"
            )
            lines.append("")
            # The polish step emits Markdown (paragraphs, inline-bold lead-ins, lists).
            # Embed verbatim — wrapping in `> ` blockquotes would defeat the readability
            # the rewrite exists for.
            lines.append(polished_content.rstrip())
            lines.append("")
        elif transcript:
            # Fallback: no LLM polish available, but a transcript exists. Render the raw
            # transcript as a blockquote with a paragraph-formatting heuristic so the file
            # isn't empty. Re-run polish_post.py once ANTHROPIC_API_KEY is set.
            lines.append(
                "_No LLM polish available — raw transcript paragraph-formatted heuristically. "
                "Set `ANTHROPIC_API_KEY` and re-run `polish_post.py` for a neutral briefing._"
            )
            lines.append("")
            for paragraph in format_content(transcript).split("\n\n"):
                if not paragraph.strip():
                    continue
                for line in paragraph.split("\n"):
                    lines.append(f"> {line}" if line else ">")
                lines.append("")
        else:
            lines.append("_Content nicht erstellt — transcript fehlt._")
            lines.append("")

        # Transcript section is a fallback for verification only when polish is missing.
        # When polish succeeded, the briefing replaces the transcript and the verbatim text
        # stays in the JSON for re-polish runs.
        if not polished_content and transcript:
            lines.append("## Transcript")
            lines.append("")
            lines.append(
                f"_Transcribed by `whisper.cpp` (model: `{transcript_model}`) on "
                f"{transcribed_at[:19] if isinstance(transcribed_at, str) else transcribed_at}._"
            )
            lines.append("")
            for line in transcript.split("\n"):
                lines.append(f"> {line}" if line else ">")
            lines.append("")

    if hashtags or mentions:
        lines.append("## Hashtags / Mentions")
        lines.append("")
        if hashtags:
            lines.append("**Hashtags:** " + " ".join(f"`#{h}`" for h in hashtags))
        if mentions:
            lines.append("**Mentions:** " + " ".join(f"@{m}" for m in mentions))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_Scraped via Apify `apify/instagram-scraper`. "
        f"Rendered: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_"
    )
    lines.append("")
    return "\n".join(lines)


def render_summary_body(entries: list[tuple[dict, Path]]) -> str:
    lines: list[str] = []
    lines.append("# Instagram batch summary")
    lines.append("")
    lines.append(
        f"_Scraped: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_ · "
        f"_{len(entries)} profile(s)_"
    )
    lines.append("")
    lines.append(
        "| # | Profile | Verified | Followers | Following | Posts (total) | Posts loaded | Avg. likes | Avg. comments | Engagement / 1k followers |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    for i, (profile, overview_path) in enumerate(entries, start=1):
        username = profile.get("username") or "—"
        posts = get_posts(profile)
        likes_list = [int(p.get("likesCount") or 0) for p in posts]
        comments_list = [int(p.get("commentsCount") or 0) for p in posts]
        followers = int(profile.get("followersCount") or 0)
        avg_likes = sum(likes_list) // len(likes_list) if likes_list else 0
        avg_comments = sum(comments_list) // len(comments_list) if comments_list else 0
        per_1k = (
            f"{((avg_likes + 2 * avg_comments) / followers * 1000):.2f}"
            if followers > 0 and posts
            else "—"
        )
        rel_link = f"{overview_path.parent.name}/{overview_path.name}"
        lines.append(
            "| {idx} | [@{u}]({link}) | {v} | {f} | {fl} | {pc} | {pl} | {al} | {ac} | {pk} |".format(
                idx=i,
                u=username,
                link=url_encode_link(rel_link),
                v="✅" if profile.get("verified") else "—",
                f=fmt_int(profile.get("followersCount")),
                fl=fmt_int(profile.get("followsCount")),
                pc=fmt_int(profile.get("postsCount")),
                pl=len(posts),
                al=fmt_int(avg_likes) if posts else "—",
                ac=fmt_int(avg_comments) if posts else "—",
                pk=per_1k,
            )
        )
    lines.append("")
    lines.append(
        "_Engagement / 1k followers ≈ (avg likes + 2 × avg comments) / followers × 1000._"
    )
    lines.append("")
    return "\n".join(lines)


# ---------- top-level rendering ---------------------------------------------------------------

def render_profile(input_path: Path) -> tuple[Path, list[Path], list[Path]]:
    """Render one profile. Returns (overview_path, post_paths_written, deleted_legacy_paths)."""
    profile = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or "username" not in profile:
        raise ValueError(f"Not a profile JSON: {input_path}")

    scraped_at = derive_scrape_timestamp(input_path, profile)
    profile_dir = input_path.parent
    profile_dir.mkdir(parents=True, exist_ok=True)
    username = profile["username"]
    raw_posts = get_posts(profile)
    posts, dropped = dedupe_caption_variants(raw_posts)
    if dropped:
        sys.stderr.write(
            f"INFO: dropped {len(dropped)} duplicate-caption post(s) with lower engagement: "
            + ", ".join(p.get("shortCode") or "?" for p in dropped) + "\n"
        )

    overview_stem = overview_filename_stem(username)
    overview_path = profile_dir / f"{overview_stem}.md"

    # Two-pass filename derivation handles duplicate captions properly: same-title posts
    # get HH-MM suffixes symmetrically rather than the second-runner-up getting a shortcode.
    stems = compute_post_stems(posts, reserved={overview_stem})
    post_files: list[tuple[dict, Path]] = [
        (post, profile_dir / f"{stem}.md") for post, stem in zip(posts, stems)
    ]

    # Write per-post files
    for post, path in post_files:
        post_date = fmt_date_iso(post.get("timestamp") or post.get("takenAtTimestamp"))
        type_label = "Reel" if is_reel(post) else (post.get("type") or "Post")
        derived_title = derive_title(post.get("caption"), post_date, type_label)
        body = render_post_body(post, profile, overview_path)
        created_str, modified_str = resolve_timestamps(path)
        fm = frontmatter_post(post, profile, scraped_at, derived_title, created_str, modified_str)
        path.write_text(fm + body, encoding="utf-8")

    # Write overview
    overview_body = render_overview_body(profile, post_files, dropped=dropped)
    overview_created, overview_modified = resolve_timestamps(overview_path)
    overview_fm = frontmatter_overview(profile, scraped_at, overview_created, overview_modified)
    overview_path.write_text(overview_fm + overview_body, encoding="utf-8")

    # Cleanup orphaned shortcode-named files from previous renders
    keep_paths = {p for _, p in post_files}
    overview_paths = {overview_path}
    deleted = cleanup_old_post_files(profile_dir, keep_paths, overview_paths, SCRAPE_SOURCE)

    return overview_path, [p for _, p in post_files], deleted


def render_batch(platform_dir: Path) -> tuple[list[Path], Path | None, list[Path]]:
    if not platform_dir.is_dir():
        raise ValueError(f"Not a directory: {platform_dir}")

    entries: list[tuple[dict, Path]] = []
    rendered_overviews: list[Path] = []
    all_deleted: list[Path] = []
    latest_scrape: datetime | None = None

    for profile_dir in sorted(platform_dir.iterdir()):
        if not profile_dir.is_dir():
            continue
        # Look for the canonical overview JSON `_<username> overview.json`. The folder name
        # may include an essence suffix (`<username> — <essence>/`), so we can't derive the
        # username from `profile_dir.name`. Glob for the underscore-prefixed file directly.
        candidates = sorted(profile_dir.glob("_*overview.json"))
        if not candidates:
            # Fall back to any `*overview.json` for files written by very old versions.
            candidates = sorted(profile_dir.glob("*overview.json"))
            if not candidates:
                continue
        overview_json = candidates[-1]
        try:
            profile = json.loads(overview_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sys.stderr.write(f"WARN: skipping unparseable JSON: {overview_json}\n")
            continue
        if not isinstance(profile, dict) or "username" not in profile:
            continue

        overview_md, _, deleted = render_profile(overview_json)
        rendered_overviews.append(overview_md)
        all_deleted.extend(deleted)
        entries.append((profile, overview_md))

        ts = derive_scrape_timestamp(overview_json, profile)
        if latest_scrape is None or ts > latest_scrape:
            latest_scrape = ts

    if len(entries) < 2:
        return rendered_overviews, None, all_deleted

    summary_ts = latest_scrape or datetime.now(timezone.utc)
    summary_path = platform_dir / f"{summary_ts.strftime('%Y-%m-%d')} instagram batch summary.md"
    summary_body = render_summary_body(entries)
    summary_created, summary_modified = resolve_timestamps(summary_path)
    summary_fm = frontmatter_summary(len(entries), summary_ts, summary_created, summary_modified)
    summary_path.write_text(summary_fm + summary_body, encoding="utf-8")
    return rendered_overviews, summary_path, all_deleted


def main() -> int:
    args = parse_args()

    if args.input is not None:
        overview_path, post_paths, deleted = render_profile(args.input)
        print(
            json.dumps(
                {
                    "mode": "single",
                    "overview": str(overview_path),
                    "posts": [str(p) for p in post_paths],
                    "deleted_legacy": [str(p) for p in deleted],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    overviews, summary, deleted = render_batch(args.batch_dir)
    print(
        json.dumps(
            {
                "mode": "batch",
                "overviews": [str(p) for p in overviews],
                "summary": str(summary) if summary else None,
                "deleted_legacy": [str(p) for p in deleted],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
