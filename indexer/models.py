from __future__ import annotations
from django.db import models


class MonitoredContract(models.Model):
    PROTOCOL_CHOICES = [
        ("generic", "Generic Soroban Contract"),
        ("blend", "Blend Lending Pool"),
        ("soroswap", "Soroswap AMM Pair"),
    ]

    contract_id = models.CharField(max_length=56, unique=True, db_index=True)
    client_address = models.CharField(max_length=56)
    escrow_balance = models.DecimalField(max_digits=38, decimal_places=0, default=0)
    fixed_bounty = models.DecimalField(max_digits=38, decimal_places=0)  # stroops (i128)
    min_ledger_threshold = models.PositiveIntegerField(default=20000)
    bump_target = models.PositiveIntegerField(default=500000)
    protocol_type = models.CharField(max_length=32, choices=PROTOCOL_CHOICES, default="generic")
    is_active = models.BooleanField(default=True, db_index=True)
    last_bootstrapped_ledger = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.contract_id} (active={self.is_active})"


class StorageKeyIndex(models.Model):
    contract = models.ForeignKey(
        MonitoredContract,
        on_delete=models.CASCADE,
        related_name="keys",
    )
    key_hash = models.CharField(max_length=64, db_index=True)  # SHA-256 of LedgerKey XDR
    key_xdr = models.TextField()  # Base64 encoded LedgerKey
    durability = models.CharField(max_length=16, default="persistent")
    live_until_ledger_seq = models.PositiveIntegerField(db_index=True)
    last_extended_ledger = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("contract", "key_hash")
        indexes = [
            models.Index(fields=["live_until_ledger_seq", "contract"]),
            models.Index(fields=["contract", "live_until_ledger_seq"]),
        ]
        ordering = ["live_until_ledger_seq"]

    def __str__(self) -> str:
        return f"{self.contract.contract_id}:{self.key_hash[:12]}@{self.live_until_ledger_seq}"


class KeeperRegistry(models.Model):
    keeper_address = models.CharField(max_length=56, unique=True, db_index=True)
    current_nonce = models.PositiveBigIntegerField(default=0)
    total_bounties_claimed = models.DecimalField(max_digits=38, decimal_places=0, default=0)
    is_active = models.BooleanField(default=True)
    last_active_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-total_bounties_claimed"]

    def __str__(self) -> str:
        return f"{self.keeper_address} (nonce={self.current_nonce})"


class VoucherLog(models.Model):
    keeper = models.ForeignKey(
        KeeperRegistry,
        on_delete=models.CASCADE,
        related_name="vouchers",
    )
    contract = models.ForeignKey(
        MonitoredContract,
        on_delete=models.CASCADE,
        related_name="vouchers",
    )
    tx_hash = models.CharField(max_length=64, unique=True, db_index=True)
    nonce = models.PositiveBigIntegerField()
    ledger_bumped = models.PositiveIntegerField()
    bounty_amount = models.DecimalField(max_digits=38, decimal_places=0)
    signature = models.CharField(max_length=128)  # 64-byte hex
    redeemed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("keeper", "nonce")
        ordering = ["nonce"]

    def __str__(self) -> str:
        return f"Voucher({self.keeper.keeper_address}#{self.nonce} -> {self.contract.contract_id})"
