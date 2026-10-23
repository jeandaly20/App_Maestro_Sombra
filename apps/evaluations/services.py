"""After the teacher evaluates a worksheet: AI recommendation and, if asked, a simpler version."""
import logging

from django.utils import timezone

from apps.adaptations import ai, services as adaptation_services

logger = logging.getLogger('maestro_sombra')


def classroom_feedback(evaluation):
    """The evaluation as text for the AI, without the student's name (it is scrubbed later)."""
    lines = [f'{question}: {answer}.' for question, answer in evaluation.answers()]
    if evaluation.observation:
        lines.append(f'Observación del docente: {evaluation.observation}')
    return ' '.join(lines)


def recommend(evaluation):
    """Saves Gemini's advice on the evaluation. Raises ai.AIError if the AI is not available."""
    adaptation = evaluation.adaptation
    student = adaptation.student
    answers = evaluation.answers()
    if evaluation.observation:
        answers.append(('Observación del docente',
                        adaptation_services.scrub_personal_data(evaluation.observation, student)))
    text, _simplify, model = ai.recommend_next_steps(
        subject=adaptation.material.subject.name, topic=adaptation.material.topic, grade=student.grade,
        condition=adaptation.condition, adjustments=adaptation.adjustment_labels,
        summary=adaptation.description, evaluation=answers,
    )
    evaluation.ai_recommendation = text
    evaluation.recommended_at = timezone.now()
    evaluation.save(update_fields=['ai_recommendation', 'recommended_at', 'updated_at'])
    logger.info('Recommendation for %s with %s', adaptation.code, model)
    return evaluation


def regenerate_from(evaluation):
    """Next, simpler version of the adaptation guided by what happened in class."""
    return adaptation_services.regenerate(evaluation.adaptation, simplify=True,
                                          feedback=classroom_feedback(evaluation))
