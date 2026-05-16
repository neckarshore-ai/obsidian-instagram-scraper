#!/usr/bin/env python3
"""Polish Instagram Reel transcripts via the Anthropic Claude API.

For every Reel (`productType == 'clips'`) in a scraped raw.json that has a transcript,
make ONE Haiku call that returns a JSON object with two fields:

  - `description` — third-person, neutral, factual, ≤120 chars. Used in YAML Properties.
  - `content`     — paragraph-formatted polish of the transcript. Preserves the speaker's
                    voice (first-person stays first-person), fixes obvious whisper-mistakes
                    in context, splits long monologues into ~3-sentence paragraphs.

Idempotent: if `description_polished` AND `content_polished` are both already set, skip.

Pricing reference: Haiku 4.5 input ~$1/MTok, output ~$5/MTok. With prompt caching on the
system instruction, per-Reel ≈ $0.005 ($0.06 for a 12-Reel profile).

Setup:
  ANTHROPIC_API_KEY in env. Get one at https://console.anthropic.com/settings/keys.
  pip install anthropic  (already pinned in requirements.txt)

Usage:
  polish_post.py --input <raw.json>                          # default model: claude-haiku-4-5-20251001
  polish_post.py --input <raw.json> --no-skip-existing       # re-polish even if fields exist
  polish_post.py --input <raw.json> --model claude-sonnet-4-6   # use a different model

The script no-ops gracefully if `ANTHROPIC_API_KEY` isn't set — prints a short setup hint and
exits 0 so it can be safely chained from `scrape_profile.py --transcribe`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _social_common.tokens import get_anthropic_key, print_anthropic_setup_hint
from _social_common.llm_helpers import extract_json_object
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_DESCRIPTION_CHARS = 120
REEL_PRODUCT_TYPE = "clips"

# Single cacheable system prompt for the entire run. Anthropic's prompt-cache TTL is 5 min,
# comfortably long for a 12-Reel batch — system tokens billed once, re-used per Reel.
SYSTEM_PROMPT = """\
You rewrite raw Instagram-Reel speech-to-text transcripts as neutral, well-structured briefings
for a content-research vault. Goal: a reader should grasp the substance faster from your
rewrite than from the raw transcript.

Given a transcript, return ONE JSON object with these fields:

{
  "description": "<one factual sentence describing the Reel's topic>",
  "content":     "<a neutral, structured Markdown briefing of the substance>",
  "tags":        ["Tag1", "Tag2", "Tag3"]
}

Rules for `description`:
- Third person, neutral, factual. No "I", "we", "you", "let's", "here's".
- Describe WHAT the Reel is about (the topic), not what the speaker is doing.
- Maximum 120 characters total. Strict.
- No marketing language ("amazing", "must-see", "incredible", "game-changer").
- No filler prefix ("This Reel covers", "In this video", "The creator says").
- One sentence. End with a period.

Rules for `content`:
- Neutral third-person voice. NOT a transcript polish, NOT first-person mimicry of the speaker.
  Translate the speaker's substance into a neutral briefing.
- Strip the performative scaffolding: hooks ("today I'll show you"), self-promo, calls to action
  ("comment X to get my guide"), and rhetorical filler. Keep only the actual content.
- Use Markdown structure aggressively for readability. Examples:
    * Short intro paragraph (1–2 sentences) naming the topic.
    * Bullet lists when the speaker enumerates features, steps, options, pros/cons.
    * `**Bold**` for product/tool names and key claims.
    * For multi-aspect Reels, use INLINE bold lead-ins as section markers, not headings:
      `**Setup —** Content here.` followed by `**Limitations —** Content here.` Each lead-in
      is its own paragraph. Do NOT use `###` or any other Markdown headings — the rendered
      file already has `## Content` as the section heading and skipping levels breaks lint.
    * Skip lead-ins entirely if the Reel is single-topic — just write paragraphs.
- Fix obvious mis-transcriptions only when context makes them UNAMBIGUOUS (e.g. "Cloud Code" →
  "Claude Code" when the speaker is clearly demoing Anthropic's CLI). When in doubt, leave the
  word verbatim — accuracy beats polish.
- Brand attribution is hard fact, not interpretation. Do NOT cross-attribute features, tools,
  or capabilities across ecosystems (OpenAI ≠ Anthropic ≠ Google ≠ Meta).
- Reference inventory of common AI tools by ecosystem (use this to disambiguate whisper
  errors — e.g. "codecs", "cloud code", "anthropix", "google stitch"):
    * OpenAI: ChatGPT, Codex (CLI for code generation), GPT-4/5/5.5
    * Anthropic: Claude, Claude Code (Anthropic's coding CLI), Claude Desktop, Claude.ai
    * Google: Gemini, Stitch (design tool), Vertex AI, Bard
    * Meta: Llama, Meta AI
- Consistency check: the IDE/CLI/tool a speaker uses must be compatible with the model
  they're demonstrating. Claude Code runs Claude models, NOT GPT models. Codex runs GPT
  models, NOT Claude. If your draft says "entered it into Claude Code (using GPT-5.5)",
  that is a contradiction — pick the right tool for the model.
- When in doubt about ANY proper noun (product, company, person), prefer omission or generic
  wording ("the model", "the tool", "the company") over a guess. A factual gap is better than
  a wrong attribution.
- Preserve EVERY substantive claim, number, name, and step. The polished version replaces the
  transcript for skim-reading; nothing of substance should be lost.
- Keep it concise. If the speaker repeats themselves, state it once.
- Do NOT add information that isn't in the transcript. Do NOT speculate or fill gaps.

Rules for `tags` (used as Obsidian content tags):
- Exactly 2 to 3 tags. No more, no less.
- Specific to THIS Reel's substance: product/tool names, models, companies, technical
  topics. Examples: `OpenAI`, `GPT-5-5`, `Claude-Code`, `Anthropic`, `Codex`, `AI-Coding`,
  `Browser-Automation`, `Design-Systems`.
- Format: PascalCase or kebab-case. ASCII letters/digits/hyphens/underscores ONLY. No
  dots (Obsidian truncates tags at `.`), no spaces, no slashes, no emoji. So write
  `GPT-5-5` not `GPT-5.5`, `Claude-Code` not `Claude Code`.
- Avoid generic structural tags ("Instagram", "Reel", "Video", "Tutorial", "Tip", "Howto",
  "AI", "Tech") — those add no filtering value because every post would have them.
- Avoid hashtag-style marketing words ("Trending", "MustWatch").
- If the Reel mentions both a product and the company that makes it, prefer the product
  unless the company itself is the news ("OpenAI" + "GPT-5-5" both OK; "Anthropic" alone is
  weak — pair with the specific product).

The `content` value is a Markdown string — embed real `\\n` line breaks (JSON-escaped) for
paragraph breaks, blank lines around lists, etc. Do NOT wrap output in code fences.

Output ONLY the JSON object. No preamble. No commentary.
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--input", type=Path, required=True, help="Path to a raw.json from scrape_profile.py")
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model ID (default: {DEFAULT_MODEL}).",
    )
    p.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip Reels that already have BOTH description_polished and content_polished. Default ON.",
    )
    return p.parse_args()


def truncate_description(text: str) -> str:
    text = text.strip().strip("\"'“”‘’ ")
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[: MAX_DESCRIPTION_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return text


def main() -> int:
    args = parse_args()

    api_key = get_anthropic_key()
    if not api_key:
        sys.stderr.write(
            "INFO: ANTHROPIC_API_KEY not set — skipping Reel-polish step.\n"
            "Setup:\n"
            "  1. Get a key at https://console.anthropic.com/settings/keys\n"
            "  2. Add to ~/.zshrc:  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  3. Reload your shell:  source ~/.zshrc\n"
        )
        print(json.dumps({"polished": 0, "skipped": 0, "reason": "no_api_key"}))
        return 0

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.stderr.write(
            "ERROR: 'anthropic' package not installed. Run:\n"
            "  pip install -r " + str(Path(__file__).parent.parent / "requirements.txt") + "\n"
        )
        return 2

    if not args.input.exists():
        sys.stderr.write(f"ERROR: input file not found: {args.input}\n")
        return 2

    profile = json.loads(args.input.read_text(encoding="utf-8"))
    posts = profile.get("latestPosts") or []
    if not isinstance(posts, list):
        sys.stderr.write("ERROR: latestPosts is not a list — is this a profile-details JSON?\n")
        return 2

    candidates: list[tuple[int, dict]] = []
    for i, p in enumerate(posts):
        if p.get("productType") != REEL_PRODUCT_TYPE:
            continue
        if not p.get("transcript"):
            continue
        if args.skip_existing and p.get("description_polished") and p.get("content_polished"):
            continue
        candidates.append((i, p))

    if not candidates:
        sys.stderr.write(f"INFO: no Reels need polishing in {args.input.name}\n")
        print(json.dumps({"polished": 0, "skipped": len(posts), "reason": "nothing_to_do"}))
        return 0

    sys.stderr.write(f"INFO: polishing {len(candidates)} Reel(s) via {args.model}\n")

    client = Anthropic(api_key=api_key)

    polished_count = 0
    failed: list[dict] = []
    polished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for n, (idx, post) in enumerate(candidates, start=1):
        shortcode = post.get("shortCode") or f"#{idx}"
        transcript = post.get("transcript") or ""
        # Trim very long transcripts (rare for ~60s Reels) to keep input tokens bounded
        prompt_input = transcript[:6000]

        sys.stderr.write(f"  [{n}/{len(candidates)}] {shortcode}: polishing ...\n")
        try:
            msg = client.messages.create(
                model=args.model,
                max_tokens=2000,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": f"Transcript:\n\n{prompt_input}"}
                ],
            )
        except Exception as exc:  # broad — keep processing the rest
            sys.stderr.write(f"    ERROR: {exc}\n")
            failed.append({"shortcode": shortcode, "reason": str(exc)[:200]})
            continue

        text_chunks = [block.text for block in msg.content if getattr(block, "type", None) == "text"]
        raw = " ".join(text_chunks).strip()
        parsed = extract_json_object(raw)

        if not isinstance(parsed, dict):
            failed.append({"shortcode": shortcode, "reason": "non_json_response", "raw_head": raw[:200]})
            continue

        description = (parsed.get("description") or "").strip()
        content = (parsed.get("content") or "").strip()
        # Tags are best-effort: missing/empty tags shouldn't fail the whole polish, the
        # renderer falls back to platform-base tags when content_tags is absent.
        raw_tags = parsed.get("tags") or []
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []

        if not description or not content:
            failed.append({"shortcode": shortcode, "reason": "missing_field", "got": list(parsed.keys())})
            continue

        post["description_polished"] = truncate_description(description)
        post["description_polished_model"] = args.model
        post["description_polished_at"] = polished_at
        post["content_polished"] = content
        post["content_polished_model"] = args.model
        post["content_polished_at"] = polished_at
        post["content_tags"] = tags
        polished_count += 1

    args.input.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "input": str(args.input),
                "polished": polished_count,
                "skipped": len(posts) - len(candidates),
                "failed": failed,
                "model": args.model,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
