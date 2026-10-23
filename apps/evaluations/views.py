import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.adaptations import ai
from apps.adaptations.views import own_adaptation
from apps.core.choices import RecordStatus

from . import services
from .forms import EvaluationForm
from .models import Evaluation, EvaluationStatus

logger = logging.getLogger('maestro_sombra')


class EvaluationView(LoginRequiredMixin, View):
    """Evaluation of one adaptation after using it in class (one per version)."""
    template_name = 'evaluations/evaluation_form.html'

    def get_evaluation(self, request, pk):
        adaptation = own_adaptation(request, pk)  # 404 for another teacher's adaptation
        try:
            return adaptation.evaluation
        except Evaluation.DoesNotExist:
            return Evaluation(adaptation=adaptation)

    def render_page(self, request, form):
        adaptation = form.instance.adaptation
        history = (Evaluation.objects
                   .filter(adaptation__student=adaptation.student, adaptation__material__subject=adaptation.material.subject,
                           status=EvaluationStatus.FINAL, record_status=RecordStatus.ACTIVE)
                   .exclude(adaptation=adaptation).select_related('adaptation__material')[:3])
        return render(request, self.template_name, {
            'form': form, 'evaluation': form.instance, 'adaptation': adaptation,
            'student': adaptation.student, 'history': history,
        })

    def get(self, request, pk):
        return self.render_page(request, EvaluationForm(instance=self.get_evaluation(request, pk)))

    def post(self, request, pk):
        finishing = request.POST.get('action') == 'final'
        form = EvaluationForm(request.POST, instance=self.get_evaluation(request, pk), finishing=finishing)
        if not form.is_valid():
            return self.render_page(request, form)
        evaluation = form.save(commit=False)
        evaluation.status = EvaluationStatus.FINAL if finishing else EvaluationStatus.DRAFT
        evaluation.save()
        adaptation = evaluation.adaptation
        logger.info('Evaluation %s saved for %s by teacher=%s', evaluation.status, adaptation.code, request.user.pk)
        if not finishing:
            messages.success(request, 'Borrador guardado. Puedes terminar la evaluación más tarde.')
            return redirect('evaluation', pk=adaptation.pk)

        try:
            services.recommend(evaluation)
            messages.success(request, 'Evaluación guardada. El asistente preparó una recomendación pedagógica.')
        except ai.AIError as error:
            messages.warning(request, f'Evaluación guardada, pero no se pudo generar la recomendación: {error}')
        if evaluation.regeneration_requested:
            try:
                new_version = services.regenerate_from(evaluation)
                messages.success(request, f'Se generó la versión {new_version.version}, más simplificada. Revísala antes de aprobarla.')
                return redirect(f"{reverse('material_detail', args=[adaptation.material_id])}?a={new_version.pk}")
            except ai.AIError as error:
                messages.error(request, f'No se pudo regenerar el material: {error}')
        return redirect(f"{reverse('bank')}?alumno={adaptation.student_id}")
