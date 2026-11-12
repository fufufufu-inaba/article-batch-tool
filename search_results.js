return (() => {
 const root=document.querySelector('#search');
 if(!root) return [];
 const results=[];
 for(const h of root.querySelectorAll('h3')) {
   const a=h.closest('a[href]');
   if(!a || !h.getBoundingClientRect().height) continue;
   // 排除明确标记的广告容器和广告跳转，不依靠结果排名排除广告。
   if(a.closest('#tads,#tadsb,[data-text-ad],[data-ta-slot],[data-ad-slot],.commercial-unit-desktop-top')) continue;
   if(/googleadservices|\/aclk\?|\/pagead\//i.test(a.href)) continue;
   // 向上找仅包含当前结果标题的最近容器，避免拿到相邻结果的cite。
   let card=a;
   while(card && card!==root) {
     if(card.querySelector('cite') && card.querySelectorAll('h3').length===1) break;
     const parent=card.parentElement;
     if(!parent || parent===root || parent.querySelectorAll('h3').length>1) {card=null;break;}
     card=parent;
   }
   if(card && Array.from(card.querySelectorAll('span')).some(s=> /^(Sponsored|Ad|Ads|赞助商广告|赞助商|广告)$/i.test(s.textContent.trim()))) continue;
   const cite=card?.querySelector('.byrV5b cite') || card?.querySelector('cite');
   results.push({title:h.innerText,url:a.href,cite:cite?.textContent?.trim() || ''});
 }
 return results;
})();
