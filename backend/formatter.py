"""
formatter.py — render pages_by_section into an llms.txt file.

Spec (from llmstxt.org):

  # Title                          ← required H1
  > Optional description           ← blockquote summary
  Optional prose paragraphs
  ## Section name                  ← H2 per content category
  - [Link title](url): description ← file list entries
  ## Optional                      ← secondary / skippable content
  - [Link title](url)

"""

import re
from typing import Dict, List, Optional

from crawler import PageNode


def _excerpt(text: str, max_chars: int = 500) -> str:
    text = " ".join((text or "").split()).strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].strip()
    return (cut + "...") if cut else text[:max_chars] + "..."


def _clean_title(title: str, site_title: str = "") -> str:
    """
    strip the site-name suffixes from page titles so that:
      'Balance Settings | Stripe API Reference'  → 'Balance Settings'
      'Receive payouts | Stripe Documentation'   → 'Receive payouts'
      'Testing'                                  → 'Testing'

    """
    if site_title:
        for sep in (" | ", " - ", " — "):
            suffix = sep + site_title
            if title.endswith(suffix):
                return title[: -len(suffix)].strip()

    for sep in (" | ", " — "):
        if sep in title:
            return title.rsplit(sep, 1)[0].strip()

    if " - " in title:
        parts = title.rsplit(" - ", 1)
        suffix = parts[1].strip()
        if len(suffix.split()) <= 4 and re.match(r"^[A-Z][^a-z0-9]{0,2}", suffix):
            return parts[0].strip()

    return title

# sections that always appear first, in this order
_PRIORITY_SECTIONS = ["Overview", "Key Pages"]

# sections that go under ## Optional regardless of site type
_OPTIONAL_SECTIONS = {"Legal"}


def format_llms_txt(
    pages_by_section: Dict[str, List[PageNode]],
    site_title: str = "",
    site_description: str = "",
    *,
    homepage_main_text: str = "",
    rss_feeds: List[str] | None = None,
    sitemap_url: str = "",
) -> str:
    """
    Convert grouped, ranked pages into an llms.txt-formatted string.

    Args:
        pages_by_section:   output of ranker.rank()
        site_title:         used for the H1 header
        site_description:   used for the blockquote summary (meta_description preferred)
        homepage_main_text: fallback if site_description is empty
    """
    lines: List[str] = []

    # h1
    lines.append(f"# {site_title or 'Website'}")
    lines.append("")

    # description
    description = site_description or _excerpt(homepage_main_text, max_chars=500)
    if description:
        lines.append(f"> {description}")
        lines.append("")

    # priorirty sections first (Overview, Key Pages), then remaining sections
    # by average page score descending, with Optional sections always last.
    def _avg_score(section: str) -> float:
        pages = pages_by_section[section]
        return sum(p.score for p in pages) / len(pages) if pages else 0.0

    def _rank(section: str) -> tuple:
        if section in _PRIORITY_SECTIONS:
            return (0, _PRIORITY_SECTIONS.index(section), 0.0)
        if section in _OPTIONAL_SECTIONS:
            return (2, 0, 0.0)
        return (1, 0, -_avg_score(section))  # negative so higher avg sorts first

    all_sections = sorted(pages_by_section.keys(), key=_rank)
    main_sections = [s for s in all_sections if s not in _OPTIONAL_SECTIONS]
    optional_sections = [s for s in all_sections if s in _OPTIONAL_SECTIONS]

    # render a section block
    def _render_section(name: str, pages: List[PageNode], include_desc: bool = True) -> None:
        if not pages:
            return
        lines.append(f"## {name}")
        lines.append("")
        for node in pages:
            raw_label = node.title or node.h1 or node.path
            label = _clean_title(raw_label, site_title)
            url = node.canonical_url or node.url
            desc = node.meta_description or (
                _excerpt(node.main_text, max_chars=160) if node.main_text else ""
            )
            if include_desc and desc:
                lines.append(f"- [{label}]({url}): {desc}")
            else:
                lines.append(f"- [{label}]({url})")
        lines.append("")

    # main sections with descriptions
    for section in main_sections:
        _render_section(section, pages_by_section[section], include_desc=True)

    # optional section with no descriptions
    if optional_sections:
        optional_pages: List[PageNode] = []
        for section in optional_sections:
            optional_pages.extend(pages_by_section[section])

        if optional_pages:
            lines.append("## Optional")
            lines.append("")
            for node in optional_pages:
                raw_label = node.title or node.h1 or node.path
                label = _clean_title(raw_label, site_title)
                url = node.canonical_url or node.url
                lines.append(f"- [{label}]({url})")
            lines.append("")

    # matadata (sitemap and rss feeds)
    if sitemap_url:
        lines.append("## Sitemap")
        lines.append("")
        lines.append(sitemap_url)
        lines.append("")

    if rss_feeds:
        feeds = [f for f in rss_feeds if f]
        if feeds:
            lines.append("## RSS/Atom feed")
            lines.append("")
            for f in feeds[:5]:
                lines.append(f"- {f}")
            lines.append("")

    return "\n".join(lines)
