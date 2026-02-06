#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FEEDS_FILE = pathlib.Path(__file__).with_name("rss_feeds.txt")
DEFAULT_OUTPUT_DIR_NAME = "gemini-2-5-pro-news"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
)
BLACKLISTED_DOMAINS = {"fool.com", "lendingtree.com"}


def _is_blacklisted(url: str) -> bool:
    return any(domain in url for domain in BLACKLISTED_DOMAINS)


def iter_feed_urls(path: pathlib.Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                url = raw_line.strip()
                if not url or url.startswith("#"):
                    continue
                if _is_blacklisted(url):
                    continue
                yield url
    except FileNotFoundError:
        print(f"Feed list not found: {path}", file=sys.stderr)
        sys.exit(1)


def fetch_feed(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP error {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach server: {exc.reason}") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_children(parent: ET.Element, name: str):
    for child in parent:
        if _local_name(child.tag) == name:
            yield child


def _first_text(element: ET.Element, candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        for child in _iter_children(element, candidate):
            if child.text:
                text = child.text.strip()
                if text:
                    return text
    return ""


def _find_link(element: ET.Element) -> str:
    # RSS-style direct link text
    link_text = _first_text(element, ("link",))
    if link_text:
        return link_text

    # Atom-style link elements with href attribute
    for link_elem in _iter_children(element, "link"):
        href = (link_elem.get("href") or "").strip()
        rel = link_elem.get("rel")
        if rel in (None, "alternate") and href:
            return href
        if href:
            link_text = link_text or href
        if link_elem.text:
            text = link_elem.text.strip()
            if text:
                return text

    # Fallbacks commonly used in some feeds
    guid = _first_text(element, ("guid", "id"))
    return guid


def parse_feed_entries(feed_xml: str) -> list[tuple[str, str]]:
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as exc:
        raise RuntimeError(f"Invalid XML: {exc}") from exc

    entries: list[tuple[str, str]] = []

    # RSS items
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        title = _first_text(item, ("title", "dc:title")) or "Untitled"
        link = _find_link(item) or "No link available"
        entries.append((title, link))

    if entries:
        return entries

    # Atom entries
    for entry in root.iter():
        if _local_name(entry.tag) != "entry":
            continue
        title = _first_text(entry, ("title",)) or "Untitled"
        link = _find_link(entry) or "No link available"
        entries.append((title, link))

    return entries


def _sanitize_title(title: str) -> str:
    ascii_title = title.encode("ascii", "ignore").decode("ascii")
    sanitized = re.sub(r"[^A-Za-z0-9]+", "-", ascii_title).strip("-")
    return sanitized or "story"


def _story_path(base: pathlib.Path, title: str) -> pathlib.Path:
    slug = _sanitize_title(title)
    candidate = base / f"{slug}.html"
    if not candidate.exists():
        return candidate
    suffix = 1
    while True:
        candidate = base / f"{slug}-{suffix}.html"
        if not candidate.exists():
            return candidate
        suffix += 1


def _write_story_html(path: pathlib.Path, title: str, link: str) -> None:
    html_title = html.escape(title)
    html_link = html.escape(link, quote=True)
    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html_title}</title>
</head>
<body>
<h1>{html_title}</h1>
<p><a href="{html_link}">Read original story</a></p>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch RSS/Atom feeds and generate local story pages."
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        help=(
            "Directory to write output into. Defaults to a 'gemini-2-5-pro-news' "
            "directory in the current working directory."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    output_dir = (
        pathlib.Path(args.output_dir).expanduser()
        if args.output_dir
        else pathlib.Path.cwd() / DEFAULT_OUTPUT_DIR_NAME
    )
    stories_dir = output_dir / "stories"

    output_dir.mkdir(parents=True, exist_ok=True)
    stories_dir.mkdir(parents=True, exist_ok=True)

    for url in iter_feed_urls(FEEDS_FILE):
        print(f"--- BEGIN FEED: {url} ---")
        entries: list[tuple[str, str]] = []
        fetch_failed = False
        parse_failed = False
        try:
            content = fetch_feed(url)
        except RuntimeError as exc:
            print(f"Error fetching {url}: {exc}", file=sys.stderr)
            fetch_failed = True
        else:
            try:
                entries = parse_feed_entries(content)
            except RuntimeError as exc:
                print(f"Error parsing {url}: {exc}", file=sys.stderr)
                parse_failed = True

        if not fetch_failed and not parse_failed:
            if not entries:
                print("No stories found in feed.")
            else:
                for title, link in entries:
                    if _is_blacklisted(link):
                        continue
                    print(f"Title: {title}")
                    print(f"Link: {link}\n")
                    story_path = _story_path(stories_dir, title)
                    _write_story_html(story_path, title, link)
        print(f"--- END FEED: {url} ---\n")
    generate_index_html(stories_dir, output_dir / "index.html")
    sys.exit(0)


def generate_index_html(
    stories_dir: pathlib.Path, output_path: pathlib.Path | None = None
) -> None:
    stories_dir.mkdir(parents=True, exist_ok=True)
    destination = output_path or pathlib.Path(__file__).with_name("index.html")
    story_files = sorted(
        story
        for story in stories_dir.glob("*.html")
        if story.name.lower() != "index.html"
    )

    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>Gemini 2.5 Pro News</title>",
        "</head>",
        "<body>",
        "<h1>Gemini 2.5 Pro News</h1>",
        "<ul>",
    ]

    for story in story_files:
        title_text = html.escape(story.stem.replace("_", " "))
        href = html.escape((pathlib.Path("stories") / story.name).as_posix(), quote=True)
        lines.append(f'<li><a href="{href}">{title_text}</a></li>')

    lines.extend(
        [
            "</ul>",
            "</body>",
            "</html>",
        ]
    )

    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
