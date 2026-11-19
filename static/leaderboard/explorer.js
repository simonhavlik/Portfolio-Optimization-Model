(() => {
    const section = document.getElementById('portfolio-explorer');
    if (!section) return;

    const plot = section.querySelector('#explore-plot');
    const form = section.querySelector('form');
    const measure = section.querySelector('#explore-measure');
    const manager = section.querySelector('#explore-manager');
    const status = section.querySelector('#explore-readout');
    const performance = JSON.parse(section.querySelector('#explore-performance').textContent);
    const selected = new Set(performance.selected);
    const historySelection = new Set(['berkshire', 'citadel', 'bridgewater']);
    const percentageMeasures = new Set(['top10', 'weight_shift', 'return',
        'risk:volatility', 'risk:max_drawdown', 'risk:alpha']);
    const config = {responsive: true, displayModeBar: false};
    let history;
    let loading;
    let rendering = Promise.resolve();

    if (!window.Plotly) {
        plot.hidden = true;
        status.textContent = 'The comparison chart is unavailable. The rankings and filings are below.';
        return;
    }

    function chart(dates, entries, metric) {
        const dollars = metric === 'value';
        const percent = metric === 'performance' || metric.startsWith('sector:') || percentageMeasures.has(metric);
        const isAnnual = metric === 'return' || metric.startsWith('risk:');
        const traces = entries.map(({name, values, benchmark}) => ({
            type: 'scatter',
            mode: isAnnual ? 'lines+markers' : 'lines',
            name,
            x: dates,
            y: metric === 'performance' ? values.map(value => value / 100 - 1) : values,
            line: benchmark ? {color: '#a9a49a', dash: 'dot', width: 1.75} : {width: 2},
            marker: {size: 4},
            connectgaps: false,
            hovertemplate: '%{y:' + (dollars ? '$.3s' : percent ? '.2%' : ',.2~f') + '}',
        }));
        const layout = {
            autosize: true,
            margin: {t: 40, r: 22, b: 35, l: 62},
            showlegend: true,
            legend: {orientation: 'h', x: 0, y: 1.04, yanchor: 'bottom'},
            hovermode: 'x unified',
            xaxis: {type: 'date', hoverformat: isAnnual ? '%Y' : '%d %b %Y', automargin: true},
            yaxis: {tickformat: dollars ? '$.3s' : percent ? '.0%' : ',.3~s', automargin: true},
        };
        // Serialize updates if users change controls while Plotly is laying out the chart.
        rendering = rendering.catch(() => {}).then(() => Plotly.newPlot(plot, traces, layout, config));
        return rendering;
    }

    function listManagers(entries, selection) {
        manager.replaceChildren(new Option('Select a manager', ''),
            ...Object.entries(entries).filter(([slug]) => !selection.has(slug))
                .sort(([, a], [, b]) => a.name.localeCompare(b.name))
                .map(([slug, row]) => new Option(row.name, slug)));
    }

    async function draw() {
        const metric = measure.value;
        if (metric === 'performance') {
            listManagers(performance.curves, selected);
            const entries = [...selected].map(slug => performance.curves[slug]);
            if (performance.benchmark.length) entries.push({
                name: 'SPY', values: performance.benchmark, benchmark: true,
            });
            status.textContent = selected.size
                ? 'Weekly total returns from disclosed holdings. Click a legend entry to hide it. Drag to zoom.'
                : 'SPY is shown. Choose a shorter return window to compare managers with continuous coverage.';
            await chart(performance.dates, entries, metric);
            return;
        }

        if (!history) {
            status.textContent = 'Loading saved filing history…';
            loading ??= fetch(section.dataset.explorer).then(response => {
                if (!response.ok) throw new Error('History unavailable');
                return response.json();
            }).then(data => {
                history = data;
                const industries = document.createElement('optgroup');
                industries.label = 'Sector exposure';
                for (const [key, name] of Object.entries(data.industries)) {
                    industries.append(new Option(name, 'sector:' + key));
                }
                measure.append(industries);
            }).catch(error => {
                loading = null;
                throw error;
            });
            await loading;
            if (metric !== measure.value) return;
        }

        listManagers(history.managers, historySelection);
        const annual = metric === 'return' || metric.startsWith('risk:');
        const dates = annual ? history.periods.filter(period => period.endsWith('12-31')) : history.periods;
        const entries = [...historySelection].filter(slug => slug in history.managers).map(slug => {
            const row = history.managers[slug];
            const values = dates.map(date => {
                if (metric === 'return') return row.returns[date.slice(0, 4)] ?? null;
                if (annual) return row.risk?.[date.slice(0, 4)]?.[metric.slice(5)] ?? null;
                const reading = row.history[date];
                if (metric.startsWith('sector:')) return reading?.sectors?.[metric.slice(7)] ?? null;
                return reading?.[metric] ?? null;
            });
            return {name: row.name, values};
        });
        status.textContent = 'Full archive. Gaps indicate periods without an observation.';
        await chart(dates, entries, metric);
    }

    function render() {
        draw().catch(() => { status.textContent = 'The selected chart is temporarily unavailable.'; });
    }

    form.addEventListener('submit', event => {
        event.preventDefault();
        if (!manager.value) return;
        const selection = measure.value === 'performance' ? selected : historySelection;
        if (selection.size >= 8) {
            // Hidden traces can be removed simply by starting a fresh selection for this measure.
            for (const slug of selection) {
                const entries = measure.value === 'performance' ? performance.curves : history?.managers;
                if (plot.data?.some(trace => trace.name === entries?.[slug]?.name && trace.visible === 'legendonly')) {
                    selection.delete(slug);
                }
            }
        }
        if (selection.size >= 8) {
            status.textContent = 'Hide a manager using the legend before adding another. Compare up to eight at once.';
            return;
        }
        selection.add(manager.value);
        render();
    });
    measure.addEventListener('change', render);
    section.querySelector('#explore-reset').addEventListener('click', () => {
        if (plot.data) Plotly.relayout(plot, {'xaxis.autorange': true, 'yaxis.autorange': true});
    });
    form.hidden = false;
    render();
})();
