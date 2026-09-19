function displayTime(at, day) {
  return at ? new Date(at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : day ? day + '（时刻待公布）' : '待公布';
}
function node(tag, text) { const el=document.createElement(tag); if(text !== undefined) el.textContent=text; return el; }
function sourceLabel(s) { return s.summary || (s.excerpt || '').split('\n')[0].slice(0,100) || s.account_name; }
function sourceLinks(parent, sources) {
  const list=node('ul'); const seen=new Set();
  for(const s of sources || []) { if(seen.has(s.url))continue; seen.add(s.url);
    const li=node('li'), a=node('a',sourceLabel(s)); a.href=s.url;a.rel='noreferrer';li.append(a);list.append(li);
  } parent.append(list);
}
const viewer=document.getElementById('image-viewer'), viewerImage=document.getElementById('viewer-image');
const viewerStage=viewer.querySelector('.viewer-stage'), zoom=document.getElementById('viewer-zoom');
let galleryItems=[], galleryIndex=0, returnFocus=null;
function showPicture(index){
 galleryIndex=(index+galleryItems.length)%galleryItems.length;
 const item=galleryItems[galleryIndex];
 viewerStage.classList.remove('original');zoom.textContent='原尺寸';zoom.setAttribute('aria-pressed','false');
 document.getElementById('viewer-status').hidden=true;viewerImage.hidden=false;
 viewerImage.alt=item.alt;viewerImage.src=item.asset_path;
 document.getElementById('viewer-caption').textContent=item.alt+' · '+(galleryIndex+1)+' / '+galleryItems.length;
 document.getElementById('viewer-original').href=item.source_url;
 for(const id of ['viewer-prev','viewer-next'])document.getElementById(id).disabled=galleryItems.length<2;
 viewerStage.scrollTop=0;viewerStage.scrollLeft=0;
}
function openGallery(items,index,trigger){
 if(typeof viewer.showModal!=='function')return false;
 galleryItems=items;returnFocus=trigger;showPicture(index);viewer.showModal();document.body.classList.add('viewer-open');return true;
}
viewerImage.addEventListener('error',()=>{viewerImage.hidden=true;document.getElementById('viewer-status').hidden=false;});
viewerImage.addEventListener('load',()=>{viewerImage.hidden=false;document.getElementById('viewer-status').hidden=true;});
document.getElementById('viewer-close').addEventListener('click',()=>viewer.close());
document.getElementById('viewer-prev').addEventListener('click',()=>showPicture(galleryIndex-1));
document.getElementById('viewer-next').addEventListener('click',()=>showPicture(galleryIndex+1));
zoom.addEventListener('click',()=>{const original=viewerStage.classList.toggle('original');zoom.textContent=original?'适应窗口':'原尺寸';zoom.setAttribute('aria-pressed',String(original));});
viewer.addEventListener('close',()=>{document.body.classList.remove('viewer-open');returnFocus?.focus();});
viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.close();});
viewer.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();showPicture(galleryIndex+(event.key==='ArrowLeft'?-1:1));}});
const eventId=new URLSearchParams(location.search).get('id')||'';
function render(data){
 const oldAnchor=location.hash.slice(1);
 if(oldAnchor.startsWith('activity-')){
  const mapped=(data.activity_redirects||{})[oldAnchor.slice(9)];
  if(mapped)history.replaceState(null,'',location.pathname+location.search+'#activity-'+mapped);
 }
 document.getElementById('title').textContent=data.title;document.title=data.title;
 const summary=document.getElementById('summary');
 for(const [key,value] of [['IP',data.ip_name],['合作方',data.partner],['公布时间',displayTime(data.announced_at)]])if(value)summary.append(node('dt',key),node('dd',value));
 const activities=document.getElementById('activities');
 for(const a of data.activities){
  const section=node('section');section.id='activity-'+a.id;
  section.append(node('h3',a.title),node('p',displayTime(a.start_at,a.start_date)+' 至 '+displayTime(a.end_at,a.end_date)));
  for(const v of a.venues)section.append(node('p',v.online_platform||[v.province,v.city,v.name,v.address,v.business_hours].filter(Boolean).join(' · ')));
  if(a.rules)section.append(node('p',a.rules));
  const list=node('ul');
  for(const action of a.actions){
   if(action.cancelled)continue;
   const li=node('li',action.title+' · '+displayTime(action.at,action.start_date)+(action.end_at||action.end_date?' 至 '+displayTime(action.end_at,action.end_date):''));
   if(action.platform)li.append(' · '+action.platform);
   if(action.requires_reservation===true)li.append(' · 需预约');
   if(action.requires_rush===true)li.append(' · 需抢购或抢名额');
   if(action.rules)li.append(node('p',action.rules));
   li.id='action-'+action.id;
   if(action.ended)li.append(node('p','已结束'));
   for(const key of ['scope','quantity_limit','end_condition'])if(action[key])li.append(node('p',action[key]));
   if(action.url){const link=node('a','预约 / 购买入口');link.href=action.url;link.rel='noreferrer';li.append(link);}
   list.append(li);
  }section.append(list);
  if(a.related_offers)section.append(node('h4','关联优惠'),node('p',a.related_offers));
  if(a.uncertainties?.length)section.append(node('p','待确认：'+a.uncertainties.join('；')));
  activities.append(section);
 }
 const sources=document.getElementById('sources');
 for(const s of data.sources){
  const section=node('section'),link=node('a',sourceLabel(s));link.href=s.url;link.rel='noreferrer';
  section.append(link,node('p',s.account_name+' · '+displayTime(s.published_at)));
  const pictures=(data.media||[]).filter(m=>m.source_id===s.id);
  const gallery=node('div');gallery.className='gallery';
  const available=pictures.filter(m=>m.asset_path).map((m,i)=>({...m,alt:s.account_name+' · 配图 '+(i+1)}));
  for(const [index,m] of pictures.entries()){
   const figure=node('figure');
   if(m.asset_path){
    const trigger=node('a');trigger.href=m.asset_path;trigger.className='thumbnail';trigger.target='_blank';trigger.rel='noopener noreferrer';
    trigger.setAttribute('aria-label','放大查看 '+s.account_name+' 配图 '+(index+1));
    const img=document.createElement('img');img.src=m.asset_path;img.alt=s.account_name+' · 配图 '+(index+1);img.loading='lazy';
    img.addEventListener('error',()=>{img.hidden=true;trigger.append(node('span','缩略图加载失败，点击查看'));},{once:true});
    trigger.append(img);trigger.addEventListener('click',event=>{if(openGallery(available,available.findIndex(p=>p.asset_path===m.asset_path),trigger))event.preventDefault();});figure.append(trigger);
   } else {const placeholder=node('div','图片暂不可用');placeholder.className='thumbnail missing-image';figure.append(placeholder);}
   const caption=node('figcaption'), a=node('a','配图 '+(index+1)+' · 查看原图 ↗');a.href=m.source_url;a.rel='noopener noreferrer';a.target='_blank';caption.append(a);figure.append(caption);gallery.append(figure);
  }if(pictures.length)section.append(gallery);sources.append(section);
 }
 const anchor=document.getElementById(decodeURIComponent(location.hash.slice(1)));
 if(anchor)anchor.scrollIntoView();
}
if(!/^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$/.test(eventId))document.getElementById('title').textContent='无效的企划编号';
else fetch('events/'+encodeURIComponent(eventId)+'.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(render).catch(()=>{document.getElementById('title').textContent='企划不存在或已经过期';});
