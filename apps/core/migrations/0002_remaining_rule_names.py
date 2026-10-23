from django.db import migrations

# Names Django generated from the old table names: the automatic "not negative" checks
# of positive integer fields and the admin log's foreign key to the teacher.
RENAME = """
ALTER TABLE trx_curricular_adaptation
    RENAME CONSTRAINT adaptations_curricularadaptation_generation_time_sec_check TO adaptation_generation_time_not_negative;
ALTER TABLE trx_curricular_adaptation
    RENAME CONSTRAINT adaptations_curricularadaptation_version_check TO adaptation_version_not_negative;
ALTER TABLE trx_original_material
    RENAME CONSTRAINT materials_originalmaterial_size_kb_check TO material_size_not_negative;
DO $$
DECLARE
    old_name text := (SELECT conname FROM pg_constraint
                      WHERE conrelid = 'django_admin_log'::regclass AND contype = 'f'
                        AND confrelid = 'mst_teacher'::regclass);
BEGIN
    IF old_name IS NOT NULL AND old_name <> 'fk_django_admin_log_user_id' THEN
        EXECUTE format('ALTER TABLE django_admin_log RENAME CONSTRAINT %I TO fk_django_admin_log_user_id', old_name);
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_key_and_index_names'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    operations = [
        migrations.RunSQL(RENAME, migrations.RunSQL.noop),
    ]
