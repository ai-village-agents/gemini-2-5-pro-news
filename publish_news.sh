#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rm -rf "${SCRIPT_DIR}/gemini-2-5-pro-news/stories"

python3 "${SCRIPT_DIR}/process_feeds.py"

git -C "${SCRIPT_DIR}/gemini-2-5-pro-news" add .

if git -C "${SCRIPT_DIR}/gemini-2-5-pro-news" diff --cached --quiet; then
    echo "No changes to commit."
else
    git -C "${SCRIPT_DIR}/gemini-2-5-pro-news" commit -m 'Automated news update'
fi

git -C "${SCRIPT_DIR}/gemini-2-5-pro-news" push origin main
