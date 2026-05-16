# Reference: Apify `apify/instagram-scraper`

Quick reference for the Apify Instagram Scraper actor used by this skill.
Read this when you need to extend the skill (other result types, edge cases, pricing math).

## Actor identity

| Field | Value |
|---|---|
| Owner / username | `apify` |
| Slug | `instagram-scraper` |
| URL form (use in API paths) | `apify~instagram-scraper` |
| Internal hex ID (use in token resource-permissions) | `shu8hvrXbJbY3Eb9W` |
| Public actor URL | <https://apify.com/apify/instagram-scraper> |

**Important when creating Apify API tokens:** The "Resource-specific permissions → Actor ID" field requires the **17-character hex ID** (`shu8hvrXbJbY3Eb9W`), NOT the slug `apify/instagram-scraper` or the URL form `apify~instagram-scraper`. Apify silently accepts the wrong format but the token then can't see the actor (404 on metadata, 403 on run-sync). If the slug-form keeps failing, fall back to **Account-level Actors: Run + Read** which works regardless of which actor.

## Endpoint

| # | Mode | URL | Use when |
|---|---|---|---|
| 1 | Run-sync (returns dataset items) | `POST https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items?token=<TOKEN>` | ≤ 10 profiles, expected runtime < 5 min. **This is what `scrape_profile.py` uses.** |
| 2 | Async run | `POST https://api.apify.com/v2/acts/apify~instagram-scraper/runs?token=<TOKEN>` → poll → `GET /datasets/<id>/items` | Larger batches, custom polling, webhooks |

## Input fields

Body of the run-sync call. All fields optional unless noted.

| # | Field | Type | Notes |
|---|---|---|---|
| 1 | `directUrls` | `string[]` | Profile / post / hashtag / location URLs. **Skill default.** |
| 2 | `username` | `string[]` | Alternative to `directUrls` for profile lookup |
| 3 | `resultsType` | `string` | `posts` \| `details` \| `comments` \| `stories` \| `highlights`. Skill uses `details`. |
| 4 | `resultsLimit` | `number` | **Quirk:** in `resultsType: details` mode this parameter is ignored — the actor returns its default ~12 latest posts regardless. To control post count, switch to `resultsType: posts` (then it cleanly limits) or trim in `render_report.py`. |
| 5 | `addParentData` | `boolean` | If true, post items embed parent profile metadata. Skill keeps `false` to avoid duplication. |
| 6 | `searchType` | `string` | `hashtag` \| `place` \| `user`. Used with `search` parameter. Not used by current skill. |
| 7 | `searchLimit` | `number` | Max search hits. Not used by current skill. |
| 8 | `proxy` | `object` | `{ "useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"] }` if blocked. |

## Output fields (resultsType: details)

One item per profile. Key fields the renderer relies on:

| # | Field | Type | Notes |
|---|---|---|---|
| 1 | `username` | `string` | Lowercased handle |
| 2 | `fullName` | `string` | Display name |
| 3 | `biography` | `string` | Bio text (may contain newlines) |
| 4 | `followersCount` | `number` | |
| 5 | `followsCount` | `number` | |
| 6 | `postsCount` | `number` | Total posts on profile |
| 7 | `verified` | `boolean` | Blue checkmark |
| 8 | `private` | `boolean` | Locked accounts return profile metadata only |
| 9 | `isBusinessAccount` | `boolean` | |
| 10 | `businessCategoryName` | `string` | e.g. "Media", "Public figure" |
| 11 | `externalUrl` | `string` | Bio link |
| 12 | `profilePicUrl` / `profilePicUrlHD` | `string` | |
| 13 | `url` | `string` | Canonical profile URL |
| 14 | `latestPosts` | `object[]` | Up to `resultsLimit` recent posts |
| 15 | `error` | `string` | Present when scrape failed for this URL — script treats as failure |

### Post object (inside `latestPosts[]`)

| # | Field | Type | Notes |
|---|---|---|---|
| 1 | `url` | `string` | `https://www.instagram.com/p/<shortcode>/` |
| 2 | `type` | `string` | `Image` \| `Video` \| `Carousel` |
| 3 | `caption` | `string` | Full text |
| 4 | `likesCount` | `number` | |
| 5 | `commentsCount` | `number` | |
| 6 | `timestamp` | `ISO 8601 string` | `2026-04-22T09:14:00.000Z` |
| 7 | `displayUrl` | `string` | Thumbnail / first frame |
| 8 | `hashtags` | `string[]` | Extracted from caption |
| 9 | `mentions` | `string[]` | Handles mentioned in caption |

## Pricing

Pay-per-result, billed at the end of each run.

| # | Result type | Price |
|---|---|---|
| 1 | Profile detail | $1.50 / 1.000 results |
| 2 | Post | $1.50 / 1.000 results |
| 3 | Comment | $2.30 / 1.000 results |

**Math for the default skill call** (`resultsType: details`, `resultsLimit: 12`):
- 1 profile = 1 detail result + 12 post results = 13 results ≈ **$0.0195 per profile**.
- 50 profiles ≈ **$0.98**.

Source of truth: <https://apify.com/apify/instagram-scraper>.

## Common errors

| # | Symptom | Likely cause | Fix |
|---|---|---|---|
| 1 | HTTP 401 | Invalid token | Re-check `APIFY_API_TOKEN`, regenerate at console.apify.com |
| 2 | HTTP 402 | No credit | Top up at console.apify.com/billing |
| 3 | Empty array response | Profile private + locked, deleted, or rate-limited | Try again with proxy group `RESIDENTIAL`, or skip |
| 4 | Item with `error` field | Single profile failed (typo, banned, etc.) | Skill logs to `failed[]`, continues with others |
| 5 | Run-sync timeout (≥ 5 min) | Too many URLs in one call | Switch to async run + dataset poll |

## Future-proofing notes

- Stories/Highlights need a different `resultsType` and **session login cookies** for non-public accounts — skill currently does not support this.
- Instagram occasionally rotates field names (`commentsCount` ↔ `comments_count`). The actor normalizes them, but if the renderer ever shows `—` for known-engaged posts, check the raw JSON for renamed fields.
- Apify version pinning: the actor at `apify~instagram-scraper` resolves to the latest stable build. To pin, use the build hash in the URL path.
