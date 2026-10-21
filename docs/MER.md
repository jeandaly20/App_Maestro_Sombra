# MER – Maestro Sombra (PostgreSQL 17)

Pega el bloque en https://mermaid.live y descárgalo como PNG o SVG.

Las tablas y columnas están en inglés, igual que las que crea Django (`auth_group`, `django_session`). Lo que ve el usuario está en español.

```mermaid
erDiagram
    TEACHER ||--o{ STUDENT : registers
    TEACHER ||--o{ ORIGINAL_MATERIAL : uploads
    SUBJECT ||--o{ ORIGINAL_MATERIAL : classifies
    ORIGINAL_MATERIAL ||--o{ CURRICULAR_ADAPTATION : originates
    STUDENT ||--o{ CURRICULAR_ADAPTATION : receives
    CURRICULAR_ADAPTATION |o--o{ CURRICULAR_ADAPTATION : regenerates
    CURRICULAR_ADAPTATION ||--o| EVALUATION : evaluated_in

    TEACHER {
        uuid id PK
        varchar full_name
        varchar email UK "lowercase"
        varchar password "PBKDF2 hash"
        varchar specialty "CHECK"
        boolean accepted_terms
        timestamptz terms_accepted_at
        boolean is_active
        boolean is_staff "administrator"
        timestamptz last_login
        timestamptz date_joined
    }
    SUBJECT {
        bigint id PK
        varchar name UK
        varchar code UK
    }
    STUDENT {
        uuid id PK
        uuid teacher_id FK
        varchar pseudonym UK "EST-XXXXXX"
        text name "encrypted"
        date birth_date
        varchar grade "CHECK"
        varchar_array conditions "CHECK"
        text notes "encrypted"
        timestamptz created_at
        timestamptz updated_at
    }
    ORIGINAL_MATERIAL {
        uuid id PK
        uuid teacher_id FK
        bigint subject_id FK
        varchar title
        varchar topic
        varchar input_type "PHOTO PDF WORD TEXT"
        varchar file "optional"
        varchar file_name
        integer size_kb
        text input_text "optional"
        varchar status "PENDING PROCESSING PROCESSED ERROR"
        jsonb ai_analysis
        text ai_error
        timestamptz created_at
        timestamptz updated_at
    }
    CURRICULAR_ADAPTATION {
        uuid id PK
        varchar code UK "ADAP-MAT-XXXXXX"
        uuid material_id FK
        uuid student_id FK
        uuid source_adaptation_id FK "optional"
        smallint version
        varchar condition "CHECK"
        varchar title
        text description
        varchar_array applied_adjustments "CHECK"
        varchar color_filter "CHECK"
        jsonb content
        varchar status "IN_REVIEW APPROVED EXPORTED"
        varchar pdf
        varchar ai_model
        integer generation_time_sec
        timestamptz created_at
        timestamptz updated_at
    }
    EVALUATION {
        uuid id PK
        uuid adaptation_id FK, UK
        varchar autonomy_level "AUTONOMOUS OCCASIONAL_SUPPORT CONSTANT_SUPPORT"
        varchar comprehension "YES DIFFICULTY NO"
        varchar overload "NONE MILD EVIDENT"
        varchar accuracy_range "BELOW_50 BETWEEN_50_75 ABOVE_75"
        text observation "encrypted"
        boolean regeneration_requested
        varchar status "DRAFT FINAL"
        text ai_recommendation
        timestamptz recommended_at
        timestamptz created_at
        timestamptz updated_at
    }
```

## Nombres de las tablas en PostgreSQL

Django antepone el nombre del módulo a cada tabla:

| Entidad del MER | Tabla en PostgreSQL | Qué guarda |
|---|---|---|
| TEACHER | `accounts_teacher` | Docentes y administrador |
| SUBJECT | `materials_subject` | Catálogo fijo de materias de EGB |
| STUDENT | `students_student` | Estudiantes con NEE (máx. 3 por docente) |
| ORIGINAL_MATERIAL | `materials_originalmaterial` | Guía que sube el docente |
| CURRICULAR_ADAPTATION | `adaptations_curricularadaptation` | Guía adaptada por la IA |
| EVALUATION | `evaluations_evaluation` | Evaluación del docente en el aula |

## Reglas de negocio (van debajo del diagrama en el documento)

1. Un docente puede registrar como máximo 3 estudiantes con NEE (trigger `tr_max_students_per_teacher` en PostgreSQL).
2. Una adaptación solo puede usar una condición que el estudiante tenga registrada.
3. Cada regeneración crea una versión nueva (`version` + 1) que apunta a la anterior (`source_adaptation_id`).
4. Cada versión tiene como máximo una evaluación.
5. Una evaluación finalizada (`FINAL`) debe tener al menos la autonomía y la comprensión.
6. Un material de tipo TEXT lleva `input_text`; los de tipo PHOTO, PDF o WORD llevan `file`.
7. A Gemini se envían el material y la condición; nunca el nombre ni las observaciones del niño.

## Leyenda

- **PK**: clave primaria.
- **FK**: clave foránea.
- **UK**: valor único.
- **encrypted**: se guarda cifrado con Fernet.
- **CHECK**: solo admite los valores del catálogo.
- **jsonb**: respuesta estructurada de la IA.
