# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

**Backend** (from `backend/`):
```bash
pip install -r requirements.txt
uvicorn main:app --reload
# Runs on http://localhost:8000
```

**Frontend** (from `frontend/`):
```bash
npm install
npm run dev
# Runs on http://localhost:5173 — Vite proxies /generate → http://localhost:8000
```

Interactive API docs: `http://localhost:8000/docs`

## Repository structure

```
backend/      Python/FastAPI — crawler, ranker, formatter, API entry point
frontend/     React/Vite — single-page UI (src/App.jsx + src/App.css)
```

Root-level `.py` files are stale copies; the canonical source is in `backend/`.

## Architecture

Three-stage pipeline triggered by `POST /generate`:

```
URL input → crawler.py → ranker.py → formatter.py → llms.txt string
```

**`backend/crawler.py`** — two-phase async crawler (httpx + BeautifulSoup). Produces `Dict[str, PageNode]`.
- Fetches `robots.txt` and `sitemap.xml` in parallel before crawling.
- **Phase 1**: fetches homepage + all sitemap URLs, capped at `MAX_SITEMAP_PAGES = 500`.
- **Hub detection** between phases: a page is a hub if it has ≤ 2 path segments, is linked from the homepage, or appears in any page's nav. Hub outbound links seed Phase 2.
- **Phase 2**: fetches outbound links discovered from hub pages only, capped at `MAX_PHASE2_PAGES = 200`. Avoids crawling deep leaf pages or faceted-nav spam.
- `_fetch_batch()` is the shared helper for both phases — fetches a fixed URL list concurrently (no BFS queue), returns `(pages_by_url, outbound_per_page, nav_links_per_page)`.
- `PageNode` is the shared data structure across all three modules — carries raw crawl data and the scoring/section fields written later by the ranker.
- Link signals (homepage links, nav links, inlink counts) are accumulated during both phases and applied to nodes in a post-processing pass.
- **Section assignment** (`node.section`) is derived in the post-processing pass via `_derive_section()`: legal path override → "Legal"; homepage → "Overview"; longest-prefix match against homepage `<nav>` anchor text → site's own label; first URL path segment title-cased as fallback.
- `_extract()` returns `nav_links: Dict[str, str]` (url → anchor text). The homepage's nav labels become the site's section taxonomy.

**`backend/ranker.py`** — pure Python, no I/O. Produces `Dict[str, List[PageNode]]` (`pages_by_section`).
- Scores with additive heuristics (sitemap membership, metadata completeness, word count, link signals, URL depth).
- Excludes non-HTML, blocked, thin, and utility pages (login/cart/search/tag paths via `_EXCLUDED_PATH_RE`).
- Deduplicates by canonical URL — highest-scoring duplicate wins.
- Groups survivors by `node.section` (set by crawler — no hardcoded section map in ranker).
- `_trim_section()` cuts each section at the first large score gap (< 50% of top score), with a hard cap of 20 pages.

**`backend/formatter.py`** — pure Python, no I/O. Produces the final `str`.
- Renders sections in a fixed display order (`_SECTION_ORDER`); legal pages go under `## Optional` per the llmstxt.org spec.

**`backend/main.py`** — FastAPI wiring only. CORS allows `localhost:5173` and `localhost:3000`. Derives `site_title`/`site_description` from the homepage node (path `/`), then calls the three modules in sequence.

**`frontend/src/App.jsx`** — single component; all state managed with `useState`. Vite dev server proxies `/generate` to the backend so no hardcoded port in the fetch call.

## Key data flow detail

`PageNode` fields are written by two different modules:
- Crawl fields (`title`, `meta_description`, `fetch_status`, `word_count`, `section`, etc.) → set in `crawler.py`
- Ranking fields (`score`, `score_reasons`, `is_relevant`, `excluded_reason`) → set in `ranker.py`

**Common change locations:**
- Scoring weights → `_compute_score()` in `ranker.py`
- Section detection logic → `_derive_section()` in `crawler.py` (nav-prefix matching + path segment fallback)
- Crawl phase caps → `MAX_SITEMAP_PAGES`, `MAX_PHASE2_PAGES` in `crawler.py`
- Hub detection criteria → `_is_hub()` in `crawler.py`
- Section display order → `_SECTION_ORDER` in `formatter.py`
- Allowed CORS origins → `app.add_middleware(CORSMiddleware, ...)` in `main.py`
