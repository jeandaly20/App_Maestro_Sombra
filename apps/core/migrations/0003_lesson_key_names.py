import importlib

from django.db import migrations

# Same naming convention (pk_, fk_, uk_, ix_) for the tables of "Adaptar clase"
_first = importlib.import_module('apps.core.migrations.0001_key_and_index_names')
RENAME = _first.RENAME.replace(
    "ARRAY['cat_subject', 'mst_teacher', 'mst_teacher_groups', 'mst_teacher_user_permissions',\n"
    "                           'mst_student', 'trx_original_material', 'trx_curricular_adaptation', 'trx_evaluation']",
    "ARRAY['trx_lesson_plan', 'trx_lesson_adaptation']",
)
# Named rules keep the name the model gives them (Django refers to them by that name)
RENAME = RENAME.replace("c.conname <> 'adaptation_version_unique'", "c.conname <> 'lesson_adaptation_unique'")
assert 'trx_lesson_plan' in RENAME and "'lesson_adaptation_unique'" in RENAME


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_remaining_rule_names'),
        ('adaptations', '0003_lesson_plan'),
    ]

    operations = [
        migrations.RunSQL(RENAME, migrations.RunSQL.noop),
    ]
