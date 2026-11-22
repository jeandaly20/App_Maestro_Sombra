"""
Backups of the database and the uploaded files (ISO/IEC 27001, control 8.13), and their restoration.

Each backup is a folder "respaldo-YYYYMMDD-HHMMSS" in settings.BACKUP_DIR with:
- base.dump: the whole PostgreSQL database (pg_dump, custom format, restorable with pg_restore);
- archivos.zip: the media folder (worksheets, figures and the encrypted documents of the students).
Only the newest settings.BACKUP_KEEP folders are kept. The database password travels only in the PGPASSWORD
variable of the child process: never on the command line nor in the log. The students' personal data stay
encrypted inside the backup, so it is only readable with FIELD_ENCRYPTION_KEY (kept apart, in .env).
"""
import glob
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

FOLDER = re.compile(r'^respaldo-\d{8}-\d{6}$')
DUMP, FILES = 'base.dump', 'archivos.zip'
TIMEOUT_SECONDS = 15 * 60


class BackupError(Exception):
    pass


def pg_tool(name):
    """pg_dump or pg_restore: from PG_BIN_DIR, the PATH, or PostgreSQL's default Windows folder (newest first)."""
    executable = name + ('.exe' if os.name == 'nt' else '')
    if settings.PG_BIN_DIR:
        candidate = Path(settings.PG_BIN_DIR) / executable
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    installed = sorted(glob.glob(f'C:/Program Files/PostgreSQL/*/bin/{executable}'),
                       key=lambda path: int(Path(path).parent.parent.name) if Path(path).parent.parent.name.isdigit() else 0,
                       reverse=True)
    if installed:
        return installed[0]
    raise BackupError(f'No se encontró {name}. Instala PostgreSQL o indica su carpeta bin en PG_BIN_DIR (.env).')


def _database(alias='default'):
    db = settings.DATABASES[alias]
    args = ['-h', db.get('HOST') or 'localhost', '-p', str(db.get('PORT') or 5432), '-U', db['USER']]
    env = {**os.environ, 'PGPASSWORD': db.get('PASSWORD') or ''}
    return db['NAME'], args, env


def _run(command, env):
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    if result.returncode != 0:
        # pg_dump's own message (it never contains the password); trimmed so the log stays readable
        raise BackupError(f'{Path(command[0]).stem} terminó con error: {result.stderr.strip()[-400:]}')


def backups(directory=None):
    """The backup folders, oldest first."""
    directory = Path(directory or settings.BACKUP_DIR)
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir() and FOLDER.match(path.name))


def prune(keep=None, directory=None):
    """Deletes the oldest backups beyond `keep`; returns the removed folders."""
    keep = settings.BACKUP_KEEP if keep is None else keep
    folders = backups(directory)
    removed = folders[:-keep] if keep > 0 else folders
    for folder in removed:
        shutil.rmtree(folder)
    return removed


def zip_media(target, media_root=None):
    media_root = Path(media_root or settings.MEDIA_ROOT)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        if media_root.exists():
            for path in sorted(media_root.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(media_root).as_posix())


def make_backup(now=None, alias='default'):
    """Creates one backup folder and prunes the old ones. Returns the new folder."""
    now = now or datetime.now()
    folder = Path(settings.BACKUP_DIR) / f'respaldo-{now:%Y%m%d-%H%M%S}'
    folder.mkdir(parents=True, exist_ok=False)
    try:
        name, args, env = _database(alias)
        _run([pg_tool('pg_dump'), '-Fc', *args, '-f', str(folder / DUMP), name], env)
        zip_media(folder / FILES)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)  # never leave a half backup that looks complete
        raise
    prune()
    return folder


def restore(folder, alias='default', media_root=None):
    """
    Puts the database back as it was in the backup (its tables are replaced) and extracts its files over the
    media folder. Files uploaded after the backup are left where they are.
    """
    folder = Path(folder)
    dump, files = folder / DUMP, folder / FILES
    if not dump.exists():
        raise BackupError(f'La carpeta {folder} no tiene {DUMP}: no es un respaldo de Maestro Sombra.')
    name, args, env = _database(alias)
    _run([pg_tool('pg_restore'), '--clean', '--if-exists', '--no-owner', *args, '-d', name, str(dump)], env)
    if files.exists():
        media_root = Path(media_root or settings.MEDIA_ROOT)
        with zipfile.ZipFile(files) as archive:
            for member in archive.namelist():
                target = (media_root / member).resolve()
                if not target.is_relative_to(media_root.resolve()):  # a path that tries to leave the media folder
                    raise BackupError(f'El archivo {member} del respaldo apunta fuera de la carpeta de archivos.')
            archive.extractall(media_root)
