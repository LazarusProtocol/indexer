from __future__ import annotations

from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction

from indexer.models import KeeperRegistry, MonitoredContract, StorageKeyIndex, VoucherLog
from indexer.services import AttestationVerifierAndSigner, HybridDiscoveryPipeline
from indexer.xdr_utils import make_persistent_ledger_key_xdr

DEMO_CLIENT_ADDRESS = "GCLIENTADDRESS000000000000000000000000000000000000000001"
DEMO_KEEPER_PRIMARY = "GKEEPERNODEADDRESS00000000000000000000000000000000000001"
DEMO_KEEPER_SECONDARY = "GKEEPERNODEADDRESS00000000000000000000000000000000000002"

DEMO_CONTRACTS = [
    {
        "contract_id": "CCBLENDPOOLCONTRACT0000000000000000000000000000000000001",
        "protocol_type": "blend",
        "escrow_balance": Decimal("5000000000"),  # 500.00 USDC
        "fixed_bounty": Decimal("1000000"),       # 0.10 USDC
        "min_ledger_threshold": 20_000,
        "bump_target": 500_000,
        "extra_keys": [
            ("Positions_UserA", "persistent", 500_000 + 850_000),  # Healthy (> 30d)
            ("Positions_UserB", "persistent", 500_000 + 920_000),  # Healthy (> 30d)
            ("Positions_UserC", "persistent", 500_000 + 1_200),    # Critical (<= 2,000)
        ],
    },
    {
        "contract_id": "CCSOROSWAPAMMPAIRCONTRACT0000000000000000000000000000002",
        "protocol_type": "soroswap",
        "escrow_balance": Decimal("2500000000"),  # 250.00 USDC
        "fixed_bounty": Decimal("1000000"),       # 0.10 USDC
        "min_ledger_threshold": 20_000,
        "bump_target": 500_000,
        "extra_keys": [
            ("LPShare_Whale1", "persistent", 500_000 + 1_100_000),  # Healthy (> 30d)
            ("PairFeeConfig", "persistent", 500_000 + 14_000),      # Warming
        ],
    },
    {
        "contract_id": "CCWAH7BVPGYOEBPRFWPZIONBYUCCQH7OARGZLOD6ZV66SOAFBVA2DHD5",
        "protocol_type": "generic",
        "escrow_balance": Decimal("1000000000"),  # 100.00 USDC
        "fixed_bounty": Decimal("1000000"),       # 0.10 USDC
        "min_ledger_threshold": 20_000,
        "bump_target": 500_000,
        "extra_keys": [
            ("OracleConfig", "persistent", 500_000 + 1_800_000),    # Healthy (> 30d)
        ],
    },
]


class Command(BaseCommand):
    help = (
        "Seeds the Lazarus database with enrolled Testnet contracts, 3-tier TTL heatmap keys "
        "(Healthy, Warming, Critical), registered keepers, and signed LAZARUS_VOUCHER_V1 vouchers."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--wallet",
            type=str,
            default="",
            help="Optional Stellar G... address to also seed as a keeper with pending vouchers.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Clear existing demo records before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        if options.get("reset"):
            VoucherLog.objects.all().delete()
            StorageKeyIndex.objects.all().delete()
            MonitoredContract.objects.all().delete()
            KeeperRegistry.objects.all().delete()

        pipeline = HybridDiscoveryPipeline()
        enrolled = []

        for spec in DEMO_CONTRACTS:
            contract, _ = MonitoredContract.objects.update_or_create(
                contract_id=spec["contract_id"],
                defaults={
                    "client_address": DEMO_CLIENT_ADDRESS,
                    "escrow_balance": spec["escrow_balance"],
                    "fixed_bounty": spec["fixed_bounty"],
                    "min_ledger_threshold": spec["min_ledger_threshold"],
                    "bump_target": spec["bump_target"],
                    "protocol_type": spec["protocol_type"],
                    "is_active": True,
                },
            )
            pipeline.execute_full_bootstrap(
                contract=contract,
                snapshot_ledger=500_000,
                current_ledger=500_150,
            )
            enrolled.append((contract, spec["extra_keys"]))

        verifier = AttestationVerifierAndSigner()
        keeper_addresses = [DEMO_KEEPER_PRIMARY, DEMO_KEEPER_SECONDARY]
        custom_wallet = (options.get("wallet") or "").strip().upper()
        if len(custom_wallet) == 56 and custom_wallet.startswith("G"):
            if custom_wallet not in keeper_addresses:
                keeper_addresses.append(custom_wallet)

        for idx, k_addr in enumerate(keeper_addresses):
            keeper, _ = KeeperRegistry.objects.get_or_create(
                keeper_address=k_addr,
                defaults={"is_active": True},
            )
            for v_num in range(1, 4):
                target_contract, _ = enrolled[(idx + v_num) % len(enrolled)]
                tx_seed = f"{idx + 1:02x}{v_num:02x}" + ("ab" * 30)
                verifier.verify_and_issue_voucher(
                    tx_hash=tx_seed,
                    contract_id=target_contract.contract_id,
                    keeper_address=k_addr,
                    ledger_bumped=500_150 + target_contract.bump_target + (v_num * 100),
                )

            # Mark nonce 1 as already redeemed on-chain so the UI shows both Claimed and Pending vouchers
            first_voucher = VoucherLog.objects.filter(keeper=keeper, nonce=1).first()
            if first_voucher and not first_voucher.redeemed:
                first_voucher.redeemed = True
                first_voucher.save(update_fields=["redeemed"])
                keeper.total_bounties_claimed += first_voucher.bounty_amount
                keeper.save(update_fields=["total_bounties_claimed"])

        # Seed extra Healthy (> 30d), Warming, and Critical (<= 2,000 ledgers) keys for the live heatmap
        for contract, extra_keys in enrolled:
            for sym_name, durability, live_until in extra_keys:
                key_hash, key_xdr = make_persistent_ledger_key_xdr(
                    contract.contract_id, sym_name
                )
                StorageKeyIndex.objects.update_or_create(
                    contract=contract,
                    key_hash=key_hash,
                    defaults={
                        "key_xdr": key_xdr,
                        "durability": durability,
                        "live_until_ledger_seq": live_until,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {MonitoredContract.objects.count()} contracts, "
                f"{StorageKeyIndex.objects.count()} storage keys, "
                f"{KeeperRegistry.objects.count()} keepers, and "
                f"{VoucherLog.objects.count()} signed vouchers."
            )
        )
