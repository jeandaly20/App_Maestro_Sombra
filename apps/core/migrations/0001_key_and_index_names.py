from django.db import migrations

# Renaming a table keeps the names Django generated for its keys, indexes and sequences
# (e.g. students_student_pkey). This gives them the naming convention of the MER:
# pk_<table>, fk_<table>_<column>, uk_<table>_<column>, ix_<table>_<column> and <table>_<column>_seq.
RENAME = r"""
DO $$
DECLARE
    tables text[] := ARRAY['cat_subject', 'mst_teacher', 'mst_teacher_groups', 'mst_teacher_user_permissions',
                           'mst_student', 'trx_original_material', 'trx_curricular_adaptation', 'trx_evaluation'];
    r record;
    cols text;
    new_name text;
BEGIN
    -- Primary, foreign and unique keys (named CHECK and UNIQUE rules keep their names)
    FOR r IN
        SELECT c.conname, c.contype, c.conrelid, c.conkey, t.relname AS tbl
        FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = ANY (tables) AND c.contype IN ('p', 'f', 'u')
          AND c.conname <> 'adaptation_version_unique'
    LOOP
        SELECT string_agg(a.attname, '_' ORDER BY k.ord) INTO cols
        FROM unnest(r.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = r.conrelid AND a.attnum = k.attnum;
        new_name := left(CASE r.contype
            WHEN 'p' THEN 'pk_' || r.tbl
            WHEN 'f' THEN 'fk_' || r.tbl || '_' || cols
            ELSE 'uk_' || r.tbl || '_' || cols
        END, 63);
        IF new_name <> r.conname THEN
            EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I', r.tbl, r.conname, new_name);
        END IF;
    END LOOP;

    -- Plain indexes (those behind a key were renamed with it)
    FOR r IN
        SELECT i.relname AS idx, t.relname AS tbl, x.indrelid, x.indkey::int2[] AS indkey
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_class t ON t.oid = x.indrelid
        WHERE t.relname = ANY (tables)
          AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = x.indexrelid AND c.conrelid = x.indrelid)
    LOOP
        SELECT string_agg(a.attname, '_' ORDER BY k.ord) INTO cols
        FROM unnest(r.indkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = r.indrelid AND a.attnum = k.attnum;
        -- *_like indexes speed up LIKE searches on text columns
        new_name := left('ix_' || r.tbl || '_' || cols || CASE WHEN r.idx LIKE '%\_like' THEN '_like' ELSE '' END, 63);
        IF new_name <> r.idx THEN
            EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx, new_name);
        END IF;
    END LOOP;

    -- Sequences of the numeric identifiers
    FOR r IN
        SELECT s.relname AS seq, t.relname AS tbl, a.attname AS col
        FROM pg_class s
        JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        WHERE s.relkind = 'S' AND t.relname = ANY (tables)
    LOOP
        new_name := left(r.tbl || '_' || r.col || '_seq', 63);
        IF new_name <> r.seq THEN
            EXECUTE format('ALTER SEQUENCE %I RENAME TO %I', r.seq, new_name);
        END IF;
    END LOOP;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_teacher_record_status_and_more'),
        ('students', '0004_trigger_active_students'),
        ('materials', '0003_originalmaterial_record_status_subject_record_status_and_more'),
        ('adaptations', '0002_curricularadaptation_record_status_and_more'),
        ('evaluations', '0002_evaluation_record_status_and_more'),
    ]

    operations = [
        migrations.RunSQL(RENAME, migrations.RunSQL.noop),
    ]
