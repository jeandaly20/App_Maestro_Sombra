from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Docente


@admin.register(Docente)
class DocenteAdmin(UserAdmin):
    """Panel del Administrador: gestión de cuentas de acceso (documento aprobado, 2.4)."""
    ordering = ['nombre_completo']
    list_display = ['nombre_completo', 'email', 'especialidad', 'is_active', 'is_staff']
    list_filter = ['is_active', 'is_staff', 'especialidad']
    search_fields = ['nombre_completo', 'email']
    readonly_fields = ['last_login', 'date_joined', 'fecha_aceptacion_terminos']
    fieldsets = [
        (None, {'fields': ['email', 'password']}),
        ('Datos del docente', {'fields': ['nombre_completo', 'especialidad']}),
        ('Términos', {'fields': ['acepta_terminos', 'fecha_aceptacion_terminos']}),
        ('Permisos', {'fields': ['is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions']}),
        ('Fechas', {'fields': ['last_login', 'date_joined']}),
    ]
    add_fieldsets = [
        (None, {
            'classes': ['wide'],
            'fields': ['email', 'nombre_completo', 'especialidad', 'password1', 'password2'],
        }),
    ]
