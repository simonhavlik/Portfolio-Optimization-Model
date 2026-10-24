/* Report header: cumulative return strip + tool actions. */
(function () {
    'use strict';

    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function el(id) { return document.getElementById(id); }

    /* ---------------- copy link ---------------- */

    var copyBtn = document.querySelector('[data-copy-link]');
    if (copyBtn) {
        copyBtn.addEventListener('click', function () {
            var url = window.location.href;
            var done = function () {
                copyBtn.setAttribute('data-copied', '1');
                var label = copyBtn.querySelector('span');
                var prev = label ? label.textContent : '';
                if (label) label.textContent = 'Copied';
                setTimeout(function () {
                    copyBtn.removeAttribute('data-copied');
                    if (label) label.textContent = prev;
                }, 1600);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(url).then(done).catch(done);
            } else {
                done();
            }
        });
    }

    /* ---------------- cumulative return strip ---------------- */

    var dataEl = el('rep-series');
    var canvas = el('rep-line');
    if (!dataEl || !canvas || !canvas.getContext) return;

    var data;
    try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
    if (!data || !data.returns || data.returns.length < 2) return;

    var ctx = canvas.getContext('2d');
    var tip = el('rep-tip');
    var wrap = canvas.parentElement;

    /* cumulative series from daily returns */
    var cum = new Array(data.returns.length);
    var acc = 1;
    for (var i = 0; i < data.returns.length; i++) {
        acc *= (1 + (data.returns[i] || 0));
        cum[i] = acc;
    }

    /* benchmark is already cumulative; resample to the portfolio length */
    var bench = data.bench && data.bench.length > 1 ? data.bench : null;
    var benchAligned = null;
    if (bench) {
        benchAligned = new Array(cum.length);
        for (var j = 0; j < cum.length; j++) {
            var pos = (j / (cum.length - 1)) * (bench.length - 1);
            var lo = Math.floor(pos), hi = Math.min(bench.length - 1, lo + 1);
            benchAligned[j] = bench[lo] + (bench[hi] - bench[lo]) * (pos - lo);
        }
    }

    var dates = data.dates || [];
    var benchName = data.bench_name || 'Benchmark';

    var LO = 46, RO = 14, TO = 14, BO = 24;
    var hover = -1;
    var progress = reduce ? 1 : 0;
    var raf = null;

    function fmtDate(d) {
        if (!d) return '';
        var p = String(d).split('-');
        if (p.length !== 3) return d;
        var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return months[(parseInt(p[1], 10) - 1) || 0] + ' ' + p[0].slice(2);
    }
    function longDate(d) {
        if (!d) return '';
        var p = String(d).split('-');
        if (p.length !== 3) return d;
        var months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
        return months[(parseInt(p[1], 10) - 1) || 0] + ' ' + parseInt(p[2], 10) + ', ' + p[0];
    }
    function pct(v) { return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; }

    function yRange() {
        var vals = cum.slice();
        if (benchAligned) vals = vals.concat(benchAligned);
        var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
        var pad = Math.max(0.004, (hi - lo) * 0.12);
        return { lo: lo - pad, hi: hi + pad };
    }

    function draw() {
        var dpr = Math.min(window.devicePixelRatio || 1, 2);
        var w = canvas.clientWidth || wrap.clientWidth;
        var h = canvas.clientHeight || 200;
        if (!w || !h) return;
        if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);

        var plotW = w - LO - RO;
        var plotH = h - TO - BO;
        var r = yRange();
        var span = r.hi - r.lo || 1;
        var n = cum.length;

        function xAt(i) { return LO + (i / (n - 1)) * plotW; }
        function yAt(v) { return TO + (1 - (v - r.lo) / span) * plotH; }

        /* grid + y labels on round percentages */
        ctx.font = "9px 'JetBrains Mono', monospace";
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        var spanPct = (r.hi - r.lo) * 100;
        var steps = [0.5, 1, 2, 2.5, 5, 10, 20, 25, 50];
        var step = steps[steps.length - 1];
        for (var si = 0; si < steps.length; si++) {
            if (spanPct / steps[si] <= 6) { step = steps[si]; break; }
        }
        var first = Math.ceil(((r.lo * 100) / step)) * step;
        for (var pctv = first; pctv <= r.hi * 100; pctv += step) {
            var y = yAt(1 + pctv / 100);
            ctx.strokeStyle = Math.abs(pctv) < 1e-9 ? 'rgba(236, 233, 226, 0.2)' : 'rgba(236, 233, 226, 0.07)';
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(LO, y); ctx.lineTo(w - RO, y); ctx.stroke();
            ctx.fillStyle = 'rgba(236, 233, 226, 0.42)';
            var label = (pctv > 0 ? '+' : '') + pctv.toFixed(step % 1 ? 1 : 0) + '%';
            ctx.fillText(label, LO - 7, y);
        }

        /* baseline 0% */
        if (r.lo < 1 && r.hi > 1) {
            ctx.strokeStyle = 'rgba(236, 233, 226, 0.18)';
            ctx.setLineDash([2, 3]);
            ctx.beginPath(); ctx.moveTo(LO, yAt(1)); ctx.lineTo(w - RO, yAt(1)); ctx.stroke();
            ctx.setLineDash([]);
        }

        /* x labels */
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        var xTicks = w > 640 ? 6 : 3;
        for (var k = 0; k <= xTicks; k++) {
            var idx = Math.round((k / xTicks) * (n - 1));
            ctx.fillStyle = 'rgba(236, 233, 226, 0.32)';
            ctx.fillText(fmtDate(dates[idx]), xAt(idx), h - BO + 7);
        }

        var upto = Math.max(1, Math.floor((n - 1) * progress));

        /* area under the portfolio line */
        var grad = ctx.createLinearGradient(0, TO, 0, TO + plotH);
        grad.addColorStop(0, 'rgba(200, 161, 90, 0.26)');
        grad.addColorStop(1, 'rgba(200, 161, 90, 0)');
        ctx.beginPath();
        ctx.moveTo(xAt(0), yAt(cum[0]));
        for (var a = 1; a <= upto; a++) ctx.lineTo(xAt(a), yAt(cum[a]));
        ctx.lineTo(xAt(upto), TO + plotH);
        ctx.lineTo(xAt(0), TO + plotH);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        /* benchmark */
        if (benchAligned) {
            ctx.beginPath();
            for (var b = 0; b <= upto; b++) {
                var bx = xAt(b), by = yAt(benchAligned[b]);
                if (b === 0) ctx.moveTo(bx, by); else ctx.lineTo(bx, by);
            }
            ctx.strokeStyle = 'rgba(196, 206, 222, 0.78)';
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.stroke();
            ctx.setLineDash([]);
        }

        /* portfolio line */
        ctx.beginPath();
        for (var c = 0; c <= upto; c++) {
            var cx = xAt(c), cy = yAt(cum[c]);
            if (c === 0) ctx.moveTo(cx, cy); else ctx.lineTo(cx, cy);
        }
        ctx.strokeStyle = '#e8cd97';
        ctx.lineWidth = 1.7;
        ctx.lineJoin = 'round';
        ctx.stroke();

        /* head dot */
        var headX = xAt(upto), headY = yAt(cum[upto]);
        ctx.beginPath();
        ctx.arc(headX, headY, 7, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(232, 205, 151, 0.16)';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(headX, headY, 2.8, 0, Math.PI * 2);
        ctx.fillStyle = '#f4e2bd';
        ctx.fill();

        /* crosshair */
        if (hover >= 0 && hover < n) {
            var hx = xAt(hover);
            ctx.strokeStyle = 'rgba(236, 233, 226, 0.22)';
            ctx.setLineDash([2, 3]);
            ctx.beginPath(); ctx.moveTo(hx, TO); ctx.lineTo(hx, TO + plotH); ctx.stroke();
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.arc(hx, yAt(cum[hover]), 3, 0, Math.PI * 2);
            ctx.fillStyle = '#fff';
            ctx.fill();
        }

        canvas._geom = { xAt: xAt, n: n, w: w };
    }

    function animate() {
        if (reduce) { progress = 1; draw(); return; }
        var start = performance.now();
        var dur = 1700;
        (function step(now) {
            var p = Math.min(1, (now - start) / dur);
            progress = 1 - Math.pow(1 - p, 3);
            draw();
            if (p < 1) raf = requestAnimationFrame(step);
            else raf = null;
        })(performance.now());
    }

    draw();
    animate();

    /* hover */
    function nearest(evt) {
        if (!canvas._geom) return -1;
        var rect = canvas.getBoundingClientRect();
        var x = evt.clientX - rect.left;
        var plotW = rect.width - LO - RO;
        var ratio = Math.max(0, Math.min(1, (x - LO) / plotW));
        return Math.round(ratio * (canvas._geom.n - 1));
    }

    canvas.addEventListener('pointermove', function (evt) {
        var idx = nearest(evt);
        if (idx === hover) return;
        hover = idx;
        draw();
        if (!tip) return;
        var rect = canvas.getBoundingClientRect();
        var wrapRect = wrap.getBoundingClientRect();
        var px = rect.left - wrapRect.left + LO + (idx / (cum.length - 1)) * (rect.width - LO - RO);
        var py = Math.min(rect.height - 30, Math.max(30, 40));
        tip.hidden = false;
        tip.style.left = px + 'px';
        tip.style.top = py + 'px';
        var benchRow = benchAligned
            ? '<div class="t-row"><span class="k">' + benchName + '</span><span class="v ' + (benchAligned[idx] >= 1 ? 'up' : 'down') + '">' + pct(benchAligned[idx] - 1) + '</span></div>'
            : '';
        tip.innerHTML = '<div class="t-date">' + longDate(dates[idx]) + '</div>' +
            '<div class="t-row"><span class="k">Portfolio</span><span class="v ' + (cum[idx] >= 1 ? 'up' : 'down') + '">' + pct(cum[idx] - 1) + '</span></div>' + benchRow;
    }, { passive: true });

    canvas.addEventListener('pointerleave', function () {
        hover = -1;
        if (tip) tip.hidden = true;
        draw();
    }, { passive: true });

    var rt = null;
    window.addEventListener('resize', function () {
        clearTimeout(rt);
        rt = setTimeout(function () { progress = 1; draw(); }, 120);
    }, { passive: true });
})();
