"""Catálogos fijos que comparten varias tablas."""
from django.db import models


class Condicion(models.TextChoices):
    """Condiciones NEE que atiende el sistema (documento aprobado, sección 2.2)."""
    DISLEXIA = 'DEA_DISLEXIA', 'DEA: Dislexia'
    DISCALCULIA = 'DEA_DISCALCULIA', 'DEA: Discalculia'
    TDAH = 'TDAH', 'TDAH'
    TEA = 'TEA', 'Trastorno del Espectro Autista'
    DI_LEVE = 'DI_LEVE', 'Discapacidad intelectual leve'
    DI_MODERADA = 'DI_MODERADA', 'Discapacidad intelectual moderada'


GRUPOS_CONDICION = {
    'DEA': [Condicion.DISLEXIA, Condicion.DISCALCULIA],
    'Neurodesarrollo': [Condicion.TDAH, Condicion.TEA],
    'Discapacidad intelectual': [Condicion.DI_LEVE, Condicion.DI_MODERADA],
}


class Grado(models.TextChoices):
    """Grados de Educación General Básica (Ecuador)."""
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


class AjusteDUA(models.TextChoices):
    """Reglas de adaptación que el docente marca en "Adaptar material"."""
    FUENTE_ACCESIBLE = 'FUENTE_ACCESIBLE', 'Fuente accesible y silabeo'
    SEGMENTACION = 'SEGMENTACION', 'Segmentación de enunciados'
    APOYO_VISUAL = 'APOYO_VISUAL', 'Apoyo visual (barras, diagramas)'
    SECUENCIA_PASOS = 'SECUENCIA_PASOS', 'Secuencia paso a paso'
    LECTURA_FACIL = 'LECTURA_FACIL', 'Lectura fácil'
    REDUCCION_ESTIMULOS = 'REDUCCION_ESTIMULOS', 'Reducción de estímulos distractores'
    ALTO_CONTRASTE = 'ALTO_CONTRASTE', 'Alto contraste'


class FiltroCromatico(models.TextChoices):
    """Color de fondo para lectores con dislexia (botón "Filtro cromático DEA")."""
    NINGUNO = 'NINGUNO', 'Sin filtro'
    CREMA = 'CREMA', 'Crema'
    AZUL = 'AZUL', 'Azul claro'
    VERDE = 'VERDE', 'Verde suave'
