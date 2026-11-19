// Shared report and leaderboard chart theme
(function () {
  if (!window.Plotly) return;
  const axisDefaults = () => ({
    gridcolor: '#242830', zerolinecolor: '#2e333c', linecolor: '#2e333c',
    tickfont: { color: '#a9a49a' }, title: { font: { color: '#a9a49a' } }
  });
  const originalNewPlot = Plotly.newPlot;
  Plotly.newPlot = function (id, data, layout, config) {
    layout = layout || {};
    layout.paper_bgcolor = 'rgba(0,0,0,0)';
    layout.plot_bgcolor = 'rgba(0,0,0,0)';
    layout.font = Object.assign({ color: '#d8d3c8', family: 'Inter, system-ui, sans-serif', size: 11 }, layout.font || {});
    layout.colorway = layout.colorway || ['#c8a15a', '#8fa3b8', '#c07a6a', '#7fa08a', '#a99bc4', '#d9c9a8', '#7f8a94', '#b98f62'];
    layout.xaxis = Object.assign(axisDefaults(), layout.xaxis || {});
    layout.yaxis = Object.assign(axisDefaults(), layout.yaxis || {});
    layout.legend = Object.assign({
      font: { color: '#b9b4aa' }, bgcolor: 'rgba(0,0,0,0)',
      bordercolor: 'rgba(0,0,0,0)', borderwidth: 0
    }, layout.legend || {});
    layout.hoverlabel = Object.assign({
      bgcolor: '#12151a', bordercolor: '#2a2f37', font: { color: '#ece9e2' }
    }, layout.hoverlabel || {});
    return originalNewPlot.call(this, id, data, layout, config);
  };
})();

