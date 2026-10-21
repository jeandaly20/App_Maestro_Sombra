import logging

from django.contrib import messages
from django.contrib.auth import login, views as auth_views
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, TemplateView

from .forms import LoginForm, NewPasswordForm, PasswordResetRequestForm, RegisterForm

logger = logging.getLogger('maestro_sombra')

REMEMBER_ME_DAYS = 14


class GuestOnlyMixin:
    """Signed-in users don't need the login or register pages."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)


class LoginView(GuestOnlyMixin, auth_views.LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get('remember_me'):
            self.request.session.set_expiry(60 * 60 * 24 * REMEMBER_ME_DAYS)
        else:
            # Shared computer: the session ends when the browser closes
            self.request.session.set_expiry(0)
        return response


class LogoutView(auth_views.LogoutView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.info(request, 'Cerraste sesión correctamente.')
        return response


class RegisterView(GuestOnlyMixin, CreateView):
    template_name = 'accounts/register.html'
    form_class = RegisterForm

    def form_valid(self, form):
        teacher = form.save()
        logger.info('Teacher account created: %s', teacher.pk)
        login(self.request, teacher, backend='django.contrib.auth.backends.ModelBackend')
        self.request.session.set_expiry(0)
        messages.success(self.request, f'¡Bienvenido, {teacher.get_short_name()}! Tu cuenta fue creada.')
        return redirect('home')


class TermsView(TemplateView):
    template_name = 'accounts/terms.html'


class PasswordResetView(GuestOnlyMixin, auth_views.PasswordResetView):
    template_name = 'accounts/password_reset.html'
    form_class = PasswordResetRequestForm
    email_template_name = 'accounts/password_reset_email.txt'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_sent')


class PasswordResetSentView(auth_views.PasswordResetDoneView):
    template_name = 'accounts/password_reset_sent.html'


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    form_class = NewPasswordForm
    success_url = reverse_lazy('login')

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info('Password reset for: %s', form.user.pk)
        messages.success(self.request, 'Tu contraseña se cambió. Ya puedes iniciar sesión.')
        return response
