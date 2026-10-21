from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import Teacher

PASSWORD = 'Aula-Inclusiva-2026'


def register_data(**changes):
    data = {
        'full_name': 'Mariana Valenzuela Ríos',
        'email': 'Mariana@Correo.com',
        'password1': PASSWORD,
        'password2': PASSWORD,
        'specialty': 'SHADOW_TEACHER',
        'accepted_terms': 'on',
    }
    data.update(changes)
    return data


class RegisterTests(TestCase):

    def test_register_creates_account_and_signs_in(self):
        response = self.client.post(reverse('register'), register_data())
        self.assertRedirects(response, reverse('home'))
        teacher = Teacher.objects.get()
        self.assertEqual(teacher.email, 'mariana@correo.com')
        self.assertTrue(teacher.accepted_terms)
        self.assertIsNotNone(teacher.terms_accepted_at)
        self.assertNotEqual(teacher.password, PASSWORD)  # only the hash is stored
        self.assertEqual(self.client.session['_auth_user_id'], str(teacher.pk))

    def test_terms_are_required(self):
        data = register_data()
        del data['accepted_terms']
        response = self.client.post(reverse('register'), data)
        self.assertContains(response, 'Debes aceptar los términos')
        self.assertFalse(Teacher.objects.exists())

    def test_duplicate_email_ignores_case(self):
        Teacher.objects.create_user('mariana@correo.com', PASSWORD, full_name='Otra Persona')
        response = self.client.post(reverse('register'), register_data(email='MARIANA@correo.com'))
        self.assertContains(response, 'Ya existe una cuenta con este correo')

    def test_passwords_must_match(self):
        response = self.client.post(reverse('register'), register_data(password2='Otra-Clave-2026'))
        self.assertContains(response, 'Las contraseñas no coinciden')

    def test_weak_password_is_rejected_in_spanish(self):
        response = self.client.post(reverse('register'), register_data(password1='12345678', password2='12345678'))
        self.assertFalse(Teacher.objects.exists())
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'contraseña')

    def test_full_name_needs_last_name(self):
        response = self.client.post(reverse('register'), register_data(full_name='Mariana'))
        self.assertContains(response, 'al menos un nombre y un apellido')

    def test_messages_stay_in_spanish_with_english_browser(self):
        response = self.client.post(reverse('register'), {}, HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
        self.assertContains(response, 'Este campo es obligatorio')
        self.assertNotContains(response, 'This field is required')


class LoginTests(TestCase):

    def setUp(self):
        cache.clear()
        self.teacher = Teacher.objects.create_user('ana@correo.com', PASSWORD, full_name='Ana Pérez')

    def sign_in(self, password=PASSWORD, **extra):
        return self.client.post(reverse('login'), {'username': 'ANA@correo.com', 'password': password, **extra})

    def test_sign_in_without_remember_me(self):
        response = self.sign_in()
        self.assertRedirects(response, reverse('home'))
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_remember_me_keeps_session_14_days(self):
        self.sign_in(remember_me='on')
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), 60 * 60 * 24 * 14)

    def test_wrong_password(self):
        response = self.sign_in(password='mala')
        self.assertContains(response, 'El correo o la contraseña no son correctos')

    def test_lockout_after_five_failed_attempts(self):
        for _ in range(5):
            self.sign_in(password='mala')
        response = self.sign_in()  # even with the right password
        self.assertContains(response, 'Demasiados intentos fallidos')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_home_requires_sign_in(self):
        response = self.client.get(reverse('home'))
        self.assertRedirects(response, f"{reverse('login')}?next=/")

    def test_logout(self):
        self.sign_in()
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)


class PasswordResetTests(TestCase):

    def test_sends_email_and_changes_password(self):
        Teacher.objects.create_user('ana@correo.com', PASSWORD, full_name='Ana Pérez')
        self.client.post(reverse('password_reset'), {'email': 'ana@correo.com'})
        self.assertEqual(len(mail.outbox), 1)
        link = next(line for line in mail.outbox[0].body.splitlines() if '/recuperar/' in line)
        path = link.split('testserver')[1]
        form_page = self.client.get(path, follow=True)
        new_password = 'Nueva-Clave-Segura-9'
        response = self.client.post(form_page.redirect_chain[-1][0],
                                    {'new_password1': new_password, 'new_password2': new_password})
        self.assertRedirects(response, reverse('login'))
        self.assertTrue(Teacher.objects.get().check_password(new_password))

    def test_unknown_email_reveals_nothing(self):
        response = self.client.post(reverse('password_reset'), {'email': 'nadie@correo.com'})
        self.assertRedirects(response, reverse('password_reset_sent'))
        self.assertEqual(len(mail.outbox), 0)
