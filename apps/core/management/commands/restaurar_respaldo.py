"""
python manage.py restaurar_respaldo <carpeta del respaldo> --confirmar

Puts the database and the uploaded files back as they were in a backup made by respaldar_base. It replaces the
current data, so it refuses to run without --confirmar. Stop the server before restoring.
"""
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core import backup

logger = logging.getLogger('maestro_sombra')


class Command(BaseCommand):
    help = 'Restaura la base de datos y los archivos desde un respaldo (reemplaza los datos actuales).'

    def add_arguments(self, parser):
        parser.add_argument('carpeta', help='Carpeta del respaldo, por ejemplo respaldos/respaldo-20261121-030000')
        parser.add_argument('--confirmar', action='store_true',
                            help='Confirma que se reemplacen los datos actuales por los del respaldo.')

    def handle(self, *args, carpeta, confirmar, **options):
        folder = Path(carpeta)
        if not (folder / backup.DUMP).exists():
            raise CommandError(f'{folder} no es un respaldo de Maestro Sombra (no tiene {backup.DUMP}).')
        if not confirmar:
            raise CommandError(f'Esto reemplaza la base de datos y los archivos actuales por los del respaldo '
                               f'{folder.name}. Detén el servidor, haz un respaldo de lo actual y vuelve a '
                               f'ejecutar el comando con --confirmar.')
        try:
            backup.restore(folder)
        except (backup.BackupError, OSError, TimeoutError) as error:
            logger.error('Restore failed: %s', error)
            raise CommandError(f'No se pudo restaurar: {error}') from error
        logger.warning('Backup restored: %s', folder.name)
        self.stdout.write(self.style.SUCCESS(f'Se restauró el respaldo {folder.name}. Ya puedes iniciar el servidor.'))
