/* Landing hero runtime: nav state, Three.js field, scripted platform walkthrough. */
(function () {
    'use strict';

    var docEl = document.documentElement;
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var hero = document.querySelector('[data-hero]');

    /* ---------------- navigation over the dark hero ---------------- */

    var nav = document.querySelector('.site-nav');
    var lastSolid = null;
    function syncNav() {
        if (!nav) return;
        var solid = window.scrollY > 24;
        if (solid === lastSolid) return;
        lastSolid = solid;
        nav.classList.toggle('nav-solid', solid);
    }
    syncNav();
    window.addEventListener('scroll', syncNav, { passive: true });

    if (!hero) return;

    /* ---------------- three.js field ---------------- */

    function initScene() {
        var canvas = document.getElementById('hero-webgl');
        if (!canvas || !window.THREE) return;

        var scene = new THREE.Scene();
        scene.fog = new THREE.FogExp2(0x06070a, 0.085);

        var camera = new THREE.PerspectiveCamera(48, 1, 0.1, 60);
        camera.position.set(0, 0, 5.9);

        var renderer;
        try {
            renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: window.innerWidth > 900 });
        } catch (e) {
            return;
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setClearColor(0x000000, 0);

        var group = new THREE.Group();
        scene.add(group);

        var small = window.matchMedia('(max-width: 720px)').matches;
        var nodeCount = small ? 210 : 460;

        /* nodes on a soft shell */
        var vecs = [];
        for (var i = 0; i < nodeCount; i++) {
            var y = 1 - (i / (nodeCount - 1)) * 2;
            var radius = Math.sqrt(Math.max(0, 1 - y * y));
            var theta = i * 2.399963229728653; /* golden angle */
            var shell = 2.15 + Math.random() * 0.55;
            vecs.push(new THREE.Vector3(
                Math.cos(theta) * radius * shell,
                y * shell * 0.86,
                Math.sin(theta) * radius * shell
            ));
        }

        var positions = new Float32Array(vecs.length * 3);
        vecs.forEach(function (v, k) {
            positions[k * 3] = v.x;
            positions[k * 3 + 1] = v.y;
            positions[k * 3 + 2] = v.z;
        });
        var pointsGeo = new THREE.BufferGeometry();
        pointsGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));

        var sprite = (function () {
            var c = document.createElement('canvas');
            c.width = c.height = 64;
            var ctx = c.getContext('2d');
            var g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
            g.addColorStop(0, 'rgba(255,255,255,1)');
            g.addColorStop(0.35, 'rgba(255,255,255,0.85)');
            g.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = g;
            ctx.fillRect(0, 0, 64, 64);
            var tex = new THREE.CanvasTexture(c);
            tex.needsUpdate = true;
            return tex;
        })();

        var pointMat = new THREE.PointsMaterial({
            color: 0xe7c894,
            size: 0.155,
            map: sprite,
            transparent: true,
            opacity: 0.95,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
            sizeAttenuation: true
        });
        var points = new THREE.Points(pointsGeo, pointMat);
        group.add(points);

        /* constellation edges between near neighbours */
        var linkPositions = [];
        var maxDist = small ? 0.5 : 0.42;
        for (var a = 0; a < vecs.length; a++) {
            for (var b = a + 1; b < vecs.length; b++) {
                if (vecs[a].distanceToSquared(vecs[b]) < maxDist * maxDist) {
                    linkPositions.push(vecs[a].x, vecs[a].y, vecs[a].z, vecs[b].x, vecs[b].y, vecs[b].z);
                }
            }
            if (linkPositions.length > 9000) break;
        }
        var linkGeo = new THREE.BufferGeometry();
        linkGeo.setAttribute('position', new THREE.Float32BufferAttribute(linkPositions, 3));
        var links = new THREE.LineSegments(linkGeo, new THREE.LineBasicMaterial({
            color: 0x8c8f96,
            transparent: true,
            opacity: 0.22,
            depthWrite: false
        }));
        group.add(links);

        /* three orbital rings — the instrument feel */
        /* faint wireframe shell — structure, not sparkle */
        var shell = new THREE.LineSegments(
            new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(2.42, 1)),
            new THREE.LineBasicMaterial({ color: 0xc8a15a, transparent: true, opacity: 0.09, depthWrite: false })
        );
        shell.rotation.set(0.4, 0.2, 0);
        group.add(shell);

        var rings = [];
        for (var r = 0; r < 3; r++) {
            var ring = new THREE.LineLoop(
                new THREE.BufferGeometry().setFromPoints(
                    (function () {
                        var pts = [];
                        for (var s = 0; s <= 220; s++) {
                            var t = (s / 220) * Math.PI * 2;
                            pts.push(new THREE.Vector3(Math.cos(t) * (2.75 + r * 0.14), 0, Math.sin(t) * (2.75 + r * 0.14)));
                        }
                        return pts;
                    })()
                ),
                new THREE.LineBasicMaterial({ color: 0xc8a15a, transparent: true, opacity: 0.22 - r * 0.045, depthWrite: false })
            );
            ring.rotation.x = Math.PI / 2.35 + r * 0.16;
            ring.rotation.z = r * 0.42;
            rings.push(ring);
            group.add(ring);
        }

        var pointer = { x: 0, y: 0, tx: 0, ty: 0 };
        hero.addEventListener('pointermove', function (e) {
            var rect = hero.getBoundingClientRect();
            pointer.tx = ((e.clientX - rect.left) / Math.max(rect.width, 1) - 0.5);
            pointer.ty = ((e.clientY - rect.top) / Math.max(rect.height, 1) - 0.5);
        }, { passive: true });
        hero.addEventListener('pointerleave', function () { pointer.tx = 0; pointer.ty = 0; }, { passive: true });

        function resize() {
            var w = canvas.clientWidth || hero.clientWidth;
            var h = canvas.clientHeight || hero.clientHeight;
            if (!w || !h) return;
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        }
        resize();
        window.addEventListener('resize', resize, { passive: true });
        if (window.ResizeObserver) new ResizeObserver(resize).observe(canvas);

        var raf = null;
        var visible = true;
        function frame(now) {
            var t = now * 0.0001;
            pointer.x += (pointer.tx - pointer.x) * 0.05;
            pointer.y += (pointer.ty - pointer.y) * 0.05;

            group.rotation.y = t * 1.35 + pointer.x * 0.32;
            group.rotation.x = Math.sin(t * 0.55) * 0.10 - pointer.y * 0.18;

            rings.forEach(function (ring, k) {
                ring.rotation.y = -t * (0.5 + k * 0.22);
                ring.rotation.z = k * 0.42 + Math.sin(t * 0.8 + k) * 0.06;
            });

            camera.position.x = pointer.x * 0.55;
            camera.position.y = -pointer.y * 0.42;
            camera.lookAt(0, 0, 0);

            renderer.render(scene, camera);
            raf = visible && !document.hidden ? requestAnimationFrame(frame) : null;
        }

        if (reduce) {
            renderer.render(scene, camera);
        } else {
            raf = requestAnimationFrame(frame);
            document.addEventListener('visibilitychange', function () {
                if (document.hidden) {
                    if (raf) cancelAnimationFrame(raf);
                    raf = null;
                } else if (!raf && visible) {
                    raf = requestAnimationFrame(frame);
                }
            });
        }

        if (window.IntersectionObserver) {
            new IntersectionObserver(function (entries) {
                visible = entries[0].isIntersecting;
                if (visible && !raf && !reduce && !document.hidden) raf = requestAnimationFrame(frame);
                if (!visible && raf) { cancelAnimationFrame(raf); raf = null; }
            }, { threshold: 0.05 }).observe(hero);
        }

    }

    /* ---------------- walkthrough ---------------- */

    function initDemo() {
        var demo = hero.querySelector('[data-demo]');
        if (!demo) return;
        var stages = Array.prototype.slice.call(demo.querySelectorAll('[data-stage]'));
        var steps = Array.prototype.slice.call(demo.querySelectorAll('[data-step]'));
        var dur = parseInt(demo.getAttribute('data-dur'), 10) || 6200;
        demo.style.setProperty('--dur', dur + 'ms');

        /* deterministic decoration */
        var matrix = demo.querySelector('[data-matrix]');
        if (matrix) {
            var frag = document.createDocumentFragment();
            for (var c = 0; c < 144; c++) {
                var cell = document.createElement('i');
                cell.style.setProperty('--d', ((c % 12) * 0.09 + Math.random() * 1.6).toFixed(2) + 's');
                frag.appendChild(cell);
            }
            matrix.appendChild(frag);
        }

        var dotGroup = demo.querySelector('[data-frontier-dots]');
        if (dotGroup) {
            var svgNS = 'http://www.w3.org/2000/svg';
            for (var d = 0; d < 30; d++) {
                var prog = d / 29;
                var x = 50 + prog * 236 + (Math.random() - 0.5) * 16;
                var y = 150 - Math.pow(prog, 0.72) * 118 + (Math.random() - 0.5) * 13;
                var cir = document.createElementNS(svgNS, 'circle');
                cir.setAttribute('cx', x.toFixed(1));
                cir.setAttribute('cy', y.toFixed(1));
                cir.setAttribute('r', (1 + Math.random() * 1.3).toFixed(1));
                cir.style.setProperty('--i', d);
                dotGroup.appendChild(cir);
            }
        }

        /* leaderboard stage: metric tabs re-rank the rows */
        var board = demo.querySelector('[data-board]');
        if (board) {
            var boardRows = Array.prototype.slice.call(board.querySelectorAll('.board-row'));
            var boardTabs = Array.prototype.slice.call(board.querySelectorAll('[data-bmetric]'));
            var boardMetrics = ['sharpe', 'sortino', 'alpha'];
            var boardIndex = 0;

            var renderMetric = function (key) {
                var scored = boardRows.map(function (row) {
                    return { row: row, value: parseFloat(row.getAttribute('data-' + key)) || 0 };
                });
                scored.sort(function (a, b) { return b.value - a.value; });
                var peak = 0;
                scored.forEach(function (d) { peak = Math.max(peak, Math.abs(d.value)); });
                peak = peak || 1;
                scored.forEach(function (d, i) {
                    d.row.style.order = i;
                    var rank = d.row.querySelector('.b-rank');
                    var val = d.row.querySelector('.b-val');
                    var fill = d.row.querySelector('.b-fill');
                    if (rank) rank.textContent = i + 1;
                    if (val) {
                        val.textContent = key === 'alpha'
                            ? (d.value > 0 ? '+' : '') + d.value.toFixed(1) + '%'
                            : d.value.toFixed(2);
                    }
                    if (fill) fill.style.width = Math.max(8, Math.round((Math.abs(d.value) / peak) * 100)) + '%';
                    d.row.classList.toggle('is-top', i === 0);
                });
                var top = boardRows.filter(function (row) { return row.classList.contains('is-top'); })[0];
                var topIdx = top ? top.getAttribute('data-bidx') : null;
                Array.prototype.forEach.call(board.querySelectorAll('.bs-dot'), function (dot) {
                    dot.classList.toggle('is-top', dot.getAttribute('data-bidx') === topIdx);
                });
                boardTabs.forEach(function (tab) {
                    tab.classList.toggle('is-active', tab.getAttribute('data-bmetric') === key);
                });
            };

            boardTabs.forEach(function (tab) {
                tab.addEventListener('click', function (evt) {
                    evt.stopPropagation();
                    boardIndex = Math.max(0, boardMetrics.indexOf(tab.getAttribute('data-bmetric')));
                    renderMetric(boardMetrics[boardIndex]);
                });
            });

            renderMetric('sharpe');
            setInterval(function () {
                if (!board.closest('.stage.is-active')) return;
                boardIndex = (boardIndex + 1) % boardMetrics.length;
                renderMetric(boardMetrics[boardIndex]);
            }, 2300);
        }

        var elapsedEl = demo.querySelector('[data-elapsed]');
        var sharpeEl = demo.querySelector('[data-sharpe]');
        var rotateEl = demo.querySelector('[data-rotate]');
        var rotateLines = [];
        try { rotateLines = JSON.parse(rotateEl ? rotateEl.getAttribute('data-rotate') : '[]'); } catch (e) { rotateLines = []; }

        var idx = 0;
        var timer = null;
        var inView = false;
        var elapsedRaf = null;
        var sharpeRaf = null;

        function stopTimers() {
            clearTimeout(timer);
            timer = null;
            if (elapsedRaf) { cancelAnimationFrame(elapsedRaf); elapsedRaf = null; }
            if (sharpeRaf) { cancelAnimationFrame(sharpeRaf); sharpeRaf = null; }
        }

        function runElapsed() {
            if (elapsedRaf) cancelAnimationFrame(elapsedRaf);
            if (!elapsedEl) return;
            var t0 = performance.now();
            var cap = Math.round(dur / 1000) + 3;
            (function tick(now) {
                var s = Math.min(cap, Math.floor((now - t0) / 1000));
                elapsedEl.textContent = '00:' + String(s).padStart(2, '0');
                elapsedRaf = requestAnimationFrame(tick);
            })(performance.now());
            elapsedEl.textContent = '00:00';
        }

        function runSharpe() {
            if (sharpeRaf) cancelAnimationFrame(sharpeRaf);
            if (!sharpeEl) return;
            var t0 = performance.now();
            var span = 1500;
            (function tick(now) {
                var p = Math.min(1, (now - t0) / span);
                var eased = 1 - Math.pow(1 - p, 3);
                sharpeEl.textContent = (1.42 * eased).toFixed(2);
                if (p < 1) sharpeRaf = requestAnimationFrame(tick);
                else sharpeRaf = null;
            })(performance.now());
        }

        function runConsole() {
            if (!rotateEl || rotateLines.length < 2) return;
            var n = 0;
            clearInterval(rotateEl._iv);
            rotateEl._iv = setInterval(function () {
                n = (n + 1) % rotateLines.length;
                rotateEl.innerHTML = rotateLines[n];
            }, 2100);
        }

        function go(n) {
            stopTimers();
            idx = ((n % stages.length) + stages.length) % stages.length;

            stages.forEach(function (s, k) {
                s.classList.remove('is-active');
                if (k === idx) s.setAttribute('aria-hidden', 'false');
                else s.setAttribute('aria-hidden', 'true');
            });
            steps.forEach(function (s, k) {
                s.classList.toggle('is-active', k === idx);
                s.setAttribute('aria-selected', k === idx ? 'true' : 'false');
                s.tabIndex = k === idx ? 0 : -1;
            });

            /* restart the CSS animations cleanly */
            void demo.offsetWidth;
            stages[idx].classList.add('is-active');

            if (idx === 1) runElapsed();
            if (idx === 2) runSharpe();
            if (idx === 0 && sharpeEl) sharpeEl.textContent = '1.42';
            if (idx === 0 && elapsedEl) elapsedEl.textContent = '00:00';
            runConsole();

            scheduleNext();
        }

        /* one place decides when the next step runs, so nothing can stall it */
        function scheduleNext() {
            clearTimeout(timer);
            timer = null;
            if (reduce || !inView || document.hidden) return;
            timer = setTimeout(function () { go(idx + 1); }, dur);
        }

        steps.forEach(function (btn, k) {
            btn.addEventListener('click', function () {
                go(k);
            });
            btn.addEventListener('keydown', function (e) {
                if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                    e.preventDefault();
                    var next = k + (e.key === 'ArrowRight' ? 1 : -1);
                    go(next);
                    steps[((next % steps.length) + steps.length) % steps.length].focus();
                }
            });
        });

        if (window.IntersectionObserver) {
            new IntersectionObserver(function (entries) {
                inView = entries[0].isIntersecting;
                scheduleNext();
            }, { threshold: 0.25 }).observe(demo);
        } else {
            inView = true;
        }

        document.addEventListener('visibilitychange', function () {
            if (document.hidden) {
                clearTimeout(timer);
                timer = null;
            } else {
                scheduleNext();
            }
        });

        /* watchdog: if the walkthrough ever goes quiet while visible, restart it */
        setInterval(function () {
            if (!document.hidden && inView && !reduce && !timer) scheduleNext();
        }, 2500);

        go(0);
    }

    function start() {
        try { initScene(); } catch (e) { /* background stays CSS-only */ }
        try { initDemo(); } catch (e) { /* stages remain on step 01 */ }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
