"""
python manage.py respaldar_base

Backs up the database and the uploaded files into BACKUP_DIR and keeps the newest BACKUP_KEEP backups. Meant to
run every day from the Windows Task Scheduler (or cron on a server).
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core import backup

logger = logging.getLogger('maestro_sombra')


class Command(BaseCommand):
    help = 'Respalda la base de datos y los archivos subidos (conserva los últimos BACKUP_KEEP respaldos).'

    def handle(self, *args, **options):
        try:
            folder = backup.make_backup()
        except (backup.BackupError, OSError, TimeoutError) as error:
            logger.error('Backup failed: %s', error)
            raise CommandError(f'No se pudo crear el respaldo: {error}') from error
        size = sum(path.stat().st_size for path in folder.iterdir()) / (1024 * 1024)
        kept = len(backup.backups())
        logger.info('Backup created: %s (%.1f MB, %s kept)', folder.name, size, kept)
        self.stdout.write(self.style.SUCCESS(f'Respaldo creado: {folder} ({size:.1f} MB).'))
        self.stdout.write(f'Se conservan {kept} respaldo(s); máximo {settings.BACKUP_KEEP}.')
        self.stdout.write('Recuerda: sin FIELD_ENCRYPTION_KEY (archivo .env) los datos cifrados del respaldo no se '
                          'pueden leer. Guarda esa clave en un lugar seguro, aparte del respaldo.')
