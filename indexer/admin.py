from __future__ import annotations
from django.contrib import admin
from .models import KeeperRegistry, MonitoredContract, StorageKeyIndex, VoucherLog


@admin.register(MonitoredContract)
class MonitoredContractAdmin(admin.ModelAdmin):
    list_display = (
        "contract_id",
        "client_address",
        "protocol_type",
        "fixed_bounty",
        "min_ledger_threshold",
        "bump_target",
        "is_active",
        "last_bootstrapped_ledger",
    )
    search_fields = ("contract_id", "client_address")
    list_filter = ("protocol_type", "is_active")


@admin.register(StorageKeyIndex)
class StorageKeyIndexAdmin(admin.ModelAdmin):
    list_display = (
        "contract",
        "key_hash",
        "durability",
        "live_until_ledger_seq",
        "last_extended_ledger",
        "updated_at",
    )
    search_fields = ("key_hash", "contract__contract_id")
    list_filter = ("durability",)


@admin.register(KeeperRegistry)
class KeeperRegistryAdmin(admin.ModelAdmin):
    list_display = (
        "keeper_address",
        "current_nonce",
        "total_bounties_claimed",
        "is_active",
        "last_active_at",
    )
    search_fields = ("keeper_address",)


@admin.register(VoucherLog)
class VoucherLogAdmin(admin.ModelAdmin):
    list_display = (
        "tx_hash",
        "keeper",
        "contract",
        "nonce",
        "ledger_bumped",
        "bounty_amount",
        "redeemed",
        "created_at",
    )
    search_fields = ("tx_hash", "keeper__keeper_address", "contract__contract_id")
    list_filter = ("redeemed",)
