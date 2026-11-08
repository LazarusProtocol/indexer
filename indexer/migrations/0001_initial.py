from __future__ import annotations
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="KeeperRegistry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "keeper_address",
                    models.CharField(db_index=True, max_length=56, unique=True),
                ),
                ("current_nonce", models.PositiveBigIntegerField(default=0)),
                (
                    "total_bounties_claimed",
                    models.DecimalField(decimal_places=0, default=0, max_digits=38),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("last_active_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-total_bounties_claimed"],
            },
        ),
        migrations.CreateModel(
            name="MonitoredContract",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "contract_id",
                    models.CharField(db_index=True, max_length=56, unique=True),
                ),
                ("client_address", models.CharField(max_length=56)),
                (
                    "escrow_balance",
                    models.DecimalField(decimal_places=0, default=0, max_digits=38),
                ),
                (
                    "fixed_bounty",
                    models.DecimalField(decimal_places=0, max_digits=38),
                ),
                ("min_ledger_threshold", models.PositiveIntegerField(default=20000)),
                ("bump_target", models.PositiveIntegerField(default=500000)),
                (
                    "protocol_type",
                    models.CharField(
                        choices=[
                            ("generic", "Generic Soroban Contract"),
                            ("blend", "Blend Lending Pool"),
                            ("soroswap", "Soroswap AMM Pair"),
                        ],
                        default="generic",
                        max_length=32,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "last_bootstrapped_ledger",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="VoucherLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("tx_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("nonce", models.PositiveBigIntegerField()),
                ("ledger_bumped", models.PositiveIntegerField()),
                (
                    "bounty_amount",
                    models.DecimalField(decimal_places=0, max_digits=38),
                ),
                ("signature", models.CharField(max_length=128)),
                ("redeemed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "contract",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vouchers",
                        to="indexer.monitoredcontract",
                    ),
                ),
                (
                    "keeper",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vouchers",
                        to="indexer.keeperregistry",
                    ),
                ),
            ],
            options={
                "ordering": ["nonce"],
                "unique_together": {("keeper", "nonce")},
            },
        ),
        migrations.CreateModel(
            name="StorageKeyIndex",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("key_hash", models.CharField(db_index=True, max_length=64)),
                ("key_xdr", models.TextField()),
                ("durability", models.CharField(default="persistent", max_length=16)),
                ("live_until_ledger_seq", models.PositiveIntegerField(db_index=True)),
                (
                    "last_extended_ledger",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "contract",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="keys",
                        to="indexer.monitoredcontract",
                    ),
                ),
            ],
            options={
                "ordering": ["live_until_ledger_seq"],
                "indexes": [
                    models.Index(
                        fields=["live_until_ledger_seq", "contract"],
                        name="indexer_sto_live_un_7b1a0c_idx",
                    )
                ],
                "unique_together": {("contract", "key_hash")},
            },
        ),
    ]
