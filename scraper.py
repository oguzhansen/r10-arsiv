import re
import logging
from typing import Optional
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from config import Config

logger = logging.getLogger(__name__)

_BROWSER_IMPERSONATE = "chrome131"
_FALLBACK_HEADERS = {
    "User-Agent": Config.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.r10.net/",
}

_TOPIC_ID_RE = re.compile(r"/(\d+)-[^/]+\.html$")


@dataclass
class CategoryInfo:
    forum_id: int
    name: str
    url: str
    parent_name: Optional[str] = None


@dataclass
class TopicInfo:
    topic_id: int
    title: str
    url: str


def _fetch_page(url: str) -> Optional[BeautifulSoup]:
    """Fetch HTML; curl_cffi bypasses R10 Cloudflare bot check."""
    text = _fetch_html(url)
    if not text:
        return None
    return BeautifulSoup(text, "lxml")


def _fetch_html(url: str) -> Optional[str]:
    try:
        resp = curl_requests.get(
            url,
            impersonate=_BROWSER_IMPERSONATE,
            timeout=25,
            headers=_FALLBACK_HEADERS,
        )
        if resp.status_code == 200:
            text = resp.content.decode("windows-1254", errors="replace")
            if "Just a moment" not in text:
                return text
        logger.warning(
            "curl_cffi HTTP %s or challenge page for %s", resp.status_code, url
        )
    except Exception as exc:
        logger.warning("curl_cffi failed for %s: %s", url, exc)

    try:
        resp = requests.get(url, headers=_FALLBACK_HEADERS, timeout=15)
        resp.encoding = "windows-1254"
        if resp.status_code == 200 and "Just a moment" not in resp.text:
            return resp.text
        logger.warning("requests HTTP %s for %s", resp.status_code, url)
    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)

    return None


def fetch_all_categories() -> list[CategoryInfo]:
    """Parse the R10 archive main page and return all forum categories."""
    soup = _fetch_page(Config.R10_ARCHIVE_URL)
    if not soup:
        return []

    categories: list[CategoryInfo] = []
    content = soup.find("div", id="content")
    if not content:
        logger.warning("Could not find #content on archive page")
        return categories

    top_level_items = content.find("ul", recursive=False)
    if not top_level_items:
        return categories

    for top_li in top_level_items.find_all("li", recursive=False):
        top_a = top_li.find("a", recursive=False)
        if not top_a:
            continue

        parent_name = top_a.get_text(strip=True)

        sub_ul = top_li.find("ul", recursive=False)
        if not sub_ul:
            continue

        _parse_category_tree(sub_ul, parent_name, categories)

    return categories


def _parse_category_tree(
    ul_tag, parent_name: str, result: list[CategoryInfo]
):
    """Recursively parse nested <ul> category trees."""
    for li in ul_tag.find_all("li", recursive=False):
        a_tag = li.find("a", recursive=False)
        if not a_tag:
            continue

        href = a_tag.get("href", "")
        name = a_tag.get_text(strip=True)

        forum_id = _extract_forum_id(href)
        if forum_id is not None:
            url = href.replace("/archive/index.php/", "/archive/")
            result.append(
                CategoryInfo(
                    forum_id=forum_id,
                    name=name,
                    url=url,
                    parent_name=parent_name,
                )
            )

        child_ul = li.find("ul", recursive=False)
        if child_ul:
            _parse_category_tree(child_ul, parent_name, result)


def _extract_forum_id(href: str) -> Optional[int]:
    """Extract forum ID from URLs like /archive/index.php/f-132.html"""
    m = re.search(r"f-(\d+)\.html", href)
    return int(m.group(1)) if m else None


def fetch_topics(forum_id: int, page: int = 1) -> list[TopicInfo]:
    """Fetch topic list from a category archive page. Page 1 = newest."""
    if page == 1:
        url = f"{Config.R10_ARCHIVE_URL}f-{forum_id}.html"
    else:
        url = f"{Config.R10_ARCHIVE_URL}f-{forum_id}-p-{page}.html"

    soup = _fetch_page(url)
    if not soup:
        return []

    topics: list[TopicInfo] = []
    content = soup.find("div", id="content")
    if not content:
        return topics

    for ol in content.find_all("ol"):
        for li in ol.find_all("li"):
            a_tag = li.find("a")
            if not a_tag:
                continue

            href = a_tag.get("href", "")
            title = a_tag.get_text(strip=True)
            topic_id = _extract_topic_id(href)

            if topic_id is not None:
                topics.append(
                    TopicInfo(topic_id=topic_id, title=title, url=href)
                )

    return topics


def _extract_topic_id(href: str) -> Optional[int]:
    """Extract numeric topic ID from URL like /adsense-odemeleri/4787721-...html"""
    m = _TOPIC_ID_RE.search(href)
    return int(m.group(1)) if m else None


def turkish_lower(text: str) -> str:
    """Case-fold a string with Turkish I/i rules."""
    text = text.replace("\u0130", "i")  # İ -> i
    text = text.replace("I", "\u0131")  # I -> ı
    return text.lower()


def matches_keyword(title: str, keyword: str) -> bool:
    """Check if a keyword appears in a topic title (Turkish-aware, case-insensitive)."""
    return turkish_lower(keyword) in turkish_lower(title)


