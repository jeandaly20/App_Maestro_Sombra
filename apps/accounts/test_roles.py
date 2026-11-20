"""
Administrator and teacher are separate roles, although they share one table of users: an administrator works
only in /admin/ and never as a teacher. Made-up accounts only.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Teacher


class RoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Teacher.objects.create_superuser('admin.prueba@maestrosombra.test', None, full_name='Admin Prueba')
        cls.teacher = Teacher.objects.create_user('docente.rol@maestrosombra.test', None, full_name='Docente Rol',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())

    def test_the_administrator_goes_to_the_panel_and_never_to_the_teaching_modules(self):
        self.client.force_login(self.admin)
        for name in ('home', 'student_list', 'student_create', 'adapt', 'bank', 'lesson_upload', 'group_progress'):
            self.assertRedirects(self.client.get(reverse(name)), reverse('admin:index'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_the_administrator_can_still_sign_out(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.post(reverse('logout')), reverse('login'), fetch_redirect_response=False)

    def test_a_teacher_works_as_always_and_cannot_open_the_panel(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_the_panel_shows_the_role_of_each_account(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse('admin:accounts_teacher_changelist')).content.decode()
        self.assertIn('Administrador', page)
        self.assertIn('Docente', page)
        teachers = self.client.get(reverse('admin:accounts_teacher_changelist'), {'rol': 'docente'}).content.decode()
        self.assertIn('Docente Rol', teachers)
        self.assertNotIn('Admin Prueba', teachers)
