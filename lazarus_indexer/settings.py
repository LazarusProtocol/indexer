from __future__ import annotations
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "lazarus-dev-secret-key-change-in-production-9f8e7d6c5b4a",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "indexer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "lazarus_indexer.middleware.LazarusCORSMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "lazarus_indexer.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "lazarus_indexer.wsgi.application"
ASGI_APPLICATION = "lazarus_indexer.asgi.application"


def _is_tcp_port_reachable(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# PostgreSQL Database Configuration (Primary) with SQLite Offline Fallback
POSTGRES_HOST = os.environ.get("POSTGRES_HOST") or "localhost"
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT") or "5432")
POSTGRES_DB = os.environ.get("POSTGRES_DB") or "lazarus_db"
POSTGRES_USER = os.environ.get("POSTGRES_USER") or "lazarus"
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD") or "lazarus"
DATABASE_URL = os.environ.get("DATABASE_URL") or ""
FORCE_POSTGRES = (os.environ.get("LAZARUS_USE_POSTGRES") or "").lower() in ("1", "true", "yes")

if DATABASE_URL.startswith(("postgres://", "postgresql://")):
    parsed_db = urlparse(DATABASE_URL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": (parsed_db.path or "/lazarus_db").lstrip("/"),
            "USER": parsed_db.username or POSTGRES_USER,
            "PASSWORD": parsed_db.password or POSTGRES_PASSWORD,
            "HOST": parsed_db.hostname or POSTGRES_HOST,
            "PORT": str(parsed_db.port or POSTGRES_PORT),
            "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE") or "60"),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "connect_timeout": 5,
            },
        }
    }
elif FORCE_POSTGRES or _is_tcp_port_reachable(POSTGRES_HOST, POSTGRES_PORT):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": POSTGRES_DB,
            "USER": POSTGRES_USER,
            "PASSWORD": POSTGRES_PASSWORD,
            "HOST": POSTGRES_HOST,
            "PORT": str(POSTGRES_PORT),
            "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE") or "60"),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "connect_timeout": 5,
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Redis Cache Configuration (Primary) with LocMem Offline Fallback
REDIS_CACHE_URL = os.environ.get("REDIS_CACHE_URL") or "redis://localhost:6379/1"
parsed_redis = urlparse(REDIS_CACHE_URL)
REDIS_HOST = parsed_redis.hostname or "localhost"
REDIS_PORT = int(parsed_redis.port or 6379)
FORCE_REDIS = (os.environ.get("LAZARUS_USE_REDIS") or "").lower() in ("1", "true", "yes")

if FORCE_REDIS or _is_tcp_port_reachable(REDIS_HOST, REDIS_PORT):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "KEY_PREFIX": "lazarus",
            "TIMEOUT": int(os.environ.get("LAZARUS_CACHE_DEFAULT_TTL") or "30"),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "lazarus-redis-fallback-cache",
            "KEY_PREFIX": "lazarus",
            "TIMEOUT": int(os.environ.get("LAZARUS_CACHE_DEFAULT_TTL") or "30"),
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Reverse Proxy & CSRF Configuration
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
_raw_csrf_origins = os.environ.get(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    os.environ.get(
        "DJANGO_CORS_ALLOWED_ORIGINS",
        "https://lazarusprotocol.online,https://www.lazarusprotocol.online,http://localhost:5173,http://127.0.0.1:5173",
    ),
)
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in _raw_csrf_origins.split(",") if origin.strip()
]

# Request Body Size Cap & HTTP Security Headers
DATA_UPLOAD_MAX_MEMORY_SIZE = 262_144  # 256 KB max JSON payload
FILE_UPLOAD_MAX_MEMORY_SIZE = 262_144
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "indexer.throttles.LazarusAnonRateThrottle",
        "indexer.throttles.LazarusUserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("LAZARUS_THROTTLE_ANON", "120/min"),
        "user": os.environ.get("LAZARUS_THROTTLE_USER", "600/min"),
        "attestation": os.environ.get("LAZARUS_THROTTLE_ATTESTATION", "60/min"),
        "bootstrap": os.environ.get("LAZARUS_THROTTLE_BOOTSTRAP", "10/min"),
        "keeper_poll": os.environ.get("LAZARUS_THROTTLE_KEEPER_POLL", "300/min"),
    },
    "NUM_PROXIES": int(os.environ.get("DJANGO_NUM_PROXIES", "1")),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Lazarus Protocol — Ingestion & Attestation Engine API",
    "DESCRIPTION": (
        "Decentralized State-Rent & Proactive State-Warming Keeper Network for Stellar (Soroban). "
        "Provides Hybrid Key Discovery (SDF Hubble BigQuery + Soroban RPC Catch-Up Replay + Blend Adapter), "
        "Expiring Footprint Schedules, Visual TTL Heatmap Diagnostics, and Domain-Separated "
        "`LAZARUS_VOUCHER_V1` Ed25519 / 2-of-3 FROST Cryptographic Voucher Attestation."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "REDOC_DIST": "https://cdn.jsdelivr.net/npm/redoc@2.1.5",
    "SWAGGER_UI_DIST": "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14",
    "CONTACT": {
        "name": "Lazarus Protocol Core Team",
        "url": "https://github.com/LazarusProtocol",
    },
    "LICENSE": {
        "name": "Apache-2.0 License",
    },
    "TAGS": [
        {"name": "Health", "description": "Service liveness and indexed state counters"},
        {"name": "Contracts", "description": "Soroban contract enrollment and hybrid key bootstrapping"},
        {"name": "Storage Keys", "description": "Persistent storage key index and expiring TTL queues"},
        {"name": "Diagnostics", "description": "Visual TTL heatmap and archival risk telemetry"},
        {"name": "Keepers", "description": "Keeper operator registry, monotonic nonces, and stroop yields"},
        {"name": "Attestation", "description": "Cryptographic BumpVoucher issuance and batch redemption"},
    ],
}

# Structured Logging Configuration
LOG_LEVEL = os.environ.get("LAZARUS_LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "lazarus.api": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "lazarus.pipeline": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "lazarus.attestation": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# Celery & Redis configuration
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Lazarus Protocol & Stellar Network Settings
LAZARUS_VAULT_CONTRACT_ID = os.environ.get(
    "LAZARUS_VAULT_CONTRACT_ID",
    "CCWAH7BVPGYOEBPRFWPZIONBYUCCQH7OARGZLOD6ZV66SOAFBVA2DHD5",
)
LAZARUS_SOROBAN_RPC_URL = os.environ.get(
    "LAZARUS_SOROBAN_RPC_URL",
    "https://soroban-testnet.stellar.org",
)
LAZARUS_HUBBLE_BQ_TABLE = os.environ.get(
    "LAZARUS_HUBBLE_BQ_TABLE",
    "crypto-stellar.crypto_stellar_dbt.contract_data_current",
)
LAZARUS_ORACLE_SECRET_HEX = (
    os.environ.get("LAZARUS_ORACLE_SECRET_HEX")
    or os.environ.get("LAZARUS_ORACLE_SIGNING_SEED_HEX")
    or ("2a" * 32)
)
LAZARUS_ATTESTATION_ENCLAVE_URL = os.environ.get("LAZARUS_ATTESTATION_ENCLAVE_URL", "")
LAZARUS_ATTESTATION_API_KEY = os.environ.get("LAZARUS_ATTESTATION_API_KEY", "")
LAZARUS_EMERGENCY_THRESHOLD = int(os.environ.get("LAZARUS_EMERGENCY_THRESHOLD", "2000"))
LAZARUS_DAY_IN_LEDGERS = 17_280
