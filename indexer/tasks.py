from __future__ import annotations
from typing import Any, Dict
from .models import MonitoredContract, StorageKeyIndex
from .services import HybridDiscoveryPipeline


try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    def shared_task(func=None, **_kwargs):
        if func is not None:
            func.delay = func
            return func

        def wrapper(f):
            f.delay = f
            return f

        return wrapper


@shared_task(name="indexer.bootstrap_contract_keys")
def bootstrap_contract_keys_task(
    contract_id: str,
    snapshot_ledger: int = 500_000,
    current_ledger: int = 500_150,
) -> Dict[str, Any]:
    """
    Celery task executing Pipeline A (Hubble BigQuery), Pipeline B (RPC Catch-Up Replay),
    and Pipeline C (Protocol-specific storage tree crawl) for an enrolled contract.
    """
    contract = MonitoredContract.objects.get(contract_id=contract_id)
    pipeline = HybridDiscoveryPipeline()
    return pipeline.execute_full_bootstrap(
        contract=contract,
        snapshot_ledger=snapshot_ledger,
        current_ledger=current_ledger,
    )


@shared_task(name="indexer.ingest_ledger_stream_tick")
def ingest_ledger_stream_tick_task(
    ledger_sequence: int,
    mutated_keys: list[dict] | None = None,
) -> Dict[str, Any]:
    """
    Pipeline D: Real-Time Incremental Stream Ingestion.
    Updates modified `ContractData` entries from live `getLedgers` stream blocks.
    """
    updated = 0
    for entry in mutated_keys or []:
        contract = MonitoredContract.objects.filter(
            contract_id=entry.get("contract_id"), is_active=True
        ).first()
        if contract is None:
            continue
        StorageKeyIndex.objects.update_or_create(
            contract=contract,
            key_hash=entry["key_hash"],
            defaults={
                "key_xdr": entry["key_xdr"],
                "durability": entry.get("durability", "persistent"),
                "live_until_ledger_seq": int(entry["live_until_ledger_seq"]),
                "last_extended_ledger": ledger_sequence,
            },
        )
        updated += 1

    return {
        "ledger_sequence": ledger_sequence,
        "entries_updated": updated,
    }
