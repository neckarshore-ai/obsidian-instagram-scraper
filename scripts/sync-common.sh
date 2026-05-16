#!/usr/bin/env bash
# sync-common.sh — pull the canonical _social_common package from
# neckarshore-ai/obsidian-social-scrapers-common@main and vendor it into
# skills/instagram-scraper/_social_common/.
#
# Run before each release. Re-vendors verbatim; manual edits to the vendored
# copy will be overwritten.
#
# Usage:
#   ./scripts/sync-common.sh
#
# Requirements: git, rsync.

set -euo pipefail

REPO_URL="${SOCIAL_COMMON_REPO_URL:-https://github.com/neckarshore-ai/obsidian-social-scrapers-common.git}"
REPO_REF="${SOCIAL_COMMON_REPO_REF:-main}"

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${PLUGIN_ROOT}/skills/instagram-scraper/_social_common"

TMP_DIR="$(mktemp -d -t obsidian-social-common-XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

echo "[sync-common] cloning ${REPO_URL}#${REPO_REF}..."
git clone --quiet --depth 1 --branch "${REPO_REF}" "${REPO_URL}" "${TMP_DIR}/common"

if [[ ! -d "${TMP_DIR}/common/_social_common" ]]; then
  echo "[sync-common] ERROR: source repo has no _social_common/ at root" >&2
  exit 1
fi

echo "[sync-common] vendoring into ${TARGET}..."
mkdir -p "${TARGET}"
rsync -a --delete \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  "${TMP_DIR}/common/_social_common/" \
  "${TARGET}/"

echo "[sync-common] running smoke test..."
python3 "${TARGET}/test_smoke.py"

SHA="$(cd "${TMP_DIR}/common" && git rev-parse HEAD)"
echo "[sync-common] done. Vendored from ${REPO_URL}@${SHA}."
echo "[sync-common] git diff --stat:"
cd "${PLUGIN_ROOT}"
git --no-pager diff --stat -- skills/instagram-scraper/_social_common/ || true
