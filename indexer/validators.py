from __future__ import annotations
import base64
import html
import re
from decimal import Decimal
from typing import Any
from rest_framework import serializers

# Soroban / Stellar Protocol Bounds
MAX_U32 = (1 << 32) - 1
MAX_U64 = (1 << 64) - 1
MAX_I128 = (1 << 127) - 1  # 170141183460469231731687303715884105727
MAX_TTL_LEDGERS = 3_110_400  # ~180 days max Soroban TTL ceiling
MAX_KEY_XDR_BYTES = 4096

# Strict Format Patterns
CONTRACT_STRKEY_RE = re.compile(r"^C[A-Z0-9]{55}$")
ACCOUNT_OR_CONTRACT_STRKEY_RE = re.compile(r"^[GC][A-Z0-9]{55}$")
HEX_64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PENDING_TX_RE = re.compile(r"^PENDING_[A-Za-z0-9_]{1,56}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_string(value: str, field_name: str = "value") -> str:
    """
    Strips leading/trailing whitespace, rejects null bytes and ASCII control
    characters, and rejects HTML/script tags to prevent injection.
    """
    if not isinstance(value, str):
        raise serializers.ValidationError(f"{field_name} must be a string.")
    cleaned = value.strip()
    if CONTROL_CHARS_RE.search(cleaned):
        raise serializers.ValidationError(
            f"{field_name} contains disallowed control characters."
        )
    if "<" in cleaned or ">" in cleaned or cleaned != html.escape(cleaned, quote=False):
        raise serializers.ValidationError(
            f"{field_name} contains disallowed HTML or special characters."
        )
    return cleaned


def validate_soroban_contract_id(value: str) -> str:
    cleaned = sanitize_string(value, "contract_id").upper()
    if not CONTRACT_STRKEY_RE.match(cleaned):
        raise serializers.ValidationError(
            "contract_id must be a valid 56-character Soroban Contract ID starting with 'C'."
        )
    return cleaned


def validate_stellar_address(value: str, field_name: str = "address") -> str:
    cleaned = sanitize_string(value, field_name).upper()
    if not ACCOUNT_OR_CONTRACT_STRKEY_RE.match(cleaned):
        raise serializers.ValidationError(
            f"{field_name} must be a valid 56-character Stellar address starting with 'G' or 'C'."
        )
    return cleaned


def validate_sha256_hex(value: str, field_name: str = "key_hash") -> str:
    cleaned = sanitize_string(value, field_name).lower()
    if not HEX_64_RE.match(cleaned):
        raise serializers.ValidationError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest."
        )
    return cleaned


def validate_tx_hash(value: str) -> str:
    cleaned = sanitize_string(value, "tx_hash")
    if PENDING_TX_RE.match(cleaned.upper()):
        return cleaned.upper()
    if not HEX_64_RE.match(cleaned):
        raise serializers.ValidationError(
            "tx_hash must be a 64-character hexadecimal transaction hash."
        )
    return cleaned.lower()


def validate_base64_xdr(value: str, field_name: str = "key_xdr") -> str:
    cleaned = sanitize_string(value, field_name)
    try:
        raw = base64.b64decode(cleaned.encode("ascii"), validate=True)
    except Exception as exc:
        raise serializers.ValidationError(
            f"{field_name} must be valid Base64-encoded XDR."
        ) from exc
    if len(raw) == 0:
        raise serializers.ValidationError(f"{field_name} cannot be empty.")
    if len(raw) > MAX_KEY_XDR_BYTES:
        raise serializers.ValidationError(
            f"{field_name} exceeds maximum allowed decoded size ({MAX_KEY_XDR_BYTES} bytes)."
        )
    # Return canonical Base64 representation
    return base64.b64encode(raw).decode("ascii")


def validate_i128_amount(
    value: Decimal | int,
    field_name: str,
    allow_zero: bool = False,
) -> Decimal:
    int_val = int(value)
    if allow_zero:
        if int_val < 0:
            raise serializers.ValidationError(f"{field_name} must be >= 0.")
    else:
        if int_val <= 0:
            raise serializers.ValidationError(
                f"{field_name} must be strictly positive (i128 > 0)."
            )
    if int_val > MAX_I128:
        raise serializers.ValidationError(
            f"{field_name} exceeds signed 128-bit integer maximum (i128::MAX)."
        )
    return Decimal(int_val)


def parse_positive_u32_param(
    raw_value: Any,
    param_name: str,
    default: int | None = None,
) -> int:
    if raw_value is None or raw_value == "":
        if default is not None:
            return default
        raise serializers.ValidationError({param_name: f"{param_name} is required."})
    try:
        val = int(str(raw_value).strip())
    except (ValueError, TypeError) as exc:
        raise serializers.ValidationError(
            {param_name: f"{param_name} must be a valid integer."}
        ) from exc
    if val < 1 or val > MAX_U32:
        raise serializers.ValidationError(
            {param_name: f"{param_name} must be between 1 and {MAX_U32}."}
        )
    return val
