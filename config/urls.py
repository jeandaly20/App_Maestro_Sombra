from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'Maestro Sombra · Administración'
admin.site.site_title = 'Maestro Sombra'
admin.site.index_title = 'Panel del administrador'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('cuenta/', include('apps.accounts.urls')),
    path('alumnos/', include('apps.students.urls')),
    path('', include('apps.core.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
