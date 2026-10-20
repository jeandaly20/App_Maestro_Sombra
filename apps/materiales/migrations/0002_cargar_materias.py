from django.db import migrations

# Asignaturas de Educación General Básica (Ecuador)
MATERIAS = [
    ('Matemática', 'MAT'),
    ('Lengua y Literatura', 'LEN'),
    ('Ciencias Naturales', 'CIE'),
    ('Estudios Sociales', 'SOC'),
    ('Inglés', 'ING'),
    ('Educación Cultural y Artística', 'ART'),
]


def cargar(apps, schema_editor):
    Materia = apps.get_model('materiales', 'Materia')
    for nombre, abreviatura in MATERIAS:
        Materia.objects.get_or_create(nombre=nombre, defaults={'abreviatura': abreviatura})


def borrar(apps, schema_editor):
    Materia = apps.get_model('materiales', 'Materia')
    Materia.objects.filter(nombre__in=[nombre for nombre, _ in MATERIAS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('materiales', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
