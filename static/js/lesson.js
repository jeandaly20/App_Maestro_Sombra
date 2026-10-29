// "Adaptar clase": recommended rules for the chosen students, copy the plan row, busy state while uploading.
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('[data-lesson-form]');
    if (form) {
        // Tick the UDL rules recommended for the chosen students (the teacher can still change them)
        const recommendedData = document.getElementById('recommended-adjustments');
        const recommended = recommendedData ? JSON.parse(recommendedData.textContent) : {};
        const ruleBoxes = [...form.querySelectorAll('input[name="adjustments"]')];
        let touched = ruleBoxes.some((box) => box.checked);
        ruleBoxes.forEach((box) => box.addEventListener('change', () => { touched = true; }));
        const syncRules = () => {
            if (touched) return;
            const chosen = [...form.querySelectorAll('input[name="students"]:checked')].map((box) => box.value);
            const wanted = new Set(chosen.flatMap((pk) => recommended[pk] || []));
            ruleBoxes.forEach((box) => { box.checked = wanted.has(box.value); });
        };
        form.querySelectorAll('input[name="students"]').forEach((box) => box.addEventListener('change', syncRules));
        syncRules();

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
