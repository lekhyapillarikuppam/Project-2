# Frontend (Readaloud)

React + Vite single-page app for the audiobook generator (SPEC §11). Plain CSS, `react-router-dom`
for `/`, `/book/:jobId`, `/sample`.

| Command | What it does |
|---|---|
| `npm run dev` | Dev server on http://localhost:5173 against the local API (`.env.development`, port 8000) |
| `npm run dev:remote` | Dev server against the Azure API (`.env.remote`); the API's CORS allows port 5173 |
| `npm test` | Vitest + Testing Library |
| `npm run build` | Production build into `dist/` (`.env.production`) |
| `npm run lint` | oxlint |

The sample audiobook in `public/sample/` is static (D22); regenerate it with
`cd backend; python scripts\make_sample.py`.
