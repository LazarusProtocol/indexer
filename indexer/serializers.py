from __future__ import annotations
import base64
import hashlib
from rest_framework import serializers
from .models import KeeperRegistry, MonitoredContract, StorageKeyIndex, VoucherLog
from .validators import (
    MAX_TTL_LEDGERS,
    MAX_U32,
    MAX_U64,
    sanitize_string,
    validate_base64_xdr,
    validate_i128_amount,
    validate_sha256_hex,
    validate_soroban_contract_id,
    validate_stellar_address,
    validate_tx_hash,
)


class StorageKeyIndexSerializer(serializers.ModelSerializer):
    contract_id = serializers.CharField(source="contract.contract_id", read_only=True)
    bump_target = serializers.IntegerField(source="contract.bump_target", read_only=True)
    fixed_bounty = serializers.DecimalField(
        source="contract.fixed_bounty", max_digits=38, decimal_places=0, read_only=True
    )

    class Meta:
        model = StorageKeyIndex
        fields = [
            "id",
            "contract_id",
            "key_hash",
            "key_xdr",
            "durability",
            "live_until_ledger_seq",
            "last_extended_ledger",
            "bump_target",
            "fixed_bounty",
            "updated_at",
        ]
        read_only_fields = ["id", "contract_id", "bump_target", "fixed_bounty", "updated_at"]

    def validate_key_hash(self, value: str) -> str:
        return validate_sha256_hex(value, "key_hash")

    def validate_key_xdr(self, value: str) -> str:
        return validate_base64_xdr(value, "key_xdr")

    def validate_durability(self, value: str) -> str:
        cleaned = sanitize_string(value, "durability").lower()
        if cleaned not in ("persistent", "instance", "contract_code"):
            raise serializers.ValidationError(
                "durability must be 'persistent', 'instance', or 'contract_code' (temporary storage cannot be extended by keepers)."
            )
        return cleaned

    def validate_live_until_ledger_seq(self, value: int) -> int:
        if value < 1 or value > MAX_U32:
            raise serializers.ValidationError(
                f"live_until_ledger_seq must be within u32 bounds (1..{MAX_U32})."
            )
        return value

    def validate(self, attrs):
        key_hash = attrs.get("key_hash")
        key_xdr = attrs.get("key_xdr")
        if key_hash and key_xdr:
            expected_hash = hashlib.sha256(base64.b64decode(key_xdr)).hexdigest()
            if key_hash.lower() != expected_hash.lower():
                raise serializers.ValidationError(
                    {
                        "key_hash": (
                            f"key_hash ({key_hash}) does not match SHA-256 of decoded key_xdr ({expected_hash})."
                        )
                    }
                )
        return attrs


class MonitoredContractSerializer(serializers.ModelSerializer):
    indexed_keys_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = MonitoredContract
        fields = [
            "id",
            "contract_id",
            "client_address",
            "escrow_balance",
            "fixed_bounty",
            "min_ledger_threshold",
            "bump_target",
            "protocol_type",
            "is_active",
            "last_bootstrapped_ledger",
            "indexed_keys_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "indexed_keys_count", "created_at", "updated_at"]

    def get_indexed_keys_count(self, obj: MonitoredContract) -> int:
        annotated = getattr(obj, "indexed_keys_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.keys.count()

    def validate_contract_id(self, value: str) -> str:
        return validate_soroban_contract_id(value)

    def validate_client_address(self, value: str) -> str:
        return validate_stellar_address(value, "client_address")

    def validate_fixed_bounty(self, value):
        return validate_i128_amount(value, "fixed_bounty", allow_zero=False)

    def validate_escrow_balance(self, value):
        return validate_i128_amount(value, "escrow_balance", allow_zero=True)

    def validate_min_ledger_threshold(self, value: int) -> int:
        if value < 100 or value > MAX_TTL_LEDGERS:
            raise serializers.ValidationError(
                f"min_ledger_threshold must be between 100 and {MAX_TTL_LEDGERS} ledgers."
            )
        return value

    def validate_bump_target(self, value: int) -> int:
        if value < 1_000 or value > MAX_TTL_LEDGERS:
            raise serializers.ValidationError(
                f"bump_target must be between 1,000 and {MAX_TTL_LEDGERS} ledgers (~180 days ceiling)."
            )
        return value

    def validate(self, attrs):
        if self.instance is not None:
            if (
                "client_address" in attrs
                and attrs["client_address"].upper() != self.instance.client_address.upper()
            ):
                raise serializers.ValidationError(
                    {"client_address": "client_address is immutable after contract registration."}
                )
        min_thresh = attrs.get(
            "min_ledger_threshold",
            getattr(self.instance, "min_ledger_threshold", 20_000),
        )
        bump_target = attrs.get(
            "bump_target",
            getattr(self.instance, "bump_target", 500_000),
        )
        if bump_target <= min_thresh:
            raise serializers.ValidationError(
                {"bump_target": "bump_target must be strictly greater than min_ledger_threshold."}
            )
        return attrs


class KeeperRegistrySerializer(serializers.ModelSerializer):
    vouchers_issued_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = KeeperRegistry
        fields = [
            "id",
            "keeper_address",
            "current_nonce",
            "total_bounties_claimed",
            "is_active",
            "vouchers_issued_count",
            "last_active_at",
        ]
        read_only_fields = [
            "id",
            "current_nonce",
            "total_bounties_claimed",
            "vouchers_issued_count",
            "last_active_at",
        ]

    def get_vouchers_issued_count(self, obj: KeeperRegistry) -> int:
        annotated = getattr(obj, "vouchers_issued_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.vouchers.count()

    def validate_keeper_address(self, value: str) -> str:
        return validate_stellar_address(value, "keeper_address")


class VoucherLogSerializer(serializers.ModelSerializer):
    keeper_address = serializers.CharField(source="keeper.keeper_address", read_only=True)
    target_contract = serializers.CharField(source="contract.contract_id", read_only=True)

    class Meta:
        model = VoucherLog
        fields = [
            "id",
            "tx_hash",
            "keeper_address",
            "target_contract",
            "ledger_bumped",
            "bounty_amount",
            "nonce",
            "signature",
            "redeemed",
            "created_at",
        ]
        read_only_fields = fields


class VoucherAttestationRequestSerializer(serializers.Serializer):
    tx_hash = serializers.CharField(max_length=64)
    contract_id = serializers.CharField(max_length=56)
    keeper_address = serializers.CharField(max_length=56)
    ledger_bumped = serializers.IntegerField(required=False, min_value=1, max_value=MAX_U32)
    tx_inclusion_ledger = serializers.IntegerField(required=False, min_value=1, max_value=MAX_U32)
    key_hash = serializers.CharField(max_length=64, required=False)

    def validate_tx_hash(self, value: str) -> str:
        return validate_tx_hash(value)

    def validate_contract_id(self, value: str) -> str:
        return validate_soroban_contract_id(value)

    def validate_keeper_address(self, value: str) -> str:
        return validate_stellar_address(value, "keeper_address")

    def validate_key_hash(self, value: str) -> str:
        return validate_sha256_hex(value, "key_hash")


class VoucherBatchRedeemRequestSerializer(serializers.Serializer):
    keeper_address = serializers.CharField(max_length=56)
    nonces = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=MAX_U64),
        min_length=1,
        max_length=10,
    )

    def validate_keeper_address(self, value: str) -> str:
        return validate_stellar_address(value, "keeper_address")

    def validate_nonces(self, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Duplicate nonces in batch request are not allowed.")
        return sorted(value)


class ContractBootstrapRequestSerializer(serializers.Serializer):
    snapshot_ledger = serializers.IntegerField(
        required=False, default=500_000, min_value=1, max_value=MAX_U32
    )
    current_ledger = serializers.IntegerField(
        required=False, min_value=1, max_value=MAX_U32
    )

    def validate(self, attrs):
        snap = attrs.get("snapshot_ledger", 500_000)
        curr = attrs.get("current_ledger", snap + 150)
        if curr < snap:
            raise serializers.ValidationError(
                {"current_ledger": "current_ledger must be >= snapshot_ledger."}
            )
        attrs["current_ledger"] = curr
        return attrs
