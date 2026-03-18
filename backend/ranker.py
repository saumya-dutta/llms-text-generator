"""
ranker.py — score, filter, deduplicate, and group PageNodes into sections.

Input:  pages_by_url: Dict[str, PageNode]   (from crawler)
Output: pages_by_section: Dict[str, List[PageNode]]  (sorted by score desc)

Section names come from the crawler (nav-derived or path-segment fallback).
No hardcoded section taxonomy here.
"""

import re
from typing import Dict, List, Tuple

from crawler import PageNode, normalize_url


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXCLUDED_PATH_RE = re.compile(
    r"/login|/signin|/signup|/register"
    r"|/account(?:/|$)|/cart(?:/|$)|/checkout(?:/|$)"
    r"|/search(?:/|$|\?)"
    r"|/tags?(?:/|$)|/archive(?:/|$)|/categories?(?:/|$)"
    r"|/author(?:/|$)"
    r"|\.(?:pdf|zip|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|eot|css|js)$",
    re.IGNORECASE,
)

# Absolute ceiling on pages per section.
# _trim_section may cut earlier based on score gaps.
_MAX_SECTION_PAGES = 20


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_score(node: PageNode) -> Tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []

    def add(pts: float, label: str) -> None:
        nonlocal score
        score += pts
        sign = "+" if pts >= 0 else ""
        reasons.append(f"{sign}{pts:.0f} {label}")

    # --- Sitemap / robots ---
    if node.in_sitemap:
        add(2, "in_sitemap")
    if node.allowed_by_robots:
        add(1, "allowed_by_robots")

    # --- Metadata completeness ---
    if node.title:
        add(1, "has_title")
    if node.meta_description:
        add(1, "has_meta_description")
    if node.canonical_url:
        add(1, "has_canonical")
    if node.h1:
        add(1, "has_h1")
    if len(node.headings) >= 2:
        add(1, "has_multiple_headings")

    # --- Word count ---
    wc = node.word_count
    if wc > 500:
        add(2, "word_count>500")
    elif wc > 200:
        add(1, "word_count>200")
    if wc < 100:
        add(-2, "word_count<100")
    if wc < 50:
        add(-2, "word_count<50")   # stacks with above → total -4

    # --- Link signals ---
    if node.linked_from_homepage:
        add(3, "linked_from_homepage")
    if node.nav_link_count > 0:
        add(3, "in_nav")
    if node.internal_inlink_count > 5:
        add(2, "high_inlinks")

    # --- URL depth (number of non-empty path segments) ---
    n_segments = len([s for s in node.path.split("/") if s])
    if n_segments == 0:
        add(1, "homepage")
    elif n_segments == 1:
        add(3, "section_hub")   # /blog/, /about/, /docs/ — high-value hub pages
    elif n_segments <= 3:
        add(1, "moderate_depth")
    elif n_segments > 5:
        add(-1, "deep_url")

    return score, reasons


# ---------------------------------------------------------------------------
# Exclusion
# ---------------------------------------------------------------------------

def _should_exclude(node: PageNode) -> Tuple[bool, str]:
    if node.fetch_status != "ok":
        return True, f"fetch_status={node.fetch_status}"
    if not node.allowed_by_robots:
        return True, "blocked_by_robots"
    if _EXCLUDED_PATH_RE.search(node.path):
        return True, "excluded_path_pattern"
    if node.word_count < 30 and not node.title and not node.h1:
        return True, "thin_content"
    return False, ""


def _canonical_key(node: PageNode, url: str) -> str:
    """Return canonical URL if it looks absolute, else fall back to the page URL."""
    c = node.canonical_url
    if c and c.startswith(("http://", "https://")):
        return normalize_url(c)
    return normalize_url(url)


def _trim_section(pages: List[PageNode]) -> None:
    """
    Trim the sorted section list in place.
    Stops at the first large score gap (next page scores < 50% of section top)
    and caps at _MAX_SECTION_PAGES regardless.
    """
    if len(pages) <= 1:
        return
    top = pages[0].score
    for i in range(1, min(len(pages), _MAX_SECTION_PAGES)):
        if top > 0 and pages[i].score < top * 0.5:
            del pages[i:]
            return
    del pages[_MAX_SECTION_PAGES:]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rank(pages_by_url: Dict[str, PageNode]) -> Dict[str, List[PageNode]]:
    """
    1. Score every page.
    2. Exclude pages that should never appear in llms.txt.
    3. Deduplicate by canonical URL (keep highest-scoring representative).
    4. Group survivors by section (derived by crawler from nav + path segments).
    5. Sort each section by score desc, then trim to the most valuable pages.
    """
    # Score all pages (even excluded ones, for debugging)
    for node in pages_by_url.values():
        node.score, node.score_reasons = _compute_score(node)

    # Filter and deduplicate
    canonical_map: Dict[str, PageNode] = {}
    for url, node in pages_by_url.items():
        excluded, reason = _should_exclude(node)
        if excluded:
            node.excluded_reason = reason
            continue

        key = _canonical_key(node, url)
        if key in canonical_map:
            existing = canonical_map[key]
            if node.score > existing.score:
                existing.excluded_reason = "duplicate_canonical_loser"
                canonical_map[key] = node
            else:
                node.excluded_reason = "duplicate_canonical_loser"
        else:
            canonical_map[key] = node

    # Group into sections using the crawler-derived section names
    pages_by_section: Dict[str, List[PageNode]] = {}
    for node in canonical_map.values():
        node.is_relevant = True
        section = node.section or "Other"
        pages_by_section.setdefault(section, []).append(node)

    # Sort each section by score descending, then trim to best pages
    for pages in pages_by_section.values():
        pages.sort(key=lambda n: n.score, reverse=True)
        _trim_section(pages)

    return pages_by_section
