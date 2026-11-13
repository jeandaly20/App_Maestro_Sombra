"""
The authorizations already signed become consent records with exactly the scope of their text, never wider:
- the signed registration form ("la IA recibe solo el grado y la necesidad educativa"): internal treatment and
  sending grade and NEE to the AI; NOT the pedagogical profile;
- an older authorization without a signed file (the former checkbox): internal treatment only.
Nothing in the students is changed, and the signed files stay where they are (only their path is referenced).
"""
from django.db import migrations

REGISTRATION_VERSION = 'REGISTRO-2026-v1'
CHECKBOX_VERSION = 'CASILLA-2026'


def keep_existing_consents(apps, schema_editor):
    Student = apps.get_model('students', 'Student')
    StudentConsent = apps.get_model('students', 'StudentConsent')
    for student in Student.objects.exclude(diagnosis_consent_at__isnull=True):
        if StudentConsent.objects.filter(student=student).exists():
            continue
        signed = bool(student.consent_file)
        StudentConsent.objects.create(
            student=student,
            document_version=REGISTRATION_VERSION if signed else CHECKBOX_VERSION,
            authorized_on=student.diagnosis_consent_at.date(),
            treatments=['INTERNAL', 'AI_ADAPTATION'] if signed else ['INTERNAL'],
            status='ACTIVE',
            signed_file=student.consent_file,
            signed_file_type=student.consent_file_type,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0011_student_consent'),
    ]

    operations = [
        # Going back removes only the consent table (0011); these rows need no undoing of their own
        migrations.RunPython(keep_existing_consents, migrations.RunPython.noop),
    ]
