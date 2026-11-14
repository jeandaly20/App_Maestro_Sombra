// Before adapting: each student's local learning history is shown only for the subject chosen in the form.
// It is a summary for the teacher; nothing of it is sent with the form to the AI.
document.addEventListener('DOMContentLoaded', () => {
    const select = document.querySelector('select[name="subject"]');
    const boxes = document.querySelectorAll('[data-history-subject]');
    if (!select || !boxes.length) return;
    const update = () => boxes.forEach((box) => { box.hidden = box.dataset.historySubject !== select.value; });
    select.addEventListener('change', update);
    update();
});
