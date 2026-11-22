"""
The cache table in PostgreSQL (settings.CACHES): the failed sign-in counters and the request limits are shared
by every server process. Created here so "migrate" is enough on any computer; createcachetable skips a table
that already exists.
"""
from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command('createcachetable', database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_lesson_key_names'),
    ]

    operations = [
        migrations.RunPython(create_cache_table, migrations.RunPython.noop),
    ]
