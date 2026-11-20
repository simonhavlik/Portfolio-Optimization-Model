(() => {
    const target = document.getElementById('timeline-plot');
    const measure = document.getElementById('timeline-metric');
    if (!target || !window.Plotly) return;
    const {chart, benchmark} = JSON.parse(document.getElementById('timeline-data').textContent);
    const interval = document.getElementById('timeline-interval');
    const caption = document.getElementById('timeline-caption');
    const percent = new Intl.NumberFormat('en', {style: 'percent', maximumFractionDigits: 1});
    const captionDefault = caption?.textContent;
    let lastView;

    function draw() {
        const key = measure.value;
        const selected = interval?.value ? chart.intervals[Number(interval.value)] : null;
        const traces = [{
            x: chart.dates, y: chart[key], name: 'Portfolio', mode: 'lines',
            connectgaps: false, hovertemplate: '%{y:.2%}', line: {width: 2},
        }];
        if (key === 'values') traces.push({
            x: chart.dates, y: chart.benchmark, name: benchmark, mode: 'lines',
            connectgaps: false, hovertemplate: '%{y:.2%}',
            line: {width: 1.65, dash: 'dot', color: '#8fa3b8'},
        });
        const axis = {
            type: 'date', hoverformat: '%d %b %Y', automargin: true,
            tickformatstops: [
                {dtickrange: [null, 86400000 * 7], value: '%d %b'},
                {dtickrange: [86400000 * 7, 'M12'], value: '%b %Y'},
                {dtickrange: ['M12', null], value: '%Y'},
            ],
        };
        if (selected) axis.range = [selected.baseline, selected.end];
        const layout = {
            margin: {t: 31, r: 17, b: 31, l: 49},
            legend: {orientation: 'h', x: 0, y: 1.03, yanchor: 'bottom'},
            showlegend: true, hovermode: 'x unified',
            xaxis: axis, yaxis: {tickformat: '.0%', automargin: true},
        };
        if (caption && !selected && key === 'values') window.reportTimelineForPrint = {data: traces, layout};
        lastView = (lastView || Promise.resolve()).catch(() => {}).then(() =>
            Plotly.newPlot(target, traces, layout, {responsive: true, displayModeBar: false}));
        if (caption) caption.textContent = selected
            ? `${selected.sessions} trading sessions · Portfolio ${percent.format(selected.portfolio)} · ${benchmark} ${percent.format(selected.benchmark)}`
            : captionDefault;
    }

    const controls = document.getElementById('timeline-controls') || measure.closest('label');
    controls.hidden = false;
    measure.addEventListener('change', draw);
    interval?.addEventListener('change', draw);
    const disclosure = target.closest('details');
    if (disclosure && !disclosure.open) {
        disclosure.addEventListener('toggle', () => { if (disclosure.open) draw(); }, {once: true});
    } else {
        draw();
    }
})();
