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
    if(!c.detail_url)section.append(node('small','详情已下线，请参考原帖'));
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
