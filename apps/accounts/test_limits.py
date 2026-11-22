"""
Request limits on the forms that create accounts or send e-mails, kept in the shared cache (PostgreSQL), so every
server process sees the same counters. Made-up accounts only.
"""
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import Teacher

PASSWORD = 'Clave-de-prueba-2026'


class SharedCacheTests(TestCase):
    def test_the_cache_lives_in_the_database(self):
        from django.conf import settings
        self.assertEqual(settings.CACHES['default']['BACKEND'], 'django.core.cache.backends.db.DatabaseCache')
        cache.set('prueba-compartida', 7, 30)
        self.assertEqual(cache.get('prueba-compartida'), 7)


class RegisterLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def register(self, number):
        return self.client.post(reverse('register'), {
            'full_name': f'Docente Limite {chr(65 + number)}', 'email': f'limite{number}@maestrosombra.test',
            'password1': PASSWORD, 'password2': PASSWORD, 'specialty': 'BASIC_EDUCATION', 'accepted_terms': 'on',
        })

    def test_one_connection_creates_at_most_five_accounts_an_hour(self):
        for number in range(views.ACCOUNTS_PER_IP):
            self.assertRedirects(self.register(number), reverse('home'), fetch_redirect_response=False)
            self.client.logout()
        response = self.register(views.ACCOUNTS_PER_IP)
        self.assertContains(response, 'demasiadas solicitudes')
        self.assertFalse(Teacher.objects.filter(email=f'limite{views.ACCOUNTS_PER_IP}@maestrosombra.test').exists())


class PasswordResetLimitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.correo@maestrosombra.test', PASSWORD,
                                                  full_name='Docente Correo', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())

    def setUp(self):
        cache.clear()

    def reset(self, email):
        return self.client.post(reverse('password_reset'), {'email': email})

    def test_a_teacher_gets_at_most_three_reset_emails_an_hour_and_the_page_looks_the_same(self):
        for _ in range(views.RESETS_PER_EMAIL + 2):
            self.assertRedirects(self.reset(self.teacher.email), reverse('password_reset_sent'),
                                 fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), views.RESETS_PER_EMAIL)

    def test_one_connection_asks_at_most_ten_resets_an_hour(self):
        for number in range(views.RESETS_PER_IP):
            self.reset(f'otra{number}@maestrosombra.test')
        self.assertContains(self.reset(self.teacher.email), 'demasiadas solicitudes')
        self.assertEqual(len(mail.outbox), 0)
