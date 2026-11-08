from __future__ import annotations
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from .models import KeeperRegistry, MonitoredContract, VoucherLog
from .xdr_utils import build_canonical_voucher_payload, compute_voucher_digest


class LazarusIndexerCBVTestSuite(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.client = APIClient()
        self.contract_id = "CCBLENDPOOLCONTRACT0000000000000000000000000000000000001"
        self.client_addr = "GCLIENTADDRESS000000000000000000000000000000000000000001"
        self.keeper_addr = "GKEEPERNODEADDRESS00000000000000000000000000000000000001"

    def test_health_check_cbv(self) -> None:
        resp = self.client.get(reverse("health-check"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "ok")

    def test_openapi_schema_swagger_ui_and_redoc_endpoints(self) -> None:
        # 1. OpenAPI 3.0 schema endpoint
        schema_resp = self.client.get(reverse("schema"))
        self.assertEqual(schema_resp.status_code, status.HTTP_200_OK)
        self.assertIn(b"Lazarus Protocol", schema_resp.content)
        self.assertIn(b"/api/v1/attestation/voucher", schema_resp.content)

        # 2. Swagger UI endpoint (/api/docs/)
        swagger_resp = self.client.get(reverse("swagger-ui"))
        self.assertEqual(swagger_resp.status_code, status.HTTP_200_OK)
        self.assertIn(b"swagger-ui", swagger_resp.content)

        # 3. ReDoc endpoint (/api/redoc/) must pin redoc@2.1.5 (redoc@latest 3.x breaks <redoc> tag)
        redoc_resp = self.client.get(reverse("redoc"))
        self.assertEqual(redoc_resp.status_code, status.HTTP_200_OK)
        self.assertIn(b"redoc@2.1.5/bundles/redoc.standalone.js", redoc_resp.content)
        self.assertNotIn(b"redoc@latest", redoc_resp.content)

    def test_contract_enrollment_and_structured_logging(self) -> None:
        payload = {
            "contract_id": self.contract_id,
            "client_address": self.client_addr,
            "escrow_balance": "10000000",
            "fixed_bounty": "1000000",
            "min_ledger_threshold": 20000,
            "bump_target": 500000,
            "protocol_type": "blend",
        }
        with self.assertLogs("lazarus.api", level="INFO") as api_logs, self.assertLogs(
            "lazarus.pipeline", level="INFO"
        ) as pipe_logs:
            resp = self.client.post(
                reverse("contract-list-create"), payload, format="json"
            )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        self.assertTrue(
            any("Enrolled new MonitoredContract" in msg for msg in api_logs.output)
        )
        self.assertTrue(
            any("Completed full hybrid bootstrap" in msg for msg in pipe_logs.output)
        )

        contract = MonitoredContract.objects.get(contract_id=self.contract_id)
        # Pipeline A (2 persistent + 1 instance + 1 contract_code = 4 Hubble keys)
        # + Pipeline C (4 Blend keys) = 8 keys indexed
        self.assertEqual(contract.keys.count(), 8)
        self.assertEqual(
            contract.keys.filter(durability="instance").count(), 1
        )
        self.assertEqual(
            contract.keys.filter(durability="contract_code").count(), 1
        )
        self.assertEqual(contract.last_bootstrapped_ledger, 500_150)

    def test_expiring_keys_and_ttl_heatmap_cbvs(self) -> None:
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "escrow_balance": "10000000",
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
                "protocol_type": "generic",
            },
            format="json",
        )

        exp_resp = self.client.get(
            reverse("expiring-keys"), {"current_ledger": 500_000}
        )
        self.assertEqual(exp_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(exp_resp.data["count"], 2)
        first_key = exp_resp.data["results"][0]
        self.assertTrue(first_key["is_emergency"])
        self.assertEqual(first_key["remaining_ledgers"], 1_500)

        heatmap_resp = self.client.get(
            reverse("diagnostics-heatmap"), {"current_ledger": 500_000}
        )
        self.assertEqual(heatmap_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(heatmap_resp.data["summary"]["critical_count"], 1)
        self.assertEqual(heatmap_resp.data["summary"]["warming_count"], 3)
        self.assertEqual(heatmap_resp.data["summary"]["total_monitored_keys"], 4)

    def test_voucher_attestation_and_batch_redemption_cbvs(self) -> None:
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "escrow_balance": "10000000",
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
            },
            format="json",
        )

        pending_resp = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "PENDING_TX_001",
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
            },
            format="json",
        )
        self.assertEqual(pending_resp.status_code, status.HTTP_202_ACCEPTED)

        v1_resp = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "a" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
                "ledger_bumped": 550_000,
            },
            format="json",
        )
        self.assertEqual(v1_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(v1_resp.data["voucher"]["nonce"], 1)
        self.assertEqual(v1_resp.data["voucher"]["bounty_amount"], 1_000_000)
        self.assertEqual(len(v1_resp.data["signature"]), 128)

        idemp_resp = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "a" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
            },
            format="json",
        )
        self.assertEqual(idemp_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(idemp_resp.data["voucher"]["nonce"], 1)

        v2_resp = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "b" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
                "ledger_bumped": 560_000,
            },
            format="json",
        )
        self.assertEqual(v2_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(v2_resp.data["voucher"]["nonce"], 2)

        redeem_resp = self.client.post(
            reverse("attestation-voucher-redeem"),
            {
                "keeper_address": self.keeper_addr,
                "nonces": [1, 2],
            },
            format="json",
        )
        self.assertEqual(redeem_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(redeem_resp.data["bounties_claimed_stroops"], 2_000_000)

        keeper = KeeperRegistry.objects.get(keeper_address=self.keeper_addr)
        self.assertEqual(keeper.current_nonce, 2)
        self.assertEqual(int(keeper.total_bounties_claimed), 2_000_000)
        self.assertEqual(VoucherLog.objects.filter(redeemed=True).count(), 2)

    @override_settings(
        REST_FRAMEWORK={
            "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
            "DEFAULT_THROTTLE_CLASSES": [
                "indexer.throttles.LazarusAnonRateThrottle",
            ],
            "DEFAULT_THROTTLE_RATES": {
                "anon": "100/min",
                "bootstrap": "2/min",
            },
        }
    )
    def test_rate_limiting_enforces_429_too_many_requests(self) -> None:
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "escrow_balance": "10000000",
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
            },
            format="json",
        )
        bootstrap_url = reverse(
            "contract-bootstrap", kwargs={"contract_id": self.contract_id}
        )
        # First 2 requests within 2/min limit succeed
        r1 = self.client.post(bootstrap_url, {}, format="json")
        r2 = self.client.post(bootstrap_url, {}, format="json")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)

        # 3rd request exceeds BootstrapRateThrottle -> 429 Too Many Requests
        r3 = self.client.post(bootstrap_url, {}, format="json")
        self.assertEqual(r3.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_input_validation_and_sanitization_rejects_malicious_payloads(self) -> None:
        # 1. Invalid contract_id (not starting with C / wrong length / XSS injection)
        bad_contract_resp = self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": "<script>alert(1)</script>",
                "client_address": self.client_addr,
                "fixed_bounty": "1000000",
            },
            format="json",
        )
        self.assertEqual(bad_contract_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("contract_id", bad_contract_resp.data)

        # 2. i128 overflow and bump_target <= min_ledger_threshold rejected
        overflow_resp = self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "fixed_bounty": str((1 << 127) + 100),  # Exceeds i128::MAX
                "min_ledger_threshold": 50000,
                "bump_target": 20000,
            },
            format="json",
        )
        self.assertEqual(overflow_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("fixed_bounty", overflow_resp.data)

        # Enroll valid contract for key & attestation validation checks
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
            },
            format="json",
        )

        # 3. Invalid Base64 key_xdr and non-hex key_hash rejected
        bad_key_resp = self.client.post(
            reverse("contract-keys", kwargs={"contract_id": self.contract_id}),
            {
                "key_hash": "not-a-valid-sha256-hex",
                "key_xdr": "!!!not_base64!!!",
                "durability": "temporary",
                "live_until_ledger_seq": 500000,
            },
            format="json",
        )
        self.assertEqual(bad_key_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("key_hash", bad_key_resp.data)
        self.assertIn("key_xdr", bad_key_resp.data)
        self.assertIn("durability", bad_key_resp.data)

        # 4. Malformed query parameter (?current_ledger=abc or negative) returns 400 instead of 500
        bad_query_resp = self.client.get(
            reverse("expiring-keys"), {"current_ledger": "DROP TABLE"}
        )
        self.assertEqual(bad_query_resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 5. Duplicate nonces in batch redemption rejected
        dup_nonce_resp = self.client.post(
            reverse("attestation-voucher-redeem"),
            {
                "keeper_address": self.keeper_addr,
                "nonces": [1, 1, 2],
            },
            format="json",
        )
        self.assertEqual(dup_nonce_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nonces", dup_nonce_resp.data)

    def test_redis_caching_hit_miss_and_signal_invalidation(self) -> None:
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
            },
            format="json",
        )

        # 1st call to expiring keys at ledger 500,000 -> Cache MISS
        r1 = self.client.get(reverse("expiring-keys"), {"current_ledger": 500_000})
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r1["X-Cache"], "MISS")

        # 2nd call to expiring keys at ledger 500,000 -> Cache HIT
        r2 = self.client.get(reverse("expiring-keys"), {"current_ledger": 500_000})
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(r2["X-Cache"], "HIT")

        # 1st call to heatmap at ledger 500,000 -> Cache MISS, 2nd call -> Cache HIT
        h1 = self.client.get(reverse("diagnostics-heatmap"), {"current_ledger": 500_000})
        h2 = self.client.get(reverse("diagnostics-heatmap"), {"current_ledger": 500_000})
        self.assertEqual(h1["X-Cache"], "MISS")
        self.assertEqual(h2["X-Cache"], "HIT")

        # Issuing an attestation voucher mutates StorageKeyIndex & VoucherLog,
        # triggering Django signals to bump the Redis state_version key!
        self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "c" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
                "ledger_bumped": 550_000,
            },
            format="json",
        )

        # Subsequent call at ledger 500,000 is now a Cache MISS with fresh TTL state
        r3 = self.client.get(reverse("expiring-keys"), {"current_ledger": 500_000})
        self.assertEqual(r3["X-Cache"], "MISS")

        # Health check reports database and redis_cache status
        health_resp = self.client.get(reverse("health-check"))
        self.assertEqual(health_resp.data["infrastructure"]["database"], "ok")
        self.assertEqual(health_resp.data["infrastructure"]["redis_cache"], "ok")

    def test_canonical_xdr_domain_separation(self) -> None:
        payload = build_canonical_voucher_payload(
            vault_address="CCLAZARUSVAULTCONTRACTADDRESS0000000000000000000000000000",
            keeper=self.keeper_addr,
            target_contract=self.contract_id,
            ledger_bumped=500_000,
            bounty_amount=1_000_000,
            nonce=1,
        )
        self.assertTrue(payload.startswith(b"LAZARUS_VOUCHER_V1"))
        digest = compute_voucher_digest(
            vault_address="CCLAZARUSVAULTCONTRACTADDRESS0000000000000000000000000000",
            keeper=self.keeper_addr,
            target_contract=self.contract_id,
            ledger_bumped=500_000,
            bounty_amount=1_000_000,
            nonce=1,
        )
        self.assertEqual(len(digest), 32)

    def test_audit_hubble_sql_pagination_and_tx_inclusion_ledger(self) -> None:
        from .services import HUBBLE_BIGQUERY_SQL

        # 1. Verify Hubble BigQuery SQL joins ttl_current and selects snapshot_ledger from history_ledgers
        self.assertIn("crypto-stellar.crypto_stellar_dbt.ttl_current", HUBBLE_BIGQUERY_SQL)
        self.assertIn("crypto-stellar.crypto_stellar.history_ledgers", HUBBLE_BIGQUERY_SQL)
        self.assertIn("ContractDataDurabilityInstance", HUBBLE_BIGQUERY_SQL)

        # 2. Enroll contract and test multi-page RPC catch-up (> 200 ledgers span = 550 ledgers -> 6 pages of 100)
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
            },
            format="json",
        )
        boot_resp = self.client.post(
            reverse("contract-bootstrap", kwargs={"contract_id": self.contract_id}),
            {"snapshot_ledger": 500_000, "current_ledger": 500_550},
            format="json",
        )
        self.assertEqual(boot_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(boot_resp.data["rpc_reconciliation"]["ledgers_replayed"], 550)
        self.assertEqual(boot_resp.data["rpc_reconciliation"]["pages_fetched"], 6)

        # 3. Verify tx_inclusion_ledger + bump_target compliance in attestation
        reject_resp = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "d" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
                "tx_inclusion_ledger": 100_000,
                "ledger_bumped": 550_000,  # < 100,000 + 500,000 (600,000)
            },
            format="json",
        )
        self.assertEqual(reject_resp.status_code, status.HTTP_400_BAD_REQUEST)

        accept_resp = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "e" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
                "tx_inclusion_ledger": 100_000,
                "ledger_bumped": 600_000,  # >= 100,000 + 500,000
            },
            format="json",
        )
        self.assertEqual(accept_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(accept_resp.data["voucher"]["ledger_bumped"], 600_000)

    def test_seed_demo_management_command_populates_three_tier_heatmap_and_vouchers(self) -> None:
        from django.core.management import call_command

        call_command("seed_demo", reset=True)
        self.assertEqual(MonitoredContract.objects.count(), 3)
        self.assertGreaterEqual(KeeperRegistry.objects.count(), 2)
        self.assertGreaterEqual(VoucherLog.objects.count(), 6)

        heatmap_resp = self.client.get(
            reverse("diagnostics-heatmap"), {"current_ledger": 500_000}
        )
        self.assertEqual(heatmap_resp.status_code, status.HTTP_200_OK)
        summary = heatmap_resp.data["summary"]
        self.assertGreater(summary["healthy_count"], 0)
        self.assertGreater(summary["warming_count"], 0)
        self.assertGreater(summary["critical_count"], 0)

    def test_contract_update_requires_mandatory_client_address_authority(self) -> None:
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "escrow_balance": "10000000",
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
            },
            format="json",
        )
        detail_url = reverse(
            "contract-detail", kwargs={"contract_id": self.contract_id}
        )

        # 1. Missing X-Client-Address / client_address rejected with 403 Forbidden
        no_auth_resp = self.client.patch(
            detail_url, {"is_active": False}, format="json"
        )
        self.assertEqual(no_auth_resp.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Wrong X-Client-Address rejected with 403 Forbidden
        wrong_auth_resp = self.client.patch(
            detail_url,
            {"is_active": False},
            format="json",
            HTTP_X_CLIENT_ADDRESS=self.keeper_addr,
        )
        self.assertEqual(wrong_auth_resp.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Matching X-Client-Address succeeds
        ok_resp = self.client.patch(
            detail_url,
            {"is_active": False},
            format="json",
            HTTP_X_CLIENT_ADDRESS=self.client_addr,
        )
        self.assertEqual(ok_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(ok_resp.data["is_active"])

    @override_settings(DEBUG=True, CSRF_TRUSTED_ORIGINS=["http://localhost:5173"])
    def test_security_cors_prefix_spoofing_key_auth_and_escrow_accounting(self) -> None:
        import base64
        import hashlib
        from .services import AttestationError, AttestationVerifierAndSigner

        # 1. CORS subdomain prefix spoofing ("http://localhost:5173.evil.com") must NOT be reflected
        spoof_resp = self.client.get(
            reverse("health-check"),
            HTTP_ORIGIN="http://localhost:5173.evil.com",
        )
        self.assertEqual(
            spoof_resp["Access-Control-Allow-Origin"], "http://localhost:5173"
        )

        # Legitimate localhost port in DEBUG=True is allowed
        legit_resp = self.client.get(
            reverse("health-check"),
            HTTP_ORIGIN="http://localhost:3000",
        )
        self.assertEqual(
            legit_resp["Access-Control-Allow-Origin"], "http://localhost:3000"
        )

        # 2. Enroll contract with exact escrow for 1 voucher (1,050,000 stroops = 1M bounty + 5% fee)
        self.client.post(
            reverse("contract-list-create"),
            {
                "contract_id": self.contract_id,
                "client_address": self.client_addr,
                "escrow_balance": "1500000",
                "fixed_bounty": "1000000",
                "min_ledger_threshold": 20000,
                "bump_target": 500000,
            },
            format="json",
        )

        keys_url = reverse("contract-keys", kwargs={"contract_id": self.contract_id})
        raw_xdr = b"VALID_KEY_XDR_BYTES_FOR_AUDIT_TEST"
        valid_b64 = base64.b64encode(raw_xdr).decode("ascii")
        valid_hash = hashlib.sha256(raw_xdr).hexdigest()

        # 3. Mismatched key_hash vs key_xdr rejected with 400 Bad Request
        mismatch_resp = self.client.post(
            keys_url,
            {
                "key_hash": "f" * 64,
                "key_xdr": valid_b64,
                "durability": "persistent",
                "live_until_ledger_seq": 600000,
            },
            format="json",
            HTTP_X_CLIENT_ADDRESS=self.client_addr,
        )
        self.assertEqual(mismatch_resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. Unauthenticated storage key injection rejected with 403 Forbidden
        unauth_key_resp = self.client.post(
            keys_url,
            {
                "key_hash": valid_hash,
                "key_xdr": valid_b64,
                "durability": "persistent",
                "live_until_ledger_seq": 600000,
            },
            format="json",
        )
        self.assertEqual(unauth_key_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Authorized storage key creation succeeds with 201 Created
        auth_key_resp = self.client.post(
            keys_url,
            {
                "key_hash": valid_hash,
                "key_xdr": valid_b64,
                "durability": "persistent",
                "live_until_ledger_seq": 600000,
            },
            format="json",
            HTTP_X_CLIENT_ADDRESS=self.client_addr,
        )
        self.assertEqual(auth_key_resp.status_code, status.HTTP_201_CREATED)

        # 5. Malformed XDR envelope fails closed in _validate_envelope_xdr
        with self.assertRaises(AttestationError):
            AttestationVerifierAndSigner._validate_envelope_xdr(
                "NOT_A_STELLAR_ENVELOPE_XDR", self.keeper_addr
            )

        # 6. Issue and redeem voucher 1 -> escrow_balance drops from 1,500,000 to 450,000 (< 1,000,000 fixed_bounty)
        v1 = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "a" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
                "ledger_bumped": 550_000,
            },
            format="json",
        )
        self.assertEqual(v1.status_code, status.HTTP_200_OK)

        self.client.post(
            reverse("attestation-voucher-redeem"),
            {"keeper_address": self.keeper_addr, "nonces": [1]},
            format="json",
        )
        contract = MonitoredContract.objects.get(contract_id=self.contract_id)
        self.assertEqual(int(contract.escrow_balance), 450_000)

        # Subsequent voucher request against depleted escrow (450,000 < 1,000,000) is rejected with 400
        v2_depleted = self.client.post(
            reverse("attestation-voucher"),
            {
                "tx_hash": "b" * 64,
                "contract_id": self.contract_id,
                "keeper_address": self.keeper_addr,
                "ledger_bumped": 560_000,
            },
            format="json",
        )
        self.assertEqual(v2_depleted.status_code, status.HTTP_400_BAD_REQUEST)



