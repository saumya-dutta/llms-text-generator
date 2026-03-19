# llms.txt Generator

Crawl any website and generate an [llms.txt](https://llmstxt.org) file — a structured, LLM-readable index of a site's most important pages.

## Running Locally

### Backend

Requires Python 3.11+.

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

API runs at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Frontend

Requires Node 18+.

```bash
cd frontend
npm install
npm run dev
```

UI runs at `http://localhost:5173`. The Vite dev server proxies `/generate` to the backend automatically — no configuration needed.

## Tech Spec

<!-- Add Google Doc link here -->
