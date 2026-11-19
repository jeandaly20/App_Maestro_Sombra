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

    // Input type: the photos of the notebook (several) or one PDF / Word file
    const fileInput = form.querySelector('input[name="file"]');
    const photoInput = form.querySelector('input[name="photos"]');
    const fileZone = form.querySelector('[data-file-zone]');
    const photoZone = form.querySelector('[data-photo-zone]');
    const help = form.querySelector('[data-file-help]');
    const helpTexts = {
        PDF: 'Sube la guía en PDF (digital o escaneado). Máximo 10 MB.',
        WORD: 'Sube la guía en Word (.docx): se leen su texto y sus imágenes, y la ficha adaptada se puede seguir editando. Máximo 10 MB.',
    };
    const applyType = () => {
        const type = form.querySelector('input[name="input_type"]:checked')?.value || 'PDF';
        const isPhoto = type === 'PHOTO';
        photoZone.hidden = !isPhoto;
        fileZone.hidden = isPhoto;
        if (!isPhoto) {
            fileInput.accept = fileInput.dataset[`accept${type.charAt(0)}${type.slice(1).toLowerCase()}`];
            help.textContent = helpTexts[type];
        }
    };
    // How many photos were chosen, and never more than the limit
    if (photoInput) {
        const counter = form.querySelector('[data-photo-count]');
        const original = counter.textContent;
        const max = Number(photoInput.dataset.maxPhotos);
        photoInput.addEventListener('change', () => {
            const count = photoInput.files.length;
            const tooMany = count > max;
            photoInput.setCustomValidity(tooMany ? `Elige como máximo ${max} fotos.` : '');
            counter.textContent = !count ? original : tooMany
                ? `Elegiste ${count} fotos: el máximo es ${max}, una por página de la guía.`
                : `${count} foto${count > 1 ? 's' : ''} lista${count > 1 ? 's' : ''}, en este orden: ${[...photoInput.files].map((f) => f.name).join(', ')}.`;
            counter.classList.toggle('field__help--error', tooMany);
        });
    }
    form.querySelectorAll('input[name="input_type"]').forEach((radio) => radio.addEventListener('change', applyType));
    applyType();

    syncStudentRules(form);

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
    const progress = () => status.querySelector('[data-progress]');
    let offline = false;
    let lastProgress = '';
    const check = async () => {
        try {
            const response = await fetch(status.dataset.pollUrl, { headers: { Accept: 'application/json' } });
            const data = await response.json();
            if (offline && progress()) progress().textContent = lastProgress;
            offline = false;
            if (data.status === 'PROCESSED' || data.status === 'ERROR') {
                window.location.reload();
                return;
            }
            // What the AI is doing now ("Leyendo el material…", "Adaptando la ficha 2 de 3…")
            if (progress() && data.progress) progress().textContent = data.progress;
            lastProgress = progress() ? progress().textContent : '';
        } catch (error) {
            // No connection: the work goes on in the server; say so and keep trying
            if (progress()) {
                if (!offline) lastProgress = progress().textContent;
                progress().textContent = 'Sin conexión con el servidor, reintentando… La adaptación sigue en curso.';
            }
            offline = true;
        }
        setTimeout(check, 2000);
    };
    setTimeout(check, 1500);
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

// The rules block of each chosen student (one per student, ticked with their own recommended rules)
function syncStudentRules(form) {
    const blocks = [...form.querySelectorAll('[data-rules-for]')];
    if (!blocks.length) return;
    const empty = form.querySelector('[data-rules-empty]');
    const sync = () => {
        const chosen = new Set([...form.querySelectorAll('input[name="students"]:checked')].map((box) => box.value));
        blocks.forEach((block) => { block.hidden = !chosen.has(block.dataset.rulesFor); });
        if (empty) empty.hidden = chosen.size > 0;
    };
    form.querySelectorAll('input[name="students"]').forEach((box) => box.addEventListener('change', sync));
    sync();
}
