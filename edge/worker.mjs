// Filled from the tracked landing assets by the deployment helper.
const html=__LANDING_HTML__;
const runtime=__LANDING_RUNTIME__;
const hero=__HERO_RUNTIME__;
export default {
 async fetch(request){
  const url=new URL(request.url);
  if(!['GET','HEAD'].includes(request.method))return new Response('Method not allowed',{status:405});
  let content,type,cache;
  if(url.pathname==='/'){content=html;type='text/html; charset=utf-8';cache='public, max-age=300, s-maxage=86400'}
  else if(url.pathname==='/__landing/runtime.js'){content=runtime;type='text/javascript; charset=utf-8';cache='public, max-age=300'}
  else if(url.pathname==='/__landing/hero.js'){content=hero;type='text/javascript; charset=utf-8';cache='public, max-age=300'}
  else return new Response('Not found',{status:404});
  return new Response(request.method==='HEAD'?null:content,{headers:{'Content-Type':type,'Cache-Control':cache,'X-Optimizer-First-Screen':'edge-v1','X-Content-Type-Options':'nosniff','Referrer-Policy':'strict-origin-when-cross-origin'}})
 }
};
