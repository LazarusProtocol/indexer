from __future__ import annotations
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from .cache_utils import LazarusCacheManager
from .models import KeeperRegistry, MonitoredContract, StorageKeyIndex, VoucherLog


@receiver([post_save, post_delete], sender=MonitoredContract)
def invalidate_on_contract_change(sender, **kwargs) -> None:
    LazarusCacheManager.invalidate_state_caches(reason="monitored_contract_mutated")


@receiver([post_save, post_delete], sender=StorageKeyIndex)
def invalidate_on_storage_key_change(sender, **kwargs) -> None:
    LazarusCacheManager.invalidate_state_caches(reason="storage_key_index_mutated")


@receiver([post_save, post_delete], sender=KeeperRegistry)
def invalidate_on_keeper_change(sender, **kwargs) -> None:
    LazarusCacheManager.invalidate_state_caches(reason="keeper_registry_mutated")


@receiver([post_save, post_delete], sender=VoucherLog)
def invalidate_on_voucher_change(sender, **kwargs) -> None:
    LazarusCacheManager.invalidate_state_caches(reason="voucher_log_mutated")
