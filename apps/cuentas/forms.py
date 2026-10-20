import logging

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.core.cache import cache
from django.utils import timezone

from apps.nucleo.forms import FormularioAccesible

from .models import Docente, Especialidad

logger = logging.getLogger('maestro_sombra')

INTENTOS_MAXIMOS = 5
MINUTOS_BLOQUEO = 15


def _clave_intentos(email, ip):
    return f'ingreso:{(email or "").strip().lower()}:{ip}'


class FormularioIngreso(FormularioAccesible, AuthenticationForm):
    """
    Inicio de sesión con correo y contraseña.
    Tras 5 intentos fallidos se bloquea ese correo 15 minutos (ISO/IEC 27001, control 8.5).
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
            'autocomplete': 'current-password', 'class': 'campo__control campo__control--con-accion',
        }),
    )
    recordar = forms.BooleanField(label='Mantener sesión iniciada en este dispositivo', required=False)

    error_messages = {
        'invalid_login': 'El correo o la contraseña no son correctos.',
        'inactive': 'Esta cuenta está desactivada. Comunícate con el administrador.',
    }

    def _ip(self):
        if self.request is None:
            return ''
        return self.request.META.get('REMOTE_ADDR', '')

    def clean(self):
        email = self.cleaned_data.get('username')
        clave = _clave_intentos(email, self._ip())
        if cache.get(clave, 0) >= INTENTOS_MAXIMOS:
            logger.warning('Ingreso bloqueado por intentos fallidos: %s', email)
            raise forms.ValidationError(
                f'Demasiados intentos fallidos. Espera {MINUTOS_BLOQUEO} minutos e inténtalo de nuevo.',
                code='bloqueado',
            )
        try:
            datos = super().clean()
        except forms.ValidationError:
            cache.set(clave, cache.get(clave, 0) + 1, MINUTOS_BLOQUEO * 60)
            raise
        cache.delete(clave)
        return datos


class FormularioRegistro(FormularioAccesible, forms.ModelForm):
    password1 = forms.CharField(
        label='Contraseña segura',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password', 'class': 'campo__control campo__control--con-accion',
        }),
    )
    password2 = forms.CharField(
        label='Confirmar contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'data-confirma': 'id_password1'}),
    )
    acepta_terminos = forms.BooleanField(
        required=True,
        error_messages={'required': 'Debes aceptar los términos para crear la cuenta.'},
    )

    class Meta:
        model = Docente
        fields = ['nombre_completo', 'email', 'especialidad']
        labels = {
            'nombre_completo': 'Nombre completo del docente',
            'email': 'Correo electrónico',
            'especialidad': 'Especialidad o nivel educativo',
        }
        widgets = {
            'nombre_completo': forms.TextInput(attrs={
                'autocomplete': 'name', 'placeholder': 'Ej.: Mariana Valenzuela Ríos',
            }),
            'email': forms.EmailInput(attrs={
                'autocomplete': 'email', 'placeholder': 'mariana.docente@correo.com',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['especialidad'].required = True
        self.fields['especialidad'].choices = [('', 'Selecciona tu área de trabajo')] + Especialidad.choices

    def clean_email(self):
        email = Docente.objects.normalize_email(self.cleaned_data['email'])
        if Docente.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya existe una cuenta con este correo.')
        return email

    def clean_nombre_completo(self):
        nombre = ' '.join(self.cleaned_data['nombre_completo'].split())
        if len(nombre.split()) < 2:
            raise forms.ValidationError('Escribe al menos un nombre y un apellido.')
        return nombre

    def clean(self):
        datos = super().clean()
        clave1, clave2 = datos.get('password1'), datos.get('password2')
        if clave1 and clave2 and clave1 != clave2:
            self.add_error('password2', 'Las contraseñas no coinciden.')
        return datos

    def _post_clean(self):
        super()._post_clean()
        # Valida la contraseña con las reglas de settings (longitud, comunes, similitud con el correo)
        clave = self.cleaned_data.get('password1')
        if clave:
            try:
                password_validation.validate_password(clave, self.instance)
            except forms.ValidationError as error:
                self.add_error('password1', error)

    def save(self, commit=True):
        docente = super().save(commit=False)
        docente.set_password(self.cleaned_data['password1'])
        docente.acepta_terminos = True
        docente.fecha_aceptacion_terminos = timezone.now()
        if commit:
            docente.save()
        return docente


class FormularioRecuperar(FormularioAccesible, PasswordResetForm):
    email = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}),
    )


class FormularioNuevaClave(FormularioAccesible, SetPasswordForm):
    new_password1 = forms.CharField(
        label='Nueva contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password', 'class': 'campo__control campo__control--con-accion',
        }),
    )
    new_password2 = forms.CharField(
        label='Confirmar nueva contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'data-confirma': 'id_new_password1'}),
    )
