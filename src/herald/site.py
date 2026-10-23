"""Minimal static JSON and HTML publisher.

Publishes activity cards, accessible image galleries and seven-day reminders
without a frontend framework or persistent server.
"""

from __future__ import annotations

import json
from hashlib import sha256
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .media import CachedMediaAsset
from .models import Campaign
from .notifications import NotificationService
from .reachability import ReachabilityClassifier
from .scheduler import ScheduleCompiler, schedule_actions
from .storage import StateStore


STYLE_CSS = r"""body{font:16px/1.6 system-ui,sans-serif;color:#222;background:#fff;margin:0}main{max-width:1050px;margin:auto;padding:24px}nav{display:flex;gap:24px;border-bottom:1px solid #bbb;padding-bottom:12px}a{color:#146c59}h1{font-size:28px}h2{font-size:22px}h3{font-size:19px}input{font:inherit;padding:6px;max-width:100%;box-sizing:border-box}#events{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:16px;list-style:none;padding:0}.card{border:1px solid #ccc;border-radius:6px;padding:16px;overflow-wrap:anywhere}.card h2{font-size:20px;margin:4px 0}.deadline{font-weight:600;color:#a52b35}section{border-top:1px solid #ddd;padding:16px 0;scroll-margin-top:16px}p,li,dd{overflow-wrap:anywhere}img{max-width:100%;height:auto}figure{margin:12px 0}summary{cursor:pointer}dt{font-weight:600}dd{margin:0 0 10px}button{font:inherit}small{color:#555}:target{outline:2px solid #146c59;outline-offset:4px}*{letter-spacing:0}"""

STYLE_CSS += r"""
body{background:#f6f7f8;color:#263238}main{max-width:1050px;padding:28px 24px 64px}
nav{gap:24px;border-color:#dfe5e5}nav a{text-decoration:none;font-weight:600;padding:4px 0}
h1{margin:28px 0 20px;line-height:1.3}h2,h3{line-height:1.4}a{text-underline-offset:3px}
.card,#activities>section,#sources>section,#news>section,#upcoming>section{background:white;border:1px solid #dfe5e5;border-radius:12px;padding:20px;margin-bottom:16px}
#summary{display:grid;grid-template-columns:max-content 1fr;gap:8px 20px}#summary dd{margin:0}
button,select{font:inherit;border:1px solid #cbd5d5;border-radius:7px;background:white;color:inherit;padding:8px 12px;cursor:pointer}
button:hover{background:#eef4f3}button:disabled{opacity:.4;cursor:default}
:focus-visible{outline:3px solid #146c59;outline-offset:3px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-top:16px}
.gallery figure{margin:0;min-width:0}.thumbnail{display:flex;align-items:center;justify-content:center;height:180px;background:#f2f4f5;border:1px solid #e2e7e8;border-radius:8px;overflow:hidden}
.thumbnail img{display:block;width:100%;height:100%;object-fit:contain}.gallery figcaption{font-size:13px;margin-top:6px}
.gallery .missing-image{font-size:14px;color:#667;padding:12px;text-align:center}
body.viewer-open{overflow:hidden}
#image-viewer{width:100vw;height:100vh;height:100dvh;max-width:none;max-height:none;border:0;border-radius:0;margin:0;padding:0;background:rgba(0,0,0,.55);color:#fff}
#image-viewer::backdrop{background:transparent}
.viewer-panel{height:100%;position:relative}.viewer-toolbar{position:absolute;bottom:22px;left:50%;transform:translateX(-50%);z-index:3;display:flex;align-items:center;width:max-content;max-width:calc(100% - 24px);padding:3px 10px;gap:10px;border:1px solid rgba(255,255,255,.1);border-radius:24px;background:rgba(20,22,25,.93);box-shadow:0 5px 24px rgba(0,0,0,.2)}
#viewer-counter{font-size:13px;color:#f3f4f5;font-variant-numeric:tabular-nums;letter-spacing:1px;margin:0;padding:0 8px}.viewer-actions{display:flex;gap:2px;align-items:center;border-left:1px solid rgba(255,255,255,.18);padding-left:6px}
#viewer-scale{min-width:34px;font-size:12px;text-align:center;font-variant-numeric:tabular-nums;color:#d2d5da}
#image-viewer .viewer-control{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;box-sizing:border-box;padding:0;border:0;border-radius:50%;background:transparent;color:#e0e2e5;text-decoration:none;cursor:pointer;transition:background .15s,color .15s}
#image-viewer .viewer-control:hover{background:rgba(255,255,255,.1);color:white}#image-viewer .viewer-control:focus-visible{outline:2px solid white;outline-offset:3px}
#image-viewer .viewer-control:disabled{visibility:hidden}#image-viewer .viewer-control svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;pointer-events:none}
#image-viewer .viewer-arrow{position:absolute;top:50%;transform:translateY(-50%);z-index:2;width:52px;height:52px;background:rgba(20,22,25,.5)}#image-viewer .viewer-arrow:hover,#image-viewer #viewer-close:hover{background:rgba(20,22,25,.85)}#image-viewer .viewer-arrow svg{width:28px;height:28px}
#image-viewer #viewer-close{position:absolute;top:16px;right:16px;z-index:3;width:48px;height:48px;background:rgba(20,22,25,.65)}
#viewer-prev{left:22px}#viewer-next{right:22px}#viewer-zoom[aria-pressed=true] .zoom-plus{display:none}
.viewer-stage{position:absolute;inset:24px 64px 112px;display:flex;overflow:auto;text-align:center;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:#5e636b transparent}
#viewer-image{display:block;flex:none;width:auto;height:auto;max-width:100%;max-height:100%;margin:auto;cursor:zoom-in}
.viewer-stage.original #viewer-image{max-width:none;max-height:none;cursor:zoom-out}
#viewer-status{color:#c5c8cc;padding:24px}.viewer-footer{position:absolute;inset:auto 72px 80px;text-align:center;pointer-events:none}
#viewer-caption{margin:0;color:#eee;font-size:12px;line-height:1.5;text-shadow:0 1px 4px #000}.viewer-hint{display:none}
@media(max-width:600px){main{padding:16px 14px 40px}.card,#activities>section,#sources>section{padding:16px}h1{font-size:25px}.gallery{grid-template-columns:repeat(2,minmax(0,1fr))}.thumbnail{height:150px}.viewer-toolbar{bottom:max(16px,env(safe-area-inset-bottom))}.viewer-stage{inset:64px 18px 108px}#image-viewer .viewer-arrow{width:40px;height:40px}#viewer-prev{left:8px}#viewer-next{right:8px}#image-viewer #viewer-close{top:10px;right:10px;width:44px;height:44px}.viewer-footer{left:16px;right:16px;bottom:78px}}
@media(prefers-reduced-motion:reduce){#image-viewer .viewer-control{transition:none}}
"""


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>活动卡片 · HERALD</title><link rel="stylesheet" href="style.css"></head>
<body><main><nav><a href="index.html">活动卡片</a><a href="today.html">每日提醒</a></nav><h1>活动卡片</h1><p id="generated-at"></p><label>搜索活动 <input id="search" type="search"></label><ul id="events"></ul></main><script src="app.js"></script></body>
</html>
"""

APP_JS = r"""function displayTime(at, day) {
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
const list=document.getElementById('events'), search=document.getElementById('search');
let cards=[];
function render() {
 list.replaceChildren(); const query=search.value.trim().toLowerCase();
 for(const c of cards.filter(c=>JSON.stringify(c).toLowerCase().includes(query))) {
   const a=c.activity, li=node('li');li.className='card';
   const link=node('a',a.title);link.href=c.url;
   const heading=node('h2');heading.append(link);
   li.append(node('small',c.campaign_title),heading);
   if(c.next){const remaining=Math.ceil((new Date(c.next.at)-new Date())/86400000);const p=node('p',c.next.title+' · '+displayTime(c.next.date?null:c.next.at,c.next.date)+(remaining>0?' · 还有'+remaining+'天':''));p.className='deadline';li.append(p);}
   li.append(node('p',displayTime(a.start_at,a.start_date)+' 至 '+displayTime(a.end_at,a.end_date)));
   li.append(node('p',(a.venues||[]).map(v=>v.online_platform||[v.city,v.name].filter(Boolean).join(' ')).filter(Boolean).join(' · ')));
   if(a.rules)li.append(node('p',a.rules));
   if(!c.next)li.append(node('p','暂无后续明确时间，请查看原帖'));
   const details=node('details');details.append(node('summary','参与事项与时间'));
   for(const action of a.actions||[]){
     const expired=action.end_at?new Date(action.end_at)<new Date():action.end_date?action.end_date<new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}):false;
     const item=node('p',action.title+' · '+(action.cancelled?'已取消':action.ended?'已结束':(expired?'已过截止时间 · ':'')+displayTime(action.at,action.start_date)+' 至 '+displayTime(action.end_at,action.end_date)));
     for(const key of ['scope','rules','quantity_limit','end_condition'])if(action[key])item.append(node('small',' · '+action[key]));
     details.append(item);
   }li.append(details);
   list.append(li);
 } if(!list.children.length)list.append(node('li','暂无符合条件的活动'));
}
search.addEventListener('input',render);
fetch('data/active.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
 cards=data.cards;document.getElementById('generated-at').textContent='更新时间：'+displayTime(data.generated_at);render();
}).catch(()=>{list.textContent='活动加载失败，请刷新重试';});
// Details use event.html?id=<campaign>#activity-<activity>.
"""

TODAY_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>每日提醒 · HERALD</title><link rel="stylesheet" href="style.css"></head>
<body><main><nav><a href="index.html">活动卡片</a><a href="today.html">每日提醒</a></nav><h1>每日提醒</h1><label>查看日期 <select id="history-date" aria-label="选择提醒日期"></select></label><p id="day"></p><h2>今日新消息</h2><div id="news"></div><h2>即将开始或结束</h2><div id="upcoming"></div></main><script src="today.js"></script></body>
</html>
"""

TODAY_JS = r"""function displayTime(at, day) {
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
let loadVersion=0;
async function loadDay(day) {
 const version=++loadVersion;
 try {
  const response=await fetch('data/reminders/'+encodeURIComponent(day)+'.json',{cache:'no-store'});
  if(!response.ok)throw Error();
  const data=await response.json();if(version!==loadVersion)return;
  document.getElementById('day').textContent=data.date+' · 当日提醒记录';
  for(const [targetId,isNews] of [['news',true],['upcoming',false]]) {
   const target=document.getElementById(targetId);target.replaceChildren();
   for(const c of data.cards) {
    const reasons=c.reasons.filter(r=>['announcement','update'].includes(r.kind)===isNews);
    if(!reasons.length)continue;
    const section=node('section'),h=node('h3');
    const title=c.activity?c.activity.title:c.campaign_title;
    if(c.detail_url){const link=node('a',title);link.href=c.detail_url;h.append(link);}else h.textContent=title;
    section.append(h,node('p',c.campaign_title));
    for(const r of reasons)section.append(node('p',r.summary+(r.expected_at?' · '+displayTime(r.date_only?null:r.expected_at,r.date_only):'')));
    if(!c.detail_url)section.append(node('small',c.extraction_status==='pending'?'待解析，请参考原帖':'详情已下线，请参考原帖'));
    sourceLinks(section,c.sources);target.append(section);
   }
   if(!target.children.length)target.textContent=isNews?'当日暂无新消息':'当日暂无开始或结束提醒';
  }
 } catch(error){if(version===loadVersion){document.getElementById('news').textContent='提醒加载失败，请重试';document.getElementById('upcoming').replaceChildren();}}
}
const dates=document.getElementById('history-date');
dates.addEventListener('change',()=>loadDay(dates.value));
fetch('data/reminders/index.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
 for(const day of data.dates){const option=node('option',day);option.value=day;dates.append(option);}
 if(data.dates.length)loadDay(data.dates[0]);
 else document.getElementById('news').textContent='暂无提醒记录';
}).catch(()=>{document.getElementById('news').textContent='提醒加载失败，请刷新重试';});
"""

DETAIL_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>企划详情 · HERALD</title><link rel="stylesheet" href="style.css"></head>
<body><main><nav><a href="index.html">活动卡片</a><a href="today.html">每日提醒</a></nav><h1 id="title">企划详情</h1><dl id="summary"></dl><h2>活动</h2><div id="activities"></div><h2>原帖与配图</h2><div id="sources"></div></main>
<dialog id="image-viewer" aria-labelledby="viewer-caption">
<div class="viewer-panel"><div class="viewer-toolbar"><p id="viewer-counter" aria-hidden="true"></p><div class="viewer-actions">
<button id="viewer-zoom" class="viewer-control" type="button" aria-label="查看原尺寸" title="查看原尺寸" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5M8 10.5h5"/><path class="zoom-plus" d="M10.5 8v5"/></svg></button>
<span id="viewer-scale" aria-hidden="true"></span>
<a id="viewer-original" class="viewer-control" aria-label="在新标签页打开原图" title="打开原图" target="_blank" rel="noopener noreferrer"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4 10 14M10 5H5v15h15v-5"/></svg></a>
</div></div><button id="viewer-close" class="viewer-control" type="button" aria-label="关闭图片查看器" title="关闭 (Esc)" autofocus><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
<button id="viewer-prev" class="viewer-control viewer-arrow" type="button" aria-label="上一张图片" title="上一张 (←)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 5-7 7 7 7"/></svg></button>
<button id="viewer-next" class="viewer-control viewer-arrow" type="button" aria-label="下一张图片" title="下一张 (→)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 5 7 7-7 7"/></svg></button>
<div class="viewer-stage"><img id="viewer-image" alt=""><p id="viewer-status" role="status" hidden>图片加载失败，请尝试打开原图。</p></div>
<div class="viewer-footer"><p id="viewer-caption" aria-live="polite">原帖配图</p><p class="viewer-hint">点击图片缩放 · ← → 切换 · Esc 关闭</p></div></div></dialog>
<script src="event.js"></script></body>
</html>
"""

DETAIL_JS = r"""function displayTime(at, day) {
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
function updateScale(){
 const scale=viewerImage.getBoundingClientRect().width/viewerImage.naturalWidth;
 document.getElementById('viewer-scale').textContent=Number.isFinite(scale)&&scale>0?Math.round(scale*100)+'%':'';
}
function setOriginal(original){
 viewerStage.classList.toggle('original',original);
 const label=original?'适应窗口':'查看原尺寸';zoom.setAttribute('aria-label',label);zoom.title=label;zoom.setAttribute('aria-pressed',String(original));
 updateScale();
}
function showPicture(index){
 galleryIndex=(index+galleryItems.length)%galleryItems.length;
 const item=galleryItems[galleryIndex];
 setOriginal(false);
 document.getElementById('viewer-status').hidden=true;viewerImage.hidden=false;
 viewerImage.alt=item.alt;viewerImage.src=item.asset_path;
 document.getElementById('viewer-caption').textContent=item.alt;
 document.getElementById('viewer-counter').textContent=(galleryIndex+1)+' / '+galleryItems.length;
 document.getElementById('viewer-original').href=item.source_url;
 for(const id of ['viewer-prev','viewer-next'])document.getElementById(id).disabled=galleryItems.length<2;
 viewerStage.scrollTop=0;viewerStage.scrollLeft=0;
}
function openGallery(items,index,trigger){
 if(typeof viewer.showModal!=='function')return false;
 galleryItems=items;returnFocus=trigger;showPicture(index);viewer.showModal();document.body.classList.add('viewer-open');updateScale();return true;
}
viewerImage.addEventListener('error',()=>{viewerImage.hidden=true;document.getElementById('viewer-status').hidden=false;});
viewerImage.addEventListener('load',()=>{viewerImage.hidden=false;document.getElementById('viewer-status').hidden=true;updateScale();});
window.addEventListener('resize',()=>{if(viewer.open)updateScale();});
document.getElementById('viewer-close').addEventListener('click',()=>viewer.close());
document.getElementById('viewer-prev').addEventListener('click',()=>showPicture(galleryIndex-1));
document.getElementById('viewer-next').addEventListener('click',()=>showPicture(galleryIndex+1));
zoom.addEventListener('click',()=>setOriginal(!viewerStage.classList.contains('original')));
viewerImage.addEventListener('click',()=>setOriginal(!viewerStage.classList.contains('original')));
viewer.addEventListener('close',()=>{document.body.classList.remove('viewer-open');returnFocus?.focus();});
viewer.addEventListener('click',event=>{if(event.target===viewer||event.target.classList.contains('viewer-panel')||event.target===viewerStage)viewer.close();});
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
"""


def _versioned_html(html: str, assets: dict[str, str]) -> str:
    for filename, content in assets.items():
        version = sha256(content.encode("utf-8")).hexdigest()[:12]
        html = html.replace(f'"{filename}"', f'"{filename}?v={version}"')
    return html


class StaticSiteBuilder:
    def __init__(self, timezone_name: str = "Asia/Shanghai") -> None:
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            self.timezone = ScheduleCompiler(timezone_name).timezone
        self.reachability = ReachabilityClassifier()

    def build(
        self,
        *,
        store: StateStore,
        output_dir: Path | str,
        now: datetime,
        watched_ip_slugs: set[str],
        origin_city: str | None = None,
        reachable_cities: list[str] | None = None,
        forbidden_values: list[str] | None = None,
        media_assets: dict[str, CachedMediaAsset] | None = None,
    ) -> list[Campaign]:
        output = Path(output_dir)
        data_dir = output / "data"
        event_dir = output / "events"
        calendar_dir = data_dir / "calendar"
        for directory in (output, data_dir, event_dir, calendar_dir):
            directory.mkdir(parents=True, exist_ok=True)

        # The page branch is a generated view, not an archive. Remove only files
        # owned by this builder so expired event detail URLs cannot linger.
        self._remove_json_files(event_dir)
        self._remove_json_files(calendar_dir)

        campaigns = [
            campaign
            for campaign in store.list_campaigns()
            if campaign.ip_slug in watched_ip_slugs and campaign.is_visible(now)
        ]
        campaigns = [c.model_copy(update={'activities': [a for a in c.activities if a.is_visible(now)]}) for c in campaigns]
        campaigns.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        notification_service = NotificationService(str(self.timezone))
        due, _ = notification_service.collect_due(
            store, now.astimezone(self.timezone).date(), include_delivered=True, now=now,
            watched_ip_slugs=watched_ip_slugs,
        )
        visible_ids = {campaign.id for campaign in campaigns}
        self._write_json(data_dir / "today.json", {
            "date": now.astimezone(self.timezone).date().isoformat(),
            "cards": notification_service.build_cards([
                item for item in due if (item.job.candidate_id or item.campaign.id in visible_ids) and
                (not item.job.activity_id or any(a.id == item.job.activity_id and a.is_visible(now) for a in item.campaign.activities))
            ]),
        })
        self._publish_reminder_history(store, data_dir, now, campaigns,
                                       watched_ip_slugs, notification_service, forbidden_values or [])
        reachable = reachable_cities or []
        summaries = [
            self._campaign_summary(
                campaign,
                now=now,
                origin_city=origin_city,
                reachable_cities=reachable,
            )
            for campaign in campaigns
        ]
        self._write_json(
            data_dir / "active.json",
            {
                "generated_at": now.astimezone(self.timezone).isoformat(),
                "campaigns": summaries,
                "cards": self._activity_cards(campaigns, now),
            },
        )

        for campaign in campaigns:
            detail = campaign.model_dump(mode="json")
            detail["media"] = self._campaign_media(
                campaign, media_assets or {}
            )
            detail["reachability"] = {
                activity.id: self.reachability.classify(
                    activity,
                    origin_city=origin_city,
                    reachable_cities=reachable,
                ).value
                for activity in campaign.activities
            }
            self._write_json(event_dir / f"{campaign.id}.json", detail)

        # Keep old deep links working after a campaign container is consolidated.
        for alias in store.list_campaigns(include_redirects=True):
            if alias.redirected_to:
                target = store.load_campaign(alias.id)
                path = event_dir / f'{target.id}.json' if target else None
                if path and path.exists():
                    detail = json.loads(path.read_text(encoding='utf-8'))
                    detail['activity_redirects'] = alias.activity_redirects
                    self._write_json(event_dir / f'{alias.id}.json', detail)

        for month, days in self._calendar(campaigns, now).items():
            self._write_json(calendar_dir / f"{month}.json", {"days": days})

        shared_assets = {"style.css": STYLE_CSS}
        (output / "index.html").write_text(
            _versioned_html(INDEX_HTML, {**shared_assets, "app.js": APP_JS}),
            encoding="utf-8",
        )
        (output / "app.js").write_text(APP_JS, encoding="utf-8")
        (output / "event.html").write_text(
            _versioned_html(DETAIL_HTML, {**shared_assets, "event.js": DETAIL_JS}),
            encoding="utf-8",
        )
        (output / "event.js").write_text(DETAIL_JS, encoding="utf-8")
        (output / "today.html").write_text(
            _versioned_html(TODAY_HTML, {**shared_assets, "today.js": TODAY_JS}),
            encoding="utf-8",
        )
        (output / "today.js").write_text(TODAY_JS, encoding="utf-8")
        (output / "style.css").write_text(STYLE_CSS, encoding="utf-8")
        (output / ".nojekyll").write_text("", encoding="utf-8")
        self._scan_forbidden(output, forbidden_values or [])
        return campaigns

    def _publish_reminder_history(self, store, data_dir, now, campaigns,
                                  watched_slugs, service, forbidden_values):
        day = now.astimezone(self.timezone).date()
        due, _ = service.collect_due(store, day, include_delivered=True, now=now,
                                   watched_ip_slugs=watched_slugs)
        old = store.load_reminder_snapshot(day) or {'date': str(day), 'cards': []}
        cards = {(c['campaign_id'], c['activity_id']): c for c in old['cards']}
        for card in service.build_cards(due):
            key = (card['campaign_id'], card['activity_id'])
            previous = cards.get(key)
            if previous:
                card['reasons'] = previous['reasons'] + [r for r in card['reasons'] if r not in previous['reasons']]
            cards[key] = card
        snapshot = {'date': str(day), 'cards': list(cards.values())}
        serialized = json.dumps(snapshot, ensure_ascii=False)
        if any(value in serialized for value in forbidden_values if len(value) >= 8):
            raise ValueError('sensitive value detected in reminder snapshot')
        store.save_reminder_snapshot(day, snapshot)
        store.prune_reminders(day)
        directory = data_dir / 'reminders'
        directory.mkdir(exist_ok=True)
        self._remove_json_files(directory)
        visible = {c.id: {a.id for a in c.activities} for c in campaigns}
        all_campaigns = {c.id: c for c in store.list_campaigns(include_redirects=True)}
        notices = {c.id: c for c in store.list_candidates() if c.ip_slug in watched_slugs}
        watched_ids = {c.id for c in all_campaigns.values() if c.ip_slug in watched_slugs} | notices.keys()
        dates = []
        for saved in store.list_reminder_snapshots(day):
            published = []
            for card in saved['cards']:
                if card['campaign_id'] not in watched_ids:
                    continue
                cid, aid = card['campaign_id'], card['activity_id']
                notice = notices.get(cid)
                detail_id = notice.campaign_id if notice else card.get('detail_campaign_id', cid)
                alias = all_campaigns.get(detail_id)
                if alias:
                    aid = alias.activity_redirects.get(aid, aid)
                target = store.load_campaign(detail_id) if detail_id else None
                detail_id = target.id if target else detail_id
                url = None
                if detail_id in visible and (not aid or aid in visible[detail_id]):
                    url = f'event.html?id={detail_id}' + (f'#activity-{aid}' if aid else '')
                published.append({
                    'campaign_id': cid, 'activity_id': aid,
                    'campaign_title': card['campaign_title'], 'ip_name': card['ip_name'],
                    'activity': {'title': card['activity']['title']} if card['activity'] else None,
                    'reasons': card['reasons'], 'detail_url': url,
                    'extraction_status': notice.status if notice else card.get('extraction_status'),
                    'sources': [{k: source.get(k) for k in ('url', 'summary', 'account_name')}
                                for source in card['sources']],
                })
            dates.append(saved['date'])
            self._write_json(directory / f"{saved['date']}.json", {'date': saved['date'], 'cards': published})
        self._write_json(directory / 'index.json', {'dates': dates})

    def _activity_cards(self, campaigns, now):
        cards = []
        for campaign in campaigns:
            for activity in campaign.activities:
                points = sorted((p for p in schedule_actions(activity, self.timezone)
                    if p.at >= now or (p.start_date and p.start_date == now.astimezone(self.timezone).date())), key=lambda p: p.at)
                cards.append({'campaign_id': campaign.id, 'campaign_title': campaign.title,
                    'ip_name': campaign.ip_name, 'activity': activity.model_dump(mode='json'),
                    'next': {'title': points[0].title, 'at': points[0].at.isoformat(),
                             'date': str(points[0].start_date) if points[0].start_date else None} if points else None,
                    'url': f'event.html?id={campaign.id}#activity-{activity.id}'})
        return sorted(cards, key=lambda c: (c['next']['at'] if c['next'] else '9999', c['activity']['id']))

    @staticmethod
    def _campaign_media(
        campaign: Campaign,
        media_assets: dict[str, CachedMediaAsset],
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for source in campaign.sources:
            for index, media_url in enumerate(source.media_urls):
                url = str(media_url)
                key = (source.id, url)
                if key in seen:
                    continue
                seen.add(key)
                item: dict[str, object] = {
                    "source_id": source.id,
                    "account_name": source.account_name,
                    "source_url": url,
                    "source_media_hash": (
                        source.media_hashes[index]
                        if index < len(source.media_hashes)
                        else None
                    ),
                    "asset_path": None,
                    "sha256": None,
                    "content_type": None,
                    "size_bytes": None,
                }
                asset = media_assets.get(url)
                if asset is not None:
                    item.update(
                        {
                            "asset_path": asset.asset_path,
                            "sha256": asset.sha256,
                            "content_type": asset.content_type,
                            "size_bytes": asset.size_bytes,
                        }
                    )
                items.append(item)
        return items

    def _campaign_summary(
        self,
        campaign: Campaign,
        *,
        now: datetime,
        origin_city: str | None,
        reachable_cities: list[str],
    ) -> dict[str, object]:
        return {
            "id": campaign.id,
            "ip_slug": campaign.ip_slug,
            "ip_name": campaign.ip_name,
            "partner": campaign.partner,
            "title": campaign.title,
            "status": campaign.status.value,
            "updated_at": campaign.updated_at.isoformat(),
            "activities": [
                {
                    "id": activity.id,
                    "kind": activity.kind.value,
                    "title": activity.title,
                    "status": activity.status.value,
                    "start_at": activity.start_at.isoformat() if activity.start_at else None,
                    "end_at": activity.end_at.isoformat() if activity.end_at else None,
                    "reachability": self.reachability.classify(
                        activity,
                        origin_city=origin_city,
                        reachable_cities=reachable_cities,
                    ).value,
                }
                for activity in campaign.activities
                if activity.is_visible(now)
            ],
        }

    def _calendar(
        self, campaigns: list[Campaign], now: datetime
    ) -> dict[str, dict[str, list[dict[str, object]]]]:
        local_today = now.astimezone(self.timezone).date()
        months: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        seen: set[tuple[str, str, str]] = set()
        for campaign in campaigns:
            for activity in campaign.activities:
                if not activity.is_visible(now):
                    continue
                for action in schedule_actions(activity, self.timezone):
                    if action.cancelled or action.at is None:
                        continue
                    local_action = action.at.astimezone(self.timezone)
                    if local_action.date() < local_today:
                        continue
                    key = (campaign.id, activity.id, action.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    month = local_action.strftime("%Y-%m")
                    day = local_action.strftime("%Y-%m-%d")
                    months[month][day].append(
                        {
                            "campaign_id": campaign.id,
                            "activity_id": activity.id,
                            "action_id": action.id,
                            "ip_name": campaign.ip_name,
                            "title": action.title,
                            "kind": action.kind.value,
                            "at": local_action.isoformat(),
                        }
                    )
        return {
            month: {day: items for day, items in sorted(days.items())}
            for month, days in sorted(months.items())
        }

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _remove_json_files(directory: Path) -> None:
        for path in directory.glob("*.json"):
            if path.is_file():
                path.unlink()

    @staticmethod
    def _scan_forbidden(output: Path, forbidden_values: list[str]) -> None:
        high_risk_values = [
            value.encode("utf-8")
            for value in forbidden_values
            if len(value) >= 8
        ]
        for path in output.rglob("*"):
            if not path.is_file():
                continue
            content = path.read_bytes()
            for value in high_risk_values:
                if value in content:
                    raise ValueError(f"sensitive value detected in generated site: {path.name}")
