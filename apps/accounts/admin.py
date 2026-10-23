from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Teacher


@admin.register(Teacher)
class TeacherAdmin(UserAdmin):
    """Administrator panel: account management (approved proposal, 2.4)."""
    ordering = ['full_name']
    list_display = ['full_name', 'email', 'specialty', 'record_status', 'is_staff']
    list_filter = ['record_status', 'is_staff', 'specialty']
    search_fields = ['full_name', 'email']
    readonly_fields = ['last_login', 'date_joined', 'terms_accepted_at']
    fieldsets = [
        (None, {'fields': ['email', 'password']}),
        ('Datos del docente', {'fields': ['full_name', 'specialty']}),
        ('Términos', {'fields': ['accepted_terms', 'terms_accepted_at']}),
        ('Permisos', {'fields': ['record_status', 'is_staff', 'is_superuser', 'groups', 'user_permissions']}),
        ('Fechas', {'fields': ['last_login', 'date_joined']}),
    ]
    add_fieldsets = [
        (None, {
            'classes': ['wide'],
            'fields': ['email', 'full_name', 'specialty', 'password1', 'password2'],
        }),
    ]
