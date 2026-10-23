// Banco de adaptaciones and evaluation form: filters that apply on change and a busy state while the AI works.
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    // The "result" select filters as soon as it changes
    document.querySelectorAll('[data-autosubmit]').forEach((select) => {
        select.addEventListener('change', () => select.form.requestSubmit());
    });

    // Regenerating or finishing an evaluation can take up to a minute: block double clicks and say why
    document.querySelectorAll('form[data-busy]').forEach((form) => {
        form.addEventListener('submit', (event) => {
            const submitter = event.submitter;
            // Saving a draft does not call the AI
            if (submitter && submitter.value === 'draft') {
                return;
            }
            form.querySelectorAll('button[type="submit"]').forEach((button) => {
                button.disabled = true;
            });
            const note = document.createElement('p');
            note.className = 'busy-note';
            note.setAttribute('role', 'status');
            note.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
            note.append(form.dataset.busy);
            (submitter ? submitter.parentElement : form).append(note);
            // A disabled button is not sent: keep its name/value so the server knows which one was used
            if (submitter && submitter.name) {
                const hidden = document.createElement('input');
                hidden.type = 'hidden';
                hidden.name = submitter.name;
                hidden.value = submitter.value;
                form.append(hidden);
            }
        });
    });
});
