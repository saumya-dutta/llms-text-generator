# llms.txt Generator

Crawl any website and generate an [llms.txt](https://llmstxt.org) file — a structured, LLM-readable index of a site's most important pages. Check it out here! https://llms-text-generator-one.vercel.app/ 

Backend hosted on: https://believable-wonder-production.up.railway.app/ 

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

for presentation reference: [Initial Tech Spec](https://docs.google.com/document/d/1d0cQQoS81y_-g-1tm5WPVeCB4KEwmCsiGVGw0pzCBJI/edit?usp=sharing) 

## Screenshots / Demo Video

Video: https://vimeo.com/1175540149 

<img width="2864" height="1254" alt="image" src="https://github.com/user-attachments/assets/b86a3fe6-8b8c-432d-9197-ba6c13346062" />
