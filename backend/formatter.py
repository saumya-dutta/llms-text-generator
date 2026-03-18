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
from typing import Dict, List

from crawler import PageNode


def _clean_title(title: str, site_title: str = "") -> str:
    """
    Strip site-name suffixes from page titles so that:
      'Balance Settings | Stripe API Reference'  → 'Balance Settings'
      'Receive payouts | Stripe Documentation'   → 'Receive payouts'
      'Testing'                                  → 'Testing'

    Strategy: strip the last ` | …`, ` - …`, or ` — …` segment.
    Also strips the site_title itself if it appears as a suffix.
    """
    # Strip known site_title suffix first (exact match after separator)
    if site_title:
        for sep in (" | ", " - ", " — "):
            suffix = sep + site_title
            if title.endswith(suffix):
                return title[: -len(suffix)].strip()

    # Fallback: strip everything after the last separator
    for sep in (" | ", " — "):
        if sep in title:
            return title.rsplit(sep, 1)[0].strip()

    # For " - " be conservative: only strip if what follows looks like a brand
    # (short, title-case, no digits) to avoid stripping from real hyphenated titles.
    if " - " in title:
        parts = title.rsplit(" - ", 1)
        suffix = parts[1].strip()
        if len(suffix.split()) <= 4 and re.match(r"^[A-Z][^a-z0-9]{0,2}", suffix):
            return parts[0].strip()

    return title

# Preferred display order for sections
_SECTION_ORDER: List[str] = [
    "Overview",
    "Key Pages",
    "Documentation",
    "API Reference",
    "Blog",
    "Changelog",
    "Community",
    "Pricing",
    "Resources",
    "Legal",
]

# Sections that go under the special "## Optional" heading
_OPTIONAL_SECTIONS = {"Legal"}


def format_llms_txt(
    pages_by_section: Dict[str, List[PageNode]],
    site_title: str = "",
    site_description: str = "",
) -> str:
    """
    Convert grouped, ranked pages into an llms.txt-formatted string.

    Args:
        pages_by_section: output of ranker.rank()
        site_title:       used for the H1 header
        site_description: used for the blockquote summary
    """
    lines: List[str] = []

    # --- H1 (required) ---
    lines.append(f"# {site_title or 'Website'}")
    lines.append("")

    # --- Blockquote description (optional) ---
    if site_description:
        lines.append(f"> {site_description}")
        lines.append("")

    # --- Sort sections ---
    def _rank(section: str) -> int:
        try:
            return _SECTION_ORDER.index(section)
        except ValueError:
            return len(_SECTION_ORDER)

    all_sections = sorted(pages_by_section.keys(), key=_rank)
    main_sections = [s for s in all_sections if s not in _OPTIONAL_SECTIONS]
    optional_sections = [s for s in all_sections if s in _OPTIONAL_SECTIONS]

    # --- Render a section block ---
    def _render_section(name: str, pages: List[PageNode], include_desc: bool = True) -> None:
        if not pages:
            return
        lines.append(f"## {name}")
        lines.append("")
        for node in pages:
            raw_label = node.title or node.h1 or node.path
            label = _clean_title(raw_label, site_title)
            url = node.canonical_url or node.url
            if include_desc and node.meta_description:
                lines.append(f"- [{label}]({url}): {node.meta_description}")
            else:
                lines.append(f"- [{label}]({url})")
        lines.append("")

    # --- Main sections (with descriptions) ---
    for section in main_sections:
        _render_section(section, pages_by_section[section], include_desc=True)

    # --- Optional section (secondary content, no descriptions needed) ---
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

    return "\n".join(lines)
