# Group Scheduler

A lightweight web app for coordinating group meeting times without requiring accounts or authentication. Create a shareable link, send it to your group, and everyone picks their available times. Once everyone submits, proposed groups and meeting windows are displayed automatically.

## How it works

1. **Create a group link** — Enter a title and a list of participant names. The app generates a unique shareable URL.
2. **Everyone submits availability** — Each participant opens the link, selects their name, and drags to select available time blocks on a weekly calendar. The calendar displays in each viewer's local timezone automatically.
3. **Groups are proposed** — Once all participants have submitted, the planner runs automatically and displays proposed groups with shared meeting windows. Anyone can revisit the link to see the results. Participants can resubmit at any time — their latest response always replaces the previous one.

The grouping algorithm is deterministic: the same availability always produces the same groups. Results only change when someone updates their submission.

## Running locally

**Requirements:** Python 3.11+

```bash
# Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# Start the server
uvicorn app.main:app --reload --app-dir src
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

**Optional environment variables** (can be set in a `.env` file):

| Variable | Default | Description |
|---|---|---|
| `CLASS_TIMEZONE` | `America/Los_Angeles` | IANA timezone for the scheduling week |
| `DATABASE_PATH` | `data.db` (in-process) | Path to the SQLite database file |

## Repo layout

```
group-scheduling-app/
├── src/
│   ├── app/
│   │   ├── algorithms/      # Grouping planner and availability normalization
│   │   ├── api/routes/      # FastAPI route handlers
│   │   ├── core/            # Session state and SQLite persistence
│   │   ├── schemas/         # Pydantic data contracts
│   │   ├── services/        # Conflict resolution
│   │   ├── static/          # CSS and client-side JavaScript
│   │   └── templates/       # Jinja2 HTML templates
│   └── tests/
├── Procfile                 # Railway / Heroku start command
└── pyproject.toml
```

## Contributing

1. Fork the repo and create a branch from `main`.
2. Install dependencies with `pip install -e '.[dev]'` and run the test suite:
   ```bash
   pytest src/tests -q
   ```
3. Open a pull request with a clear description of the change.

Please keep pull requests focused — one feature or fix per PR.
