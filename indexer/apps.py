from __future__ import annotations
from django.apps import AppConfig


class IndexerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "indexer"
    verbose_name = "Lazarus Dynamic Key Ingestion & Attestation Engine"

    def ready(self) -> None:
        from . import signals  # noqa: F401
