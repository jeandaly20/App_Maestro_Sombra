"""
Crea la base de datos de la aplicación en PostgreSQL.

Se ejecuta UNA sola vez con el superusuario (postgres), que pide la
contraseña por teclado y no la guarda. La aplicación nunca usa ese
superusuario: se conecta con un usuario propio que no puede crear bases
de datos ni otros usuarios (principio de mínimo privilegio, ISO/IEC 27001 A.8.2).

Uso:  venv\\Scripts\\python.exe scripts\\crear_bd.py
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


def conectar_admin(dbname):
    return psycopg.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        user='postgres',
        password=ADMIN_PASSWORD,
        dbname=dbname,
        autocommit=True,  # CREATE DATABASE no puede ir dentro de una transacción
    )


with conectar_admin('postgres') as conn:
    if conn.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', [APP_USER]).fetchone():
        print(f'El usuario "{APP_USER}" ya existe.')
    else:
        conn.execute(sql.SQL(
            'CREATE ROLE {} LOGIN PASSWORD {} '
            'NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 20'
        ).format(sql.Identifier(APP_USER), sql.Literal(APP_PASSWORD)))
        print(f'Usuario "{APP_USER}" creado.')

    # La base de la app y la base de pruebas (los tests la reutilizan con --keepdb,
    # así el usuario de la app no necesita permiso para crear bases)
    for nombre in (DB_NAME, f'test_{DB_NAME}'):
        if conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', [nombre]).fetchone():
            print(f'La base "{nombre}" ya existe.')
        else:
            conn.execute(sql.SQL(
                "CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0"
            ).format(sql.Identifier(nombre), sql.Identifier(APP_USER)))
            print(f'Base "{nombre}" creada.')

        # Por defecto cualquier usuario del servidor puede conectarse; lo quitamos
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC')
                     .format(sql.Identifier(nombre)))

for nombre in (DB_NAME, f'test_{DB_NAME}'):
    with conectar_admin(nombre) as conn:
        # Solo el dueño (el usuario de la app) puede usar el esquema public
        conn.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')

print('Listo. Ahora ejecuta: venv\\Scripts\\python.exe manage.py migrate')
print('Para las pruebas: venv\\Scripts\\python.exe manage.py test --keepdb')
