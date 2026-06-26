# Media Janitor

Human-in-the-loop tool for reclaiming wasted space on a homelab *arr stack.

There are two main components:

- A scan worker periodically (or as-triggered) scans a filesystem and associated *arr apps to look for files that aren't
  hard-linked in multiple places and aren't being seeded.
- A web application displays the latest scan so the user can explore storage and safely delete orphaned files.

## Configuration

Media Janitor reads its settings from environment variables. Copy [.env.example](.env.example) to `.env` and adjust
them for your setup.

### Mount points

Media Janitor and every app it talks to read the same media share, but each may mount it at a different path in the
container and may only mount a sub-path of the share. So that paths line up no matter which app reported them, Media
Janitor stores everything relative to the share root. For each app you set a **data root**: the path in that app's
container that maps to the share root. Media Janitor strips it to recover the share-relative path.

For example, a share on a NAS at `/volume1/media`:

```
/volume1/media
├── torrents
│   ├── movies
│   └── tv
└── media
    ├── movies
    └── tv
```

mounted into three apps:

| App             | Mounts                                        | Reports paths like         | Data root     |
|-----------------|-----------------------------------------------|----------------------------|---------------|
| qBittorrent     | `torrents/` subpath only, at `/data/torrents` | `/data/torrents/movies`    | `/data`       |
| Sonarr / Radarr | whole share, at `/data`                       | `/data/media/movies`       | `/data`       |
| Jellyfin        | whole share, at `/data/media`                 | `/data/media/media/movies` | `/data/media` |

The data root is the path that maps to the share root, which is not always the mount path: in this example qBittorrent
mounts only the share's `torrents/` subdirectory, but its paths are still rooted at `/data`, so stripping `/data` from
`/data/torrents/movies` correctly yields the share-relative `torrents/movies`.

### Settings

Copy [.env.example](.env.example) to `.env`, then update the settings below.

| Setting          | Description                                                                       |
|------------------|-----------------------------------------------------------------------------------|
| `SECRET_KEY`     | Django secret key. Must be set for the application to start.                      |
| `SHARE_ROOT`     | Path where Media Janitor sees the share root. The scan walks everything under it. |
| `QBIT_HOST`      | qBittorrent WebUI URL                                                             |
| `QBIT_API_KEY`   | qBittorrent 5.2 WebUI API key (Settings > WebUI > Authentication)                 |
| `QBIT_DATA_ROOT` | qBittorrent data root (see [Mount points](#mount-points))                         |

The env file also has settings which are used only when running through [docker-compose.yml](docker-compose.yml):

| Setting         | Description                                                                                  |
|-----------------|----------------------------------------------------------------------------------------------|
| NFS_SERVER      | NFS server hostname (IP or DNS record). Gets mounted to the worker container.                |
| NFS_EXPORT_PATH | NFS export / share path on the server. In the above examples, this would be `/volume1/media` |
| NFS_UID         | Worker container user ID. May be required if your NFS server doesn't have squash enabled.    |
| NFS_GID         | Worker container group ID. May be required if your NFS server doesn't have squash enabled.   |

## Development

### Environment Variables

Sample environment variables are provided in [.env.example](.env.example). Copy them to `.env`:

```bash
cp .env.example .env
```

Make sure to change placeholder values (especially the secret key) before running in a real environment.
See [above](#settings) for a description of the settings.

### Python Environment

This project uses Python 3.14 and [uv](https://docs.astral.sh/uv/) for environment management. Install uv by following
the official [installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

uv will automatically create a virtual environment and install all dependencies (including the required Python version)
from the lockfile:

```bash
uv sync
```

### Node.js

Node.js is required for compiling Tailwind CSS. Install Node.js v24+ using your preferred method, for example
with [nodenv](https://github.com/nodenv/nodenv).

### Database

An external Postgres database is required so that the sync worker and web application can share a data store (we also
use Postgres advisory locks, so other databases are not supported). Any version of Postgres supported by Django is fine.
A Postgres 18 database for local development is provided in [docker-compose.yml](docker-compose.yml). When running the
rest of the app on the host (`uv run ...`), bring up just the database:

```bash
docker compose up -d db
```

### First Run

On the first run, migrations need to be applied and a superuser created:

```bash
cd media_janitor

uv run manage.py migrate
uv run manage.py createsuperuser
```

Dependencies also need to be installed for Tailwind (requires Node.js):

```bash
uv run manage.py tailwind install
```

### Running the Development Server

From within the `media_janitor` directory, the `tailwind dev` command will run the Django development server and enable
watch mode for Tailwind:

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

To run scans, a separate worker process is required:

```bash
uv run manage.py db_worker
```

### Running in Docker

As an alternative to running the development server and database worker locally, they can be run in Docker with hot
reloading. This also allows you to easily mount an NFS share to the container for running scans on an actual NAS without
needing to mount it locally. See [docker-compose.yaml](docker-compose.yml) for more details.

To start the stack (database, web server, worker, and the Tailwind CSS watcher):

```bash
docker compose up -d
```

To run migrations without needing host python:

```bash
docker compose run --rm web python manage.py migrate
```

To create a superuser:

```bash
docker compose run --rm web python manage.py createsuperuser
```

To install Tailwind for the first time:

```bash
docker compose run --rm tailwind python manage.py tailwind install
```

To fully remove the stack, including the database volume, run:

```bash
docker compose down --volumes
```

### Tests and Linting

Run tests and linting within the `media_janitor` directory.

Lint:

```bash
uv run ruff check .
```

Type check:

```bash
uv run mypy .
```

Tests:

```bash
uv run pytest
```

Linting and formatting for Django templates:

```bash
uv run djlint .
uv run djlint --reformat .
```

[pre-commit](https://pre-commit.com/) hooks are available to run these on commit. To configure them:

```bash
uv run pre-commit install
```

## Stack

Backend:

- Python 3.14
- Django 6.0
- Postgres (an external database is required since the scan runs in another process)
- Background work: Django 6.0 Tasks + [`django-tasks-db`](https://github.com/RealOrangeOne/django-tasks-db)
  (`manage.py db_worker`)

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
