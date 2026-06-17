"""
Django settings for the Media Janitor project.
"""

from pathlib import Path

import environ

# media_janitor/ (holds manage.py, config/, the apps). The repo root is one level up.
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

# Load repo-root .env if present (dev). In deployment, real env vars take precedence.
environ.Env.read_env(REPO_ROOT / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Filesystem. SHARE_ROOT is where Media Janitor sees the share root. The scan walks
# everything under it.
SHARE_ROOT = env("SHARE_ROOT", default="")

# qBittorrent. The application will start, but the scan will fail if values aren't set.
QBIT_HOST = env("QBIT_HOST", default="")
QBIT_API_KEY = env("QBIT_API_KEY", default="")
QBIT_DATA_ROOT = env("QBIT_DATA_ROOT", default="")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_tasks_db",
    "django_htmx",
    "tailwind",
    "theme",
    # First-party
    "scanner",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# Must use a database that supports multiple concurrent connections. Postgres recommended.
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://media_janitor:media_janitor@localhost:5432/media_janitor",
    ),
}


# Background tasks: Django 6.0 Tasks framework backed by Postgres (django-tasks-db).
# Run the worker with `manage.py db_worker`.
TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default"],
    },
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Frontend: Tailwind v4 + DaisyUI via django-tailwind (npm).
TAILWIND_APP_NAME = "theme"


LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"


# == Development-only additions =================================================
if DEBUG:
    INSTALLED_APPS += ["django_browser_reload"]
    MIDDLEWARE += ["django_browser_reload.middleware.BrowserReloadMiddleware"]
    INTERNAL_IPS = ["127.0.0.1"]
