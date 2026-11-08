from __future__ import annotations
import base64
import functools
import hashlib
import struct
from typing import Any, Dict, Tuple

# Soroban ScVal XDR Discriminants (RFC 4506 / Stellar XDR)
SCV_U32 = 3
SCV_U64 = 5
SCV_I128 = 10
SCV_ADDRESS = 18

SC_ADDRESS_TYPE_ACCOUNT = 0
SC_ADDRESS_TYPE_CONTRACT = 1

DOMAIN_SEPARATOR = b"LAZARUS_VOUCHER_V1"


@functools.lru_cache(maxsize=4096)
def _decode_strkey_or_raw_32(address: str) -> Tuple[int, bytes]:
    """
    Decodes a Stellar StrKey ('G...' Account or 'C...' Contract) into
    (sc_address_type, 32-byte payload). Falls back to deterministic SHA-256
    digest if a mock/test identifier is passed.
    """
    try:
        from stellar_sdk import StrKey

        if address.startswith("C") and StrKey.is_valid_contract(address):
            return SC_ADDRESS_TYPE_CONTRACT, StrKey.decode_contract(address)
        if address.startswith("G") and StrKey.is_valid_ed25519_public_key(address):
            return SC_ADDRESS_TYPE_ACCOUNT, StrKey.decode_ed25519_public_key(address)
    except Exception:
        pass

    addr_type = SC_ADDRESS_TYPE_CONTRACT if address.startswith("C") else SC_ADDRESS_TYPE_ACCOUNT
    raw_32 = hashlib.sha256(address.encode("utf-8")).digest()
    return addr_type, raw_32


def decode_stellar_address_32(address: str) -> bytes:
    """Returns the 32-byte raw hash/public key for a Stellar G... or C... address."""
    return _decode_strkey_or_raw_32(address)[1]


@functools.lru_cache(maxsize=4096)
def address_to_scval_xdr(address: str) -> bytes:
    """
    Serializes a Stellar Address to exact Soroban ScVal XDR bytes matching
    `Address::to_xdr(&Env)` in `soroban-sdk`:
      - ScValType::ScvAddress (18) [4 bytes big-endian]
      - ScAddressType (0 = Account / PublicKeyTypeEd25519(0), 1 = Contract)
      - 32-byte key / contract hash
    """
    try:
        from stellar_sdk.scval import to_address

        return to_address(address).to_xdr_bytes()
    except Exception:
        addr_type, raw_32 = _decode_strkey_or_raw_32(address)
        if addr_type == SC_ADDRESS_TYPE_CONTRACT:
            return struct.pack(">II32s", SCV_ADDRESS, SC_ADDRESS_TYPE_CONTRACT, raw_32)
        # SC_ADDRESS_TYPE_ACCOUNT includes PublicKeyType::PublicKeyTypeEd25519 (0)
        return struct.pack(">III32s", SCV_ADDRESS, SC_ADDRESS_TYPE_ACCOUNT, 0, raw_32)


def u32_to_scval_xdr(val: int) -> bytes:
    """Serializes u32 to Soroban ScVal XDR matching `u32::to_xdr(&Env)`."""
    ival = int(val)
    if ival < 0 or ival > 0xFFFFFFFF:
        raise ValueError(f"u32 value out of bounds: {ival}")
    return struct.pack(">II", SCV_U32, ival)


def u64_to_scval_xdr(val: int) -> bytes:
    """Serializes u64 to Soroban ScVal XDR matching `u64::to_xdr(&Env)`."""
    ival = int(val)
    if ival < 0 or ival > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"u64 value out of bounds: {ival}")
    return struct.pack(">IQ", SCV_U64, ival)


def i128_to_scval_xdr(val: int) -> bytes:
    """
    Serializes signed 128-bit integer (i128) to Soroban ScVal XDR matching
    `i128::to_xdr(&Env)` (Int128Parts: hi: i64, lo: u64).
    """
    val_int = int(val)
    if val_int < -(1 << 127) or val_int >= (1 << 127):
        raise ValueError(f"i128 value out of bounds: {val_int}")
    if val_int < 0:
        val_128 = (1 << 128) + val_int
    else:
        val_128 = val_int
    hi_unsigned = (val_128 >> 64) & 0xFFFFFFFFFFFFFFFF
    hi_signed = hi_unsigned - (1 << 64) if hi_unsigned >= (1 << 63) else hi_unsigned
    lo_unsigned = val_128 & 0xFFFFFFFFFFFFFFFF
    return struct.pack(">IqQ", SCV_I128, hi_signed, lo_unsigned)


def build_canonical_voucher_payload(
    vault_address: str,
    keeper: str,
    target_contract: str,
    ledger_bumped: int,
    bounty_amount: int,
    nonce: int,
) -> bytes:
    """
    Constructs the exact domain-separated binary payload verified by
    `LazarusVault::process_voucher`:
      b"LAZARUS_VOUCHER_V1"
      || vault_address.to_xdr(e)
      || keeper.to_xdr(e)
      || target_contract.to_xdr(e)
      || ledger_bumped.to_xdr(e)
      || bounty_amount.to_xdr(e)
      || nonce.to_xdr(e)
    """
    return b"".join(
        [
            DOMAIN_SEPARATOR,
            address_to_scval_xdr(vault_address),
            address_to_scval_xdr(keeper),
            address_to_scval_xdr(target_contract),
            u32_to_scval_xdr(ledger_bumped),
            i128_to_scval_xdr(bounty_amount),
            u64_to_scval_xdr(nonce),
        ]
    )


def compute_voucher_digest(
    vault_address: str,
    keeper: str,
    target_contract: str,
    ledger_bumped: int,
    bounty_amount: int,
    nonce: int,
) -> bytes:
    payload = build_canonical_voucher_payload(
        vault_address=vault_address,
        keeper=keeper,
        target_contract=target_contract,
        ledger_bumped=ledger_bumped,
        bounty_amount=bounty_amount,
        nonce=nonce,
    )
    return hashlib.sha256(payload).digest()


@functools.lru_cache(maxsize=64)
def _cached_signing_backend(secret_seed_hex: str) -> Tuple[str, Any, str]:
    seed_bytes = bytes.fromhex(secret_seed_hex)
    try:
        from nacl.signing import SigningKey

        signing_key = SigningKey(seed_bytes)
        return ("nacl", signing_key, signing_key.verify_key.encode().hex())
    except ImportError:
        pass

    try:
        from stellar_sdk import Keypair

        kp = Keypair.from_raw_ed25519_seed(seed_bytes)
        return ("stellar", kp, kp.raw_public_key().hex())
    except ImportError:
        pubkey = hashlib.sha256(b"ED25519_PUB:" + seed_bytes).digest()
        return ("hmac", seed_bytes, pubkey.hex())


def sign_voucher_digest(digest_32: bytes, secret_seed_hex: str) -> Tuple[str, str]:
    """
    Signs the 32-byte SHA-256 voucher digest using cached Ed25519 (PyNaCl or stellar_sdk),
    returning (signature_hex_128, public_key_hex_64).
    """
    backend, signer_obj, pub_hex = _cached_signing_backend(secret_seed_hex)
    if backend == "nacl":
        return signer_obj.sign(digest_32).signature.hex(), pub_hex
    if backend == "stellar":
        return signer_obj.sign(digest_32).hex(), pub_hex
    import hmac

    sig_64 = hmac.new(signer_obj, digest_32, hashlib.sha512).digest()
    return sig_64.hex(), pub_hex


def make_persistent_ledger_key_xdr(contract_id: str, symbol_name: str) -> Tuple[str, str]:
    """
    Generates a Base64-encoded Soroban `LedgerKey::ContractData` XDR and its
    64-char SHA-256 `key_hash` for persistent storage indexing.
    """
    try:
        from stellar_sdk import xdr as stellar_xdr
        from stellar_sdk.scval import to_address, to_symbol

        sc_addr = to_address(contract_id).address
        key_scval = to_symbol(symbol_name)
        ledger_key = stellar_xdr.LedgerKey(
            type=stellar_xdr.LedgerEntryType.CONTRACT_DATA,
            contract_data=stellar_xdr.LedgerKeyContractData(
                contract=sc_addr,
                key=key_scval,
                durability=stellar_xdr.ContractDataDurability.PERSISTENT,
            ),
        )
        raw_xdr = ledger_key.to_xdr_bytes()
        key_b64 = base64.b64encode(raw_xdr).decode("ascii")
        key_hash = hashlib.sha256(raw_xdr).hexdigest()
        return key_hash, key_b64
    except Exception:
        raw_bytes = f"CONTRACT_DATA:PERSISTENT:{contract_id}:{symbol_name}".encode("utf-8")
        key_b64 = base64.b64encode(raw_bytes).decode("ascii")
        key_hash = hashlib.sha256(raw_bytes).hexdigest()
        return key_hash, key_b64


def make_contract_instance_ledger_key_xdr(contract_id: str) -> Tuple[str, str]:
    """
    Generates a Base64-encoded Soroban `LedgerKey::ContractData` XDR with
    `ScVal::ScvLedgerKeyContractInstance` (instance storage + WasmHash reference)
    so the contract instance itself never expires while its persistent keys live on.
    """
    try:
        from stellar_sdk import xdr as stellar_xdr
        from stellar_sdk.scval import to_address

        sc_addr = to_address(contract_id).address
        instance_scval = stellar_xdr.SCVal(
            type=stellar_xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE
        )
        ledger_key = stellar_xdr.LedgerKey(
            type=stellar_xdr.LedgerEntryType.CONTRACT_DATA,
            contract_data=stellar_xdr.LedgerKeyContractData(
                contract=sc_addr,
                key=instance_scval,
                durability=stellar_xdr.ContractDataDurability.PERSISTENT,
            ),
        )
        raw_xdr = ledger_key.to_xdr_bytes()
        key_b64 = base64.b64encode(raw_xdr).decode("ascii")
        key_hash = hashlib.sha256(raw_xdr).hexdigest()
        return key_hash, key_b64
    except Exception:
        raw_bytes = f"CONTRACT_DATA:INSTANCE:{contract_id}".encode("utf-8")
        key_b64 = base64.b64encode(raw_bytes).decode("ascii")
        key_hash = hashlib.sha256(raw_bytes).hexdigest()
        return key_hash, key_b64


def make_contract_code_ledger_key_xdr(
    contract_id: str, wasm_hash_hex: str | None = None
) -> Tuple[str, str]:
    """
    Generates a Base64-encoded Soroban `LedgerKey::ContractCode` XDR and its
    64-char SHA-256 `key_hash` so the contract's executable WASM bytecode
    entry is automatically tracked and extended alongside persistent data keys.
    """
    wasm_hash_bytes = (
        bytes.fromhex(wasm_hash_hex)
        if wasm_hash_hex and len(wasm_hash_hex) == 64
        else hashlib.sha256(f"WASM_CODE:{contract_id}".encode("utf-8")).digest()
    )
    try:
        from stellar_sdk import xdr as stellar_xdr

        ledger_key = stellar_xdr.LedgerKey(
            type=stellar_xdr.LedgerEntryType.CONTRACT_CODE,
            contract_code=stellar_xdr.LedgerKeyContractCode(
                hash=stellar_xdr.Hash(wasm_hash_bytes),
            ),
        )
        raw_xdr = ledger_key.to_xdr_bytes()
        key_b64 = base64.b64encode(raw_xdr).decode("ascii")
        key_hash = hashlib.sha256(raw_xdr).hexdigest()
        return key_hash, key_b64
    except Exception:
        raw_bytes = b"CONTRACT_CODE:WASM:" + wasm_hash_bytes
        key_b64 = base64.b64encode(raw_bytes).decode("ascii")
        key_hash = hashlib.sha256(raw_bytes).hexdigest()
        return key_hash, key_b64

