from __future__ import annotations
from django.urls import path
from .views import (
    ContractBootstrapAPIView,
    ContractStorageKeyListCreateAPIView,
    ExpiringStorageKeysAPIView,
    HealthCheckAPIView,
    KeeperDetailAPIView,
    KeeperListCreateAPIView,
    MonitoredContractDetailAPIView,
    MonitoredContractListCreateAPIView,
    TTLHeatmapDiagnosticsAPIView,
    VoucherAttestationAPIView,
    VoucherBatchRedeemAPIView,
    VoucherListAPIView,
)

urlpatterns = [
    path("health", HealthCheckAPIView.as_view(), name="health-check"),
    path("contracts", MonitoredContractListCreateAPIView.as_view(), name="contract-list-create"),
    path("contracts/<str:contract_id>", MonitoredContractDetailAPIView.as_view(), name="contract-detail"),
    path("contracts/<str:contract_id>/bootstrap", ContractBootstrapAPIView.as_view(), name="contract-bootstrap"),
    path("contracts/<str:contract_id>/keys", ContractStorageKeyListCreateAPIView.as_view(), name="contract-keys"),
    path("keys/expiring", ExpiringStorageKeysAPIView.as_view(), name="expiring-keys"),
    path("diagnostics/heatmap", TTLHeatmapDiagnosticsAPIView.as_view(), name="diagnostics-heatmap"),
    path("keepers", KeeperListCreateAPIView.as_view(), name="keeper-list-create"),
    path("keepers/<str:keeper_address>", KeeperDetailAPIView.as_view(), name="keeper-detail"),
    path("attestation/voucher", VoucherAttestationAPIView.as_view(), name="attestation-voucher"),
    path("attestation/vouchers", VoucherListAPIView.as_view(), name="attestation-voucher-list"),
    path("attestation/vouchers/redeem", VoucherBatchRedeemAPIView.as_view(), name="attestation-voucher-redeem"),
]
