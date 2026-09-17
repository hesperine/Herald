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
fetch('data/today.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
 document.getElementById('day').textContent=data.date;
 for(const [targetId,isNews] of [['news',true],['upcoming',false]]) {
  const target=document.getElementById(targetId);
  for(const c of data.cards) {
   const reasons=c.reasons.filter(r=>['announcement','update'].includes(r.kind)===isNews);
   if(!reasons.length)continue;
   const section=node('section'),h=node('h3'),link=node('a',c.activity?c.activity.title:c.campaign_title);
   link.href='event.html?id='+encodeURIComponent(c.campaign_id)+(c.activity_id?'#activity-'+encodeURIComponent(c.activity_id):'');h.append(link);
   section.append(h,node('p',c.campaign_title));
   for(const r of reasons)section.append(node('p',r.summary+(r.expected_at?' · '+displayTime(r.date_only?null:r.expected_at,r.date_only):'')));
   sourceLinks(section,c.sources);target.append(section);
  } if(!target.children.length)target.textContent=isNews?'今天暂无新消息':'今天暂无开始或结束提醒';
 }
}).catch(()=>{document.getElementById('news').textContent='提醒加载失败，请刷新重试';});
