"""
Creates the application database in PostgreSQL.

Run ONCE with the superuser (postgres). Its password is typed at the prompt
and never stored. The application never uses that superuser: it connects with
its own user, which cannot create databases or other users
(least privilege, ISO/IEC 27001 control 8.2).

Usage:  venv\\Scripts\\python.exe scripts\\create_db.py
"""
import os
from getpass import getpass
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

DB_NAME = os.environ['DB_NAME']
APP_USER = os.environ['DB_USER']
APP_PASSWORD = os.environ['DB_PASSWORD']

ADMIN_PASSWORD = getpass('Contraseña del usuario postgres: ')


def connect_admin(dbname):
    return psycopg.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        user='postgres',
        password=ADMIN_PASSWORD,
        dbname=dbname,
        autocommit=True,  # CREATE DATABASE cannot run inside a transaction
    )


DATABASES = (DB_NAME,)

with connect_admin('postgres') as conn:
    if conn.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', [APP_USER]).fetchone():
        print(f'El usuario "{APP_USER}" ya existe.')
    else:
        conn.execute(sql.SQL(
            'CREATE ROLE {} LOGIN PASSWORD {} '
            'NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 20'
        ).format(sql.Identifier(APP_USER), sql.Literal(APP_PASSWORD)))
        print(f'Usuario "{APP_USER}" creado.')

    for name in DATABASES:
        if conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', [name]).fetchone():
            print(f'La base "{name}" ya existe.')
        else:
            conn.execute(sql.SQL(
                "CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0"
            ).format(sql.Identifier(name), sql.Identifier(APP_USER)))
            print(f'Base "{name}" creada.')

        # By default any server user can connect; remove that
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))

for name in DATABASES:
    with connect_admin(name) as conn:
        # Only the owner (the app user) may use the public schema
        conn.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')

print('Listo. Ahora ejecuta: venv\\Scripts\\python.exe manage.py migrate')
