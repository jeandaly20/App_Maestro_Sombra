from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class HomeView(LoginRequiredMixin, TemplateView):
    """Main dashboard. Its cards and metrics are built in the dashboard part."""
    template_name = 'core/home.html'
