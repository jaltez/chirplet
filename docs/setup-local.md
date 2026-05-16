# Local Setup

## Prerequisites

- Python 3.11+
- Hermes running locally or reachable from the machine
- Desktop browser with microphone access

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set:

- `HERMES_BASE_URL`
- `HERMES_MODEL`
- `HERMES_API_KEY` when required

## Run

```bash
uvicorn apps.api.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Notes

- The first build uses browser speech APIs to keep the local demo simple.
- If browser speech recognition is unavailable, open the debug panel and send manual text.
- Session history is in memory only and resets when the backend restarts.
