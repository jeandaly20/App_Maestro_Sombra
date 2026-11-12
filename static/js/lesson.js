// "Adaptar clase": each chosen student's rules, copy the plan row, busy state while uploading.
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('[data-lesson-form]');
    if (form) {
        // The rules block of each chosen student (syncStudentRules is in adapt.js, loaded on this page too)
        if (typeof syncStudentRules === 'function') syncStudentRules(form);

        form.addEventListener('submit', () => {
            const button = form.querySelector('[data-submit]');
            button.disabled = true;
            button.textContent = 'Subiendo la clase…';
        });
    }

    // Copy the "Adaptación curricular" text to paste it in the plan
    document.querySelectorAll('[data-copy]').forEach((button) => {
        button.addEventListener('click', async () => {
            const text = document.getElementById(button.dataset.copy).textContent.trim();
            try {
                await navigator.clipboard.writeText(text);
                button.lastChild.textContent = 'Copiado';
            } catch (error) {
                window.getSelection().selectAllChildren(document.getElementById(button.dataset.copy));
            }
        });
    });
});
