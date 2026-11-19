"""
Work in progress survives a reload: every page where the teacher types or chooses a lot marks its form with
data-draft, and forms.js keeps it in the tab's sessionStorage until it is sent. Made-up data only.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = Path(settings.BASE_DIR) / 'templates'
DRAFT_FORMS = {
    'students/student_page.html': 'data-draft="estudiante"',            # register / edit a student
    'adaptations/adapt_form.html': 'data-draft="adaptar-material"',     # Adaptar material
    'adaptations/lesson_setup.html': 'data-draft="adaptar-clase"',      # Adaptar clase, step 2
    'adaptations/lesson_detail.html': 'data-draft="clase-',             # editing the adapted class
    'adaptations/adaptation_edit.html': 'data-draft="ficha"',           # editing the worksheet
    'evaluations/evaluation_form.html': 'data-draft="evaluacion"',      # the evaluation after the class
}


class DraftTests(SimpleTestCase):
    def test_every_long_form_keeps_its_work_in_progress(self):
        for template, marker in DRAFT_FORMS.items():
            self.assertIn(marker, (TEMPLATES / template).read_text(encoding='utf-8'), template)

    def test_the_script_never_keeps_files_or_passwords_and_drafts_expire(self):
        script = (Path(settings.BASE_DIR) / 'static' / 'js' / 'forms.js').read_text(encoding='utf-8')
        self.assertIn("const DRAFT_SKIPPED = ['file', 'password', 'hidden'", script)
        self.assertIn('window.localStorage', script)  # survives a power cut
        self.assertIn('const DRAFT_HOURS = 12;', script)
        self.assertIn('document.body.dataset.user', script)  # tied to the teacher's account
        self.assertIn('navigator.onLine', script)  # not sent without internet
        self.assertIn('beforeunload', script)

    def test_the_page_knows_the_teacher_and_the_servers_answer(self):
        self.assertIn('data-user="{{ user.pk }}"', (TEMPLATES / 'base.html').read_text(encoding='utf-8'))
        self.assertEqual((TEMPLATES / 'includes' / 'messages.html').read_text(encoding='utf-8').count('data-server-message'), 2)

    def test_sign_in_and_password_forms_have_no_draft(self):
        for template in ('accounts/login.html', 'accounts/register.html', 'accounts/password_reset_confirm.html'):
            self.assertNotIn('data-draft', (TEMPLATES / template).read_text(encoding='utf-8'), template)
