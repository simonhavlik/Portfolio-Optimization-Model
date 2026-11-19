(() => {
    const target = document.getElementById('timeline-plot');
    const measure = document.getElementById('timeline-metric');
    if (!target || !window.Plotly) return;
    const {chart, benchmark} = JSON.parse(document.getElementById('timeline-data').textContent);

    function draw() {
        const key = measure.value;
        const traces = [{
            x: chart.dates, y: chart[key], name: 'Portfolio', mode: 'lines',
            connectgaps: false, hovertemplate: '%{y:.2%}', line: {width: 2},
        }];
        if (key === 'values') traces.push({
            x: chart.dates, y: chart.benchmark, name: benchmark, mode: 'lines',
            connectgaps: false, hovertemplate: '%{y:.2%}',
            line: {width: 1.75, dash: 'dot', color: '#a9a49a'},
        });
        Plotly.newPlot(target, traces, {
            margin: {t: 40, r: 20, b: 35, l: 60},
            legend: {orientation: 'h', x: 0, y: 1.04, yanchor: 'bottom'},
            showlegend: true, hovermode: 'x unified',
            xaxis: {type: 'date', hoverformat: '%d %b %Y', automargin: true},
            yaxis: {tickformat: '.0%', automargin: true},
        }, {responsive: true, displayModeBar: false});
    }

    measure.closest('label').hidden = false;
    measure.addEventListener('change', draw);
    draw();
})();
