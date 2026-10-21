from django.db import migrations

# Ecuadorian General Basic Education subjects (names are shown to the user)
SUBJECTS = [
    ('Matemática', 'MAT'),
    ('Lengua y Literatura', 'LEN'),
    ('Ciencias Naturales', 'CIE'),
    ('Estudios Sociales', 'SOC'),
    ('Inglés', 'ING'),
    ('Educación Cultural y Artística', 'ART'),
]


def load(apps, schema_editor):
    Subject = apps.get_model('materials', 'Subject')
    for name, code in SUBJECTS:
        Subject.objects.get_or_create(name=name, defaults={'code': code})


def unload(apps, schema_editor):
    Subject = apps.get_model('materials', 'Subject')
    Subject.objects.filter(name__in=[name for name, _ in SUBJECTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(load, unload),
    ]
