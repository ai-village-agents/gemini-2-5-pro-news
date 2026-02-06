#!/usr/bin/env python3
"""Download RSS feeds and generate per-story HTML plus a linked index."""

from __future__ import annotations

import html
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

FEEDS_FILE = Path("rss_feeds.txt")
STORIES_DIR = Path("stories")
INDEX_FILE = Path("index.html")
STORY_FILENAME_MAX_LENGTH = 80
DOMAIN_BLACKLIST = {"fool.com"}


@dataclass
class Story:
    title: str
    link: Optional[str]
    description: Optional[str]
    published: Optional[str]
    feed_title: Optional[str]
    file_name: str


def read_feed_urls(feeds_file: Path) -> List[str]:
    if not feeds_file.exists():
        raise FileNotFoundError(f"Feed list not found: {feeds_file}")

    urls: List[str] = []
    with feeds_file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


def fetch_feed(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download feed {url!r}: {exc}") from exc


def strip_namespace(tag: str) -> str:
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    if ":" in tag:
        tag = tag.split(":", 1)[1]
    return tag


def find_first_child_text(element: ET.Element, candidate_names: Iterable[str]) -> Optional[str]:
    candidates = {name.lower() for name in candidate_names}
    for child in element:
        name = strip_namespace(child.tag).lower()
        if name in candidates:
            text = child.text or ""
            return text.strip()
    return None


def get_atom_link(element: ET.Element) -> Optional[str]:
    for child in element:
        name = strip_namespace(child.tag).lower()
        if name != "link":
            continue
        href = child.attrib.get("href")
        if href:
            rel = child.attrib.get("rel", "alternate")
            if rel == "alternate":
                return href
    return None


def parse_feed(xml_bytes: bytes) -> Tuple[Optional[str], List[Story]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"Feed XML parse error: {exc}") from exc

    root_name = strip_namespace(root.tag).lower()
    stories: List[Story] = []

    if root_name == "rss":
        channel = next((child for child in root if strip_namespace(child.tag).lower() == "channel"), root)
        feed_title = find_first_child_text(channel, ("title",))
        items = [child for child in channel if strip_namespace(child.tag).lower() == "item"]
        for item in items:
            title = find_first_child_text(item, ("title",)) or "Untitled Story"
            link = find_first_child_text(item, ("link", "guid"))
            description = find_first_child_text(item, ("description", "encoded", "summary", "content"))
            published = find_first_child_text(item, ("pubdate", "published", "updated"))
            stories.append(
                Story(
                    title=title,
                    link=link,
                    description=description,
                    published=published,
                    feed_title=feed_title,
                    file_name="",  # placeholder until we generate filenames
                )
            )
        return feed_title, stories

    if root_name == "feed":  # Atom
        feed_title = find_first_child_text(root, ("title",))
        entries = [child for child in root if strip_namespace(child.tag).lower() == "entry"]
        for entry in entries:
            title = find_first_child_text(entry, ("title",)) or "Untitled Story"
            link = get_atom_link(entry)
            description = find_first_child_text(entry, ("content", "summary"))
            published = find_first_child_text(entry, ("updated", "published"))
            stories.append(
                Story(
                    title=title,
                    link=link,
                    description=description,
                    published=published,
                    feed_title=feed_title,
                    file_name="",
                )
            )
        return feed_title, stories

    raise RuntimeError(f"Unsupported feed format: root element <{root_name}>")


def sanitize_filename(title: str, used_names: set[str]) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    if not slug:
        slug = "story"
    slug = slug[:STORY_FILENAME_MAX_LENGTH]

    candidate = slug
    counter = 1
    while candidate in used_names:
        counter += 1
        suffix = f"-{counter}"
        base = slug[: STORY_FILENAME_MAX_LENGTH - len(suffix)]
        candidate = f"{base}{suffix}"

    used_names.add(candidate)
    return f"{candidate}.html"


def is_blacklisted(link: Optional[str], blacklist: set[str]) -> bool:
    if not link:
        return False
    parsed = urllib.parse.urlparse(link)
    hostname = parsed.hostname
    if not hostname:
        return False
    hostname = hostname.lower()
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in blacklist
    )


def ensure_stories_dir(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)


def detect_description_html(text: Optional[str]) -> Tuple[str, bool]:
    if not text:
        return "", False
    stripped = text.strip()
    if "<" in stripped and ">" in stripped:
        return stripped, True
    return html.escape(stripped), False


def write_story_file(story: Story, story_path: Path) -> None:
    description_content, is_html = detect_description_html(story.description)
    description_block = description_content if is_html else f"<p>{description_content}</p>"
    link_block = ""
    if story.link:
        link_block = (
            f'<p><a href="{html.escape(story.link, quote=True)}" target="_blank" rel="noopener">'
            f"Read original</a></p>"
        )

    published_block = (
        f"<p><strong>Published:</strong> {html.escape(story.published)}</p>"
        if story.published
        else ""
    )

    feed_block = (
        f"<p><strong>Source feed:</strong> {html.escape(story.feed_title)}</p>"
        if story.feed_title
        else ""
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(story.title)}</title>
</head>
<body>
  <article>
    <h1>{html.escape(story.title)}</h1>
    {feed_block}
    {published_block}
    {link_block}
    <div class="story-content">
      {description_block}
    </div>
  </article>
</body>
</html>
"""
    story_path.write_text(html_content, encoding="utf-8")


def write_index(stories: List[Story], index_path: Path, cache_buster: str) -> None:
    items_html = []
    for story in stories:
        link_href = f"stories/{story.file_name}?v={cache_buster}"
        feed_info = f" — {html.escape(story.feed_title)}" if story.feed_title else ""
        items_html.append(
            f'    <li><a href="{link_href}">{html.escape(story.title)}</a>{feed_info}</li>'
        )
    items_block = "\n".join(items_html)
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>News Stories</title>
</head>
<body>
  <h1>News Stories</h1>
  <ul>
{items_block}
  </ul>
</body>
</html>
"""
    index_path.write_text(html_content, encoding="utf-8")


def main() -> int:
    try:
        feed_urls = read_feed_urls(FEEDS_FILE)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    ensure_stories_dir(STORIES_DIR)

    all_stories: List[Story] = []
    used_filenames: set[str] = set()

    for url in feed_urls:
        try:
            xml_bytes = fetch_feed(url)
            feed_title, stories = parse_feed(xml_bytes)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            continue

        for story in stories:
            story.feed_title = feed_title
            if is_blacklisted(story.link, DOMAIN_BLACKLIST):
                print(
                    f"Skipping story {story.title!r} from blacklisted domain {story.link!r}",
                    file=sys.stderr,
                )
                continue
            story.file_name = sanitize_filename(story.title, used_filenames)
            story_path = STORIES_DIR / story.file_name
            write_story_file(story, story_path)
            all_stories.append(story)

    if not all_stories:
        print("No stories were written.", file=sys.stderr)
        return 1

    cache_buster = str(int(time.time()))
    write_index(all_stories, INDEX_FILE, cache_buster)
    print(f"Wrote {len(all_stories)} stories and updated {INDEX_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
