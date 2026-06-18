# Development image for running the application locally
#
# Build context is the repo root (see docker-compose.yml)

FROM python:3.14-slim AS base

# uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Node.js for the Tailwind/DaisyUI build (django-tailwind calls out to npm). The
# `tailwind` service uses the host's bind-mounted node_modules, so we only need
# the node + npm runtime here, not an in-image install.
COPY --from=node:24-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-slim /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # The runtime user (set via compose `user:`) may not be able to write to the source
    # tree, so do not try to emit .pyc files next to it.
    PYTHONDONTWRITEBYTECODE=1 \
    # A writable HOME for any uid the container is run as.
    HOME=/tmp

WORKDIR /app

# Install dependencies first for layer caching. --no-install-project: the app is
# run from the source tree (Django apps under media_janitor/), not as an installed
# distribution. Dev dependencies are included: this image runs with DEBUG on
# and uses the autoreloading runserver.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Application source. In this dev image the source is also bind-mounted over this
# path (see docker-compose.yml) for hot reload without rebuilds; the copy keeps
# the image runnable on its own.
COPY media_janitor/ ./media_janitor/

# Django runs from the project dir, where manage.py and config.settings live.
WORKDIR /app/media_janitor

# Default to running the tasks worker. Overwritten in docker-compose.yml for
# other commands.
CMD ["python", "manage.py", "db_worker"]
