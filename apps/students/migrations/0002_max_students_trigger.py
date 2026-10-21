from django.db import migrations

# The "max 3 students per teacher" rule is also enforced in PostgreSQL,
# so it holds even when data does not come through Django forms.
CREATE = """
CREATE OR REPLACE FUNCTION fn_max_students_per_teacher() RETURNS trigger AS $$
BEGIN
    -- Lock the teacher row so two simultaneous inserts cannot exceed the limit
    PERFORM 1 FROM accounts_teacher WHERE id = NEW.teacher_id FOR UPDATE;
    IF (SELECT COUNT(*) FROM students_student
        WHERE teacher_id = NEW.teacher_id AND id <> NEW.id) >= 3 THEN
        RAISE EXCEPTION 'Un docente puede registrar como máximo 3 estudiantes.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_max_students_per_teacher
    BEFORE INSERT OR UPDATE OF teacher_id ON students_student
    FOR EACH ROW EXECUTE FUNCTION fn_max_students_per_teacher();
"""

DROP = """
DROP TRIGGER IF EXISTS tr_max_students_per_teacher ON students_student;
DROP FUNCTION IF EXISTS fn_max_students_per_teacher();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(CREATE, DROP),
    ]
