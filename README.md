# Maestro Sombra

Aplicativo web de apoyo para docentes sombra en la automatización de adaptaciones curriculares inclusivas.
Caso práctico de titulación · Ingeniería de Software · Universidad Estatal de Milagro (UNEMI).

## Tecnologías

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12 · Django 5.0 (arquitectura MVT) |
| Base de datos | PostgreSQL 17 · campos JSONB, arreglos, CHECK, UNIQUE y trigger |
| Frontend | HTML5 semántico · CSS3 · JavaScript nativo (ES6+) |
| Seguridad | Cifrado Fernet de datos personales · usuario de BD con mínimo privilegio · claves en `.env` |
| PDF | xhtml2pdf con tipografía Atkinson Hyperlegible (expedientes y guías adaptadas) |
| IA | API REST de Google Gemini (`gemini-flash-latest`, con respaldo `gemini-flash-lite-latest`) |
| Word | python-docx (lectura de guías .docx) |

El código y la base de datos están en inglés; todo lo que ve el usuario está en español.

## Estructura

```
APP maestro sombra/
├── config/                 Configuración del proyecto (settings, urls)
├── apps/
│   ├── core/               Catálogos compartidos, campo cifrado, panel de inicio
│   ├── accounts/           Docentes: iniciar sesión, registro, recuperar contraseña
│   ├── students/           Estudiantes con NEE (máximo 3 por docente)
│   ├── materials/          Materias y guías originales que sube el docente
│   ├── adaptations/        Adaptaciones curriculares generadas por la IA
│   └── evaluations/        Evaluación del docente después de aplicar la guía
├── templates/              Pantallas en HTML5
├── static/                 Estilos (css/) y JavaScript (js/)
├── scripts/create_db.py    Crea la base de datos en PostgreSQL (una sola vez)
├── requirements.txt        Librerías de Python
└── .env.example            Plantilla de configuración (el .env real no se sube a Git)
```

## Avance

| Parte | Estado |
|---|---|
| 0. Proyecto, base de datos (6 tablas del MER) y seguridad | ✅ Terminada |
| 1. Estilos, iniciar sesión, registro de docente, recuperar contraseña | ✅ Terminada |
| 2. Registro de alumnos NEE, directorio y expedientes en PDF | ✅ Terminada |
| 3. Adaptar material con IA (Gemini): carga, comparativa, ajustes, regenerar y PDF | ✅ Terminada |
| 4. Banco de adaptaciones, evaluación en aula, recomendación de la IA y baja de estudiantes | ✅ Terminada |
| 5. Panel principal con métricas y actividad reciente | ✅ Terminada |
| 6. Validación con guías reales y medición del tiempo frente al proceso manual | Pendiente |

## Cómo ejecutarlo

Requisitos: Python 3.12 y PostgreSQL 17 encendido.

```bash
venv\Scripts\python.exe manage.py runserver
```

Abrir http://127.0.0.1:8000 en el navegador.

### Primera instalación (en otro equipo)

```bash
py -3.12 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
venv\Scripts\python.exe scripts\create_db.py
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py createsuperuser
```

Antes de `create_db.py`, completa el `.env` (clave secreta, clave de cifrado y contraseña del usuario `maestro_app`).

## Direcciones

| Pantalla | Dirección |
|---|---|
| Iniciar sesión | `/cuenta/ingresar/` |
| Registro de docente | `/cuenta/registro/` |
| Recuperar contraseña | `/cuenta/recuperar/` |
| Términos (LOPDP) | `/cuenta/terminos/` |
| Registro de alumnos | `/alumnos/` |
| Expedientes en PDF | `/alumnos/expedientes.pdf` |
| Adaptar material | `/adaptar/` |
| Banco de adaptaciones | `/adaptar/banco/` |
| Evaluación en el aula | `/evaluar/<adaptación>/` |
| Panel principal | `/` |
| Administrador | `/admin/` |
