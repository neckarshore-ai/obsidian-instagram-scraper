---
name: instagram-scraper
description: >
  Scrape Instagram profiles and their latest posts (incl. **spoken transcripts of Reels**) via
  the Apify `apify/instagram-scraper` actor + local `whisper.cpp`. Use this skill whenever the
  user asks to scrape, fetch, pull, ziehen, analyze, audit, monitor, or research one or more
  Instagram accounts — including follower counts, bios, verification status, business category,
  recent posts, likes/comments, engagement, **what was said in a Reel**, or competitive/influencer
  research. Strong triggers: "scrape Instagram", "Instagram-Profil ziehen", "analyse @handle",
  "wie viele Follower hat …", "engagement von …", "letzte Posts von …", "Reels transkribieren",
  "was sagt @X in seinen Reels", "Instagram report for …", "compare these IG accounts", "screen
  these influencers", or any message containing one or more `@handle` references where data
  extraction or content analysis is implied. Auto-detects single vs. batch input. Returns one
  Obsidian-friendly subfolder per influencer with an overview note and one file per post (Reels
  carry a neutral Claude-Haiku-polished briefing of the spoken content inline). Costs ~$0.02
  per profile via Apify; transcription is free (local whisper.cpp); optional polish via
  Anthropic Haiku 4.5 adds ~$0.06 per 12-Reel profile.
---

# Instagram scraper + Reel transcription + polish

Wraps the `apify/instagram-scraper` actor, a local `whisper.cpp` transcription pipeline, and an
Anthropic-Haiku polish step in four Python scripts:

| File | Purpose |
|---|---|
| `scripts/scrape_profile.py` | Calls Apify, writes one `<username>/_<username> overview.json` per profile. With `--transcribe`, chains transcription + polish automatically. |
| `scripts/transcribe_videos.py` | For every Reel (`productType == clips`) in a JSON: download → ffmpeg-extract audio → whisper-cli → write `transcript` back into JSON. |
| `scripts/polish_post.py` | For every Reel with a transcript: one Haiku call returns `{description, content, tags}` — neutral third-person briefing + Obsidian-safe content tags. Idempotent; skips Reels already polished. |
| `scripts/essence_profile.py` | One Haiku call per profile returns a one-sentence "essence" (≤60 chars) of what the account stands for. Stored in JSON `_essence`; used for the folder name `<username> — <essence>`. Idempotent; locked at first scrape. |
| `scripts/render_report.py` | Renders the JSON into Obsidian-friendly Markdown: overview note + one per-post note per profile, plus a cross-influencer summary in batch mode. Dedupes caption-variants across the profile (highest engagement wins). |

## Setup (one-time)

### Apify

```bash
# Add to ~/.zshrc, then `source ~/.zshrc`
export APIFY_API_TOKEN='apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
```

Token at <https://console.apify.com/account/integrations>. **Resource-specific Actor permissions
must use the 17-char hex ID** (`shu8hvrXbJbY3Eb9W` for `apify/instagram-scraper`), not the slug —
see `references/apify-actor.md` for the exact token config.

### Transcription stack (only needed if you'll use `--transcribe`)

```bash
brew install whisper-cpp ffmpeg
mkdir -p ~/.local/share/whisper-cpp/models
curl -L -o ~/.local/share/whisper-cpp/models/ggml-medium.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin
```

`medium.en` is the default. Multilingual creators → swap to `medium` (no `.en` suffix). See
`references/transcription.md` for the full model matrix and trade-offs.

### Anthropic polish (auto-chained after `--transcribe`)

```bash
# Add to ~/.zshrc, then `source ~/.zshrc`
export ANTHROPIC_API_KEY='sk-ant-...'
```

Token at <https://console.anthropic.com/settings/keys>. If the key is missing, the polish step
no-ops with a setup hint and the pipeline continues — Reels just won't get a `## Content`
briefing or content tags. Default model: `claude-haiku-4-5-20251001`.

### Skill venv

```bash
SKILL_DIR=~/.claude/skills/instagram-scraper
PY=$SKILL_DIR/.venv/bin/python
```

## When to invoke this skill

1. The user names one or more Instagram handles and asks for any data about them.
2. The user asks for the **content** of someone's Reels ("was sagt X", "transcribe X's reels",
   "what does X talk about"). → Use `--transcribe`.
3. The user asks for an Instagram-focused competitive analysis or influencer screen.
4. The user wants to monitor or refresh prior Instagram data ("re-scrape these").

Do **not** invoke for: comments-only scraping, hashtag/place searches, Stories, anything
requiring login. Offer to extend the skill instead — see "Out-of-scope" below.

## Workflow

### 1. Parse the user's input into a list of usernames

The script normalizes `@user`, `user`, `https://instagram.com/user/`, mixed delimiters — pass
the user's text through. Ambiguous request ("scrape some food bloggers")? Ask once for explicit
handles; the skill does not do discovery.

### 2. Decide whether to transcribe (and polish)

Default: **no transcripts** (saves wall-clock time). Pass `--transcribe` when the user clearly
cares about Reel content rather than just metadata. Transcription cost is zero (local), but it
adds ~30 seconds per Reel of wall-clock time.

`--transcribe` also auto-chains the **polish step** (`polish_post.py`) once `ANTHROPIC_API_KEY`
is set. The polish step calls Haiku 4.5 once per Reel and writes a neutral third-person
briefing (`content_polished`) plus 2–3 content tags into the JSON. Without the key, the
chain no-ops and Reels render the raw transcript as a fallback. See "Polishing" below.

### 3. Decide on `--posts-limit`

Default 12. Apify's `details` mode ignores this in practice and returns ~12 latest posts
regardless. Don't promise more than that — see `references/apify-actor.md`.

### 4. Run the scrape

**`--out-dir` resolves from `$OBSIDIAN_VAULT_PATH`** — the env var pointing to the user's
Obsidian vault root. Always pass `--out-dir` explicitly:

```bash
"$PY" "$SKILL_DIR/scripts/scrape_profile.py" \
  --usernames "<comma-separated handles or URLs>" \
  --posts-limit 12 \
  --transcribe \
  --out-dir "${OBSIDIAN_VAULT_PATH:?Set OBSIDIAN_VAULT_PATH to your vault root before running this skill}/Instagram Scraper"
```

If the user's vault has a dedicated inbox folder (e.g. `Inbox/Social Scrapers/Instagram Scraper`),
append that path segment. Otherwise the default `<vault>/Instagram Scraper` is fine — that's
where omitted `--out-dir` also lands (the script auto-resolves from `$OBSIDIAN_VAULT_PATH`).

The script creates `<out-dir>/<username>/` subfolders, writes one `_<username> overview.json`
per profile (overwritten on re-scrape), and (with `--transcribe`) fills the JSON with
`transcript` fields for every Reel — then auto-chains `polish_post.py` to add
`description_polished`, `content_polished`, and `content_tags` for each Reel. Stdout is a JSON
summary with `succeeded`/`failed`/`transcription_results`.

Override the default destination only when the user explicitly says where (project repo, Desktop,
etc.). Otherwise, always the Obsidian vault.

### 5. Render reports

```bash
"$PY" "$SKILL_DIR/scripts/render_report.py" --batch-dir "<platform-folder>"
```

The renderer walks every `<username>/` subfolder, picks up `_<username> overview.json`, and
writes:

- `<username>/_<username> overview.md` — profile card + posts index (sorts to top via `_` prefix)
- `<username>/<post-date> <title-slug>.md` — one note per post; Reels carry the polished
  briefing (or transcript fallback) inline

Caption-dedup is applied automatically — duplicate captions across the profile collapse to the
highest-engagement variant; dropped posts surface in `## Removed duplicates (N)` in the overview.

For ≥ 2 influencers it also writes `<DATE> instagram batch summary.md` at the platform-folder
root, with a cross-influencer comparison table.

To re-render just one profile: `--input <path-to-overview.json>`.

### 6. Tell the user what landed where

Show file paths and a 3-bullet TL;DR. Don't paste the full Markdown unless asked.

## Output shape

Per-influencer subfolder (`Social Scrapers/Instagram Scraper/<username> — <essence>/`):

```
chase.h.ai — AI tools and Claude automation for developers/
├── _chase.h.ai overview.json                # raw Apify response (hidden in Obsidian)
├── _chase.h.ai overview.md                  # profile card + stats + posts index (sorts to top via leading "_")
├── 2026-04-21 the-best-ai-coding-tool.md    # one note per post — `<post-date> <title-slug>.md`
├── 2026-04-20 i-built-a-saas-in-an-hour.md  # title slug derived from caption (engagement-bait stripped)
└── ... (≤ 12 files after caption-dedup)
```

Filenames in detail:
- **Folder** name carries the LLM-generated essence after the first scrape:
  `<username> — <essence>/` (em-dash with spaces). The essence is locked at first scrape and
  never auto-updates — folder renames break Obsidian links, so stability wins. Force a fresh
  one with `essence_profile.py --regenerate` followed by a manual `mv` of the folder.
- **Overview file** is undated and uses a leading underscore so Obsidian sorts it to the top
  of the profile folder. Filename stays `_<username> overview.{json,md}` regardless of folder
  name — username inside the file, essence outside the file.
- **Per-post** stems are `<post-date> <title-slug>`. Collisions (multiple posts on the same
  date with the same slug) get an `(HH-MM)` suffix; if minutes also collide, the whole group
  falls back to `(<shortcode>)` — never a mixed-suffix group.

Caption-dedup runs every render: the same caption posted multiple times (cross-day reposts,
A/B tests) is collapsed to the single highest-engagement variant. Dropped posts are listed in
a `## Removed duplicates (N)` section in the overview with a link to the variant that won.

Re-scraping the same profile **overwrites** both the overview and per-post files (refreshes
likes/comments/transcript/polish).

For ≥ 2 profiles, additionally:

```
Social Scrapers/Instagram Scraper/
├── chase.h.ai/
├── nasa/
└── 2026-04-25 instagram batch summary.md  # cross-influencer comparison
```

## Body shape (per-post Reel)

```markdown
# <derived title from caption>

_username · 2026-04-21 · Reel · 170 ❤ · 8 💬 · 4775 👁 · [Open](...) · [← Profile](...)_

## Content

_Neutral briefing rewritten by Anthropic Claude (`claude-haiku-4-5-20251001`) from the raw transcript._

<polished Markdown verbatim — paragraphs, **inline bold lead-ins**, bullet lists>

## Hashtags / Mentions

**Hashtags:** `#tag1` `#tag2`
**Mentions:** @user1 @user2
```

If the polish step didn't run (no `ANTHROPIC_API_KEY`), `## Content` falls back to a heuristic
paragraph-formatted blockquote of the raw transcript and a `## Transcript` section is appended
for verification. Once the key is set and `polish_post.py` is re-run, the next render replaces
both with the briefing.

For **non-Reel posts** (Image, Sidecar, etc.), `## Content` is replaced by `## Caption` which
holds the post's caption verbatim — non-Reels carry their substance in the caption itself, not
in spoken audio, so polish doesn't apply. Reels intentionally omit `## Caption` because the
polished `## Content` already covers the substance and the caption is usually redundant
scaffolding (hooks, hashtags, CTA).

## Frontmatter (Obsidian Properties)

**Overview note:**
| Property | Value | Purpose |
|---|---|---|
| `title` | `"@<username> overview"` | Obsidian title slot |
| `description` | first 125 chars of bio | hover preview / Bases card |
| `status` | `draft` | manual workflow |
| `username` | string | filter / Dataview |
| `followers`, `following`, `posts_total` | int as string with `.` thousand separator | Dataview-queryable via `number(prop.replace(".",""))` |
| `verified`, `private` | bool | filterable profile metrics |
| `scraped_at`, `source` | provenance | never changes |
| `created`, `modified` | scrape timestamp `YYYY-MM-DD HH:MM` | Obsidian housekeeping; `created` is preserved across re-renders |
| `tags` | `[Instagram, Overview]` | vault filtering |

**Per-post note:**
| Property | Value |
|---|---|
| `title` | derived from caption (engagement-bait stripped, max 80 chars) |
| `description` | LLM-polished `description_polished` (≤120 chars) or heuristic fallback |
| `status` | `draft` |
| `username`, `post_date`, `shortcode`, `scraped_at`, `source` | identifiers + provenance |
| `created`, `modified` | `created` preserved across re-renders |
| `tags` | `[Instagram, <2–3 LLM content tags>]` (e.g. `Instagram, GPT-5-5, Codex, OpenAI`) |

`likes`, `comments`, `video_views`, `post_type`, `has_transcript`, `transcript_model` are
**not** in YAML — the visible byline already shows likes/comments/views, the type is encoded
in the byline's "Reel"/"Post" label, and the transcript section in the body documents whether
transcription happened. Removing them keeps the Properties panel scannable.

Content tags come from `polish_post.py` (the `tags` field of its JSON output) and are
sanitized for Obsidian-safe format (`GPT-5.5` → `GPT-5-5` since Obsidian truncates tags at
`.`). Without polish, only the `Instagram` base tag is set.

**Batch summary:**
Same shape as overview, tag set `[Instagram, Summary]`.

The renderer **overwrites files in full** — manual edits to frontmatter or body are lost on
re-render. `created` is the only field preserved across re-renders. Copy to a different
filename if you want to maintain content manually.

## Polishing (`polish_post.py`)

For every Reel that has a `transcript` field, one Haiku call returns:
- `description` — third-person factual sentence (≤120 chars). Goes to YAML `description`.
- `content` — neutral Markdown briefing of the transcript's substance. Strips hooks,
  self-promo, calls-to-action; keeps every claim, number, name, step. Goes to `## Content`.
- `tags` — 2–3 Obsidian-safe content tags (PascalCase or kebab-case, no dots/spaces).
  Specific to the Reel's substance, not generic ("Instagram", "AI" are excluded by prompt).

The system prompt is cacheable (Anthropic 5-min TTL): it's billed once per profile, reused
per Reel. Idempotent: skips Reels with both `description_polished` and `content_polished`
already set unless `--no-skip-existing` is passed.

To re-polish only (after editing the prompt or fixing a mis-attribution):

```bash
"$PY" "$SKILL_DIR/scripts/polish_post.py" --input "<path-to-overview.json>" --no-skip-existing
"$PY" "$SKILL_DIR/scripts/render_report.py" --input "<path-to-overview.json>"
```

## Error handling

| # | Situation | Script behavior | Your behavior |
|---|---|---|---|
| 1 | `APIFY_API_TOKEN` missing | scrape exits 2 with setup snippet | Surface the snippet, ask user to add it, retry |
| 2 | Token rejected (401) | exit 3 | Tell user to verify the token at console.apify.com |
| 3 | Out of credit (402) | exit 3 | Top up; do not retry |
| 4 | 403 insufficient-permissions | exit 3 | Token Actor-ID misconfigured; see `references/apify-actor.md` |
| 5 | Profile not found / private | listed under `failed[]` | Continue with the rest; mention failures |
| 6 | `--transcribe` and `whisper-cli` not installed | transcribe step warns + skips | Tell user to `brew install whisper-cpp` and re-run |
| 7 | One Reel fails to download/transcribe | listed in `transcription_results[].failed` | Other Reels still get transcripts |
| 8 | `ANTHROPIC_API_KEY` missing | polish step prints setup hint, exits 0, pipeline continues | Tell user to set the key, then `polish_post.py --no-skip-existing` + re-render |
| 9 | One Reel fails to polish (rate limit, malformed JSON) | listed in polish output `failed[]`, others still polished | Re-run `polish_post.py` later; idempotent skip protects already-polished Reels |

## Pricing

| Item | Cost per profile |
|---|---|
| Apify scrape (12 posts) | ~$0.02 |
| Transcription (12 Reels × ~60s = 12 min, local whisper.cpp) | $0 |
| Polish (12 Reels × Haiku 4.5, system-prompt cached) | ~$0.06 |
| Essence (1 Haiku call per profile, locked at first scrape) | ~$0.001 |
| **Total per profile with `--transcribe` + polish + essence** | **~$0.08** |

Mention the cost ballpark **once** before the first scrape if the user hasn't acknowledged it.

## Going deeper

- `references/apify-actor.md` — Actor input/output schema, token-permission-Gotcha (slug vs hex
  ID), pricing details, error catalogue.
- `references/transcription.md` — whisper.cpp setup, model trade-offs, ffmpeg command rationale,
  performance benchmarks, idempotency rules.

## Out-of-scope (intentionally)

- Comments-only scraping (different `resultsType`).
- Hashtag, place, search-based scraping.
- Stories, Highlights, IGTV beyond what `latestPosts` returns.
- Anything requiring session cookies / login.
- Charting / trend visualizations.
- Scheduling regular re-scrapes (use `/schedule` skill).
- Multi-platform support — LinkedIn and X are planned as sibling skills (or future
  generalization), not part of this one yet.

If the user asks for one of these, say it isn't supported yet and ask whether to extend the
skill — that's a separate, scoped task.
