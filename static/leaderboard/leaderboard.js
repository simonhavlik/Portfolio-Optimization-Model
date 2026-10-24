/* Fund leaderboard: charts, sorting, row detail. */
(function () {
    'use strict';

    var el = document.getElementById('lb-data');
    if (!el) return;
    var board;
    try { board = JSON.parse(el.textContent); } catch (e) { return; }
    var rows = (board.rows || []).filter(function (r) { return r.metrics && r.metrics.sharpe_ratio != null; });

    var NS = 'http://www.w3.org/2000/svg';
    function svg(tag, attrs) {
        var node = document.createElementNS(NS, tag);
        for (var k in attrs) node.setAttribute(k, attrs[k]);
        return node;
    }
    function shortName(name) {
        return name.replace(' Asset Management', '').replace(' Capital', '').replace(' Family Office', '')
                   .replace(' Group', '').replace(' Financial', '').replace(' Hathaway', '');
    }

    /* ---------------- risk / return scatter ---------------- */

    function drawScatter() {
        var host = document.getElementById('lb-scatter');
        if (!host || !rows.length) return;
        var W = 560, H = 268, L = 46, R = 14, T = 14, B = 30;
        var xs = rows.map(function (r) { return r.metrics.volatility * 100; });
        var ys = rows.map(function (r) { return r.metrics.annualized_return * 100; });
        var xMin = Math.min.apply(null, xs) * 0.9, xMax = Math.max.apply(null, xs) * 1.08;
        var yMin = Math.min.apply(null, ys) - 3, yMax = Math.max.apply(null, ys) + 3;
        var x = function (v) { return L + (v - xMin) / (xMax - xMin) * (W - L - R); };
        var y = function (v) { return T + (1 - (v - yMin) / (yMax - yMin)) * (H - T - B); };

        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Risk versus return' });
        var grid = svg('g', { 'class': 'lb-grid' });
        for (var i = 0; i <= 4; i++) {
            var gy = T + (H - T - B) * i / 4;
            grid.appendChild(svg('line', { x1: L, y1: gy, x2: W - R, y2: gy }));
            var gx = L + (W - L - R) * i / 4;
            grid.appendChild(svg('line', { x1: gx, y1: T, x2: gx, y2: H - B }));
        }
        root.appendChild(grid);

        var axis = svg('g', { 'class': 'lb-axis' });
        for (var j = 0; j <= 4; j++) {
            var vy = yMin + (yMax - yMin) * j / 4;
            var t = svg('text', { x: L - 6, y: y(vy) + 2.5, 'text-anchor': 'end' });
            t.textContent = vy.toFixed(0) + '%';
            axis.appendChild(t);
            var vx = xMin + (xMax - xMin) * j / 4;
            var t2 = svg('text', { x: x(vx), y: H - B + 12, 'text-anchor': 'middle' });
            t2.textContent = vx.toFixed(0) + '%';
            axis.appendChild(t2);
        }
        var xl = svg('text', { x: (L + W - R) / 2, y: H - 4, 'text-anchor': 'middle' });
        xl.textContent = 'VOLATILITY';
        axis.appendChild(xl);
        var yl = svg('text', { x: 10, y: T + 8, 'text-anchor': 'start' });
        yl.textContent = 'ANNUAL RETURN';
        axis.appendChild(yl);
        root.appendChild(axis);

        var placed = [];
        rows.forEach(function (row) {
            var m = row.metrics;
            var cx = x(m.volatility * 100), cy = y(m.annualized_return * 100);
            var r = 4 + (row.score || 0) / 14;
            var dot = svg('circle', { 'class': 'lb-dot', cx: cx, cy: cy, r: r.toFixed(1) });
            var title = svg('title', {});
            title.textContent = row.name + ' — Sharpe ' + m.sharpe_ratio.toFixed(2) +
                ', return ' + (m.annualized_return * 100).toFixed(1) + '%' +
                ', vol ' + (m.volatility * 100).toFixed(1) + '%';
            dot.appendChild(title);
            root.appendChild(dot);
            var name = shortName(row.name);
            var labelW = name.length * 4.4;
            var labelX = cx + r + 3;
            var labelY = cy + 2.5;
            var collides = placed.some(function (p) {
                return Math.abs(p.y - labelY) < 9 && labelX < p.x + p.w && labelX + labelW > p.x;
            });
            if (collides) { labelY = cy - r - 5; }
            var label = svg('text', { 'class': 'lb-dot-label', x: labelX, y: labelY });
            label.textContent = name;
            root.appendChild(label);
            placed.push({ x: labelX, y: labelY, w: labelW });
        });
        host.innerHTML = '';
        host.appendChild(root);
    }

    /* ---------------- Sharpe bars ---------------- */

    function drawBars() {
        var host = document.getElementById('lb-bars');
        if (!host || !rows.length) return;
        var data = rows.slice().sort(function (a, b) { return b.metrics.sharpe_ratio - a.metrics.sharpe_ratio; });
        var W = 420, H = 268, L = 150, R = 58, T = 12, B = 14;
        var values = data.map(function (r) { return r.metrics.sharpe_ratio; });
        var lo = Math.min(0, Math.min.apply(null, values));
        var hi = Math.max.apply(null, values) * 1.08 || 1;
        var zeroX = L + (0 - lo) / (hi - lo) * (W - L - R);
        var rowH = (H - T - B) / data.length;

        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Sharpe ratio by fund' });
        root.appendChild(svg('line', { x1: zeroX, y1: T, x2: zeroX, y2: H - B, stroke: 'rgba(236,233,226,0.18)', 'stroke-width': 0.7 }));

        data.forEach(function (row, i) {
            var v = row.metrics.sharpe_ratio;
            var y = T + i * rowH + rowH * 0.2;
            var h = rowH * 0.6;
            var x1 = Math.min(zeroX, zeroX + (v / (hi - lo)) * (W - L - R));
            var w = Math.abs(zeroX + (v / (hi - lo)) * (W - L - R) - zeroX);
            var bar = svg('rect', { 'class': 'lb-bar', x: x1, y: y, width: Math.max(1, w), height: h, rx: 0.5 });
            var title = svg('title', {});
            title.textContent = row.name + ': ' + v.toFixed(2);
            bar.appendChild(title);
            root.appendChild(bar);

            var label = svg('text', { 'class': 'lb-bar-label', x: L - 6, y: y + h / 2 + 2.5, 'text-anchor': 'end' });
            label.textContent = shortName(row.name).slice(0, 17);
            root.appendChild(label);

            var val = svg('text', { 'class': 'lb-bar-label', x: W - 6, y: y + h / 2 + 2.5, 'text-anchor': 'end' });
            val.textContent = v.toFixed(2);
            root.appendChild(val);
        });
        host.innerHTML = '';
        host.appendChild(root);
    }

    /* ---------------- sorting ---------------- */

    var table = document.getElementById('lb-table');
    if (table) {
        var tbody = table.tBodies[0];
        var pairs = [];
        Array.prototype.forEach.call(tbody.rows, function (tr) {
            if (tr.classList.contains('lb-row')) {
                var detail = tr.nextElementSibling;
                pairs.push({ row: tr, detail: detail && detail.classList.contains('lb-detail') ? detail : null });
            }
        });
        var boardBySlug = {};
        (board.rows || []).forEach(function (r) { boardBySlug[r.slug] = r; });

        function valueFor(row, key) {
            var data = boardBySlug[row.dataset.slug];
            if (!data) return null;
            if (key === 'covid' || key === 'y2022') return (data.crisis || {})[key];
            if (key === 'score') return data.score;
            if (key === 'pe') return data.pe && data.pe.weighted;
            return (data.metrics || {})[key];
        }

        Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th) {
            var key = th.getAttribute('data-sort');
            if (!key) return;
            th.addEventListener('click', function () {
                var dir = th.getAttribute('data-dir') === 'desc' ? 'asc' : 'desc';
                Array.prototype.forEach.call(table.tHead.rows[0].cells, function (c) { c.removeAttribute('data-dir'); });
                th.setAttribute('data-dir', dir);
                pairs.sort(function (a, b) {
                    var va = valueFor(a.row, key);
                    var vb = valueFor(b.row, key);
                    if (va == null) return 1;
                    if (vb == null) return -1;
                    return dir === 'desc' ? vb - va : va - vb;
                });
                pairs.forEach(function (pair) {
                    tbody.appendChild(pair.row);
                    if (pair.detail) tbody.appendChild(pair.detail);
                });
            });
        });

        /* ---------------- row detail ---------------- */

        pairs.forEach(function (pair) {
            var toggle = function () {
                if (!pair.detail) return;
                var open = !pair.detail.hidden;
                pairs.forEach(function (p) { if (p.detail) p.detail.hidden = true; });
                pair.detail.hidden = open;
            };
            pair.row.addEventListener('click', toggle);
            pair.row.addEventListener('keydown', function (evt) {
                if (evt.key === 'Enter' || evt.key === ' ') { evt.preventDefault(); toggle(); }
            });
        });
    }

    drawScatter();
    drawBars();
    window.addEventListener('resize', function () {
        clearTimeout(window.__lbResize);
        window.__lbResize = setTimeout(function () { drawScatter(); drawBars(); }, 150);
    }, { passive: true });
})();
