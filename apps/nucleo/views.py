from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class InicioView(LoginRequiredMixin, TemplateView):
    """Panel principal. Sus tarjetas y métricas se construyen en la parte del panel."""
    template_name = 'nucleo/inicio.html'
