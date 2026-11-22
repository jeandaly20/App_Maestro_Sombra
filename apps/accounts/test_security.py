"""
Sign-in protection (ISO/IEC 27001, control 8.5) and what the access log keeps (control 8.15, LOPDP).
Made-up accounts only.
"""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import forms
from .models import Teacher
from .signals import masked_email

PASSWORD = 'Clave-de-prueba-2026'
LOCKED = 'Demasiados intentos fallidos'


class SignInProtectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.seguro@maestrosombra.test', PASSWORD,
                                                  full_name='Docente Seguro', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())

    def setUp(self):
        cache.clear()

    def sign_in(self, email, password):
        return self.client.post(reverse('login'), {'username': email, 'password': password})

    def test_the_right_password_signs_in(self):
        self.assertRedirects(self.sign_in(self.teacher.email, PASSWORD), reverse('home'),
                             fetch_redirect_response=False)

    def test_an_account_is_locked_after_five_failed_attempts_even_with_the_right_password(self):
        for _ in range(forms.MAX_FAILED_ATTEMPTS):
            self.assertNotContains(self.sign_in(self.teacher.email, 'otra-clave'), LOCKED)
        self.assertContains(self.sign_in(self.teacher.email, PASSWORD), LOCKED)

    def test_one_password_tried_on_many_accounts_locks_the_address(self):
        # Password spraying: never 5 tries on the same account, so only the per-address limit can stop it
        for number in range(forms.MAX_FAILED_PER_IP):
            self.assertNotContains(self.sign_in(f'cuenta{number}@maestrosombra.test', PASSWORD), LOCKED)
        self.assertContains(self.sign_in(self.teacher.email, PASSWORD), LOCKED)

    def test_signing_in_to_ones_own_account_does_not_clear_the_failures_of_the_address(self):
        for number in range(forms.MAX_FAILED_PER_IP - 1):
            self.sign_in(f'cuenta{number}@maestrosombra.test', PASSWORD)
        self.sign_in(self.teacher.email, PASSWORD)  # the attacker's own valid account
        self.client.logout()
        self.sign_in('otra.cuenta@maestrosombra.test', PASSWORD)
        self.assertContains(self.sign_in(self.teacher.email, PASSWORD), LOCKED)

    def test_the_log_never_keeps_the_whole_email(self):
        with self.assertLogs('maestro_sombra', level='WARNING') as logs:
            self.sign_in(self.teacher.email, 'otra-clave')
        text = '\n'.join(logs.output)
        self.assertNotIn(self.teacher.email, text)
        self.assertIn('d******@maestrosombra.test', text)

    def test_the_lock_message_in_the_log_is_masked_too(self):
        for _ in range(forms.MAX_FAILED_ATTEMPTS):
            self.sign_in(self.teacher.email, 'otra-clave')
        with self.assertLogs('maestro_sombra', level='WARNING') as logs:
            self.sign_in(self.teacher.email, PASSWORD)
        self.assertNotIn(self.teacher.email, '\n'.join(logs.output))


class MaskedEmailTests(TestCase):
    def test_only_the_first_letter_and_the_domain_remain(self):
        self.assertEqual(masked_email('Docente.Seguro@UNEMI.edu.ec'), 'd******@unemi.edu.ec')
        self.assertEqual(masked_email('ab@x.test'), 'a*@x.test')
        self.assertEqual(masked_email(''), '(vacío)')
        self.assertEqual(masked_email(None), '(vacío)')
