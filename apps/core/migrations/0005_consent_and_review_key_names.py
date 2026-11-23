import importlib

from django.db import migrations

# Same naming convention (pk_, fk_, uk_, ix_) for the tables added later: the representative's authorizations and
# the teacher's decisions on recommendations
_first = importlib.import_module('apps.core.migrations.0001_key_and_index_names')
RENAME = _first.RENAME.replace(
    "ARRAY['cat_subject', 'mst_teacher', 'mst_teacher_groups', 'mst_teacher_user_permissions',\n"
    "                           'mst_student', 'trx_original_material', 'trx_curricular_adaptation', 'trx_evaluation']",
    "ARRAY['trx_student_consent', 'trx_recommendation_review']",
)
# Named rules keep the name the model gives them (Django refers to them by that name)
RENAME = RENAME.replace("c.conname <> 'adaptation_version_unique'", "c.conname <> 'recommendation_review_unique'")
assert 'trx_student_consent' in RENAME and "'recommendation_review_unique'" in RENAME


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_shared_cache_table'),
        ('students', '0013_evaluation_treatment'),
        ('evaluations', '0005_recommendation_source'),
    ]

    operations = [
        migrations.RunSQL(RENAME, migrations.RunSQL.noop),
    ]
