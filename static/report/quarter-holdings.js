(() => {
    const panel = document.querySelector('[data-allocation-feed]');
    if (!panel) return;
    const {quarters, initial} = JSON.parse(document.getElementById('report-quarter-data').textContent);
    const slider = document.getElementById('report-quarter-slider');
    const chart = document.getElementById('chart-sector-allocation');
    const rows = document.getElementById('report-quarter-rows');
    const sectors = document.getElementById('report-quarter-sectors');
    if (!slider) return;
    const prev = document.getElementById('report-quarter-prev');
    const next = document.getElementById('report-quarter-next');
    const picked = document.getElementById('report-quarter-date');
    const filing = document.getElementById('report-quarter-filing');
    const exits = document.getElementById('report-quarter-exits');
    const archive = document.getElementById('report-quarter-archive');
    const palette = ['#c8a15a', '#8fa3b8', '#c07a6a', '#7fa08a', '#a99bc4', '#d9c9a8', '#7f8a94', '#b98f62'];
    const weight = new Intl.NumberFormat('en', {style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2});
    const shares = new Intl.NumberFormat('en', {signDisplay: 'always', maximumFractionDigits: 0});
    const cache = new Map(initial ? [[initial.period, initial]] : []);
    let activeRequest;
    let timer;

    function draw(data) {
        const weights = Object.entries(data?.sectors || {});
        if (window.Plotly && weights.length) {
            chart.hidden = false;
            Plotly.newPlot(chart, [{
                type: 'pie', hole: .56, textinfo: 'none',
                labels: weights.map(([label]) => label), values: weights.map(([,value]) => value),
                marker: {colors: weights.map((_, index) => palette[index % palette.length])},
                hovertemplate: '%{label} · %{percent}<extra></extra>',
            }], {margin: {t: 0, b: 0, l: 0, r: 0}, showlegend: false},
                {responsive: true, displayModeBar: false});
        } else {
            chart.hidden = true;
        }
        if (!data) return;
        const update = (type, text) => {
            const node = document.createElement(type);
            node.textContent = text;
            return node;
        };
        rows.replaceChildren(...data.rows.map(row => {
            const tr = document.createElement('tr');
            const movement = row.shares_change ? `${row.action} · ${shares.format(row.shares_change)} shares` : (row.action || '—');
            for (const value of [row.ticker || '—', row.name, weight.format(row.weight), movement]) {
                tr.append(update('td', value));
            }
            return tr;
        }));
        sectors.replaceChildren(...weights.map(([label, value]) => {
            const item = document.createElement('div');
            item.append(update('dt', label), update('dd', weight.format(value)));
            return item;
        }));
        filing.textContent = data.filed
            ? [`Filed ${data.filed}`, `${data.count} long-equity positions`,
                data.previous ? `Compared with ${data.previous}` : ''].filter(Boolean).join(' · ')
            : `A complete allocation is unavailable for ${data.period}.`;
        exits.textContent = data.exit_count ? `${data.exit_count} exits · ${data.exits.join(' · ')}` : '';
        archive.href = panel.dataset.archiveUrl + '?' + new URLSearchParams({quarter: data.period});
    }

    async function load(day) {
        if (cache.has(day)) {
            draw(cache.get(day));
            return;
        }
        const controller = new AbortController();
        activeRequest = controller;
        filing.textContent = `Loading the ${day} disclosure…`;
        try {
            const response = await fetch(panel.dataset.allocationFeed + '?' + new URLSearchParams({quarter: day}),
                {signal: controller.signal});
            if (!response.ok) throw new Error('Allocation unavailable');
            const data = await response.json();
            cache.set(day, data);
            if (quarters[Number(slider.value)] === day) draw(data);
        } catch (error) {
            if (error.name !== 'AbortError') filing.textContent = `The ${day} disclosure could not be loaded.`;
        }
    }

    function choose() {
        const index = Number(slider.value);
        const day = quarters[index];
        picked.value = day;
        slider.setAttribute('aria-valuetext', day);
        prev.disabled = index === 0;
        next.disabled = index === quarters.length - 1;
        activeRequest?.abort();
        clearTimeout(timer);
        if (cache.has(day)) draw(cache.get(day));
        else timer = setTimeout(() => load(day), 160);
    }

    slider.addEventListener('input', choose);
    prev.addEventListener('click', () => { slider.stepDown(); choose(); });
    next.addEventListener('click', () => { slider.stepUp(); choose(); });
    panel.querySelector('.report-quarter-control').hidden = false;
    choose();
})();
