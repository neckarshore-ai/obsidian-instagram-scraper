"""Environment-token loaders shared by all social scrapers."""

from __future__ import annotations

import os
import sys


APIFY_SETUP_HINT = """\
ERROR: APIFY_API_TOKEN environment variable is not set.

Setup:
  1. Get your token at https://console.apify.com/account/integrations
  2. Add to ~/.zshrc:  export APIFY_API_TOKEN='apify_api_...'
  3. Reload your shell:  source ~/.zshrc
"""


X_API_SETUP_HINT = """\
ERROR: X_API_BEARER_TOKEN environment variable is not set.

Setup:
  1. Open your X Developer App at https://developer.x.com/en/portal/projects-and-apps
     (or https://console.x.com)
  2. Go to your app -> Keys & Tokens -> App-Only Authentication -> Bearer Token
     (Generate or Regenerate if you do not have one yet)
  3. Add to ~/.zshrc:  export X_API_BEARER_TOKEN='AAAA...'
  4. Reload your shell:  source ~/.zshrc

Note: this is the Bearer Token from the official X API v2 (developer.x.com).
It is NOT an xAI / Grok key (api.x.ai) and NOT an Apify token.
"""


ANTHROPIC_SETUP_HINT = """\
INFO: ANTHROPIC_API_KEY not set — skipping LLM-driven step.
Setup:
  1. Get a key at https://console.anthropic.com/settings/keys
  2. Add to ~/.zshrc:  export ANTHROPIC_API_KEY='sk-ant-...'
  3. Reload your shell:  source ~/.zshrc
"""


def get_apify_token() -> str:
    """Read APIFY_API_TOKEN from env. On miss, print setup hint to stderr and exit 2."""
    token = os.environ.get("APIFY_API_TOKEN", "").strip()
    if not token:
        sys.stderr.write(APIFY_SETUP_HINT)
        sys.exit(2)
    return token


def get_x_api_token() -> str:
    """Read X_API_BEARER_TOKEN from env. On miss, print setup hint to stderr and exit 2.

    Used by the x-scraper skill for the official X API v2 (developer.x.com).
    Distinct from APIFY_API_TOKEN (Apify, used by the IG/LinkedIn scrapers) and from any
    xAI / Grok key (api.x.ai), which can NOT read tweets.
    """
    token = os.environ.get("X_API_BEARER_TOKEN", "").strip()
    if not token:
        sys.stderr.write(X_API_SETUP_HINT)
        sys.exit(2)
    return token


def get_anthropic_key() -> str | None:
    """Read ANTHROPIC_API_KEY from env. Returns None if missing (caller decides to no-op)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key or None


def print_anthropic_setup_hint() -> None:
    sys.stderr.write(ANTHROPIC_SETUP_HINT)
