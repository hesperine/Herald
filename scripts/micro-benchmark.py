"""Frozen public inputs and review-first micro-benchmark. No network calls."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BENCH=ROOT/'benchmarks/extraction-v1'


def load_suite():
    return json.loads((BENCH/'suite.json').read_text(encoding='utf-8'))


def dataset(case):
    observations=[]
    for item in case['inputs']:
        path=BENCH/item['file']
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:
            raise ValueError('Benchmark input changed: '+item['file'])
        observations.append(json.loads(raw))
    return {'ip_slug':case['ip_slug'],'cutoff':case['cutoff'],'observations':observations}


def prepare(output):
    suite=load_suite()
    inputs=[(case,dataset(case)) for case in suite['cases']]
    output.mkdir(parents=True,exist_ok=False)
    try:
        revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    except subprocess.CalledProcessError:
        revision=None
    for case,data in inputs:
        folder=output/case['id'];folder.mkdir()
        (folder/'dataset.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (folder/'review.json').write_text(json.dumps({'case_id':case['id'],'expectations_version':suite['version'],
            'review_status':suite['review_status'],'checks':[dict(c,result='not_reviewed',notes='') for c in case['checks']]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest={'suite':suite,'source_revision':revision,'code_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['src/herald/ai.py','src/herald/data/activity-examples.json','src/herald/semantic_pipeline.py']},
              'note':'Prepared offline. No model run or quality score yet. Preserve actual request traces for model/config/prompt provenance.'}
    (output/'benchmark-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def render():
    suite=load_suite()
    lines=['# 提取 micro-benchmark：预期事实审阅稿','',
           '**草稿，等待用户审核。不是模型成绩，也不是已确认标准答案。**','',
           '仅根据冻结的帖子正文判断；图片链接供人工核对，本版不将图片内容纳入期望事实。',
           '同一输入可重复运行不同模型和 Prompt；评分不比较标题措辞、数组顺序或生成 ID。',
           '检查方式标为 deterministic_candidate 的项目仍需先定位正确的实体，定位不确定时人工审阅，不能直接判错。', '']
    for case in suite['cases']:
        lines += ['## '+case['id']+' · '+case['title'],'',case['purpose'],'',
                  f"执行：{case['mode']}；分界：{case['cutoff']}；页面模拟时间：{case['now']}",'']
        for o in dataset(case)['observations']:
            s=o['source'];lines += ['### 原帖 '+o['id'],'',f"发布时间：{s['published_at']} · [原帖]({s['url']})",'',o['text'],'']
            for i,url in enumerate(o.get('media_urls') or s.get('media_urls',[])):
                lines.append(f'- [配图 {i+1}]({url})')
            lines.append('')
        lines += ['### 待审核的预期事实','']
        for check in case['checks']:
            lines += [f"- **{check['id']}** [{check['method']}] {check['expectation']}"]
            for ev in check.get('evidence',[]):
                lines.append('  - 依据 '+ev['source_id']+'：'+ev['quote'].replace('\n',' / '))
        lines += ['','### 请重点确认','',case['review_question'],'']
    (BENCH/'REVIEW.md').write_text('\n'.join(lines).rstrip()+'\n',encoding='utf-8')
    # Standalone readable document without a local server or remote dependencies.
    body=[]
    for line in lines:
        if line.startswith('### '):body.append('<h3>'+html.escape(line[4:])+'</h3>')
        elif line.startswith('## '):body.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line.startswith('# '):body.append('<h1>'+html.escape(line[2:])+'</h1>')
        else:
            import re
            safe=html.escape(line)
            safe=re.sub(r'\[([^\]]+)\]\((https://[^ )]+)\)',r'<a href="\2" rel="noreferrer">\1</a>',safe)
            body.append('<p style="white-space:pre-wrap">'+safe+'</p>')
    (BENCH/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>Micro-benchmark 审阅稿</title><main style="max-width:1000px;margin:24px auto;font:16px/1.6 system-ui">'+''.join(body)+'</main>',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',type=Path);parser.add_argument('--render',action='store_true')
    args=parser.parse_args()
    if args.prepare:prepare(args.prepare)
    if args.render:render()
    if not args.prepare and not args.render:
        for case in load_suite()['cases']:print(case['id'],case['title'])
