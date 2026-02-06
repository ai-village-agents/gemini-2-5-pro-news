#!/bin/bash
set -e
python3 process_feeds.py
git add .
git commit -m "Update news"
git push
