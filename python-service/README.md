# GitHub Issue Discovery Service

Python service for ContriPlace. It fetches open, unassigned issues from popular repositories with GitHub GraphQL, then classifies each issue by domain and estimates difficulty.

## Quick Start

### 1. Install dependencies

```powershell
cd python-service
python -m pip install -r requirements.txt
```

### 2. Configure environment

Create `.env` in this directory:

```env
GITHUB_TOKEN=github_pat_or_ghp_token
GEMINI_API_KEY=google_ai_studio_api_key
GEMINI_MODELS=gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-3-flash-preview,gemini-2.5-flash
GEMINI_MODEL_RPMS=gemini-3.1-flash-lite:15,gemini-2.5-flash-lite:10,gemini-3-flash-preview:5,gemini-2.5-flash:5
GEMINI_REQUESTS_PER_MINUTE=5
GEMINI_BATCH_SIZE=10
REPOS=apache/superset,grafana/grafana,storybookjs/storybook,streamlit/streamlit,plotly/dash,metabase/metabase,refinedev/refine,twentyhq/twenty
```

`GEMINI_API_KEY` is required for issue classification. When it is missing or all configured Gemini models are unavailable, issues are stored without classification and do not appear in discovery results until Gemini classifies them.
`GEMINI_MODELS` is an ordered fallback list. If one model is exhausted or unavailable, sync tries the next one.
`GEMINI_MODEL_RPMS` sets the per-model request-per-minute pacing used by the fallback queue.
`GEMINI_REQUESTS_PER_MINUTE` should match your Gemini quota. With the default value of `5`, sync waits about 12 seconds between Gemini classifications.
`GEMINI_BATCH_SIZE` controls how many GitHub issues are sent in one Gemini request. With the default value of `10`, 100 issue classifications use about 10 Gemini requests.

By default, the local SQLite database is stored under `%LOCALAPPDATA%\\contriplace\\issues.db`.

### 3. Run the API

```powershell
uvicorn main:app --reload --port 8010
```

### 4. Sync issues into the database

```powershell
python main.py sync
```

Or call:

`POST http://127.0.0.1:8010/admin/sync`

### 5. Try it

`GET http://127.0.0.1:8010/health`

`GET http://127.0.0.1:8010/issues/discover`

## What It Does

- Fetches recent open issues from a curated list of popular repos
- Filters out assigned issues and issues with an open linked pull request
- Tags issues as `frontend`, `backend`, `security`, `devops`, `docs`, or `other`
- Scores difficulty from 1-10 and labels it as `easy` for 1-3, `medium` for 4-7, or `hard` for 8-10
- Optionally uses Google AI Studio/Gemini for classification
- Stores fetched issues and classifications in SQLite
- Exposes cached results as JSON for the web app

## Files

- `github_graphql.py`: async GitHub GraphQL client and repo/issue models
- `issue_intelligence.py`: Gemini issue classification
- `db.py`: SQLite schema and query helpers
- `sync_service.py`: fetch, classify, upsert, and archive pipeline
- `main.py`: FastAPI endpoints and CLI entry point
- `config.py`: environment-driven configuration and default repo catalog
