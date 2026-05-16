"""Shared utilities for social-media scraper skills (instagram-scraper, linkedin-scraper,
x-scraper). Pure functions only — platform-specific logic stays in each skill.

Public API — each scraper script may import either via the package root:

    from _social_common import fmt_int, get_apify_token, extract_json_object

…or via the explicit submodule path (preferred when a script touches one area only):

    from _social_common.render_helpers import fmt_int
    from _social_common.tokens import get_apify_token
    from _social_common.llm_helpers import extract_json_object

Both forms are stable. `__all__` below is the canonical list.
"""

from _social_common.cleanup import cleanup_old_post_files
from _social_common.folder_rename import rename_folder_with_essence
from _social_common.llm_helpers import extract_json_object
from _social_common.render_helpers import (
    content_preview,
    fmt_int,
    fmt_int_yaml_str,
    md_escape_pipe,
    sanitize_tag,
    slugify_for_filename,
    truncate_at_word,
    url_encode_link,
    yaml_quote,
)
from _social_common.timestamps import (
    derive_scrape_timestamp,
    fmt_batch_log_ts,
    fmt_date_iso,
    fmt_property_ts,
    read_existing_created,
    resolve_timestamps,
)
from _social_common.tokens import (
    get_anthropic_key,
    get_apify_token,
    print_anthropic_setup_hint,
)

__all__ = [
    "cleanup_old_post_files",
    "content_preview",
    "derive_scrape_timestamp",
    "extract_json_object",
    "fmt_batch_log_ts",
    "fmt_date_iso",
    "fmt_int",
    "fmt_int_yaml_str",
    "fmt_property_ts",
    "get_anthropic_key",
    "get_apify_token",
    "md_escape_pipe",
    "print_anthropic_setup_hint",
    "read_existing_created",
    "rename_folder_with_essence",
    "resolve_timestamps",
    "sanitize_tag",
    "slugify_for_filename",
    "truncate_at_word",
    "url_encode_link",
    "yaml_quote",
]
