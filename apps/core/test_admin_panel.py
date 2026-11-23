"""
What the administrator can do in /admin/: manage accounts and the subject catalog, deactivating instead of deleting,
and only consult the teachers' work (students by pseudonym, materials, worksheets, evaluations). Made-up data only.
"""
from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.materials.models import Subject
from apps.students.models import Student


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class AdminPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Teacher.objects.create_superuser('admin.panel@maestrosombra.test', None, full_name='Admin Panel')
        cls.teacher = Teacher.objects.create_user('docente.panel@maestrosombra.test', None, full_name='Docente Panel',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name='Estudiante Reservado Panel', birth_date=date(2016, 1, 1),
                              grade='EGB_4', conditions=['ADHD'], diagnosis_source='IN_EVALUATION')
        cls.student.save()

    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_administrator_manages_accounts_and_the_subject_catalog(self):
        self.assertEqual(self.client.get(reverse('admin:accounts_teacher_add')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:accounts_teacher_change', args=[self.teacher.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:materials_subject_add')).status_code, 200)

    def test_nothing_is_deleted_from_the_panel(self):
        subject = Subject.objects.get(code='MAT')
        for url in (reverse('admin:accounts_teacher_delete', args=[self.teacher.pk]),
                    reverse('admin:materials_subject_delete', args=[subject.pk]),
                    reverse('admin:students_student_delete', args=[self.student.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(Teacher.objects.filter(pk=self.teacher.pk).exists())
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())
        # Nor in bulk from a list
        page = self.client.get(reverse('admin:accounts_teacher_changelist')).content.decode()
        self.assertNotIn('delete_selected', page)

    def test_the_teachers_work_is_only_consulted(self):
        url = reverse('admin:students_student_change', args=[self.student.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)  # view only
        self.assertNotContains(page, 'Estudiante Reservado Panel')  # never the student's name
        self.assertContains(page, self.student.pseudonym)
        self.assertEqual(self.client.post(url, {'grade': 'EGB_5'}).status_code, 403)
        for name in ('admin:students_student_add', 'admin:adaptations_curricularadaptation_add',
                     'admin:evaluations_evaluation_add', 'admin:materials_originalmaterial_add'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
