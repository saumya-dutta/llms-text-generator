"""
main.py — FastAPI entry point.

Endpoints:
  POST /generate   { "url": "https://example.com" }
                   → returns llms.txt as a downloadable text file

  GET  /health     → { "status": "ok" }
"""

import logging
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, HttpUrl

from crawler import crawl
from ranker import rank
from formatter import format_llms_txt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="llms.txt Generator",
    description="Crawl any website and generate an llms.txt file per the llmstxt.org spec.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


class GenerateRequest(BaseModel):
    url: HttpUrl


@app.post("/generate", summary="Generate llms.txt for a website")
async def generate_llms_txt(request: GenerateRequest) -> Response:
    """
    Crawl the given URL, rank the pages, and return a formatted llms.txt file.
    """
    url = str(request.url)
    logger.info("Received /generate request for: %s", url)

    try:
        # --- Step 1: Crawl ---
        pages_by_url = await crawl(url)

        ok_pages = [n for n in pages_by_url.values() if n.fetch_status == "ok"]
        if not ok_pages:
            raise HTTPException(
                status_code=422,
                detail=(
                    "422:Could not crawl any pages from the provided URL. "
                    "The site may be blocking crawlers or the URL may be unreachable."
                ),
            )

        # --- Step 2: Rank + group ---
        pages_by_section = rank(pages_by_url)

        if not pages_by_section:
            raise HTTPException(
                status_code=422,
                detail="No relevant pages found after filtering. The site may have very thin content.",
            )

        # --- Step 3: Derive site title / description from the homepage node ---
        parsed = urlparse(url)
        site_title = parsed.netloc
        site_description = ""
        homepage_main_text = ""
        rss_feeds = []
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

        for page_url, node in pages_by_url.items():
            if node.path in ("/", "") and node.fetch_status == "ok":
                site_title = node.title or node.h1 or parsed.netloc
                site_description = node.meta_description or ""
                homepage_main_text = node.main_text or ""
                rss_feeds = list(node.rss_feeds or [])
                break

        # --- Step 4: Format ---
        content = format_llms_txt(
            pages_by_section,
            site_title,
            site_description,
            homepage_main_text=homepage_main_text,
            rss_feeds=rss_feeds,
            sitemap_url=sitemap_url,
        )

        logger.info(
            "Generated llms.txt for %s — %d sections, %d relevant pages",
            url,
            len(pages_by_section),
            sum(len(p) for p in pages_by_section.values()),
        )

        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="llms.txt"',
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Unexpected error for %s: %s", url, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal error while generating llms.txt: {exc}",
        )


@app.get("/health", summary="Health check")
async def health() -> dict:
    return {"status": "ok"}
