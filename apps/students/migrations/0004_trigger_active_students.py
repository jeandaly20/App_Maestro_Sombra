from django.db import migrations

# The tables now carry their type prefix (mst_) and a record status, so the trigger
# reads the new names and only counts active students: an inactive student frees a slot.
CREATE = """
CREATE OR REPLACE FUNCTION fn_max_students_per_teacher() RETURNS trigger AS $$
BEGIN
    IF NEW.record_status <> 'ACTIVE' THEN
        RETURN NEW;
    END IF;
    -- Lock the teacher row so two simultaneous inserts cannot exceed the limit
    PERFORM 1 FROM mst_teacher WHERE id = NEW.teacher_id FOR UPDATE;
    IF (SELECT COUNT(*) FROM mst_student
        WHERE teacher_id = NEW.teacher_id AND record_status = 'ACTIVE' AND id <> NEW.id) >= 3 THEN
        RAISE EXCEPTION 'Un docente puede registrar como máximo 3 estudiantes.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_max_students_per_teacher ON mst_student;
CREATE TRIGGER tr_max_students_per_teacher
    BEFORE INSERT OR UPDATE OF teacher_id, record_status ON mst_student
    FOR EACH ROW EXECUTE FUNCTION fn_max_students_per_teacher();
"""

# Back to the previous rule (all students counted); runs before the tables get their old names back
DROP = """
CREATE OR REPLACE FUNCTION fn_max_students_per_teacher() RETURNS trigger AS $$
BEGIN
    PERFORM 1 FROM mst_teacher WHERE id = NEW.teacher_id FOR UPDATE;
    IF (SELECT COUNT(*) FROM mst_student
        WHERE teacher_id = NEW.teacher_id AND id <> NEW.id) >= 3 THEN
        RAISE EXCEPTION 'Un docente puede registrar como máximo 3 estudiantes.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_max_students_per_teacher ON mst_student;
CREATE TRIGGER tr_max_students_per_teacher
    BEFORE INSERT OR UPDATE OF teacher_id ON mst_student
    FOR EACH ROW EXECUTE FUNCTION fn_max_students_per_teacher();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0003_student_record_status_and_more'),
        ('accounts', '0002_teacher_record_status_and_more'),
    ]

    operations = [
        migrations.RunSQL(CREATE, DROP),
    ]
