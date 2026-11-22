"""
Permanent deletion of a student (LOPDP, art. 15; HU-28): everything about them goes, files included, and only
their own teacher can do it, after confirming. Made-up data only.
"""
from datetime import date
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations import ai, services as adaptation_services
from apps.adaptations.models import CurricularAdaptation
from apps.adaptations.tests import FakeGemini
from apps.core.choices import RecordStatus
from apps.evaluations.models import Evaluation, EvaluationStatus
from apps.materials.models import OriginalMaterial, Subject

from .models import Student, StudentConsent


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class PermanentDeletionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.eliminar@maestrosombra.test', None,
                                                  full_name='Docente Eliminar', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.other = Teacher.objects.create_user('otro.docente@maestrosombra.test', None, full_name='Otro Docente',
                                                accepted_terms=True, terms_accepted_at=timezone.now())
        cls.language = Subject.objects.get(code='LEN')

    def setUp(self):
        self.client.force_login(self.teacher)
        self.student = Student(teacher=self.teacher, name='Estudiante Borrable', birth_date=date(2016, 2, 1),
                               grade='EGB_4', conditions=['ADHD'], diagnosis_source='IN_EVALUATION',
                               notes='Observación ficticia.')
        self.student.save()
        self.material = OriginalMaterial.objects.create(
            teacher=self.teacher, subject=self.language, title='Sustantivos', topic='El sustantivo',
            input_type='TEXT', input_text='1. Subraya. 2. Escribe. 3. Lee. 4. Clasifica. 5. Explica.')
        with mock.patch.object(ai, 'generate_json', FakeGemini()):
            adaptation_services.process_material(self.material.pk, [(self.student.pk, ['ADHD'])],
                                                 {self.student.pk: []})
        self.worksheet = CurricularAdaptation.objects.get(student=self.student)
        self.worksheet.pdf.save('ficha-borrable.pdf', ContentFile(b'%PDF-prueba'))
        Evaluation.objects.create(adaptation=self.worksheet, status=EvaluationStatus.FINAL, goal_attainment='LESS',
                                  autonomy_level='FREQUENT_SUPPORT', comprehension='DIFFICULTY', overload='MILD')
        self.signed = default_storage.save('students/prueba/autorizacion-borrable.bin', ContentFile(b'cifrado'))
        StudentConsent.objects.create(student=self.student, document_version='PRUEBA', authorized_on=date(2026, 11, 1),
                                      treatments=['INTERNAL'], signed_file=self.signed,
                                      signed_file_type='application/pdf')
        self.files = [self.worksheet.pdf.name, self.signed]
        self.addCleanup(lambda: [default_storage.delete(path) for path in self.files if default_storage.exists(path)])

    def delete(self, student=None, confirm=True):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('student_delete', args=[(student or self.student).pk]),
                                    {'confirm': 'on'} if confirm else {})

    def test_the_confirmation_page_says_everything_that_will_go(self):
        page = self.client.get(reverse('student_delete', args=[self.student.pk])).content.decode()
        self.assertIn('no se puede deshacer', page)
        self.assertIn('1 ficha adaptada', page)
        self.assertIn('1 evaluación', page)
        self.assertIn('1 autorización', page)
        self.assertIn(reverse('student_file', args=[self.student.pk]), page)  # download it first, if needed

    def test_deleting_removes_the_student_their_history_and_their_files(self):
        response = self.delete()
        self.assertRedirects(response, reverse('student_list'), fetch_redirect_response=False)
        self.assertFalse(Student.objects.filter(pk=self.student.pk).exists())
        self.assertFalse(CurricularAdaptation.objects.filter(student_id=self.student.pk).exists())
        self.assertFalse(Evaluation.objects.filter(adaptation_id=self.worksheet.pk).exists())
        self.assertFalse(StudentConsent.objects.filter(student_id=self.student.pk).exists())
        for path in self.files:
            self.assertFalse(default_storage.exists(path), path)
        # The teacher's own material stays: it holds no data of the student
        self.assertTrue(OriginalMaterial.objects.filter(pk=self.material.pk).exists())

    def test_nothing_is_deleted_without_confirming(self):
        self.delete(confirm=False)
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(default_storage.exists(self.signed))

    def test_a_student_given_leave_can_be_deleted_too(self):
        Student.objects.filter(pk=self.student.pk).update(record_status=RecordStatus.INACTIVE)
        self.assertContains(self.client.get(reverse('student_list')), 'Estudiantes dados de baja')
        self.delete()
        self.assertFalse(Student.objects.filter(pk=self.student.pk).exists())

    def test_another_teacher_cannot_see_or_delete_the_student(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('student_delete', args=[self.student.pk])).status_code, 404)
        self.assertEqual(self.delete().status_code, 404)
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())

    def test_the_log_keeps_only_the_pseudonym(self):
        with self.assertLogs('maestro_sombra', level='INFO') as logs:
            self.delete()
        text = '\n'.join(logs.output)
        self.assertIn(self.student.pseudonym, text)
        self.assertNotIn(self.student.name, text)
