"""
After the teacher evaluates a worksheet: the recommendation and, if asked, a simpler version.

Task 4.1. Data of an evaluation reaches the AI only when every check passes: the system setting is on, THIS
student has an authorization in force that names the evaluation treatment, the context is the minimal one
(curricular data and the structured indicators, as fixed labels) and the privacy check finds no identifier in
it. The teacher's observations, the description of the supports, the learning history and the profile never go,
not even with the authorization. When a check fails, or the AI is not available, the recommendation is made by
local rules and the evaluation is saved all the same.
"""
import logging
import re

from django.conf import settings
from django.utils import timezone

from apps.adaptations import ai, document_privacy, services as adaptation_services
from apps.students.models import ai_evaluation_allowed

from . import local_recommendations
from .models import SUPPORT_CHOICES

logger = logging.getLogger('maestro_sombra')

DISABLED, NO_CONSENT, PRIVACY, AI_ERROR = 'disabled', 'no_consent', 'privacy', 'ai_error'
REASONS = {
    DISABLED: 'El envío de datos de evaluación a la IA está deshabilitado en el sistema: se usaron reglas locales.',
    NO_CONSENT: 'Este estudiante no tiene una autorización vigente que cubra el tratamiento de datos de evaluación '
                'mediante IA: se usaron reglas locales.',
    PRIVACY: 'La comprobación de privacidad encontró posibles datos personales en el contexto, así que no se envió '
             'a la IA: se usaron reglas locales.',
    AI_ERROR: 'La IA no estuvo disponible: se usaron reglas locales.',
}
SUPPORT_LABELS = dict(SUPPORT_CHOICES)


def objective_of(adaptation):
    """The objective of the activity: the adapted one of its class, or the worksheet's own goal."""
    lesson = next((item for item in adaptation.lesson_adaptations.all() if item.content.get('adapted_objective')), None)
    return lesson.content['adapted_objective'] if lesson else (adaptation.content or {}).get('learning_goal', '')


def transfer_check(evaluation):
    """'' when the evaluation's indicators may be sent to the AI; otherwise why not (a key of REASONS)."""
    if not getattr(settings, 'AI_EVALUATION_TRANSFER_ENABLED', False):
        return DISABLED
    if not ai_evaluation_allowed(evaluation.adaptation.student):
        return NO_CONSENT
    return ''


def minimal_context(evaluation):
    """
    Exactly what may reach the AI about one evaluation: the subject, the topic and the worksheet's summary
    (cleaned of names and identifiers), the grade, the NEE, the UDL rules of the worksheet and the structured
    answers with their fixed labels, plus the supports applied and judged useful (fixed labels too).
    Never: name, birth date, identifiers, documents, consents, history, profile, observations, supports note.
    """
    adaptation = evaluation.adaptation
    names = adaptation_services.teacher_student_names(adaptation.material.teacher)
    answers = list(evaluation.answers())
    applied = [SUPPORT_LABELS[k] for k in evaluation.supports_applied if k in SUPPORT_LABELS]
    useful = [SUPPORT_LABELS[k] for k in evaluation.supports_useful if k in SUPPORT_LABELS]
    if applied:
        answers.append(('Apoyos aplicados durante la actividad', ', '.join(applied)))
    if useful:
        answers.append(('Apoyos que el docente consideró útiles', ', '.join(useful)))
    return {
        'subject': adaptation.material.subject.name,
        'topic': document_privacy.sanitize_text(adaptation.material.topic, names)[0],
        'grade': adaptation.student.grade,
        'conditions': adaptation.conditions or [adaptation.condition],
        'adjustments': adaptation.adjustment_labels,
        'summary': document_privacy.sanitize_text(adaptation.description, names)[0],
        'evaluation': answers,
    }


def _sentences(text):
    return [part.strip() for part in re.split(r'[.\n;]', text or '') if len(part.strip()) >= 15]


def privacy_check(context, evaluation):
    """'' if the context may leave; PRIVACY if it holds an identifier or any piece of a private text."""
    adaptation = evaluation.adaptation
    text = ' '.join([context['subject'], context['topic'], context['summary'], *context['adjustments'],
                     *[f'{q}: {a}' for q, a in context['evaluation']]])
    names = adaptation_services.teacher_student_names(adaptation.material.teacher)
    if document_privacy.sanitize_text(text, names)[1]:
        return PRIVACY
    lowered = text.lower()
    for private in (evaluation.observation, evaluation.supports_note):
        if private and (private.strip().lower() in lowered
                        or any(sentence.lower() in lowered for sentence in _sentences(private))):
            return PRIVACY
    return ''


def recommend(evaluation):
    """
    Saves the recommendation of a final evaluation. The local one is always prepared; the AI's only when every
    check passes. Never raises because of the AI: the evaluation must be saved even without it.
    """
    adaptation = evaluation.adaptation
    names = adaptation_services.teacher_student_names(adaptation.material.teacher)
    objective = document_privacy.sanitize_text(objective_of(adaptation), names)[0]
    evaluation.local_recommendations = local_recommendations.recommend(evaluation, objective=objective)
    reason = transfer_check(evaluation)
    context = None if reason else minimal_context(evaluation)
    if not reason:
        reason = privacy_check(context, evaluation)
    source, model, fields = 'LOCAL', '', []
    if not reason:
        try:
            text, _simplify, model = ai.recommend_next_steps(**context)
            evaluation.ai_recommendation, evaluation.recommended_at = text, timezone.now()
            source, fields = 'AI', ['ai_recommendation', 'recommended_at']
        except Exception as error:  # noqa: BLE001 - the AI must never stop the evaluation from being saved
            reason = AI_ERROR
            logger.warning('Evaluation recommendation for %s: AI unavailable (%s)', adaptation.code, type(error).__name__)
    evaluation.recommendation_source = source
    evaluation.recommendation_note = REASONS.get(reason, '')
    evaluation.recommendation_reviewed_at = None
    evaluation.save(update_fields=fields + ['local_recommendations', 'recommendation_source', 'recommendation_note',
                                            'recommendation_reviewed_at', 'updated_at'])
    # Technical data only: the worksheet code, the origin and which check decided it
    logger.info('Evaluation recommendation for %s: source=%s check=%s model=%s', adaptation.code, source,
                reason or 'ok', model or '-')
    return evaluation


def regeneration_feedback(evaluation):
    """
    What a regeneration may tell the AI about this evaluation: the structured indicators when every check
    passes, otherwise nothing (the new version is made from the worksheet, its NEE and its rules, which are
    already authorized). The observations are never part of it.
    """
    if transfer_check(evaluation):
        return ''
    context = minimal_context(evaluation)
    if privacy_check(context, evaluation):
        return ''
    return 'Indicadores registrados en el aula: ' + '; '.join(f'{q}: {a}' for q, a in context['evaluation'])


def regenerate_from(evaluation):
    """Next, simpler version of the adaptation. Returns (new version, whether the indicators were used)."""
    feedback = regeneration_feedback(evaluation)
    version = adaptation_services.regenerate(evaluation.adaptation, simplify=True, feedback=feedback)
    logger.info('Regeneration of %s from its evaluation: indicators=%s', evaluation.adaptation.code, bool(feedback))
    return version, bool(feedback)
