(() => {
    const section = document.querySelector('[data-explorer]');
    if (!section) return;

    const canvas = section.querySelector('canvas');
    const context = canvas.getContext('2d');
    const measure = section.querySelector('#explore-measure');
    const manager = section.querySelector('#explore-manager');
    const legend = section.querySelector('#explore-series');
    const readout = section.querySelector('#explore-readout');
    const palette = ['#dbc084', '#9db4cc', '#97b3a2', '#c9a6b2', '#b5accc', '#e1a98a', '#d3c9b5', '#8ebaaf'];
    const percentages = new Set(['top10', 'weight_shift', 'return']);
    const chosen = new Set();
    let dataset;
    let start = 0;
    let end = 0;
    let hovered = null;
    let dragStart = null;
    let bounds;

    const formatValue = (amount) => {
        if (amount == null) return '—';
        const metric = measure.value;
        if (metric === 'value') {
            return new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD',
                notation: 'compact', maximumFractionDigits: 2}).format(amount);
        }
        if (metric.startsWith('sector:') || percentages.has(metric) || (metric.startsWith('risk:') && metric !== 'risk:sharpe_ratio')) {
            return (100 * amount).toFixed(1) + '%';
        }
        return new Intl.NumberFormat('en-US', {maximumFractionDigits: 1}).format(amount);
    };

    const dates = () => (measure.value === 'return' || measure.value.startsWith('risk:'))
        ? dataset.periods.filter(period => period.endsWith('12-31'))
        : dataset.periods;

    const point = (slug, date) => {
        const metric = measure.value;
        const entry = dataset.managers[slug];
        if (metric === 'return') return entry.returns[date.slice(0, 4)];
        if (metric.startsWith('risk:')) return entry.risk?.[date.slice(0, 4)]?.[metric.slice(5)];
        const row = entry.history[date];
        return metric.startsWith('sector:') ? row?.sectors[metric.slice(7)] : row?.[metric];
    };

    function inspect() {
        const labels = dates();
        if (hovered == null) {
            readout.textContent = labels[start] + ' to ' + labels[end];
            return;
        }
        const entries = Array.from(chosen, slug =>
            dataset.managers[slug].name + ' ' + formatValue(point(slug, labels[hovered])));
        readout.textContent = [labels[hovered], ...entries].join(' · ');
    }

    function draw() {
        if (!dataset) return;
        const pixelRatio = window.devicePixelRatio || 1;
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        canvas.width = width * pixelRatio;
        canvas.height = height * pixelRatio;
        context.scale(pixelRatio, pixelRatio);
        context.font = '12px system-ui';
        const left = 74;
        const right = width - 18;
        const top = 20;
        const bottom = height - 34;
        const labels = dates();
        const selectedDates = labels.slice(start, end + 1);
        const values = [...chosen].flatMap(slug => selectedDates.map(day => point(slug, day)))
            .filter(Number.isFinite);
        const minimum = Math.min(0, ...values);
        const maximum = Math.max(...values, minimum + (values.length ? Number.EPSILON : 1));
        const x = index => left + (right - left) * (index - start) / Math.max(1, end - start);
        const y = value => bottom - (bottom - top) * (value - minimum) / (maximum - minimum);
        bounds = {left, right};
        for (let i = 0; i < 5; i += 1) {
            const tick = minimum + i * (maximum - minimum) / 4;
            const vertical = y(tick);
            context.strokeStyle = '#272c32';
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(left, vertical);
            context.lineTo(right, vertical);
            context.stroke();
            context.textAlign = 'right';
            context.fillStyle = '#a7a6a0';
            context.fillText(formatValue(tick), left - 10, vertical + 4);
        }
        const labelCount = Math.min(end - start, Math.max(1, Math.floor((right - left) / 100)));
        for (let i = 0; i <= labelCount; i += 1) {
            const index = start + Math.round((end - start) * i / Math.max(1, labelCount));
            context.textAlign = i === 0 ? 'left' : i === labelCount ? 'right' : 'center';
            context.fillText(labels[index].slice(0, 7), x(index), bottom + 22);
        }
        [...chosen].forEach((slug, index) => {
            context.strokeStyle = palette[index];
            context.fillStyle = palette[index];
            context.lineWidth = 2;
            context.beginPath();
            let connected = false;
            selectedDates.forEach((day, offset) => {
                const amount = point(slug, day);
                if (!Number.isFinite(amount)) {
                    connected = false;
                    return;
                }
                const px = x(start + offset);
                if (connected) context.lineTo(px, y(amount));
                else context.moveTo(px, y(amount));
                connected = true;
            });
            context.stroke();
            if (measure.value === 'return' || measure.value.startsWith('risk:')) {
                selectedDates.forEach((day, offset) => {
                    const amount = point(slug, day);
                    if (!Number.isFinite(amount)) return;
                    context.beginPath();
                    context.arc(x(start + offset), y(amount), 2.75, 0, Math.PI * 2);
                    context.fill();
                });
            }
        });
        if (hovered != null) {
            context.strokeStyle = '#e5edf3';
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(x(hovered), top);
            context.lineTo(x(hovered), bottom);
            context.stroke();
        }
        if (dragStart != null && hovered != null) {
            context.fillStyle = 'rgba(200, 161, 90, .16)';
            context.fillRect(x(Math.min(dragStart, hovered)), top,
                Math.abs(x(hovered) - x(dragStart)), bottom - top);
        }
        inspect();
    }

    function reset() {
        start = 0;
        end = dates().length - 1;
        hovered = null;
        dragStart = null;
        draw();
    }

    function updateLegend() {
        const buttons = [...chosen].map((slug, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'research-button';
            button.style.setProperty('--series-color', palette[index]);
            button.textContent = dataset.managers[slug].name + ' ×';
            button.setAttribute('aria-label', 'Remove ' + dataset.managers[slug].name);
            button.addEventListener('click', () => {
                chosen.delete(slug);
                updateLegend();
            });
            return button;
        });
        legend.replaceChildren(...buttons);
        draw();
    }

    function dateIndex(event) {
        const position = event.clientX - canvas.getBoundingClientRect().left;
        const fraction = (position - bounds.left) / (bounds.right - bounds.left);
        return Math.min(end, Math.max(start, start + Math.round((end - start) * fraction)));
    }

    canvas.addEventListener('pointerdown', event => {
        if (!bounds || event.pointerType === 'touch') return;
        dragStart = dateIndex(event);
        canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
        if (!bounds) return;
        hovered = dateIndex(event);
        draw();
    });
    canvas.addEventListener('pointerup', event => {
        if (!bounds) return;
        const stop = dateIndex(event);
        if (dragStart != null && Math.abs(stop - dragStart) > 1) {
            start = Math.min(stop, dragStart);
            end = Math.max(stop, dragStart);
        }
        dragStart = null;
        hovered = stop;
        draw();
    });
    canvas.addEventListener('pointerleave', () => {
        if (dragStart != null) return;
        hovered = null;
        draw();
    });
    canvas.addEventListener('keydown', event => {
        if (!dataset || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        hovered = Math.max(start, Math.min(end, (hovered ?? start) + (event.key === 'ArrowRight' ? 1 : -1)));
        draw();
    });
    section.querySelector('form').addEventListener('submit', event => {
        event.preventDefault();
        if (!dataset || !manager.value) return;
        if (chosen.size >= palette.length && !chosen.has(manager.value)) {
            readout.textContent = 'Remove a manager before adding another. The chart supports eight at a time.';
            return;
        }
        chosen.add(manager.value);
        updateLegend();
    });
    measure.addEventListener('change', reset);
    section.querySelector('#explore-reset').addEventListener('click', () => { if (dataset) reset(); });
    new ResizeObserver(draw).observe(canvas);

    fetch(section.dataset.explorer)
        .then(response => {
            if (!response.ok) throw new Error('Portfolio history unavailable');
            return response.json();
        })
        .then(data => {
            dataset = data;
            Object.entries(data.managers)
                .sort(([, a], [, b]) => a.name.localeCompare(b.name))
                .forEach(([slug, row]) => manager.add(new Option(row.name, slug)));
            Object.entries(data.industries)
                .forEach(([key, name]) => measure.add(new Option(name + ' exposure', 'sector:' + key)));
            ['berkshire', 'citadel', 'bridgewater', 'renaissance'].filter(
                slug => slug in data.managers).forEach(slug => chosen.add(slug));
            reset();
            updateLegend();
        })
        .catch(() => { readout.textContent = 'Saved portfolio history is temporarily unavailable.'; });
})();
