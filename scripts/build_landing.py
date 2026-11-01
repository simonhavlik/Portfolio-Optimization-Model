"""Build the dependency-free first screen from the existing hero and nav."""
import ast
import gzip
import re
from pathlib import Path
from types import SimpleNamespace
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
index = (ROOT/'templates/index.html').read_text()
base = (ROOT/'templates/base.html').read_text()
hero = index[index.index('{% set holdings ='):index.index('\n{% endif %}\n\n<!-- Example Portfolio Card -->')]
nav = base[base.index('    <nav class="site-nav"'):base.index('    </nav>')+len('    </nav>')]
portfolio = ast.parse((ROOT/'services/example_portfolio.py').read_text())
holdings = next(ast.literal_eval(n.value)['holdings'] for n in portfolio.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='EXAMPLE_PORTFOLIO' for t in n.targets))
env = Environment(autoescape=True)
hero = env.from_string(hero).render(example_data={'holdings': holdings})
nav = env.from_string(nav).render(current_user=SimpleNamespace(is_authenticated=False),request=SimpleNamespace(path='/'))
nav = nav.replace('<i class="fas fa-bars" aria-hidden="true"></i>', '<span aria-hidden="true">☰</span>')
nav = nav.replace('id="mobile-menu-btn"','id="mobile-menu-btn" aria-expanded="false" aria-controls="mobile-menu"')
css = (ROOT/'static/hero/hero.css').read_text()
css += base[base.index('        .site-nav {'):base.index('    {% block extra_css %}')].removesuffix('    </style>\n\n')
css = css.replace('</style>', '')
css += '''
*,*::before,*::after{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#06070a;color:#f1ede5;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button{font:inherit}a{color:inherit}ul,ol{list-style:none;padding:0}p,h1,h2,figure{margin:0}button{cursor:pointer}.hidden{display:none!important}
.hero{width:100%;margin:0;min-height:calc(100svh - 58px)}.hero-copy>*,.hero-demo{animation:none!important;opacity:1;transform:none}.checks li,.stage.is-active .tickers li{opacity:1}.hero .mono{font-family:ui-monospace,monospace}.nav-inner{height:58px}.site-nav{position:sticky;top:0;z-index:50}.nav-drawer:not(.hidden){display:flex;flex-direction:column}.nav-drawer a{padding:12px 20px;text-decoration:none}#example{background:#f8fafc;min-height:200px;scroll-margin-top:60px}#example-frame{display:block;border:0;width:100%;min-height:260px}.example-wait{padding:32px;text-align:center;color:#475569}.example-wait a{color:#6b4f24;text-underline-offset:4px}footer.shell-footer{padding:25px;text-align:center;font-size:12px;color:#9d968a}footer.shell-footer a{margin-left:12px}
@media(max-width:740px){.nav-name{font-size:12px}.nav-tag{display:none}.nav-actions{gap:8px}.nav-cta{font-size:10px;padding:8px}.hero-inner{padding-top:30px}.hero-copy{padding-top:8px}}
'''
css = re.sub(r'/\*.*?\*/','',css,flags=re.S)
css = re.sub(r'\s+',' ',css)
css = re.sub(r'\s*([{};:,])\s*',r'\1',css)
favicon = base[base.index('    <link rel="icon"'):base.index('    <meta name="description"')].strip()
html = '''<!doctype html><html lang="en" class="has-hero"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Portfolio Optimizer</title><meta name="description" content="Portfolio analytics from one upload: risk, optimisation, stress tests, PDF and Excel."><link rel="canonical" href="https://optimizer.simonhavlik.com/">'''+favicon+'<style>'+css+'</style></head><body>'+nav+'<main>'+hero+'''<section id="example" aria-label="Example portfolio"><p class="example-wait" id="example-wait">Explore the full example below. <a href="/example">Open the example report</a></p><iframe id="example-frame" title="Example portfolio analysis" data-src="/example?embedded=1" loading="lazy"></iframe></section></main><footer class="shell-footer">Šimon Havlík · Portfolio Optimizer<a href="/privacy">Privacy</a></footer><noscript><p class="example-wait"><a href="/example">Open the complete example analysis</a></p></noscript><script>try{new PerformanceObserver(l=>{for(let e of l.getEntries())if(e.name==='first-contentful-paint')document.documentElement.dataset.firstPaintMs=Math.round(e.startTime)}).observe({type:'paint',buffered:true})}catch{};requestAnimationFrame(()=>requestAnimationFrame(()=>{let s=document.createElement('script');s.src='/__landing/runtime.js';document.body.append(s)}))</script></body></html>'''
html = re.sub(r'<!--.*?-->','',html,flags=re.S)
html = re.sub(r'>\s+<','><',html)
(ROOT/'static/landing/index.html').write_text(html)
print(f'First screen: {len(html.encode()):,} bytes; gzip {len(gzip.compress(html.encode())):,} bytes; zero external render dependencies')
assert len(gzip.compress(html.encode())) < 15000, 'First-screen transfer budget exceeded'
