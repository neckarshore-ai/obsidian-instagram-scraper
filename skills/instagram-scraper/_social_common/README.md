# `_social_common/` — shared utilities for social-scraper skills

Pure-function helpers that all three social-scraper skills (`instagram-scraper`,
`linkedin-scraper`, `x-scraper`) import from. Extracting these eliminates ~300 lines of
copy-pasted code per skill and ensures formatting fixes ripple to all platforms at once.

## Layout

| Module | Exports |
|---|---|
| `tokens.py` | `get_apify_token()` (exits 2 on miss), `get_anthropic_key()` (returns Optional), `print_anthropic_setup_hint()` |
| `llm_helpers.py` | `extract_json_object(text)` — defensive parser for LLM JSON output |
| `render_helpers.py` | `fmt_int`, `fmt_int_yaml_str`, `yaml_quote`, `md_escape_pipe`, `url_encode_link`, `content_preview`, `sanitize_tag`, `slugify_for_filename`, `truncate_at_word` |
| `timestamps.py` | `fmt_property_ts`, `fmt_batch_log_ts` (UTC + Berlin), `fmt_date_iso`, `derive_scrape_timestamp`, `read_existing_created`, `resolve_timestamps` |
| `folder_rename.py` | `rename_folder_with_essence(profile_dir, raw_path, handle)` |
| `cleanup.py` | `cleanup_old_post_files(profile_dir, keep_paths, overview_paths, scrape_source)` |

## Importing from a skill script

Each skill's scripts live at `~/.claude/skills/<skill>/scripts/*.py`. To import the shared
package, prepend `~/.claude/skills/` to `sys.path`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Form 1: import from the package root (cleaner when touching multiple modules)
from _social_common import (
    get_apify_token, get_anthropic_key,
    extract_json_object,
    fmt_int, yaml_quote, sanitize_tag,
    resolve_timestamps, derive_scrape_timestamp,
    rename_folder_with_essence,
    cleanup_old_post_files,
)

# Form 2: import from the explicit submodule (preferred when one script touches one area)
from _social_common.tokens import get_apify_token, get_anthropic_key
from _social_common.llm_helpers import extract_json_object
from _social_common.render_helpers import fmt_int, yaml_quote, sanitize_tag
from _social_common.timestamps import resolve_timestamps, derive_scrape_timestamp
from _social_common.folder_rename import rename_folder_with_essence
from _social_common.cleanup import cleanup_old_post_files
```

Both forms are stable. The package's `__init__.py` re-exports the full public surface; new
helpers must be added there too.

## Smoke tests

```bash
python3 ~/.claude/skills/_social_common/test_smoke.py
```

Stdlib-only assertions — no pytest dependency. Every pure helper has at least one happy-path
and one edge-case assertion. When adding a new helper, extend `test_smoke.py` first.

## What stays per-skill

- `polish_*.py`, `themes_profile.py`, `essence_profile.py` system prompts (platform-specific
  voice rules, content scaffolding to strip).
- `scrape_profile.py` Apify endpoint + payload (each actor has its own input/output schema).
- `render_report.py` body renderers, frontmatter blocks, and `compute_post_stems` (depends on
  per-platform field paths for date / id / title).
- `thread_detection.py` (X-only), `filter_relevance.py` (X-only), `transcribe_videos.py`
  (Instagram-only) — single-platform features.
- `BATCH_LOG_HEADER` and `append_run_to_batch_log` in each `scrape_profile.py` — each
  platform writes a different per-row format.

## Design rules

1. **Pure functions only** — no side effects beyond stderr in `tokens.py` and
   `folder_rename.py` (where it's documented).
2. **No platform names baked in** — `cleanup_old_post_files` takes `scrape_source` as a
   parameter; tokens hints don't mention IG / LinkedIn / X.
3. **Byte-identical output** — every helper here was extracted verbatim from the existing
   skills. Any behavior change must regress-test against the backed-up reference renders.
4. **No new dependencies** — stdlib only (`re`, `urllib.parse`, `datetime`, `json`, `pathlib`).
   Skills still install `requests` + `anthropic` for their own scripts.

## Versioning

This package is intentionally unversioned — it lives inside the user's local skills
directory and is consumed only by sibling skills in the same tree. There is no PyPI release
and no semver contract. The history below is the closest thing to a changelog.

| Version tag | Date | Highlight |
|---|---|---|
| v0 (pre-extraction) | up to 2026-04-26 | Each scraper script carried its own copy of `fmt_int`, `yaml_quote`, `extract_json_object`, etc. ~300 lines per skill duplicated 3×. |
| v1 (extraction)   | 2026-04-26 | First extraction with 6 modules + `__init__.py`. ~379 lines moved into `_social_common`. All 3 skills migrated and verified byte-identical against backups. |
| v1.1 (stabilization) | 2026-04-26 | Explicit `__all__` + package-root re-exports in `__init__.py`. `test_smoke.py` (~30 assertions). README expanded with versioning + anti-pattern section. Tech-debt cleanup: dead local `extract_json_object` defs and unused `import os` removed from 6 + 9 files. SCRAPE_SOURCE marker fixed in x-scraper. |

## Adding a helper

If you find code duplicated across 2+ skills, move it here. Rules:

1. Pure function or simple class.
2. Stdlib only.
3. Zero platform-specific knowledge (or take it as a parameter).
4. Add to README's Layout table + the relevant module.
5. Add to `__init__.py` re-exports + `__all__`.
6. Add a smoke-test assertion (happy + edge case).
7. Re-test all 3 skills' renders against backups (`/tmp/x-scraper-refactor-backup/`).

## Anti-patterns — what does NOT belong here

| Anti-pattern | Why it stays per-skill |
|---|---|
| **System prompts for `polish_*.py`** | Each platform has its own voice rules: LinkedIn strips engagement-bait hooks; Instagram strips Reel-caption tropes; X handles thread numbering. A shared prompt collapses these into a lowest-common-denominator that loses platform-specific scrubbing. |
| **`compute_post_stems` from `render_report.py`** | Field paths differ across actors: IG uses `post.timestamp`, LinkedIn uses `post.postedAt`, X has multiple fallbacks. Pulling this in would force a unified field-accessor abstraction that doesn't match any single actor's actual schema. |
| **Apify call + retry loop** | Each actor's input/output schema differs enough that a shared call layer would either be too generic (just a thin `requests` wrapper, providing no value) or too specific (knowing about all 3 schemas, defeating the abstraction). |
| **Engagement-score formula** | LinkedIn weights shares 3×, IG weights nothing extra, X weights retweets+quotes 2×. A shared scorer would force a config blob per platform — more code than just keeping the formula local. |
| **`BATCH_LOG_HEADER` template** | Per-platform YAML frontmatter (different `tags`, different `source`) and per-platform table columns. The structure isn't shared, only the idea of "append-only run log" is. |
| **Field accessors that touch one platform's schema** | E.g. X's `post_text(post)`, `post_id(post)`, `quoted_tweet(post)`. These walk through the actor's specific JSON shape. If 2+ skills end up with structurally identical accessors, that's the moment to revisit — not before. |
| **Helpers that depend on a `requests` / `anthropic` import** | `_social_common` is stdlib-only by rule 4. Anything that hits the network or calls an LLM lives in the per-skill scripts that already install those deps. |

The driving principle: **abstract on duplication you've already seen, not duplication you
imagine you'll see**. Three identical lines is fine; three near-identical lines with platform
quirks is the start of a debate, not an automatic extraction.
