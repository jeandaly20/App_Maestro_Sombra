from django.db import migrations

# La regla "máximo 3 estudiantes por docente" se valida también en PostgreSQL,
# para que se cumpla aunque el dato no entre por los formularios de Django.
CREAR = """
CREATE OR REPLACE FUNCTION fn_maximo_estudiantes_por_docente() RETURNS trigger AS $$
BEGIN
    -- Bloquea al docente para que dos registros simultáneos no superen el límite
    PERFORM 1 FROM cuentas_docente WHERE id = NEW.docente_id FOR UPDATE;
    IF (SELECT COUNT(*) FROM estudiantes_estudiante
        WHERE docente_id = NEW.docente_id AND id <> NEW.id) >= 3 THEN
        RAISE EXCEPTION 'Un docente puede registrar como máximo 3 estudiantes.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_maximo_estudiantes_por_docente
    BEFORE INSERT OR UPDATE OF docente_id ON estudiantes_estudiante
    FOR EACH ROW EXECUTE FUNCTION fn_maximo_estudiantes_por_docente();
"""

BORRAR = """
DROP TRIGGER IF EXISTS tr_maximo_estudiantes_por_docente ON estudiantes_estudiante;
DROP FUNCTION IF EXISTS fn_maximo_estudiantes_por_docente();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('estudiantes', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(CREAR, BORRAR),
    ]
