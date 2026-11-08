from __future__ import annotations
from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle


class DynamicRateMixin:
    """
    Resolves throttle rates dynamically from `settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`
    so runtime configuration and `@override_settings` take effect immediately.
    """

    scope: str = "anon"

    def get_rate(self) -> str | None:
        rf_config = getattr(settings, "REST_FRAMEWORK", {})
        rates = rf_config.get("DEFAULT_THROTTLE_RATES", {})
        if self.scope in rates:
            return rates[self.scope]
        return super().get_rate()  # type: ignore[misc]


class LazarusAnonRateThrottle(DynamicRateMixin, AnonRateThrottle):
    scope = "anon"


class LazarusUserRateThrottle(DynamicRateMixin, UserRateThrottle):
    scope = "user"


class AttestationRateThrottle(DynamicRateMixin, AnonRateThrottle):
    """
    Rate limit for cryptographic voucher attestation requests (`POST /api/v1/attestation/voucher`)
    to protect the Enclave / FROST signing pipeline from DoS floods.
    """

    scope = "attestation"


class BootstrapRateThrottle(DynamicRateMixin, AnonRateThrottle):
    """
    Strict rate limit on analytical Hubble BigQuery + RPC catch-up replay bootstrap triggers
    (`POST /api/v1/contracts/<contract_id>/bootstrap`).
    """

    scope = "bootstrap"


class KeeperPollingRateThrottle(DynamicRateMixin, AnonRateThrottle):
    """
    Higher-throughput rate limit for keeper nodes polling `/api/v1/keys/expiring`
    every ledger (~5 seconds).
    """

    scope = "keeper_poll"


__all__ = [
    "LazarusAnonRateThrottle",
    "LazarusUserRateThrottle",
    "AttestationRateThrottle",
    "BootstrapRateThrottle",
    "KeeperPollingRateThrottle",
    "ScopedRateThrottle",
]
