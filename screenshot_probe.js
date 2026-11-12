return (() => {
 const visible = e => { const r=e.getBoundingClientRect(), s=getComputedStyle(e); return r.width>3 && r.height>3 && r.top<innerHeight && r.bottom>0 && r.left<innerWidth && r.right>0 && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity)>0; };
 const all=Array.from(document.querySelectorAll('*')).filter(visible);
 const text=(document.body?.innerText||'').slice(0,12000);
 const title=document.title;
 const challenge=/just a moment|verify you are human|verifying you are human|checking your browser|unusual traffic|人机验证|安全验证/i.test(title+' '+text) || all.some(e=>e.matches('iframe[src*="challenges.cloudflare.com"],iframe[src*="recaptcha"],iframe[src*="captcha"],.g-recaptcha,#challenge-running'));
 const cookie=all.some(e=>{
   const t=(e.innerText||'').trim();
   if(t.length<12||t.length>3500||! /cookies?|隐私|饼干/i.test(t)) return false;
   const s=getComputedStyle(e);
   return (e.matches('[role="dialog"],[id*="cookie"],[class*="cookie"],[id*="consent"],[class*="consent"]')||s.position==='fixed'||s.position==='absolute') && !!e.querySelector('button,a,input,[role="button"]');
 });
 const pending=[], broken=[];
 for(const e of all.filter(e=>e.tagName==='IMG')) {
   if(!e.complete) pending.push('图片'); else if(!e.naturalWidth) broken.push(e.currentSrc||e.src||'图片');
 }
 window.__shotBG ||= {};
 for(const e of all) for(const pseudo of [null,'::before','::after']) {
   const bg=getComputedStyle(e,pseudo).backgroundImage;
   for(const match of bg.matchAll(/url\(["']?(.*?)["']?\)/g)) {
     const url=match[1];
     if(!window.__shotBG[url]) { const im=new Image(); window.__shotBG[url]=im; im.src=url; }
     const im=window.__shotBG[url];
     if(!im.complete) pending.push('背景图'); else if(!im.naturalWidth) broken.push(url);
   }
 }
 for(const v of all.filter(e=>e.tagName==='VIDEO')) {
   if(v.readyState<2||!v.videoWidth) pending.push('视频首帧');
   if(v.error) broken.push('视频加载失败');
 }
 if(document.fonts?.status==='loading') pending.push('字体');
 const error=/^(404|403|500|502|503)\b|access denied|site not found|privacy error/i.test(title) || /no results were found|this site can.t be reached|ERR_NAME_NOT_RESOLVED|ERR_CERT_|网页无法访问|无法访问此网站/i.test(text);
 return {challenge,cookie,error,pending,broken,title,url:location.href,ready:document.readyState!=='loading',textLength:text.trim().length};
})();
