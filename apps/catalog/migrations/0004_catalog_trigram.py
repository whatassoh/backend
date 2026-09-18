from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_storeproduct")]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="product",
            index=GinIndex(
                fields=["normalized_name"],
                name="catalog_product_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        migrations.AddIndex(
            model_name="productalias",
            index=GinIndex(
                fields=["normalized_alias"],
                name="catalog_alias_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
