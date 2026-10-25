from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView

from apps.adaptations.models import AdaptationStatus, CurricularAdaptation
from apps.core.choices import RecordStatus
from apps.evaluations.models import COMPREHENSION_SCORE, Evaluation, EvaluationStatus
from apps.students.models import MAX_STUDENTS_PER_TEACHER

RECENT_ROWS = 5


def greeting(now):
    hour = timezone.localtime(now).hour
    if hour < 12:
        return 'Buenos días'
    if hour < 19:
        return 'Buenas tardes'
    return 'Buenas noches'


def is_pending(adaptation):
    """Still needs the teacher: review it, or evaluate it in class."""
    if adaptation.status == AdaptationStatus.IN_REVIEW:
        return True
    try:
        return adaptation.evaluation.status != EvaluationStatus.FINAL
    except Evaluation.DoesNotExist:
        return True


class HomeView(LoginRequiredMixin, TemplateView):
    """Main dashboard: the teacher's numbers, the three modules and the latest adaptations."""
    template_name = 'core/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        teacher = self.request.user
        students = list(teacher.students.filter(record_status=RecordStatus.ACTIVE).order_by('-created_at'))
        adaptations = list(CurricularAdaptation.objects
                           .filter(material__teacher=teacher, record_status=RecordStatus.ACTIVE,
                                   student__record_status=RecordStatus.ACTIVE)
                           .select_related('material__subject', 'student', 'evaluation')
                           .order_by('-created_at'))
        evaluations = [a.evaluation for a in adaptations
                       if hasattr(a, 'evaluation') and a.evaluation.status == EvaluationStatus.FINAL]
        scores = [COMPREHENSION_SCORE[e.comprehension] for e in evaluations if e.comprehension]

        # Time saved: manual reference minus what the AI actually took, per adaptation
        generated = [a.generation_time_sec for a in adaptations if a.generation_time_sec is not None]
        manual_minutes = settings.MANUAL_ADAPTATION_MINUTES
        saved_minutes = sum(max(manual_minutes - seconds / 60, 0) for seconds in generated)

        pending = [a for a in adaptations if is_pending(a)]
        show_pending = self.request.GET.get('ver') == 'pendientes'
        context.update({
            'greeting': greeting(timezone.now()),
            'students': students,
            'student_count': len(students),
            'max_students': MAX_STUDENTS_PER_TEACHER,
            'student_percent': round(len(students) * 100 / MAX_STUDENTS_PER_TEACHER),
            'adaptation_count': len(adaptations),
            'approved_count': sum(1 for a in adaptations if a.status != AdaptationStatus.IN_REVIEW),
            'in_review_count': sum(1 for a in adaptations if a.status == AdaptationStatus.IN_REVIEW),
            'saved_hours': round(saved_minutes / 60, 1),
            'manual_minutes': manual_minutes,
            'average_seconds': round(sum(generated) / len(generated)) if generated else None,
            'comprehension': round(sum(scores) / len(scores)) if scores else None,
            'evaluation_count': len(evaluations),
            'pending_count': len(pending),
            'show_pending': show_pending,
            'recent': (pending if show_pending else adaptations)[:RECENT_ROWS],
        })
        return context
