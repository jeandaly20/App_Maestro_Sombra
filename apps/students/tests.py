from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Teacher

from .models import Student

PASSWORD = 'Aula-Inclusiva-2026'


def student_data(**changes):
    data = {
        'name': 'Mateo Benítez',
        'birth_date': '2016-05-01',
        'grade': 'EGB_4',
        'conditions': ['DYSLEXIA', 'DYSCALCULIA'],
        'notes': 'Buena comprensión oral.',
        'diagnosis_source': 'UDAI_REPORT',
        'diagnosis_date': '2025-03-10',
    }
    data.update(changes)
    return data


class StudentRegistryTests(TestCase):

    def setUp(self):
        self.teacher = Teacher.objects.create_user('ana@correo.com', PASSWORD, full_name='Ana Pérez')
        self.client.force_login(self.teacher)
        self.url = reverse('student_list')

    def add_student(self, teacher=None, **changes):
        return Student.objects.create(
            teacher=teacher or self.teacher, name=changes.get('name', 'Sofía Ramos'),
            birth_date=date(2017, 3, 2), grade='EGB_3', conditions=changes.get('conditions', ['ASD']),
        )

    def test_page_requires_sign_in(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_register_student(self):
        response = self.client.post(self.url, student_data(), follow=True)
        student = Student.objects.get()
        self.assertEqual(student.teacher, self.teacher)
        self.assertEqual(student.conditions, ['DYSLEXIA', 'DYSCALCULIA'])
        self.assertContains(response, 'Ficha de Mateo Benítez guardada')
        self.assertContains(response, student.pseudonym)

    def test_at_least_one_condition(self):
        response = self.client.post(self.url, student_data(conditions=[]))
        self.assertContains(response, 'Selecciona al menos una condición')
        self.assertFalse(Student.objects.exists())

    def test_only_one_intellectual_disability_level(self):
        response = self.client.post(self.url, student_data(conditions=['ID_MILD', 'ID_MODERATE']))
        self.assertContains(response, 'Elige un solo nivel de discapacidad intelectual')

    def test_future_birth_date_is_rejected(self):
        response = self.client.post(self.url, student_data(birth_date='2999-01-01'))
        self.assertContains(response, 'La fecha no puede ser futura')

    def test_fourth_student_is_rejected_with_message(self):
        for _ in range(3):
            self.add_student()
        page = self.client.get(self.url)
        self.assertContains(page, 'Ya registraste el máximo de 3 estudiantes')
        response = self.client.post(self.url, student_data())
        self.assertContains(response, 'como máximo 3 estudiantes')
        self.assertEqual(Student.objects.count(), 3)

    def test_directory_only_shows_own_students(self):
        other = Teacher.objects.create_user('luis@correo.com', PASSWORD, full_name='Luis Mora')
        self.add_student(name='Sofía Ramos')
        self.add_student(teacher=other, name='Daniel Vera')
        response = self.client.get(self.url)
        self.assertContains(response, 'Sofía Ramos')
        self.assertNotContains(response, 'Daniel Vera')

    def test_edit_own_student(self):
        student = self.add_student()
        response = self.client.post(reverse('student_edit', args=[student.pk]),
                                    student_data(name='Sofía Ramos Vera', conditions=['ASD', 'ADHD']))
        self.assertRedirects(response, self.url)
        student.refresh_from_db()
        self.assertEqual(student.name, 'Sofía Ramos Vera')
        self.assertEqual(student.conditions, ['ADHD', 'ASD'])

    def test_cannot_edit_another_teachers_student(self):
        other = Teacher.objects.create_user('luis@correo.com', PASSWORD, full_name='Luis Mora')
        foreign = self.add_student(teacher=other)
        response = self.client.get(reverse('student_edit', args=[foreign.pk]))
        self.assertEqual(response.status_code, 404)

    def test_records_pdf(self):
        self.add_student()
        response = self.client.get(reverse('student_records_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_recommended_adjustments_follow_groups(self):
        student = self.add_student(conditions=['DYSLEXIA', 'ADHD'])
        self.assertEqual([g.key for g in student.groups], ['dea', 'neuro'])
        self.assertIn('Fuente accesible y silabeo', student.recommended_adjustments)
        self.assertIn('Secuencia paso a paso', student.recommended_adjustments)

    def test_inactive_student_is_hidden(self):
        student = self.add_student(name='Sofía Ramos')
        student.record_status = 'INACTIVE'
        student.save()
        self.assertNotContains(self.client.get(self.url), 'Sofía Ramos')
        self.assertEqual(self.client.get(reverse('student_edit', args=[student.pk])).status_code, 404)

    def test_give_student_leave(self):
        student = self.add_student(name='Sofía Ramos')
        response = self.client.post(reverse('student_deactivate', args=[student.pk]))
        self.assertRedirects(response, self.url)
        student.refresh_from_db()
        self.assertEqual(student.record_status, 'INACTIVE')
        self.assertNotContains(self.client.get(self.url), 'Sofía Ramos')
        # Another teacher gets a 404
        other = Teacher.objects.create_user('luis@correo.com', PASSWORD, full_name='Luis Mora')
        foreign = self.add_student(teacher=other, name='Daniel Vera')
        self.assertEqual(self.client.post(reverse('student_deactivate', args=[foreign.pk])).status_code, 404)

    def test_learning_disorder_cannot_be_combined_with_intellectual_disability(self):
        response = self.client.post(self.url, student_data(conditions=['DYSLEXIA', 'ADHD', 'ID_MILD']))
        self.assertContains(response, 'no se registran junto con discapacidad intelectual')
        self.assertFalse(Student.objects.exists())
        # Neurodevelopment + intellectual disability is a valid combination
        self.client.post(self.url, student_data(conditions=['ASD', 'ID_MILD'], diagnosis_source='MSP_CERTIFICATE'))
        self.assertEqual(Student.objects.get().conditions, ['ASD', 'ID_MILD'])

    def test_diagnosis_backing_rules(self):
        no_date = self.client.post(self.url, student_data(diagnosis_date=''))
        self.assertContains(no_date, 'Indica la fecha del informe o certificado.')
        future = self.client.post(self.url, student_data(diagnosis_date='2999-01-01'))
        self.assertContains(future, 'no puede ser futura')
        dece_for_id = self.client.post(self.url, student_data(conditions=['ID_MODERATE'], diagnosis_source='DECE_REPORT'))
        self.assertContains(dece_for_id, 'certificado o carné de discapacidad del MSP')
        self.assertFalse(Student.objects.exists())
        self.client.post(self.url, student_data(diagnosis_source='IN_EVALUATION', diagnosis_date='2025-03-10',
                                                diagnosis_reference='UDAI-09D01-123'))
        student = Student.objects.get()
        self.assertIsNone(student.diagnosis_date)  # "in evaluation" keeps no date
        self.assertTrue(student.is_diagnosis_pending)

    def test_new_student_has_no_preselected_document(self):
        page = self.client.get(self.url)
        self.assertContains(page, '<option value="" selected>Selecciona el documento</option>', html=True)
        response = self.client.post(self.url, student_data(diagnosis_source=''))
        self.assertContains(response, 'Este campo es obligatorio.')
