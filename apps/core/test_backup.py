"""
Backups (ISO/IEC 27001, control 8.13): folder, retention, cleanup on failure and a safe restore. pg_dump and
pg_restore are simulated here; the real round trip (backup and restore of the test database with the same rows)
was checked by hand. Temporary folders only.
"""
import tempfile
import zipfile
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from apps.core import backup


def fake_pg_dump(command, env):
    # Writes the file pg_dump would write, and checks the password never travels on the command line
    assert env['PGPASSWORD'] not in [part for part in command if part] or not env['PGPASSWORD']
    Path(command[command.index('-f') + 1]).write_bytes(b'PGDMP-prueba')


class BackupTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'
        (self.media / 'materials').mkdir(parents=True)
        (self.media / 'materials' / 'guia.pdf').write_bytes(b'%PDF-prueba')
        settings_override = override_settings(BACKUP_DIR=self.root / 'respaldos', BACKUP_KEEP=3,
                                              MEDIA_ROOT=self.media)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def test_a_backup_holds_the_database_and_the_files(self):
        with mock.patch.object(backup, '_run', fake_pg_dump), mock.patch.object(backup, 'pg_tool', lambda n: n):
            folder = backup.make_backup(now=datetime(2026, 11, 21, 3, 0, 0))
        self.assertEqual(folder.name, 'respaldo-20261121-030000')
        self.assertEqual((folder / 'base.dump').read_bytes(), b'PGDMP-prueba')
        with zipfile.ZipFile(folder / 'archivos.zip') as archive:
            self.assertEqual(archive.namelist(), ['materials/guia.pdf'])

    def test_only_the_newest_backups_are_kept(self):
        with mock.patch.object(backup, '_run', fake_pg_dump), mock.patch.object(backup, 'pg_tool', lambda n: n):
            for day in range(1, 6):
                backup.make_backup(now=datetime(2026, 11, day, 3, 0, 0))
        self.assertEqual([f.name for f in backup.backups()],
                         ['respaldo-20261103-030000', 'respaldo-20261104-030000', 'respaldo-20261105-030000'])

    def test_a_failed_backup_leaves_no_half_folder(self):
        def failing(command, env):
            raise backup.BackupError('pg_dump terminó con error: sin conexión')
        with mock.patch.object(backup, '_run', failing), mock.patch.object(backup, 'pg_tool', lambda n: n):
            with self.assertRaises(backup.BackupError):
                backup.make_backup(now=datetime(2026, 11, 21, 3, 0, 0))
        self.assertEqual(backup.backups(), [])

    def test_restore_refuses_a_file_that_points_outside_the_media_folder(self):
        folder = self.root / 'respaldos' / 'respaldo-20261121-030000'
        folder.mkdir(parents=True)
        (folder / 'base.dump').write_bytes(b'PGDMP-prueba')
        with zipfile.ZipFile(folder / 'archivos.zip', 'w') as archive:
            archive.writestr('../fuera.txt', 'no')
        with mock.patch.object(backup, '_run'), mock.patch.object(backup, 'pg_tool', lambda n: n):
            with self.assertRaisesMessage(backup.BackupError, 'fuera de la carpeta'):
                backup.restore(folder)
        self.assertFalse((self.root / 'fuera.txt').exists())

    def test_the_restore_command_needs_confirmation(self):
        folder = self.root / 'respaldos' / 'respaldo-20261121-030000'
        folder.mkdir(parents=True)
        (folder / 'base.dump').write_bytes(b'PGDMP-prueba')
        with mock.patch.object(backup, 'restore') as restore:
            with self.assertRaisesMessage(CommandError, '--confirmar'):
                call_command('restaurar_respaldo', str(folder), stdout=StringIO())
            restore.assert_not_called()
            call_command('restaurar_respaldo', str(folder), '--confirmar', stdout=StringIO())
            restore.assert_called_once()

    def test_the_password_goes_only_in_the_environment(self):
        name, args, env = backup._database()
        self.assertNotIn('PGPASSWORD', ' '.join(args))
        self.assertIn('PGPASSWORD', env)
