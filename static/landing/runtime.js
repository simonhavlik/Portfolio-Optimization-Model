/* All enhancements start after the dependency-free first screen has painted. */
(()=>{
'use strict';
const menu=document.getElementById('mobile-menu'),button=document.getElementById('mobile-menu-btn');
button?.addEventListener('click',()=>{const open=menu.classList.toggle('hidden')===false;button.setAttribute('aria-expanded',String(open))});
const frame=document.getElementById('example-frame'),wait=document.getElementById('example-wait');
let loaded=false;
function loadReport(){if(loaded)return;loaded=true;frame.src=frame.dataset.src}
if('IntersectionObserver'in window){const observer=new IntersectionObserver(entries=>{if(entries.some(x=>x.isIntersecting)){loadReport();observer.disconnect()}},{rootMargin:'100px'});observer.observe(frame)}else{loadReport()}
document.querySelector('.hero-jump')?.addEventListener('click',loadReport);
window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==frame.contentWindow)return;if(event.data?.type==='optimizer-report-height'&&Number.isFinite(event.data.height)){frame.style.height=Math.min(Math.max(260,event.data.height),100000)+'px';wait.hidden=true}});
function script(src){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=src;s.onload=resolve;s.onerror=reject;document.body.append(s)})}
// The walkthrough needs no charting framework. Keep GPU effects optional.
const constrained=matchMedia('(prefers-reduced-motion: reduce)').matches||navigator.connection?.saveData||(navigator.deviceMemory&&navigator.deviceMemory<=4)||matchMedia('(pointer: coarse)').matches;
const enhance=async()=>{if(!constrained){try{await script('https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js')}catch{}}await script('/__landing/hero.js').catch(()=>{})};
setTimeout(()=>{if('requestIdleCallback'in window)requestIdleCallback(enhance,{timeout:2000});else enhance()},1000);
})();
