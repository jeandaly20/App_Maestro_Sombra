from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'Maestro Sombra · Administración'
admin.site.site_title = 'Maestro Sombra'
admin.site.index_title = 'Panel del administrador'

# Uploaded worksheets are NOT served from /media/: only their owner can open them
# through apps.adaptations.views.MaterialFileView (LOPDP).
urlpatterns = [
    path('admin/', admin.site.urls),
    path('cuenta/', include('apps.accounts.urls')),
    path('alumnos/', include('apps.students.urls')),
    path('adaptar/', include('apps.adaptations.urls')),
    path('', include('apps.core.urls')),
]
