from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Teacher


class RoleFilter(admin.SimpleListFilter):
    """The two roles that share the table of users: a teacher, or an administrator (is_staff)."""
    title = 'rol'
    parameter_name = 'rol'

    def lookups(self, request, model_admin):
        return [('docente', 'Docente'), ('administrador', 'Administrador')]

    def queryset(self, request, queryset):
        if self.value() == 'docente':
            return queryset.filter(is_staff=False)
        if self.value() == 'administrador':
            return queryset.filter(is_staff=True)
        return queryset


@admin.register(Teacher)
class TeacherAdmin(UserAdmin):
    """
    Administrator panel: account management (approved proposal, 2.4). Teachers and administrators share this
    table; the role column tells them apart, and an administrator never works as a teacher (core.middleware).
    """
    ordering = ['full_name']
    list_display = ['full_name', 'email', 'role', 'specialty', 'record_status']
    list_filter = [RoleFilter, 'record_status', 'specialty']
    search_fields = ['full_name', 'email']
    readonly_fields = ['last_login', 'date_joined', 'terms_accepted_at']
    fieldsets = [
        (None, {'fields': ['email', 'password']}),
        ('Datos del docente', {'fields': ['full_name', 'specialty']}),
        ('Términos', {'fields': ['accepted_terms', 'terms_accepted_at']}),
        ('Rol y permisos', {
            'fields': ['record_status', 'is_staff', 'is_superuser', 'groups', 'user_permissions'],
            'description': 'Con «Es staff» marcado la cuenta es de administrador: solo entra a este panel, no '
                           'registra estudiantes ni usa los módulos del docente.',
        }),
        ('Fechas', {'fields': ['last_login', 'date_joined']}),
    ]
    add_fieldsets = [
        (None, {
            'classes': ['wide'],
            'fields': ['email', 'full_name', 'specialty', 'password1', 'password2'],
        }),
    ]

    @admin.display(description='Rol', ordering='is_staff')
    def role(self, teacher):
        return 'Administrador' if teacher.is_staff else 'Docente'
