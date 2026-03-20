"""
ranker.py — score, filter, deduplicate, and group PageNodes into sections.

Input:  pages_by_url: Dict[str, PageNode]   (from crawler)
Output: pages_by_section: Dict[str, List[PageNode]]  (sorted by score desc)

"""

import logging
from typing import Dict, List, Tuple

from crawler import PageNode, normalize_url, _EXCLUDED_PATH_RE

logger = logging.getLogger(__name__)

# hard coded limits
# pages per section
_MAX_SECTION_PAGES = 20

# repetitive page
_MAX_REPETITIVE_PAGES = 4

def _compute_score(node: PageNode) -> Tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []

    def add(pts: float, label: str) -> None:
        nonlocal score
        score += pts
        sign = "+" if pts >= 0 else ""
        reasons.append(f"{sign}{pts:.0f} {label}")

    # --- links in sitemap / robots ---
    if node.in_sitemap:
        add(2, "in_sitemap")
    if node.allowed_by_robots:
        add(1, "allowed_by_robots")

    # --- metadata completeness ---
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

    # --- word count scoring ---
    wc = node.word_count
    if wc > 500:
        add(2, "word_count>500")
    elif wc > 200:
        add(1, "word_count>200")
    if wc < 100:
        add(-2, "word_count<100")
    if wc < 50:
        add(-2, "word_count<50")   # stacks with above → total -4

    # --- link signals ---
    if node.linked_from_homepage:
        add(3, "linked_from_homepage")
    if node.nav_link_count > 0:
        add(3, "in_nav")
    if node.internal_inlink_count > 5:
        add(2, "high_inlinks")

    # --- url depth (number of non-empty path segments) ---
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

def _should_exclude(node: PageNode) -> bool:
    """
    excluded if:
    1. failed fetches
    2. blocked by robots.txt
    3. excluded path patterns from _EXCLUDED_PATH_RE
    4. thin content (less than 30 words and no title or h1)
    """
    return bool(
        node.fetch_status != "ok" or
        not node.allowed_by_robots or
        _EXCLUDED_PATH_RE.search(node.path) or
        (node.word_count < 30 and not node.title and not node.h1)
    )


def _canonical_key(node: PageNode, url: str) -> str:
    """ return canonical URL if it looks absolute, else fall back to the page URL."""
    c = node.canonical_url
    if c and c.startswith(("http://", "https://")):
        return normalize_url(c)
    return normalize_url(url)


def _trim_section(pages: List[PageNode]) -> None:
    """
    trim the sorted section list in place.

    1. Repetitive cluster check: if ≥3 pages share the same meta_description,
       the section is a template/inventory list (e.g. arena tags, movie stubs).
       Cap tightly at _MAX_REPETITIVE_PAGES.
    2. Score gap: stop at the first page that scores < 50% of the section top.
    3. Hard cap at _MAX_SECTION_PAGES regardless.
    """
    if len(pages) <= 1:
        return

    # Repetitive cluster detection — identical descriptions are the clearest signal
    # that pages are generated from a template rather than written individually.
    if len(pages) >= 3:
        desc_counts: Dict[str, int] = {}
        for n in pages:
            if n.meta_description:
                desc_counts[n.meta_description] = desc_counts.get(n.meta_description, 0) + 1
        if desc_counts and max(desc_counts.values()) >= 3:
            top_desc, top_count = max(desc_counts.items(), key=lambda x: x[1])
            section_name = pages[0].section
            logger.info(
                "Repetitive cluster in '%s': %d/%d pages share description %r — trimming to %d",
                section_name, top_count, len(pages), top_desc[:60], _MAX_REPETITIVE_PAGES,
            )
            del pages[_MAX_REPETITIVE_PAGES:]
            return

    top = pages[0].score
    for i in range(1, min(len(pages), _MAX_SECTION_PAGES)):
        if top > 0 and pages[i].score < top * 0.15:
            del pages[i:]
            return
    del pages[_MAX_SECTION_PAGES:]


def rank(pages_by_url: Dict[str, PageNode]) -> Dict[str, List[PageNode]]:
    """
    1. Score every page.
    2. Exclude pages that should never appear in llms.txt.
    3. Deduplicate by canonical URL (keep highest-scoring representative).
    4. Group survivors by section (derived by crawler from nav + path segments).
    5. Sort each section by score desc, then trim to the most valuable pages.
    """
    # score all pages (even excluded ones, for debugging)
    for node in pages_by_url.values():
        node.score, node.score_reasons = _compute_score(node)

    # filter and deduplicate
    canonical_map: Dict[str, PageNode] = {}
    for url, node in pages_by_url.items():
        if _should_exclude(node):
            node.excluded = True
            continue

        key = _canonical_key(node, url)
        if key in canonical_map:
            existing = canonical_map[key]
            if node.score > existing.score:
                existing.excluded = True
                canonical_map[key] = node
            else:
                node.excluded = True
        else:
            canonical_map[key] = node

    # group into sections using the crawler-derived section names
    pages_by_section: Dict[str, List[PageNode]] = {}
    for node in canonical_map.values():
        node.is_relevant = True
        section = node.section or "Other"
        pages_by_section.setdefault(section, []).append(node)

    # sort each section by score descending, then trim to best pages
    for pages in pages_by_section.values():
        pages.sort(key=lambda n: n.score, reverse=True)
        _trim_section(pages)

    return pages_by_section
