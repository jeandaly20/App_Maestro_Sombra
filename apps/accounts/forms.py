import logging

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.core.cache import cache
from django.utils import timezone

from apps.core.forms import AccessibleFormMixin

from .models import Specialty, Teacher

logger = logging.getLogger('maestro_sombra')

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _attempts_key(email, ip):
    return f'login-attempts:{(email or "").strip().lower()}:{ip}'


class LoginForm(AccessibleFormMixin, AuthenticationForm):
    """
    Email and password sign-in.
    After 5 failed attempts that email is locked for 15 minutes (ISO/IEC 27001, control 8.5).
    """
    username = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={
            'autofocus': True, 'autocomplete': 'email', 'placeholder': 'ejemplo@correo.com',
        }),
    )
    password = forms.CharField(
        label='Contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'current-password', 'class': 'field__control field__control--with-action',
        }),
    )
    remember_me = forms.BooleanField(label='Mantener sesión iniciada en este dispositivo', required=False)

    error_messages = {
        'invalid_login': 'El correo o la contraseña no son correctos.',
        'inactive': 'Esta cuenta está desactivada. Comunícate con el administrador.',
    }

    def _client_ip(self):
        if self.request is None:
            return ''
        return self.request.META.get('REMOTE_ADDR', '')

    def clean(self):
        email = self.cleaned_data.get('username')
        key = _attempts_key(email, self._client_ip())
        if cache.get(key, 0) >= MAX_FAILED_ATTEMPTS:
            logger.warning('Sign-in locked after failed attempts: %s', email)
            raise forms.ValidationError(
                f'Demasiados intentos fallidos. Espera {LOCKOUT_MINUTES} minutos e inténtalo de nuevo.',
                code='locked',
            )
        try:
            data = super().clean()
        except forms.ValidationError:
            cache.set(key, cache.get(key, 0) + 1, LOCKOUT_MINUTES * 60)
            raise
        cache.delete(key)
        return data


class RegisterForm(AccessibleFormMixin, forms.ModelForm):
    password1 = forms.CharField(
        label='Contraseña segura',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password', 'class': 'field__control field__control--with-action',
        }),
    )
    password2 = forms.CharField(
        label='Confirmar contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'data-confirm': 'id_password1'}),
    )
    accepted_terms = forms.BooleanField(
        required=True,
        error_messages={'required': 'Debes aceptar los términos para crear la cuenta.'},
    )

    class Meta:
        model = Teacher
        fields = ['full_name', 'email', 'specialty']
        labels = {
            'full_name': 'Nombre completo del docente',
            'email': 'Correo electrónico',
            'specialty': 'Especialidad o nivel educativo',
        }
        widgets = {
            'full_name': forms.TextInput(attrs={
                'autocomplete': 'name', 'placeholder': 'Ej.: Mariana Valenzuela Ríos',
            }),
            'email': forms.EmailInput(attrs={
                'autocomplete': 'email', 'placeholder': 'mariana.docente@correo.com',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['specialty'].required = True
        self.fields['specialty'].choices = [('', 'Selecciona tu área de trabajo')] + Specialty.choices

    def clean_email(self):
        email = Teacher.objects.normalize_email(self.cleaned_data['email'])
        if Teacher.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya existe una cuenta con este correo.')
        return email

    def clean_full_name(self):
        full_name = ' '.join(self.cleaned_data['full_name'].split())
        if len(full_name.split()) < 2:
            raise forms.ValidationError('Escribe al menos un nombre y un apellido.')
        return full_name

    def clean(self):
        data = super().clean()
        password1, password2 = data.get('password1'), data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', 'Las contraseñas no coinciden.')
        return data

    def _post_clean(self):
        super()._post_clean()
        # Checks the password against the settings rules (length, common, similar to the email)
        password = self.cleaned_data.get('password1')
        if password:
            try:
                password_validation.validate_password(password, self.instance)
            except forms.ValidationError as error:
                self.add_error('password1', error)

    def save(self, commit=True):
        teacher = super().save(commit=False)
        teacher.set_password(self.cleaned_data['password1'])
        teacher.accepted_terms = True
        teacher.terms_accepted_at = timezone.now()
        if commit:
            teacher.save()
        return teacher


class PasswordResetRequestForm(AccessibleFormMixin, PasswordResetForm):
    email = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}),
    )


class NewPasswordForm(AccessibleFormMixin, SetPasswordForm):
    new_password1 = forms.CharField(
        label='Nueva contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password', 'class': 'field__control field__control--with-action',
        }),
    )
    new_password2 = forms.CharField(
        label='Confirmar nueva contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'data-confirm': 'id_new_password1'}),
    )
