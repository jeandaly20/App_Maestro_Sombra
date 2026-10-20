from django.contrib import admin

from .models import Estudiante


@admin.register(Estudiante)
class EstudianteAdmin(admin.ModelAdmin):
    # El administrador solo ve seudónimos: el nombre real es del docente (LOPDP)
    list_display = ['seudonimo', 'grado', 'condiciones', 'creado']
    list_filter = ['grado']
    exclude = ['nombre', 'observaciones', 'fecha_nacimiento']
