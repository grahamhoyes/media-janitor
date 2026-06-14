# Media Janitor

Human-in-the-loop tool for reclaiming wasted space on a homelab *arr stack.

There are two main components:
- A scan worker periodically (or as-triggered) scans a filesystem and associated *arr apps to look for files that aren't hard-linked in multiple places and aren't being seeded.
- A web application displays the latest scan so the user can explore storage and safely delete orphaned files.

## Getting Started

### Python Environment

This project uses Python 3.14 and [uv](https://docs.astral.sh/uv/) for environment management. Install uv by following the official [installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

uv will automatically create a virtual environment and install all dependencies (including the required Python version) from the lockfile:

```bash
uv sync
```

### Environment Variables

Sample environment variables are provided in [.env.sample](.env.example). Copy them to `.env`:

```bash
cp .env.sample .env
```

Make sure to change placeholder values (especially the secret key) before running in a real environment.

### Database

An external Postgres database is required so that the sync worker and web application can share a data store. Any version of Postgres supported by Django is fine. A Postgres 18 database for local development is provided in [docker-compose.yml](docker-compose.yml):

```bash
docker compose up -d
```

### First Run

On the first run, migrations need to be applied and a superuser created:

```bash
cd media_janitor

uv run manage.py migrate
uv run manage.py createsuperuser
```

### Running the Development Server

From within the `media_janitor` directory, the `tailwind dev` command will run the Django development server and enable watch mode for Tailwind:

```bash
uv run manage.py tailwind dev
```

Alternatively, the Django and Tailwind watch processes can be run separately:

```bash
# Django development server
uv run manage.py runserver

# Tailwind watch
uv run manage.py tailwind start
```

The server will be available at http://localhost:8000

### Tests and Linting

Run tests and linting within the `media_janitor` directory.

Lint:

```bash
uv run ruff check .
```

Tests:

```bash
uv run pytest
```

## Stack

Backend:
- Python 3.14
- Django 6.0
- Postgres (an external database is required since the scan runs in another process)
- Background work: Django 6.0 Tasks + [`django-tasks-db`](https://github.com/RealOrangeOne/django-tasks-db) (`manage.py db_worker`)

Frontend:
- Tailwind v4 + DaisyUI via `django-tailwind`
- HTMX
- Alpine.js

Tooling:
- `uv`
- `ruff`
- `pytest`

## Layout

The Django project lives in `media_janitor/` (run `manage.py` from there). Apps:

- `scanner`: snapshot models + scan pipeline (no views)
- `web`: views, templates, deletion flow, audit log
- `theme`: django-tailwind app (Tailwind/DaisyUI build)
