"""
crawler.py — async website crawler that extracts metadata into PageNode objects.

Flow:
  1. Fetch robots.txt + sitemap.xml in parallel
  2. Phase 1: fetch homepage + all sitemap URLs (up to MAX_SITEMAP_PAGES)
  3. Lightweight hub scoring between phases
  4. Phase 2: fetch outbound links discovered from hub pages only (up to MAX_PHASE2_PAGES)
  5. Post-process to compute homepage/nav/inlink signals and derive page sections

Section assignment strategy (in priority order):
  1. Legal path override  → always "Legal" (formatter places these under ## Optional)
  2. Homepage path        → "Overview"
  3. Longest-prefix match against homepage nav labels  → site's own taxonomy
  4. First URL path segment, title-cased               → path-segment clustering fallback
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 10
REQUEST_TIMEOUT = 15.0
DEFAULT_MAX_PAGES = 500

# Phase 1: fetch homepage + sitemap URLs (hard cap to avoid sitemap explosion)
MAX_SITEMAP_PAGES = 500
# Phase 2: outbound links discovered from hub pages only
MAX_PHASE2_PAGES = 200

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LLMsTxtGenerator/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Minimal override: legal pages always map to "Legal" so the formatter can place
# them under ## Optional regardless of how the site's nav labels them.
_LEGAL_PATH_RE = re.compile(
    r"/legal|/privacy|/terms|/cookies|/tos|/gdpr|/eula|/disclaimer",
    re.IGNORECASE,
)


@dataclass
class PageNode:
    url: str
    path: str

    title: str = ""
    meta_description: str = ""
    canonical_url: str = ""
    h1: str = ""
    headings: List[str] = field(default_factory=list)
    main_text: str = ""
    word_count: int = 0

    # Section derived from the site's own nav labels + URL path segments.
    # Set during the post-processing pass after all pages are collected.
    section: str = ""

    in_sitemap: bool = False
    allowed_by_robots: bool = True
    discovered_from: List[str] = field(default_factory=list)

    # ok / failed / non_html / blocked
    fetch_status: str = "ok"
    linked_from_homepage: bool = False
    nav_link_count: int = 0
    internal_inlink_count: int = 0

    # filled by ranker
    score: float = 0.0
    score_reasons: List[str] = field(default_factory=list)
    is_relevant: bool = False
    excluded_reason: Optional[str] = None


def normalize_url(url: str) -> str:
    """Lowercase scheme/host, strip query string, fragment, and trailing slash."""
    try:
        p = urlparse(url)
        path = p.path.rstrip("/") or "/"
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))
    except Exception:
        return url


def _derive_section(path: str, nav_labels: Dict[str, str]) -> str:
    """
    Assign a section name for a page.

    Priority:
      1. Legal path match  → "Legal" (hardcoded so formatter's ## Optional works)
      2. Root path         → "Overview"
      3. Longest nav-URL prefix match → site's own label (e.g. "Blog", "Docs")
      4. First path segment, de-slugified → e.g. "api-reference" → "Api Reference"
    """
    if _LEGAL_PATH_RE.search(path):
        return "Legal"

    if path in ("/", ""):
        return "Overview"

    # Longest-prefix match against homepage nav labels.
    # e.g. nav has /blog/ → "Blog"; page /blog/2022/post → matches with len 5.
    clean = path.rstrip("/")
    best_label = ""
    best_len = 0
    for nav_url, label in nav_labels.items():
        nav_path = urlparse(nav_url).path.rstrip("/")
        if not nav_path or nav_path == "/":
            continue
        if clean == nav_path or clean.startswith(nav_path + "/"):
            if len(nav_path) > best_len:
                best_label = label
                best_len = len(nav_path)

    if best_label:
        return best_label

    # Fallback: first path segment, slugs converted to title case.
    segments = [s for s in path.split("/") if s]
    if segments:
        return " ".join(w.capitalize() for w in re.split(r"[-_]", segments[0]))

    return "Overview"


def _is_hub(node: PageNode, homepage_links: Set[str]) -> bool:
    """
    Return True if a page is a hub worth expanding in Phase 2.

    Hub criteria (any one suffices):
      - Short URL path (≤ 2 non-empty segments): /blog/, /docs/guides/
      - Directly linked from the homepage
      - Appears in multiple pages' navs
    """
    n_segments = len([s for s in node.path.split("/") if s])
    return (
        n_segments <= 2
        or node.url in homepage_links
        or node.nav_link_count > 0
    )


# ---------------------------------------------------------------------------
# Robots / Sitemap helpers
# ---------------------------------------------------------------------------

async def _fetch_robots(base_url: str, client: httpx.AsyncClient) -> RobotFileParser:
    p = urlparse(base_url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        resp = await client.get(robots_url)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
    except Exception as exc:
        logger.debug("robots.txt unavailable: %s", exc)
    return rp


async def _fetch_sitemap_urls(base_url: str, client: httpx.AsyncClient) -> Set[str]:
    p = urlparse(base_url)
    root_sitemap = f"{p.scheme}://{p.netloc}/sitemap.xml"
    found: Set[str] = set()
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    async def _parse(url: str, depth: int = 0) -> None:
        if depth > 2:
            return
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return
            root = ET.fromstring(r.content)
            sub_sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
            if sub_sitemaps:
                await asyncio.gather(
                    *[_parse(s.text.strip(), depth + 1) for s in sub_sitemaps[:10]],
                    return_exceptions=True,
                )
            for loc in root.findall(".//sm:url/sm:loc", ns):
                found.add(normalize_url(loc.text.strip()))
        except Exception as exc:
            logger.debug("Sitemap parse failed for %s: %s", url, exc)

    await _parse(root_sitemap)
    logger.info("Sitemap: %d URLs found", len(found))
    return found


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

def _extract(
    url: str, html: str, base_domain: str
) -> Tuple[dict, List[str], Dict[str, str]]:
    """
    Parse HTML and return:
      - metadata dict
      - deduplicated internal links (for discovery + inlink counts)
      - nav links: {normalized_url: anchor_text}
          Used for two purposes:
            (a) nav_link_count scoring signal (how many pages' navs link here)
            (b) homepage nav labels for section taxonomy derivation
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    def _text(selector, **kw) -> str:
        el = soup.find(selector, **kw)
        return el.get_text(strip=True) if el else ""

    title = _text("title")
    h1 = _text("h1")

    meta_desc = ""
    m = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if m:
        meta_desc = m.get("content", "").strip()

    canonical = ""
    c = soup.find("link", attrs={"rel": "canonical"})
    if c:
        canonical = c.get("href", "").strip()

    headings = [
        h.get_text(strip=True)
        for h in soup.find_all(["h2", "h3"])
        if h.get_text(strip=True)
    ]

    body = soup.find("main") or soup.find("article") or soup.body
    raw_text = (body or soup).get_text(separator=" ", strip=True)
    main_text = re.sub(r"\s+", " ", raw_text).strip()[:8000]
    word_count = len(main_text.split())

    # Nav links with anchor text — first occurrence per URL wins.
    # Collected from <nav> and <header> elements only.
    nav_links: Dict[str, str] = {}
    for nav_el in soup.find_all(["nav", "header"]):
        for a in nav_el.find_all("a", href=True):
            href = urljoin(url, a["href"])
            ph = urlparse(href)
            if ph.netloc == base_domain:
                norm = normalize_url(href)
                text = a.get_text(strip=True)
                if text and norm not in nav_links:
                    nav_links[norm] = text

    # All internal links (deduplicated, order-preserved)
    seen: Set[str] = set()
    internal_links: List[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        ph = urlparse(href)
        if ph.netloc == base_domain and ph.scheme in ("http", "https"):
            norm = normalize_url(href)
            if norm not in seen:
                seen.add(norm)
                internal_links.append(norm)

    meta = {
        "title": title,
        "meta_description": meta_desc,
        "canonical_url": canonical,
        "h1": h1,
        "headings": headings[:20],
        "main_text": main_text,
        "word_count": word_count,
    }
    return meta, internal_links, nav_links


# ---------------------------------------------------------------------------
# Batch fetcher — fetches a fixed set of URLs concurrently (no BFS)
# ---------------------------------------------------------------------------

async def _fetch_batch(
    urls: List[str],
    sitemap_urls: Set[str],
    base_domain: str,
    robots: RobotFileParser,
    client: httpx.AsyncClient,
    source_label: str = "",
) -> Tuple[Dict[str, PageNode], Dict[str, List[str]], Dict[str, Dict[str, str]]]:
    """
    Fetch a fixed list of URLs concurrently.

    Returns:
      pages_by_url        — {url: PageNode}
      outbound_per_page   — {url: [internal_link, ...]}
      nav_links_per_page  — {url: {nav_url: anchor_text}}
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def fetch_one(url: str) -> Tuple[str, PageNode, List[str], Dict[str, str]]:
        path = urlparse(url).path or "/"
        node = PageNode(
            url=url,
            path=path,
            in_sitemap=(url in sitemap_urls),
            discovered_from=[source_label] if source_label else [],
        )

        if not robots.can_fetch("*", url):
            node.allowed_by_robots = False
            node.fetch_status = "blocked"
            return url, node, [], {}

        async with semaphore:
            try:
                resp = await client.get(url)
                ct = resp.headers.get("content-type", "")
                if "text/html" not in ct:
                    node.fetch_status = "non_html"
                    return url, node, [], {}
                if resp.status_code != 200:
                    node.fetch_status = "failed"
                    return url, node, [], {}

                meta, internal_links, nav_links = _extract(url, resp.text, base_domain)
                node.title = meta["title"]
                node.meta_description = meta["meta_description"]
                node.canonical_url = meta["canonical_url"]
                node.h1 = meta["h1"]
                node.headings = meta["headings"]
                node.main_text = meta["main_text"]
                node.word_count = meta["word_count"]
                node.fetch_status = "ok"
                return url, node, internal_links, nav_links

            except httpx.RequestError as exc:
                logger.warning("Request error %s: %s", url, exc)
                node.fetch_status = "failed"
                return url, node, [], {}

    results = await asyncio.gather(
        *[fetch_one(u) for u in urls], return_exceptions=True
    )

    pages_by_url: Dict[str, PageNode] = {}
    outbound_per_page: Dict[str, List[str]] = {}
    nav_links_per_page: Dict[str, Dict[str, str]] = {}

    for result in results:
        if isinstance(result, Exception):
            logger.warning("Unhandled exception in batch: %s", result)
            continue
        url, node, links, nav_links = result
        pages_by_url[url] = node
        outbound_per_page[url] = links
        nav_links_per_page[url] = nav_links

    return pages_by_url, outbound_per_page, nav_links_per_page


# ---------------------------------------------------------------------------
# Main crawl entry point
# ---------------------------------------------------------------------------

async def crawl(
    start_url: str, max_pages: int = DEFAULT_MAX_PAGES
) -> Dict[str, PageNode]:
    """
    Two-phase crawl of start_url.

    Phase 1: fetch homepage + sitemap URLs (up to MAX_SITEMAP_PAGES).
    Phase 2: fetch outbound links discovered from hub pages only
             (up to MAX_PHASE2_PAGES additional pages).

    Returns pages_by_url: Dict[normalized_url, PageNode].
    """
    parsed = urlparse(start_url)
    base_domain = parsed.netloc
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    norm_start = normalize_url(start_url)

    limits = httpx.Limits(
        max_connections=MAX_CONCURRENT,
        max_keepalive_connections=MAX_CONCURRENT,
    )
    async with httpx.AsyncClient(
        headers=_HEADERS,
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
        limits=limits,
    ) as client:
        robots, sitemap_urls = await asyncio.gather(
            _fetch_robots(base_url, client),
            _fetch_sitemap_urls(base_url, client),
        )

        # ----------------------------------------------------------------
        # Phase 1: homepage + sitemap URLs, capped at MAX_SITEMAP_PAGES
        # ----------------------------------------------------------------
        phase1_urls: List[str] = [norm_start]
        seen_urls: Set[str] = {norm_start}

        # Sort sitemap URLs by path depth (ascending) so shallow pages
        # (/blog, /pricing) are fetched before deep ones (/blog/2024/01/post).
        sorted_sitemap = sorted(
            sitemap_urls,
            key=lambda u: len([s for s in urlparse(u).path.split("/") if s]),
        )
        for su in sorted_sitemap:
            if len(phase1_urls) >= MAX_SITEMAP_PAGES:
                break
            pu = urlparse(su)
            if pu.netloc == base_domain and pu.scheme in ("http", "https"):
                if su not in seen_urls:
                    seen_urls.add(su)
                    phase1_urls.append(su)

        logger.info("Phase 1: fetching %d URLs", len(phase1_urls))
        p1_pages, p1_outbound, p1_nav = await _fetch_batch(
            phase1_urls, sitemap_urls, base_domain, robots, client, source_label="sitemap"
        )

        # Aggregate link signals from Phase 1
        homepage_links: Set[str] = set(p1_outbound.get(norm_start, []))
        homepage_nav_labels: Dict[str, str] = p1_nav.get(norm_start, {})
        nav_link_counts: Dict[str, int] = {}
        inlink_counts: Dict[str, int] = {}

        for url, links in p1_outbound.items():
            for link in links:
                inlink_counts[link] = inlink_counts.get(link, 0) + 1
        for url, nav_links in p1_nav.items():
            for nav_url in nav_links:
                nav_link_counts[nav_url] = nav_link_counts.get(nav_url, 0) + 1

        # Apply preliminary nav_link_count so _is_hub() can use it
        for url, node in p1_pages.items():
            node.nav_link_count = nav_link_counts.get(url, 0)

        # ----------------------------------------------------------------
        # Hub detection: which Phase 1 pages are worth expanding?
        # ----------------------------------------------------------------
        phase2_candidates: Set[str] = set()
        for url, node in p1_pages.items():
            if node.fetch_status != "ok":
                continue
            if _is_hub(node, homepage_links):
                for link in p1_outbound.get(url, []):
                    if link not in seen_urls:
                        phase2_candidates.add(link)

        # ----------------------------------------------------------------
        # Phase 2: outbound links from hubs, capped at MAX_PHASE2_PAGES
        # ----------------------------------------------------------------
        phase2_urls: List[str] = []
        for link in phase2_candidates:
            if len(phase2_urls) >= MAX_PHASE2_PAGES:
                break
            pu = urlparse(link)
            if pu.netloc == base_domain and pu.scheme in ("http", "https"):
                phase2_urls.append(link)
                seen_urls.add(link)

        logger.info("Phase 2: fetching %d URLs from hub outbound links", len(phase2_urls))
        p2_pages, p2_outbound, p2_nav = await _fetch_batch(
            phase2_urls, sitemap_urls, base_domain, robots, client, source_label="hub_outbound"
        )

        # Accumulate Phase 2 link signals
        for url, links in p2_outbound.items():
            for link in links:
                inlink_counts[link] = inlink_counts.get(link, 0) + 1
        for url, nav_links in p2_nav.items():
            for nav_url in nav_links:
                nav_link_counts[nav_url] = nav_link_counts.get(nav_url, 0) + 1

        # ----------------------------------------------------------------
        # Merge results, respecting max_pages
        # ----------------------------------------------------------------
        pages_by_url: Dict[str, PageNode] = {}
        for url, node in {**p1_pages, **p2_pages}.items():
            if len(pages_by_url) >= max_pages:
                break
            pages_by_url[url] = node

        # ----------------------------------------------------------------
        # Post-processing: apply aggregated signals and derive sections
        # ----------------------------------------------------------------
        for url, node in pages_by_url.items():
            if url in homepage_links:
                node.linked_from_homepage = True
            node.nav_link_count = nav_link_counts.get(url, 0)
            node.internal_inlink_count = inlink_counts.get(url, 0)
            node.section = _derive_section(node.path, homepage_nav_labels)
            # Single-segment paths are top-level hub pages — always "Key Pages"
            # regardless of whether the nav is JS-rendered or not.
            n_segments = len([s for s in node.path.split("/") if s])
            if n_segments == 1:
                node.section = "Key Pages"

        logger.info("Crawl complete: %d pages collected", len(pages_by_url))
        return pages_by_url
