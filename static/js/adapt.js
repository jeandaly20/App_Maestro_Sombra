// Adapt material: input type tabs, recommended rules, status polling, scale, color filter and guided reading.
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    setUpForm();
    setUpPolling();
    setUpComparisonTools();
});

function setUpForm() {
    const form = document.querySelector('[data-adapt-form]');
    if (!form) return;

    // Input type: photo / PDF / Word use the file field; text uses the textarea
    const fileInput = form.querySelector('input[type="file"]');
    const fileZone = form.querySelector('[data-file-zone]');
    const textZone = form.querySelector('[data-text-zone]');
    const help = form.querySelector('[data-file-help]');
    const helpTexts = {
        PHOTO: 'En el celular se abre la cámara. Toma la foto de frente, con buena luz y sin sombras. Máximo 10 MB.',
        PDF: 'Sube la guía en PDF. Máximo 10 MB.',
        WORD: 'Sube la guía en Word (.docx). Se leerá su texto. Máximo 10 MB.',
    };
    const applyType = () => {
        const type = form.querySelector('input[name="input_type"]:checked')?.value || 'PHOTO';
        const isText = type === 'TEXT';
        fileZone.hidden = isText;
        textZone.hidden = !isText;
        if (!isText) {
            fileInput.accept = fileInput.dataset[`accept${type.charAt(0)}${type.slice(1).toLowerCase()}`];
            // capture="environment" opens the rear camera on phones, only for photos
            if (type === 'PHOTO') fileInput.setAttribute('capture', 'environment');
            else fileInput.removeAttribute('capture');
            help.textContent = helpTexts[type];
        }
    };
    form.querySelectorAll('input[name="input_type"]').forEach((radio) => radio.addEventListener('change', applyType));
    applyType();

    // Tick the UDL rules recommended for the chosen students (the teacher can still change them)
    const recommendedData = document.getElementById('recommended-adjustments');
    const recommended = recommendedData ? JSON.parse(recommendedData.textContent) : {};
    const ruleBoxes = [...form.querySelectorAll('input[name="adjustments"]')];
    let touchedByTeacher = ruleBoxes.some((box) => box.checked);
    ruleBoxes.forEach((box) => box.addEventListener('change', () => { touchedByTeacher = true; }));
    const syncRules = () => {
        if (touchedByTeacher) return;
        const chosen = [...form.querySelectorAll('input[name="students"]:checked')].map((box) => box.value);
        const wanted = new Set(chosen.flatMap((pk) => recommended[pk] || []));
        ruleBoxes.forEach((box) => { box.checked = wanted.has(box.value); });
    };
    form.querySelectorAll('input[name="students"]').forEach((box) => box.addEventListener('change', syncRules));
    syncRules();

    // Avoid double submissions while the file uploads
    form.addEventListener('submit', () => {
        const button = form.querySelector('[data-submit]');
        button.disabled = true;
        button.textContent = 'Subiendo material…';
    });
}

function setUpPolling() {
    const status = document.querySelector('[data-poll-url]');
    if (!status) return;
    const check = async () => {
        try {
            const response = await fetch(status.dataset.pollUrl, { headers: { Accept: 'application/json' } });
            const data = await response.json();
            if (data.status === 'PROCESSED' || data.status === 'ERROR') {
                window.location.reload();
                return;
            }
        } catch (error) {
            // Network hiccup: keep trying
        }
        setTimeout(check, 3000);
    };
    setTimeout(check, 3000);
}

function setUpComparisonTools() {
    const adapted = document.querySelector('[data-adapted]');
    if (!adapted) return;

    // Text scale 100 / 125 / 150 %
    const scaleButtons = [...document.querySelectorAll('[data-scale]')];
    scaleButtons.forEach((button) => {
        button.addEventListener('click', () => {
            adapted.style.setProperty('--scale', button.dataset.scale);
            scaleButtons.forEach((other) => other.setAttribute('aria-pressed', String(other === button)));
        });
    });

    // Color filter: preview at once and save it for the PDF
    const filterForm = document.querySelector('[data-color-filter-form]');
    if (filterForm) {
        filterForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const value = event.submitter.value;
            const body = new FormData(filterForm);
            body.set('color_filter', value);
            adapted.className = adapted.className.replace(/adapted--\w+/, `adapted--${value.toLowerCase()}`);
            filterForm.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.value === value)));
            // getAttribute: the form has an input named "action", which hides form.action
            await fetch(filterForm.getAttribute('action'), { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } });
        });
    }

    // Guided reading: highlights one part at a time; arrows or the button move forward
    const worksheet = adapted.querySelector('[data-worksheet]');
    const toggle = document.querySelector('[data-guided-reading]');
    const lines = [...worksheet.querySelectorAll('[data-reading-line]')];
    let current = -1;
    const show = (index) => {
        current = Math.max(0, Math.min(index, lines.length - 1));
        lines.forEach((line, i) => line.classList.toggle('is-reading', i === current));
        lines[current].scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
    toggle.addEventListener('click', () => {
        const active = worksheet.classList.toggle('worksheet--guided');
        toggle.setAttribute('aria-pressed', String(active));
        toggle.lastChild.textContent = active ? 'Salir de lectura guiada (usa las flechas)' : 'Modo lectura guiada';
        if (active) {
            show(0);
        } else {
            lines.forEach((line) => line.classList.remove('is-reading'));
        }
    });
    lines.forEach((line, i) => line.addEventListener('click', () => {
        if (worksheet.classList.contains('worksheet--guided')) show(i);
    }));
    document.addEventListener('keydown', (event) => {
        if (!worksheet.classList.contains('worksheet--guided')) return;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); show(current + 1); }
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); show(current - 1); }
        if (event.key === 'Escape') toggle.click();
    });

    // Regenerating takes a few seconds: tell the teacher
    const regenerate = document.querySelector('[data-regenerate]');
    if (regenerate) {
        regenerate.addEventListener('submit', () => {
            const button = regenerate.querySelector('button');
            button.disabled = true;
            button.textContent = 'Generando versión más simple…';
        });
    }
}
