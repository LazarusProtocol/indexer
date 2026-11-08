## Lazarus Indexer

[![CI](https://github.com/LazarusProtocol/indexer/actions/workflows/ci.yml/badge.svg)](https://github.com/LazarusProtocol/indexer/actions)

Storage key indexing, TTL monitoring, and cryptographic voucher attestation engine for the Lazarus Protocol on Stellar. Built with Django REST Framework, Celery, Redis, and PostgreSQL.

* **Production API:** [https://api.lazarusprotocol.online/](https://api.lazarusprotocol.online/)
* **Web Console:** [https://lazarusprotocol.online/](https://lazarusprotocol.online/)

---

### Core Architecture

#### 1. 4-Stage Hybrid Key Discovery
Tracks persistent storage entries, contract instances, and WASM bytecodes to detect expiration risks:
* **Analytical Bootstrap (Hubble / BigQuery):** Queries Stellar Hubble (`contract_data_current` + `ttl_current`) for bulk historical storage states.
* **RPC Catch-Up Replay:** Reconciles BigQuery microbatch latency by replaying recent closed ledgers via Stellar RPC `getLedgers`.
* **Deterministic Tree Walking:** Protocol-specific adapters (e.g., Blend) to discover composite storage keys and positions.
* **Live Ingestion:** Celery worker polling closed ledgers to keep TTL countdowns continuously updated.

#### 2. Cryptographic Attestation Engine
Validates keeper operations before issuing bounty claim vouchers:
* **On-Chain Verification:** Confirms `ExtendFootprintTTLOp` inclusion and validates achieved TTL compliance.
* **Atomic Nonce Allocation:** Prevents keeper race conditions and replay attacks using atomic database locks (`select_for_update`).
* **Canonical XDR Signing:** Generates domain-separated `LAZARUS_VOUCHER_V1` vouchers signed via Ed25519 / 2-of-3 FROST, redeemable on `lazarus_vault`.

---

### Development & Run

```bash
# Start PostgreSQL, Redis, Django API (:8000), and Celery worker
docker compose up -d --build

# Run test suite
python manage.py test -v 2
```
