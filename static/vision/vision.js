/* Reveal the vision sections as they arrive, once each. */
(function () {
    'use strict';
    var nodes = document.querySelectorAll('[data-reveal]');
    if (!nodes.length) return;

    if (!('IntersectionObserver' in window)) {
        Array.prototype.forEach.call(nodes, function (n) { n.classList.add('is-in'); });
        return;
    }

    var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            var el = entry.target;
            var index = Array.prototype.indexOf.call(nodes, el);
            el.style.transitionDelay = Math.min(index * 60, 240) + 'ms';
            el.classList.add('is-in');
            observer.unobserve(el);
        });
    }, { threshold: 0.18, rootMargin: '0px 0px -8% 0px' });

    Array.prototype.forEach.call(nodes, function (n) { observer.observe(n); });
})();
