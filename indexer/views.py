from __future__ import annotations
import logging
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Q, Sum, Value, When
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from .cache_utils import LazarusCacheManager
from .models import KeeperRegistry, MonitoredContract, StorageKeyIndex, VoucherLog
from .serializers import (ContractBootstrapRequestSerializer,
    KeeperRegistrySerializer,
    MonitoredContractSerializer,
    StorageKeyIndexSerializer,
    VoucherAttestationRequestSerializer,
    VoucherBatchRedeemRequestSerializer,
    VoucherLogSerializer,
)
from .services import (AttestationError, AttestationVerifierAndSigner, HybridDiscoveryPipeline,)
from .throttles import (AttestationRateThrottle,BootstrapRateThrottle,KeeperPollingRateThrottle, LazarusAnonRateThrottle, LazarusUserRateThrottle,)
from .validators import (parse_positive_u32_param,validate_soroban_contract_id,validate_stellar_address,)

logger = logging.getLogger("lazarus.api")

DAY_IN_LEDGERS = getattr(settings, "LAZARUS_DAY_IN_LEDGERS", 17_280)
EMERGENCY_THRESHOLD = getattr(settings, "LAZARUS_EMERGENCY_THRESHOLD", 2_000)


class HealthCheckAPIView(APIView):
    """
    Class-Based View returning service health, PostgreSQL + Redis connectivity,
    active contract counts, and vault config.
    GET /api/v1/health
    """

    throttle_classes = []

    @extend_schema(
        tags=["Health"],
        summary="Service liveness, PostgreSQL/Redis health, and indexed state counters",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request: Request) -> Response:
        def _build_health() -> dict:
            monitored = MonitoredContract.objects.filter(is_active=True).count()
            indexed_keys = StorageKeyIndex.objects.count()
            keepers = KeeperRegistry.objects.filter(is_active=True).count()
            infra = LazarusCacheManager.check_infrastructure_health()
            return {
                "status": "ok",
                "service": "lazarus-ingestion-attestation-engine",
                "vault_contract_id": getattr(settings, "LAZARUS_VAULT_CONTRACT_ID", ""),
                "infrastructure": infra,
                "monitored_contracts": monitored,
                "indexed_storage_keys": indexed_keys,
                "registered_keepers": keepers,
            }

        payload, cache_status = LazarusCacheManager.get_or_set_health(builder_fn=_build_health)
        response = Response(payload, status=status.HTTP_200_OK)
        response["X-Cache"] = cache_status
        return response


@extend_schema(tags=["Contracts"])
class MonitoredContractListCreateAPIView(generics.ListCreateAPIView):
    """
    Class-Based View to list all monitored contracts or enroll a new Soroban contract.
    GET, POST /api/v1/contracts
    Automatically triggers the 4-stage Hybrid Discovery Pipeline on enrollment if
    `auto_bootstrap=true` (default).
    """

    serializer_class = MonitoredContractSerializer
    throttle_classes = [LazarusAnonRateThrottle, LazarusUserRateThrottle]

    def get_queryset(self):
        return MonitoredContract.objects.annotate(indexed_keys_count=Count("keys")).order_by(
            "-created_at"
        )

    def list(self, request: Request, *args, **kwargs) -> Response:
        def _build_list():
            queryset = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset, many=True)
            return serializer.data

        data, cache_status = LazarusCacheManager.get_or_set_contracts_list(builder_fn=_build_list)
        response = Response(data, status=status.HTTP_200_OK)
        response["X-Cache"] = cache_status
        return response

    def perform_create(self, serializer: MonitoredContractSerializer) -> None:
        contract = serializer.save()
        logger.info(
            "Enrolled new MonitoredContract contract_id=%s protocol_type=%s fixed_bounty=%s",
            contract.contract_id,
            contract.protocol_type,
            contract.fixed_bounty,
        )
        auto_bootstrap = self.request.data.get("auto_bootstrap", True)
        if auto_bootstrap:
            pipeline = HybridDiscoveryPipeline()
            pipeline.execute_full_bootstrap(contract=contract)


@extend_schema(tags=["Contracts"])
class MonitoredContractDetailAPIView(generics.RetrieveUpdateAPIView):
    """
    Class-Based View to retrieve or update non-custodial parameters of a monitored contract.
    GET, PATCH, PUT /api/v1/contracts/<str:contract_id>
    Note: Arbitrary contract deletion is disallowed to prevent denial-of-service against
    enrolled protocols. Client authority is strictly verified on every mutation.
    """

    serializer_class = MonitoredContractSerializer
    lookup_field = "contract_id"
    throttle_classes = [LazarusAnonRateThrottle, LazarusUserRateThrottle]
    http_method_names = ["get", "patch", "put", "head", "options"]

    def get_queryset(self):
        return MonitoredContract.objects.annotate(indexed_keys_count=Count("keys"))

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        caller_addr = (
            request.headers.get("X-Client-Address")
            or request.data.get("client_address")
        )
        if not caller_addr or str(caller_addr).strip().upper() != instance.client_address.upper():
            return Response(
                {
                    "error": (
                        "Unauthorized: X-Client-Address header or client_address must match "
                        "the registered contract client authority."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)


class ContractBootstrapAPIView(APIView):
    """
    Class-Based View to explicitly trigger the Hybrid Bootstrapping & Reconciliation Pipeline
    (Pipeline A: Hubble BigQuery -> Pipeline B: RPC Catch-Up Replay -> Pipeline C: Protocol Adapter).
    POST /api/v1/contracts/<str:contract_id>/bootstrap
    """

    throttle_classes = [BootstrapRateThrottle]

    @extend_schema(
        tags=["Contracts"],
        summary="Trigger 4-stage Hybrid Key Discovery & RPC Catch-Up Reconciliation",
        request=ContractBootstrapRequestSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, contract_id: str) -> Response:
        cleaned_contract_id = validate_soroban_contract_id(contract_id)
        contract = get_object_or_404(MonitoredContract, contract_id=cleaned_contract_id)

        serializer = ContractBootstrapRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        snapshot_ledger = serializer.validated_data["snapshot_ledger"]
        current_ledger = serializer.validated_data["current_ledger"]

        logger.info(
            "Triggering manual hybrid bootstrap for contract_id=%s snapshot=%d current=%d",
            cleaned_contract_id,
            snapshot_ledger,
            current_ledger,
        )
        pipeline = HybridDiscoveryPipeline()
        report = pipeline.execute_full_bootstrap(
            contract=contract,
            snapshot_ledger=snapshot_ledger,
            current_ledger=current_ledger,
        )
        return Response(report, status=status.HTTP_200_OK)


@extend_schema(tags=["Storage Keys"])
class ContractStorageKeyListCreateAPIView(generics.ListCreateAPIView):
    """
    Class-Based View to list or register persistent `StorageKeyIndex` entries for a contract.
    GET, POST /api/v1/contracts/<str:contract_id>/keys
    """

    serializer_class = StorageKeyIndexSerializer
    throttle_classes = [LazarusAnonRateThrottle, LazarusUserRateThrottle]

    def get_queryset(self):
        contract_id = validate_soroban_contract_id(self.kwargs["contract_id"])
        return StorageKeyIndex.objects.select_related("contract").filter(
            contract__contract_id=contract_id
        )

    def perform_create(self, serializer: StorageKeyIndexSerializer) -> None:
        contract_id = validate_soroban_contract_id(self.kwargs["contract_id"])
        contract = get_object_or_404(MonitoredContract, contract_id=contract_id)
        caller_addr = (
            self.request.headers.get("X-Client-Address")
            or self.request.data.get("client_address")
        )
        if (
            not caller_addr
            or str(caller_addr).strip().upper() != contract.client_address.upper()
        ):
            raise PermissionDenied(
                "Unauthorized: X-Client-Address header or client_address must match "
                "the registered contract client authority to register storage keys."
            )
        instance = serializer.save(contract=contract)
        logger.info(
            "Indexed StorageKeyIndex contract_id=%s key_hash=%s live_until=%d",
            contract.contract_id,
            instance.key_hash,
            instance.live_until_ledger_seq,
        )


class ExpiringStorageKeysAPIView(APIView):
    """
    Class-Based View polled by `LazarusKeeperNode.fetch_expiring_keys()`.
    GET /api/v1/keys/expiring?current_ledger=<int>
    Cached in Redis per `(state_version, current_ledger)` for 1 ledger (~5s) to absorb
    concurrent polling across keeper node fleets without overloading PostgreSQL.
    """

    throttle_classes = [KeeperPollingRateThrottle]

    @extend_schema(
        tags=["Storage Keys"],
        summary="List expiring persistent storage keys eligible for proactive warming (Redis cached)",
        parameters=[
            OpenApiParameter(
                name="current_ledger",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Current Stellar ledger sequence number (default: 500000)",
                required=False,
            ),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request: Request) -> Response:
        current_ledger = parse_positive_u32_param(
            request.query_params.get("current_ledger"),
            param_name="current_ledger",
            default=500_000,
        )

        def _build_expiring() -> dict:
            # High-speed SQL pushdown using .values() projection to avoid ORM model instantiation overhead
            expiring_rows = (
                StorageKeyIndex.objects.filter(contract__is_active=True)
                .filter(
                    Q(live_until_ledger_seq__lte=current_ledger + F("contract__min_ledger_threshold"))
                    | Q(live_until_ledger_seq__lte=current_ledger + EMERGENCY_THRESHOLD)
                )
                .order_by("live_until_ledger_seq")
                .values(
                    "id",
                    "contract__contract_id",
                    "key_hash",
                    "key_xdr",
                    "durability",
                    "live_until_ledger_seq",
                    "contract__min_ledger_threshold",
                    "contract__bump_target",
                    "contract__fixed_bounty",
                )[:500]
            )
            expiring_payload = [
                {
                    "id": row["id"],
                    "contract_id": row["contract__contract_id"],
                    "key_hash": row["key_hash"],
                    "key_xdr": row["key_xdr"],
                    "durability": row["durability"],
                    "live_until_ledger_seq": row["live_until_ledger_seq"],
                    "remaining_ledgers": row["live_until_ledger_seq"] - current_ledger,
                    "min_ledger_threshold": row["contract__min_ledger_threshold"],
                    "bump_target": current_ledger + row["contract__bump_target"],
                    "bump_ledgers_delta": row["contract__bump_target"],
                    "fixed_bounty": int(row["contract__fixed_bounty"]),
                    "is_emergency": (row["live_until_ledger_seq"] - current_ledger) <= EMERGENCY_THRESHOLD,
                }
                for row in expiring_rows
            ]
            return {
                "current_ledger": current_ledger,
                "count": len(expiring_payload),
                "results": expiring_payload,
            }

        data, cache_status = LazarusCacheManager.get_or_set_expiring_keys(
            current_ledger=current_ledger,
            builder_fn=_build_expiring,
        )
        logger.debug(
            "Expiring keys polled at ledger=%d: count=%d cache=%s",
            current_ledger,
            data["count"],
            cache_status,
        )
        response = Response(data, status=status.HTTP_200_OK)
        response["X-Cache"] = cache_status
        return response


class TTLHeatmapDiagnosticsAPIView(APIView):
    """
    Class-Based View for the Developer Dashboard Visual TTL Heatmap (`mvp.md` Section 5):
      - Healthy (green): > 30 days TTL remaining (> 518,400 ledgers)
      - Warming Window (yellow): 2,000 to 518,400 ledgers remaining
      - Critical Risk (red): <= 2,000 ledgers remaining (Emergency Threshold Bypass active)
    GET /api/v1/diagnostics/heatmap?current_ledger=<int>
    Cached in Redis per `(state_version, contract_id, current_ledger)` for 15s (~3 ledgers).
    """

    throttle_classes = [LazarusAnonRateThrottle, LazarusUserRateThrottle]

    @extend_schema(
        tags=["Diagnostics"],
        summary="Visual TTL Heatmap risk distribution (Healthy / Warming / Critical) (Redis cached)",
        parameters=[
            OpenApiParameter(
                name="current_ledger",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Current Stellar ledger sequence number (default: 500000)",
                required=False,
            ),
            OpenApiParameter(
                name="contract_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Optional Soroban Contract ID filter",
                required=False,
            ),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request: Request) -> Response:
        current_ledger = parse_positive_u32_param(
            request.query_params.get("current_ledger"),
            param_name="current_ledger",
            default=500_000,
        )
        contract_filter = request.query_params.get("contract_id")
        cleaned_cid = (
            validate_soroban_contract_id(contract_filter) if contract_filter else None
        )

        def _build_heatmap() -> dict:
            healthy_cutoff = current_ledger + (30 * DAY_IN_LEDGERS)  # 518,400 ledgers
            critical_cutoff = current_ledger + EMERGENCY_THRESHOLD

            base_qs = StorageKeyIndex.objects.filter(contract__is_active=True)
            if cleaned_cid:
                base_qs = base_qs.filter(contract__contract_id=cleaned_cid)

            # High performance SQL aggregation for accurate summary counts across any scale
            agg_counts = base_qs.aggregate(
                critical=Sum(
                    Case(
                        When(live_until_ledger_seq__lte=critical_cutoff, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                warming=Sum(
                    Case(
                        When(
                            live_until_ledger_seq__gt=critical_cutoff,
                            live_until_ledger_seq__lte=healthy_cutoff,
                            then=Value(1),
                        ),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                healthy=Sum(
                    Case(
                        When(live_until_ledger_seq__gt=healthy_cutoff, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
            )
            c_count = agg_counts["critical"] or 0
            w_count = agg_counts["warming"] or 0
            h_count = agg_counts["healthy"] or 0
            total_count = c_count + w_count + h_count

            # Fetch representative samples (up to 100 per tier) using lightweight .values() projection
            def _serialize_keys(keys_qs, tier_name):
                return [
                    {
                        "contract_id": row["contract__contract_id"],
                        "key_hash": row["key_hash"],
                        "live_until_ledger_seq": row["live_until_ledger_seq"],
                        "remaining_ledgers": row["live_until_ledger_seq"] - current_ledger,
                        "tier": tier_name,
                    }
                    for row in keys_qs.values(
                        "contract__contract_id", "key_hash", "live_until_ledger_seq"
                    )[:100]
                ]

            critical_keys = _serialize_keys(
                base_qs.filter(live_until_ledger_seq__lte=critical_cutoff),
                "critical",
            )
            warming_keys = _serialize_keys(
                base_qs.filter(
                    live_until_ledger_seq__gt=critical_cutoff,
                    live_until_ledger_seq__lte=healthy_cutoff,
                ),
                "warming",
            )
            healthy_keys = _serialize_keys(
                base_qs.filter(live_until_ledger_seq__gt=healthy_cutoff),
                "healthy",
            )

            return {
                "current_ledger": current_ledger,
                "summary": {
                    "healthy_count": h_count,
                    "warming_count": w_count,
                    "critical_count": c_count,
                    "total_monitored_keys": total_count,
                },
                "tiers": {
                    "healthy": healthy_keys,
                    "warming": warming_keys,
                    "critical": critical_keys,
                },
            }

        data, cache_status = LazarusCacheManager.get_or_set_heatmap(
            current_ledger=current_ledger,
            contract_id=cleaned_cid,
            builder_fn=_build_heatmap,
        )
        response = Response(data, status=status.HTTP_200_OK)
        response["X-Cache"] = cache_status
        return response


@extend_schema(tags=["Keepers"])
class KeeperListCreateAPIView(generics.ListCreateAPIView):
    """
    Class-Based View to list registered keeper nodes or enroll a new keeper operator.
    GET, POST /api/v1/keepers
    """

    serializer_class = KeeperRegistrySerializer
    throttle_classes = [LazarusAnonRateThrottle, LazarusUserRateThrottle]

    def get_queryset(self):
        return KeeperRegistry.objects.annotate(vouchers_issued_count=Count("vouchers")).order_by(
            "-total_bounties_claimed"
        )

    def list(self, request: Request, *args, **kwargs) -> Response:
        def _build_list():
            queryset = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset, many=True)
            return serializer.data

        data, cache_status = LazarusCacheManager.get_or_set_keepers_list(builder_fn=_build_list)
        response = Response(data, status=status.HTTP_200_OK)
        response["X-Cache"] = cache_status
        return response


@extend_schema(tags=["Keepers"])
class KeeperDetailAPIView(generics.RetrieveAPIView):
    """
    Class-Based View to inspect a specific keeper node's monotonic nonce and earnings.
    GET /api/v1/keepers/<str:keeper_address>
    """

    serializer_class = KeeperRegistrySerializer
    lookup_field = "keeper_address"
    throttle_classes = [LazarusAnonRateThrottle, LazarusUserRateThrottle]

    def get_queryset(self):
        return KeeperRegistry.objects.annotate(vouchers_issued_count=Count("vouchers"))


class VoucherAttestationAPIView(APIView):
    """
    Class-Based View implementing the Attestation Signer endpoint (`mvp.md` line 890):
    POST /api/v1/attestation/voucher
    Verifies on-chain `ExtendFootprintTTLOp` and returns a domain-separated Ed25519
    signed `BumpVoucher` (HTTP 200) or HTTP 202 while awaiting block inclusion.
    """

    throttle_classes = [AttestationRateThrottle]

    @extend_schema(
        tags=["Attestation"],
        summary="Verify ExtendFootprintTTLOp and issue a signed Ed25519 BumpVoucher",
        request=VoucherAttestationRequestSerializer,
        responses={
            200: OpenApiTypes.OBJECT,
            202: OpenApiTypes.OBJECT,
            400: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
            429: OpenApiTypes.OBJECT,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = VoucherAttestationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        verifier = AttestationVerifierAndSigner()
        try:
            http_status, payload = verifier.verify_and_issue_voucher(
                tx_hash=data["tx_hash"],
                contract_id=data["contract_id"],
                keeper_address=data["keeper_address"],
                ledger_bumped=data.get("ledger_bumped"),
                tx_inclusion_ledger=data.get("tx_inclusion_ledger"),
                key_hash=data.get("key_hash"),
            )
            logger.info(
                "Attestation voucher request tx_hash=%s keeper=%s status=%d",
                data["tx_hash"],
                data["keeper_address"],
                http_status,
            )
            return Response(payload, status=http_status)
        except AttestationError as exc:
            logger.warning(
                "Attestation rejected tx_hash=%s keeper=%s reason=%s",
                data["tx_hash"],
                data["keeper_address"],
                exc.message,
            )
            return Response({"error": exc.message}, status=exc.status_code)


@extend_schema(
    tags=["Attestation"],
    parameters=[
        OpenApiParameter(
            name="keeper_address",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Filter vouchers by keeper Stellar public key",
            required=False,
        ),
        OpenApiParameter(
            name="contract_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Filter vouchers by target Soroban contract ID",
            required=False,
        ),
        OpenApiParameter(
            name="redeemed",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            description="Filter by on-chain redemption status (true/false)",
            required=False,
        ),
    ],
)
class VoucherListAPIView(generics.ListAPIView):
    """
    Class-Based View listing issued `BumpVoucher` records, filterable by `keeper_address`, `contract_id`, and `redeemed`.
    GET /api/v1/attestation/vouchers
    """

    serializer_class = VoucherLogSerializer
    throttle_classes = [LazarusAnonRateThrottle, LazarusUserRateThrottle]

    def get_queryset(self):
        qs = VoucherLog.objects.select_related("keeper", "contract").all()
        keeper_addr = self.request.query_params.get("keeper_address")
        contract_id = self.request.query_params.get("contract_id")
        redeemed = self.request.query_params.get("redeemed")
        if keeper_addr:
            cleaned_keeper = validate_stellar_address(keeper_addr, "keeper_address")
            qs = qs.filter(keeper__keeper_address=cleaned_keeper)
        if contract_id:
            cleaned_cid = validate_soroban_contract_id(contract_id)
            qs = qs.filter(contract__contract_id=cleaned_cid)
        if redeemed is not None:
            is_redeemed = redeemed.strip().lower() in ("1", "true", "yes")
            qs = qs.filter(redeemed=is_redeemed)
        return qs


class VoucherBatchRedeemAPIView(APIView):
    """
    Class-Based View recording on-chain `record_bump_batch` redemption completion.
    POST /api/v1/attestation/vouchers/redeem
    """

    throttle_classes = [AttestationRateThrottle]

    @extend_schema(
        tags=["Attestation"],
        summary="Record batch redemption of up to 10 BumpVouchers",
        request=VoucherBatchRedeemRequestSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    @transaction.atomic
    def post(self, request: Request) -> Response:
        serializer = VoucherBatchRedeemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        keeper_address = serializer.validated_data["keeper_address"]
        nonces = serializer.validated_data["nonces"]

        keeper = get_object_or_404(
            KeeperRegistry.objects.select_for_update(),
            keeper_address=keeper_address,
        )
        unredeemed = list(
            VoucherLog.objects.select_for_update()
            .select_related("contract")
            .filter(
                keeper=keeper,
                nonce__in=nonces,
                redeemed=False,
            )
        )

        claimed_delta = Decimal("0")
        redeemed_nonces = []
        contracts_to_update = {}
        for voucher in unredeemed:
            voucher.redeemed = True
            voucher.save(update_fields=["redeemed"])
            claimed_delta += voucher.bounty_amount
            redeemed_nonces.append(voucher.nonce)

            contract = contracts_to_update.get(voucher.contract_id, voucher.contract)
            protocol_fee = (voucher.bounty_amount * Decimal("500")) // Decimal("10000")
            total_deduction = voucher.bounty_amount + protocol_fee
            if contract.escrow_balance > Decimal("0"):
                contract.escrow_balance = max(
                    Decimal("0"), contract.escrow_balance - total_deduction
                )
                contracts_to_update[voucher.contract_id] = contract

        for contract in contracts_to_update.values():
            contract.save(update_fields=["escrow_balance", "updated_at"])

        if claimed_delta > 0:
            keeper.total_bounties_claimed += claimed_delta
            keeper.save(update_fields=["total_bounties_claimed", "last_active_at"])

        logger.info(
            "Batch voucher redemption recorded keeper=%s nonces=%s claimed_stroops=%s",
            keeper.keeper_address,
            redeemed_nonces,
            claimed_delta,
        )
        return Response(
            {
                "keeper_address": keeper.keeper_address,
                "redeemed_nonces": redeemed_nonces,
                "bounties_claimed_stroops": int(claimed_delta),
                "total_bounties_claimed_stroops": int(keeper.total_bounties_claimed),
            },
            status=status.HTTP_200_OK,
        )
