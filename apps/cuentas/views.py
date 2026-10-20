import logging

from django.contrib import messages
from django.contrib.auth import login, views as auth_views
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, TemplateView

from .forms import FormularioIngreso, FormularioNuevaClave, FormularioRecuperar, FormularioRegistro

logger = logging.getLogger('maestro_sombra')

DIAS_RECORDAR_SESION = 14


class SoloInvitadosMixin:
    """Quien ya inició sesión no necesita ver el login ni el registro."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('inicio')
        return super().dispatch(request, *args, **kwargs)


class IngresoView(SoloInvitadosMixin, auth_views.LoginView):
    template_name = 'cuentas/ingreso.html'
    authentication_form = FormularioIngreso

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        if form.cleaned_data.get('recordar'):
            self.request.session.set_expiry(60 * 60 * 24 * DIAS_RECORDAR_SESION)
        else:
            # Equipo compartido: la sesión termina al cerrar el navegador
            self.request.session.set_expiry(0)
        return respuesta


class SalidaView(auth_views.LogoutView):
    def post(self, request, *args, **kwargs):
        respuesta = super().post(request, *args, **kwargs)
        messages.info(request, 'Cerraste sesión correctamente.')
        return respuesta


class RegistroView(SoloInvitadosMixin, CreateView):
    template_name = 'cuentas/registro.html'
    form_class = FormularioRegistro

    def form_valid(self, form):
        docente = form.save()
        logger.info('Cuenta de docente creada: %s', docente.pk)
        login(self.request, docente, backend='django.contrib.auth.backends.ModelBackend')
        self.request.session.set_expiry(0)
        messages.success(self.request, f'¡Bienvenido, {docente.get_short_name()}! Tu cuenta fue creada.')
        return redirect('inicio')


class TerminosView(TemplateView):
    template_name = 'cuentas/terminos.html'


class RecuperarView(SoloInvitadosMixin, auth_views.PasswordResetView):
    template_name = 'cuentas/recuperar.html'
    form_class = FormularioRecuperar
    email_template_name = 'cuentas/correo_recuperar.txt'
    subject_template_name = 'cuentas/correo_recuperar_asunto.txt'
    success_url = reverse_lazy('recuperar_enviado')


class RecuperarEnviadoView(auth_views.PasswordResetDoneView):
    template_name = 'cuentas/recuperar_enviado.html'


class NuevaClaveView(auth_views.PasswordResetConfirmView):
    template_name = 'cuentas/nueva_clave.html'
    form_class = FormularioNuevaClave
    success_url = reverse_lazy('login')

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        logger.info('Contraseña restablecida para: %s', form.user.pk)
        messages.success(self.request, 'Tu contraseña se cambió. Ya puedes iniciar sesión.')
        return respuesta
