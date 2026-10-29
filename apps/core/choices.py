"""Fixed catalogs shared by several tables. Values in English, labels in Spanish."""
from django.db import models


class Condition(models.TextChoices):
    """SEN conditions handled by the system (approved proposal, section 2.2)."""
    DYSLEXIA = 'DYSLEXIA', 'DEA: Dislexia'
    DYSCALCULIA = 'DYSCALCULIA', 'DEA: Discalculia'
    ADHD = 'ADHD', 'TDAH'
    ASD = 'ASD', 'Trastorno del Espectro Autista (TEA)'
    ID_MILD = 'ID_MILD', 'Discapacidad intelectual leve'
    ID_MODERATE = 'ID_MODERATE', 'Discapacidad intelectual moderada'


# Names used inside each group box, where the group already gives the context
CONDITION_SHORT_NAMES = {
    Condition.DYSLEXIA: 'Dislexia',
    Condition.DYSCALCULIA: 'Discalculia',
    Condition.ADHD: 'TDAH',
    Condition.ASD: 'TEA (espectro autista)',
    Condition.ID_MILD: 'Leve',
    Condition.ID_MODERATE: 'Moderada',
}

CONDITION_DESCRIPTIONS = {
    Condition.DYSLEXIA: 'Dificultad en la lectura fluida',
    Condition.DYSCALCULIA: 'Dificultad lógico-matemática',
    Condition.ADHD: 'Atención dispersa e hiperactividad',
    Condition.ASD: 'Procesamiento sensorial y estructura',
    Condition.ID_MILD: 'Apoyos pedagógicos focalizados',
    Condition.ID_MODERATE: 'Currículo adaptado significativo',
}


# DSM-5, specific learning disorder, criterion D: the difficulties must not be better explained by an
# intellectual disability, so DEA and intellectual disability are not registered together.
LEARNING_DISORDERS = [Condition.DYSLEXIA, Condition.DYSCALCULIA]
INTELLECTUAL_DISABILITY = [Condition.ID_MILD, Condition.ID_MODERATE]


class DiagnosisSource(models.TextChoices):
    """Document that backs the student's SEN (the file itself is not uploaded: LOPDP, data minimization)."""
    UDAI_REPORT = 'UDAI_REPORT', 'Informe psicopedagógico de la UDAI'
    MSP_CERTIFICATE = 'MSP_CERTIFICATE', 'Certificado o carné de discapacidad del MSP'
    DECE_REPORT = 'DECE_REPORT', 'Informe del DECE de la institución'
    CLINICAL_REPORT = 'CLINICAL_REPORT', 'Diagnóstico clínico (neurólogo, psicólogo o psiquiatra)'
    # Only offered while the document is optional (settings.DIAGNOSIS_DOCUMENT_REQUIRED = False)
    IN_EVALUATION = 'IN_EVALUATION', 'En proceso de evaluación (sin informe aún)'


# Intellectual disability is a SEN associated with disability: it is backed by the MSP or the UDAI
DISABILITY_SOURCES = [DiagnosisSource.MSP_CERTIFICATE, DiagnosisSource.UDAI_REPORT]


class Grade(models.TextChoices):
    """Ecuadorian General Basic Education (EGB) grades."""
    EGB_1 = 'EGB_1', '1.º de EGB'
    EGB_2 = 'EGB_2', '2.º de EGB'
    EGB_3 = 'EGB_3', '3.º de EGB'
    EGB_4 = 'EGB_4', '4.º de EGB'
    EGB_5 = 'EGB_5', '5.º de EGB'
    EGB_6 = 'EGB_6', '6.º de EGB'
    EGB_7 = 'EGB_7', '7.º de EGB'
    EGB_8 = 'EGB_8', '8.º de EGB'
    EGB_9 = 'EGB_9', '9.º de EGB'
    EGB_10 = 'EGB_10', '10.º de EGB'


class UdlAdjustment(models.TextChoices):
    """UDL (DUA) rules the teacher ticks in "Adaptar material"."""
    ACCESSIBLE_FONT = 'ACCESSIBLE_FONT', 'Fuente accesible y silabeo'
    SEGMENTATION = 'SEGMENTATION', 'Segmentación de enunciados'
    VISUAL_SUPPORT = 'VISUAL_SUPPORT', 'Apoyo visual (barras, diagramas)'
    STEP_SEQUENCE = 'STEP_SEQUENCE', 'Secuencia paso a paso'
    EASY_READ = 'EASY_READ', 'Lectura fácil'
    FEWER_DISTRACTIONS = 'FEWER_DISTRACTIONS', 'Reducción de estímulos distractores'
    HIGH_CONTRAST = 'HIGH_CONTRAST', 'Alto contraste'


class ConditionGroup:
    """A group of SEN conditions and the UDL adjustments the system recommends for it."""

    def __init__(self, key, label, conditions, adjustments):
        self.key = key
        self.label = label
        self.conditions = conditions
        self.adjustments = adjustments

    @property
    def adjustment_labels(self):
        return [UdlAdjustment(value).label for value in self.adjustments]


CONDITION_GROUPS = [
    ConditionGroup(
        'dea', 'DEA (Dificultades específicas del aprendizaje)',
        [Condition.DYSLEXIA, Condition.DYSCALCULIA],
        [UdlAdjustment.ACCESSIBLE_FONT, UdlAdjustment.SEGMENTATION, UdlAdjustment.VISUAL_SUPPORT],
    ),
    ConditionGroup(
        'neuro', 'Neurodesarrollo',
        [Condition.ADHD, Condition.ASD],
        [UdlAdjustment.STEP_SEQUENCE, UdlAdjustment.FEWER_DISTRACTIONS, UdlAdjustment.VISUAL_SUPPORT],
    ),
    ConditionGroup(
        'id', 'Discapacidad intelectual',
        [Condition.ID_MILD, Condition.ID_MODERATE],
        [UdlAdjustment.EASY_READ, UdlAdjustment.SEGMENTATION, UdlAdjustment.STEP_SEQUENCE],
    ),
]


def group_of(condition):
    """Returns the ConditionGroup a condition belongs to."""
    return next(group for group in CONDITION_GROUPS if condition in group.conditions)


class RecordStatus(models.TextChoices):
    """Record status present in every table: INACTIVE is a logical delete (the row is kept for traceability)."""
    ACTIVE = 'ACTIVE', 'Activo'
    INACTIVE = 'INACTIVE', 'Inactivo'


class AdaptationGrade(models.TextChoices):
    """Degrees of curricular adaptation used in Ecuador's inclusive education."""
    ACCESS = 'ACCESS', 'Grado 1 · De acceso'
    NON_SIGNIFICANT = 'NON_SIGNIFICANT', 'Grado 2 · No significativa'
    SIGNIFICANT = 'SIGNIFICANT', 'Grado 3 · Significativa'


GRADE_NUMBERS = {AdaptationGrade.ACCESS: 1, AdaptationGrade.NON_SIGNIFICANT: 2, AdaptationGrade.SIGNIFICANT: 3}


class ColorFilter(models.TextChoices):
    """Background tint for readers with dyslexia ("Filtro cromático DEA" button)."""
    NONE = 'NONE', 'Sin filtro'
    CREAM = 'CREAM', 'Crema'
    BLUE = 'BLUE', 'Azul claro'
    GREEN = 'GREEN', 'Verde suave'
