CivicRoute — Karnataka Public Service Navigator

CivicRoute is an AI-assisted platform designed to help citizens discover Karnataka government services, understand application procedures, identify required documents and find relevant service centres.

The goal is to make government-service information easier to understand and access.

Features

- AI Service Assistant: Understands citizen questions and identifies relevant government services.
- Document Requirements: Provides required, conditional and supplementary document information.
- Application Guidance: Explains service procedures in simple language.
- Official Sources: Uses government sources to support service information.
- Hybrid Retrieval: Combines trusted catalogue information, online retrieval and cached results.
- Service Centre Discovery: Designed to help users locate relevant service centres.
- English and Kannada: Supports English and Kannada language preferences.

Technology Stack

Component| Technology
Backend| Python, FastAPI
Frontend| React, TypeScript, Vite
AI| Google Gemini API
Data validation| Pydantic
Information retrieval| Official-source retrieval
Testing| Pytest
Version control| Git and GitHub

Project Structure

CivicRoute/
│
├── backend/
│   ├── main.py
│   ├── pipeline.py
│   ├── schemas.py
│   ├── llm.py
│   ├── official_sources.py
│   ├── web_fetcher.py
│   ├── hybrid_retrieval.py
│   ├── document_cache.py
│   ├── data/
│   │   └── services.json
│   └── tests/
│
├── frontend/
│
├── requirements.txt
├── .env.example
└── README.md

Installation and Setup

1. Clone the repository

git clone <your-repository-url>
cd CivicRoute

2. Create a Python virtual environment

py -m venv .venv
.\.venv\Scripts\Activate.ps1

3. Install dependencies

python -m pip install -r requirements.txt

4. Configure environment variables

Create a ".env" file in the repository root.

GEMINI_API_KEY=
GEMINI_MODEL=
CORS_ORIGINS=http://localhost:5173
LOG_LEVEL=INFO

OFFICIAL_SOURCE_PROVIDER=
BRAVE_SEARCH_API_KEY=
OFFICIAL_SOURCE_APPROVED_HOSTS=

Add your API credentials only to ".env". Never commit API keys to GitHub.

Gemini and Brave retrieval are optional. Live search requires the relevant provider configuration and an approved government-domain allowlist.

5. Start the backend

From the repository root:

python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

Backend: http://127.0.0.1:8000

API documentation: http://127.0.0.1:8000/docs

Health check: http://127.0.0.1:8000/health

6. Start the frontend

Open another terminal:

cd frontend
npm install
npm run dev

Frontend: http://localhost:5173

API Usage

CivicRoute provides a REST API through FastAPI.

Ask a question

Endpoint: "POST /ask"

Example request:

{
  "question": "I need an income certificate",
  "language": "en"
}

Supported language values:

- "en" — English
- "kn" — Kannada

The API identifies the requested service and returns a structured response when trusted service information is available.

Interactive API documentation is available at "/docs".

Government Service Data

Government-service information is maintained separately from unverified discoveries.

The main catalogue is:

backend/data/services.json

New services can be added through validated catalogue data rather than hardcoding every service into the application.

The catalogue can cover:

- Aadhaar and identity services
- Income, caste and residence certificates
- Revenue and land-record services
- Transport services
- Forest and local-government services
- Grama One, Nemmadi and Karnataka One
- Public grievances and other citizen services

New service records must include official source information and undergo review before being trusted.

Unverified discoveries are not automatically promoted into the trusted catalogue.

Running Tests

From the repository root:

python -m pytest backend/tests -q

Important Notes

- The checked-in development catalogue is empty and unverified.
- Only reviewed government-service information should be added to the trusted catalogue.
- Online retrieval is optional and depends on provider configuration.
- AI-generated information must not be treated as officially verified without supporting evidence.
- Service-centre authorisation must be verified independently; proximity alone does not establish authorisation.
- Production deployment requires appropriate API security, rate limiting, CORS configuration and secret management.

Project Goal

CivicRoute aims to reduce confusion, unnecessary visits and information gaps when citizens access government services.

Making government services easier to understand and access.