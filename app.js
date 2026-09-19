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
