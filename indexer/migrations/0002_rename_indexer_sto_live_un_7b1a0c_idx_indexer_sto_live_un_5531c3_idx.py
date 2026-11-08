from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('indexer', '0001_initial'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='storagekeyindex',
            new_name='indexer_sto_live_un_5531c3_idx',
            old_name='indexer_sto_live_un_7b1a0c_idx',
        ),
    ]
