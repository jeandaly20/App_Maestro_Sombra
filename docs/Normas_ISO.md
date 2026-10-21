# Normas ISO aplicadas en Maestro Sombra

Cada norma indica qué se hizo en el proyecto y en qué archivo está, para poder mostrarlo al tutor.

## 1. ISO/IEC 25010:2023 – Modelo de calidad del producto de software

| Característica | Cómo se cumple | Dónde |
|---|---|---|
| Adecuación funcional | Cada pantalla tiene su tabla y sus campos; nada de la interfaz queda sin dato de origen | MER, `apps/*/models.py` |
| Mantenibilidad (modularidad) | Una aplicación de Django por módulo: accounts, students, materials, adaptations, evaluations (código en inglés, interfaz en español) | `apps/` |
| Seguridad | Ver ISO/IEC 27001 (sección 2) | `config/settings.py` |
| Capacidad de interacción (usabilidad y accesibilidad) | Fuentes Lexend y Atkinson Hyperlegible, contraste AA, botones de 44 px | Plantillas y CSS (Parte 1) |
| Fiabilidad | Estados del material (Pendiente, Procesando, Procesado, Error) y mensaje cuando falla la IA | `OriginalMaterial.status`, `ai_error` |

## 2. ISO/IEC 27001:2022 – Seguridad de la información (controles del Anexo A)

| Control | Cómo se cumple | Dónde |
|---|---|---|
| 5.17 Información de autenticación | Contraseñas guardadas con hash PBKDF2, nunca en texto; mínimo 8 caracteres | `AUTH_PASSWORD_VALIDATORS` |
| 5.34 Privacidad y protección de datos personales | Seudónimo `EST-XXXXXX`; nombre, observaciones y notas de evaluación cifrados | `EncryptedTextField` |
| 8.2 Derechos de acceso privilegiado | La app usa el usuario `maestro_app`, que no puede crear bases ni usuarios; `postgres` solo se usa una vez | `scripts/create_db.py` |
| 8.5 Autenticación segura | Sesión de 8 horas, cookies HttpOnly, SameSite y Secure en producción | `config/settings.py` |
| 8.9 Gestión de la configuración | Claves en `.env`, fuera del código y de Git | `.env.example`, `.gitignore` |
| 8.15 Registro de eventos | Archivo de registro rotativo con fecha ISO 8601 | `LOGGING`, carpeta `logs/` |
| 8.24 Uso de criptografía | Cifrado Fernet (AES-128 + HMAC-SHA256) | `apps/core/fields.py` |
| 8.28 Codificación segura | Protección CSRF, `X-Frame-Options: DENY`, `nosniff`, validación de extensión y tamaño de archivos (10 MB) | `config/settings.py`, `apps/materials/models.py` |
| 8.32 Gestión de cambios | Control de versiones con Git y GitHub | Repositorio |

## 3. ISO/IEC 27701 – Gestión de la privacidad (junto con la LOPDP de Ecuador)

- **Consentimiento:** el docente acepta los términos y queda guardada la fecha (`accepted_terms`, `terms_accepted_at`).
- **Minimización:** a Gemini solo se envía el material y la condición, nunca el nombre del niño.
- **Acceso restringido:** el administrador ve solo seudónimos; el nombre real lo ve únicamente el docente que registró al alumno.

## 4. ISO/IEC 25012 – Calidad de los datos

Las reglas se validan en Django y también en PostgreSQL, para que se cumplan aunque el dato no llegue por un formulario:
- restricciones `CHECK`: condiciones, grados, estados y tipos de entrada válidos;
- restricciones `UNIQUE`: correo, seudónimo y código de adaptación;
- trigger que impide más de 3 estudiantes por docente.

## 5. ISO/IEC 40500 (WCAG 2) e ISO 9241-110 / 9241-210 – Accesibilidad y diseño centrado en el usuario

- Objetivo: WCAG 2.2 nivel AA en todas las pantallas.
- Etiquetas siempre visibles, foco de teclado visible, textos alternativos y zoom de 100 %, 125 % y 150 %.
- Las pantallas se diseñaron a partir de la tarea real del docente: subir, adaptar, revisar y evaluar.

## 6. ISO 8601 – Fechas y horas

En la base las fechas se guardan en UTC, en formato ISO 8601. En pantalla se muestran en la hora de Ecuador (`America/Guayaquil`).

## 7. ISO/IEC 29110 e ISO/IEC 12207 – Ciclo de vida del software

El proyecto se organiza con Scrum, en partes (sprints): requisitos, diseño (MER e interfaces), construcción, pruebas y entrega. ISO/IEC 29110 está pensada para equipos pequeños como el nuestro.

## 8. ISO/IEC/IEEE 29119 – Pruebas de software

Se aplicará en la parte de pruebas: casos de prueba por funcionalidad, pruebas de las reglas de la base de datos y prueba comparativa contra usar Gemini directamente.
