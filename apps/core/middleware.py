"""Separates the two roles of the system, which share one table of users (accounts.Teacher)."""
from django.shortcuts import redirect

# What an administrator may open outside the admin panel: its files and signing out
ADMIN_ALLOWED = ('/admin/', '/static/', '/cuenta/salir/')


class AdminPanelOnlyMiddleware:
    """
    An administrator (is_staff) manages accounts and catalogs in /admin/, and is never a teacher: any page of the
    teaching modules (students, adaptations, evaluations, history) takes them to the panel. So an administrator
    neither registers students nor counts as a teacher anywhere, and never sees a student's personal data (the
    panel hides names, observations and profiles).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.is_staff \
                and not request.path.startswith(ADMIN_ALLOWED):
            return redirect('admin:index')
        return self.get_response(request)
