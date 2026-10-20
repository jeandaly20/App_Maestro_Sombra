# MER – Maestro Sombra (PostgreSQL 17)

Pega el bloque en https://mermaid.live y descárgalo como PNG o SVG.

```mermaid
erDiagram
    DOCENTE ||--o{ ESTUDIANTE : registra
    DOCENTE ||--o{ MATERIAL_ORIGINAL : sube
    MATERIA ||--o{ MATERIAL_ORIGINAL : clasifica
    MATERIAL_ORIGINAL ||--o{ ADAPTACION_CURRICULAR : origina
    ESTUDIANTE ||--o{ ADAPTACION_CURRICULAR : recibe
    ADAPTACION_CURRICULAR |o--o{ ADAPTACION_CURRICULAR : regenera
    ADAPTACION_CURRICULAR ||--o| EVALUACION : evaluada_en

    DOCENTE {
        uuid id PK
        varchar nombre_completo
        varchar email UK "minúsculas"
        varchar password "hash PBKDF2"
        varchar especialidad "CHECK"
        boolean acepta_terminos
        timestamptz fecha_aceptacion_terminos
        boolean is_active
        boolean is_staff "administrador"
        timestamptz last_login
        timestamptz date_joined
    }
    MATERIA {
        bigint id PK
        varchar nombre UK
        varchar abreviatura UK
    }
    ESTUDIANTE {
        uuid id PK
        uuid docente_id FK
        varchar seudonimo UK "EST-XXXXXX"
        text nombre "cifrado"
        date fecha_nacimiento
        varchar grado "CHECK"
        varchar_array condiciones "CHECK"
        text observaciones "cifrado"
        timestamptz creado
        timestamptz actualizado
    }
    MATERIAL_ORIGINAL {
        uuid id PK
        uuid docente_id FK
        bigint materia_id FK
        varchar titulo
        varchar tema
        varchar tipo_entrada "FOTO PDF WORD TEXTO"
        varchar archivo "opcional"
        varchar nombre_archivo
        integer tamano_kb
        text texto_ingresado "opcional"
        varchar estado "PENDIENTE PROCESANDO PROCESADO ERROR"
        jsonb analisis_ia
        text error_ia
        timestamptz creado
        timestamptz actualizado
    }
    ADAPTACION_CURRICULAR {
        uuid id PK
        varchar codigo UK "ADAP-MAT-XXXXXX"
        uuid material_id FK
        uuid estudiante_id FK
        uuid adaptacion_origen_id FK "opcional"
        smallint version
        varchar condicion "CHECK"
        varchar titulo
        text descripcion
        varchar_array ajustes_aplicados "CHECK"
        varchar filtro_cromatico "CHECK"
        jsonb contenido
        varchar estado "EN_REVISION APROBADA EXPORTADA"
        varchar pdf
        varchar modelo_ia
        integer tiempo_generacion_seg
        timestamptz creado
        timestamptz actualizado
    }
    EVALUACION {
        uuid id PK
        uuid adaptacion_id FK, UK
        varchar nivel_autonomia "AUTONOMO APOYO_PUNTUAL APOYO_CONSTANTE"
        varchar comprension "SI DIFICULTAD NO"
        varchar sobrecarga "NO LEVE EVIDENTE"
        varchar rango_acierto "MENOS_50 ENTRE_50_75 MAS_75"
        text observacion "cifrado"
        boolean solicita_regeneracion
        varchar estado "BORRADOR FINALIZADA"
        text recomendacion_ia
        timestamptz fecha_recomendacion
        timestamptz creado
        timestamptz actualizado
    }
```

## Reglas de negocio (van debajo del diagrama en el documento)

1. Un docente puede registrar como máximo 3 estudiantes con NEE (trigger en PostgreSQL).
2. Una adaptación solo puede usar una condición que el estudiante tenga registrada.
3. Cada regeneración crea una versión nueva (`version` + 1) que apunta a la anterior (`adaptacion_origen_id`).
4. Cada versión tiene como máximo una evaluación.
5. Una evaluación finalizada debe tener al menos la autonomía y la comprensión.
6. Un material de tipo TEXTO lleva `texto_ingresado`; los de tipo FOTO, PDF o WORD llevan `archivo`.
7. A Gemini se envían el material y la condición; nunca el nombre ni las observaciones del niño.

## Leyenda

- **PK**: clave primaria.
- **FK**: clave foránea.
- **UK**: valor único.
- **cifrado**: se guarda cifrado con Fernet.
- **CHECK**: solo admite los valores del catálogo.
- **jsonb**: respuesta estructurada de la IA.
