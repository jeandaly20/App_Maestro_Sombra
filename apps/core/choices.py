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


CONDITION_GROUPS = {
    'DEA': [Condition.DYSLEXIA, Condition.DYSCALCULIA],
    'Neurodesarrollo': [Condition.ADHD, Condition.ASD],
    'Discapacidad intelectual': [Condition.ID_MILD, Condition.ID_MODERATE],
}


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


class ColorFilter(models.TextChoices):
    """Background tint for readers with dyslexia ("Filtro cromático DEA" button)."""
    NONE = 'NONE', 'Sin filtro'
    CREAM = 'CREAM', 'Crema'
    BLUE = 'BLUE', 'Azul claro'
    GREEN = 'GREEN', 'Verde suave'
