# Maestro Sombra

Aplicativo web de apoyo para docentes sombra en la automatización de adaptaciones curriculares inclusivas.
Caso práctico de titulación · Ingeniería de Software · Universidad Estatal de Milagro (UNEMI).

## Tecnologías

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12 · Django 5.2.19 LTS (arquitectura MVT) |
| Base de datos | PostgreSQL 17 · 20 tablas · campos JSONB, arreglos, CHECK, UNIQUE y trigger |
| Frontend | Plantillas de Django · HTML5 semántico · CSS3 propio · JavaScript nativo (ES6+) · gráficos SVG |
| Seguridad | Cifrado Fernet de datos personales · usuario de BD con mínimo privilegio · bloqueo por intentos fallidos · claves en `.env` |
| IA | API REST de Google Gemini: `gemini-flash-lite-latest`, con respaldo `gemini-3.1-flash-lite` y `gemini-3-flash-preview` |
| Privacidad local | Tesseract OCR: revisa fotos y PDF escaneados antes de enviarlos a la IA |
| Documentos | xhtml2pdf con tipografía Atkinson Hyperlegible (guías, expedientes e informes en PDF) · python-docx (Word) · openpyxl y xlrd (Excel) · pypdf y pypdfium2 (PDF) |

El código y la base de datos están en inglés; todo lo que ve el usuario está en español.

## Funcionalidades

- **Docentes:** registro, inicio de sesión con bloqueo tras 5 intentos fallidos y recuperación de contraseña por correo.
- **Estudiantes con NEE** (máximo 3 por docente): dislexia, discalculia, TDAH, TEA y discapacidad intelectual leve o
  moderada (23 combinaciones admitidas), con documento de respaldo y autorización del representante, perfil pedagógico
  cifrado, expediente en ZIP y eliminación definitiva.
- **Adaptar material:** guía en fotos del cuaderno, PDF o Word para uno o más estudiantes a la vez; comparativa con el
  original, ajuste manual, regeneración y descarga en PDF.
- **Adaptar clase completa:** planificación semanal en Excel, Word o PDF con las páginas del libro; la clase adaptada
  vuelve en el mismo formato de la planificación.
- **Verificación de pautas:** cada adaptación se revisa con las reglas de cada condición (`nee_modules.py`) y el docente
  la aprueba antes de descargarla.
- **Evaluación y seguimiento:** cinco preguntas con la escala del Reglamento General a la LOEI (art. 26), informe de
  evaluación y de avance en PDF, historial del estudiante y del grupo, y banco de adaptaciones.
- **Administrador** (`/admin/`): gestiona cuentas de docentes y materias; el trabajo de los docentes es solo de
  consulta y nada se elimina desde el panel.
- **Respaldos:** base de datos (pg_dump) y archivos subidos.

## Estructura

```
APP maestro sombra/
├── config/                 Configuración (settings, settings_test, urls)
├── apps/
│   ├── core/               Catálogos, campo cifrado, panel de inicio, respaldos, permisos del administrador
│   ├── accounts/           Docentes: iniciar sesión, registro, recuperar contraseña
│   ├── students/           Estudiantes con NEE, respaldo, autorizaciones, expediente y eliminación
│   ├── materials/          Materias y guías originales que sube el docente
│   ├── adaptations/        IA (ai.py), reglas DUA (udl_rules.py, nee_modules.py), privacidad, clase completa
│   └── evaluations/        Evaluación en el aula, informes PDF y recomendaciones
├── templates/              Pantallas en HTML5
├── static/                 Estilos (css/), JavaScript (js/), tipografías (fonts/) e imágenes (img/)
├── scripts/create_db.py    Crea la base de datos y el usuario de mínimo privilegio (una sola vez)
├── requirements.txt        Librerías de la aplicación (versiones fijas)
├── requirements-dev.txt    Herramientas de desarrollo: cobertura, estilo y seguridad
└── .env.example            Plantilla de configuración (el .env real no se sube a Git)
```

## Cómo ejecutarlo

Requisitos: Python 3.12, PostgreSQL 17 encendido y, para adaptar fotos o PDF escaneados, Tesseract OCR con el idioma
español.

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

Antes de `create_db.py`, completa el `.env`: clave secreta de Django, clave de cifrado, contraseña del usuario
`maestro_app`, clave de Gemini (`GEMINI_API_KEY`) y el correo para recuperar contraseñas.

### Respaldos

```bash
venv\Scripts\python.exe manage.py respaldar_base
venv\Scripts\python.exe manage.py restaurar_respaldo respaldos\respaldo-AAAAMMDD-HHMMSS --confirmar
```

Se guardan en `respaldos/` (o en `BACKUP_DIR`) y se conservan los últimos 14 (`BACKUP_KEEP`).

## Pruebas y calidad

```bash
venv\Scripts\python.exe manage.py test apps --settings=config.settings_test
```

263 pruebas (unitarias, de integración y de escenario) con una cobertura del 88 % del código. Las pruebas usan una
base de datos aislada que Django crea y borra en cada ejecución, así que no tocan los datos reales.

Con las herramientas de desarrollo (`venv\Scripts\pip.exe install -r requirements-dev.txt`):

```bash
venv\Scripts\python.exe -m coverage run manage.py test apps --settings=config.settings_test
venv\Scripts\python.exe -m coverage report
venv\Scripts\ruff.exe check apps config
venv\Scripts\bandit.exe -r apps config -x "*/migrations/*,*/test*.py,*/tests.py"
venv\Scripts\pip-audit.exe -r requirements.txt
```

El primero ejecuta las pruebas midiendo la cobertura y el segundo muestra el reporte por archivo; ruff revisa el estilo
del código, bandit busca fallas de seguridad y pip-audit comprueba que las librerías no tengan vulnerabilidades
conocidas.

## Direcciones

| Pantalla | Dirección |
|---|---|
| Iniciar sesión | `/cuenta/ingresar/` |
| Registro de docente | `/cuenta/registro/` |
| Recuperar contraseña | `/cuenta/recuperar/` |
| Términos (LOPDP) | `/cuenta/terminos/` |
| Panel principal | `/` |
| Historial del grupo | `/historial-grupo/` |
| Estudiantes | `/alumnos/` |
| Expedientes (ZIP) | `/alumnos/expedientes.zip` |
| Adaptar material | `/adaptar/` |
| Adaptar clase completa | `/adaptar/clase/` |
| Banco de adaptaciones | `/adaptar/banco/` |
| Evaluación en el aula | `/evaluar/<adaptación>/` |
| Administrador | `/admin/` |
