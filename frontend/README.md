# CivicRoute frontend

Standalone React + TypeScript + Vite + TanStack Router frontend, prepared to connect to the CivicRoute FastAPI backend.

## Requirements
- Node.js 20 or newer recommended
- npm
- CivicRoute backend running at `http://127.0.0.1:8000`

## Run it
Open a terminal in this `frontend` folder:

```bash
npm install
copy .env.example .env.local
npm run dev
```

On PowerShell, `copy` works. On macOS/Linux use `cp .env.example .env.local`.

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

## Connect to backend
The frontend sends:
- `POST http://127.0.0.1:8000/ask`
- `GET http://127.0.0.1:8000/health` is useful for checking the backend separately.

Set `VITE_API_BASE_URL` in `.env.local` if your backend runs at a different address. Restart Vite after changing environment variables.

The backend CORS configuration must allow `http://localhost:5173` and `http://127.0.0.1:5173`.

## Nearby centres map

The “Find Nearby Centres” button loads Google Maps only after it is selected. The map currently has no centre markers because the backend does not yet provide an approved centres endpoint or verified centre data.

Set `VITE_GOOGLE_MAPS_API_KEY` in the local `.env.local` file to a browser key for the Maps JavaScript API. This Vite-prefixed variable is included in browser code, so it is public by design: restrict it in Google Cloud to the frontend HTTP referrers you use (including the local Vite origin and production domain) and to only the APIs this frontend needs. Do not use a backend Gemini key here. `.env.local` is ignored by Git; commit only the blank placeholder in `.env.example`.

The map uses a neutral world view and displays no fabricated centre locations or markers. Connect verified centre data only after the maps/integration owner approves the endpoint and response contract.

## TanStack route generation
The TanStack Router Vite plugin generates `src/routeTree.gen.ts` when Vite starts. The route file is `src/routes/index.tsx`.

## Voice input
The Speak button uses the browser's Web Speech API when available. Voice support varies by browser and generally works best in Chrome over localhost or HTTPS.

## Notes
- Do not hard-code API keys in frontend source. Use only restricted browser keys through ignored local environment configuration.
- Do not enter Aadhaar numbers, passwords or other sensitive information into the request form.
- The frontend can display official sources and warnings returned by the backend, but cannot itself verify government requirements.
