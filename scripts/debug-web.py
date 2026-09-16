"""Loopback-only development workbench. Never serves secrets or sends SMTP."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.herald-work'
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
JOB = {'running': False}


def safe_path(value):
    path = (WORK / value).resolve()
    if not path.is_relative_to(WORK.resolve()):
        raise ValueError('path outside debug workspace')
    return path


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def datasets():
    paths = set(WORK.rglob('dataset.json')) | set(WORK.rglob('observations.json'))
    paths |= set(WORK.glob('**/candidates/*.json'))
    result = []
    for p in sorted(paths):
        try:
            d = read_json(p)
            if isinstance(d, dict) and d.get('ip_slug') and d.get('observations'):
                result.append({'id':p.relative_to(WORK).as_posix(), 'ip':d['ip_slug'],
                    'count':len(d['observations']), 'cutoff':d.get('cutoff'),
                    'dates':sorted(o['source']['published_at'] for o in d['observations'])})
        except (ValueError, KeyError):
            continue
    return result


def read_results(root):
    calls = []
    traces = sorted((root/'traces').glob('*')) if (root/'traces').exists() else []
    for trace in traces:
        for p in sorted(trace.glob('[0-9][0-9][0-9].json')):
            d = read_json(p, {})
            try:
                d['parsed'] = json.loads(d.get('answer') or 'null')
            except ValueError:
                d['parsed'] = None
            d['trace'] = trace.name
            calls.append(d)
    latest = traces[-1] if traces else root
    return {'calls':calls, 'campaigns':read_json(latest/'after.json', []),
            'diagnostics':read_json(latest/'diagnostics.json', []),
            'report':read_json(latest/'report.json', []),
            'settings':read_json(root/'debug-options.json', {})}


def validate_options(data):
    options = {'max_tokens':int(data.get('max_tokens',8192)),
               'batch_size':int(data.get('batch_size',2)),
               'thinking':data.get('thinking','disabled'),
               'model':str(data.get('model','')).strip(),
               'prompt':str(data.get('prompt','')),
               'mode':data.get('mode','bootstrap')}
    if not 256 <= options['max_tokens'] <= 32768 or not 1 <= options['batch_size'] <= 8:
        raise ValueError('输出上限须为256—32768，批大小须为1—8')
    if options['thinking'] not in ('', 'enabled','disabled') or options['mode'] not in ('bootstrap','both'):
        raise ValueError('无效运行模式')
    if len(options['prompt']) > 20000 or len(options['model']) > 200:
        raise ValueError('参数过长')
    return options


def default_prompt():
    sys.path.insert(0, str(ROOT/'src'))
    from herald.ai import OpenAICompatibleProvider, ExtractionInput
    example = read_json(ROOT/'src/herald/data/activity-examples.json')[0]
    provider = OpenAICompatibleProvider(client=None, base_url='https://example.invalid',model='debug',api_key='unused')
    return provider._request_payload(ExtractionInput.model_validate(example['input']))['messages'][0]['content']


def launch(command, directory):
    def work():
        try:
            completed = subprocess.run(command,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            JOB.update(running=False,exit_code=completed.returncode)
        except Exception:
            JOB.update(running=False,exit_code=-1)
        finally:
            LOCK.release()
    JOB.clear();JOB.update(running=True,directory=directory)
    threading.Thread(target=work,daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, value, status=200):
        payload=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload)

    def trusted(self):
        host = self.headers.get('Host','')
        return host == f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.trusted():
            return self.respond({'error':'invalid host'},403)
        url=urlparse(self.path);q=parse_qs(url.query)
        try:
            if url.path=='/':
                content=(ROOT/'scripts/debug-web.html').read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(content)
            elif url.path=='/api/init':
                self.respond({'token':TOKEN,'datasets':datasets(),'prompt':default_prompt(),'job':JOB})
            elif url.path=='/api/status':
                self.respond(JOB.copy())
            elif url.path=='/api/result':
                relative=q['dataset'][0]
                if relative not in {d['id'] for d in datasets()}:
                    raise ValueError('请选择列表中的数据集')
                path=safe_path(relative)
                manifest=None
                for parent in (path.parent, path.parent.parent):
                    if parent.is_relative_to(WORK.resolve()) and (parent/'manifest.json').exists():
                        manifest=read_json(parent/'manifest.json');break
                self.respond({'dataset':read_json(path),'collection':manifest,**read_results(path.parent)})
            else:
                self.respond({'error':'not found'},404)
        except (ValueError,KeyError,OSError):
            self.respond({'error':'无法读取所选公开调试数据'},400)

    def do_POST(self):
        if not self.trusted() or self.headers.get('X-Debug-Token') != TOKEN:
            return self.respond({'error':'invalid local session'},403)
        acquired=False
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0 < size <= 100000:
                raise ValueError('请求过大')
            d=json.loads(self.rfile.read(size))
            if self.path=='/api/run':
                options=validate_options(d)
                if d['dataset'] not in {x['id'] for x in datasets()}:
                    raise ValueError('数据集不存在')
                source=read_json(safe_path(d['dataset']))
                ids=set(d.get('ids',[]))
                observations=[o for o in source['observations'] if o['id'] in ids]
                if not observations:
                    raise ValueError('至少选择一篇帖子')
                cutoff=datetime.fromisoformat(d['cutoff'])
                now=datetime.fromisoformat(d['now'])
                if cutoff.tzinfo is None or now.tzinfo is None:
                    raise ValueError('时间需含时区，例如+08:00')
                if not any(datetime.fromisoformat(o['source']['published_at'].replace('Z','+00:00')) < cutoff for o in observations):
                    raise ValueError('初始化分界前至少需要一篇帖子')
                options.update(now=now.isoformat())
                folder='debug-web/runs/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
                acquired=LOCK.acquire(blocking=False)
                if not acquired:
                    return self.respond({'error':'已有任务运行中'},409)
                root=safe_path(folder);root.mkdir(parents=True)
                (root/'dataset.json').write_text(json.dumps({'ip_slug':source['ip_slug'],'cutoff':cutoff.isoformat(),'observations':observations},ensure_ascii=False,indent=2),encoding='utf-8')
                (root/'debug-options.json').write_text(json.dumps(options,ensure_ascii=False,indent=2),encoding='utf-8')
                launch([sys.executable,str(ROOT/'scripts/debug-worker.py'),str(root)],folder)
            elif self.path=='/api/collect':
                since=datetime.fromisoformat(d['since']);before=datetime.fromisoformat(d['before'])
                pages=int(d.get('pages',3))
                if since.tzinfo is None or before.tzinfo is None or since>=before or not 1<=pages<=20:
                    raise ValueError('请检查采集日期范围及页数（1—20）')
                folder='debug-web/collections/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
                acquired=LOCK.acquire(blocking=False)
                if not acquired:
                    return self.respond({'error':'已有任务运行中'},409)
                launch([sys.executable,str(ROOT/'scripts/debug-worker.py'),'--collect',str(safe_path(folder)),since.isoformat(),before.isoformat(),str(pages)],folder)
            else:
                return self.respond({'error':'not found'},404)
            self.respond(JOB.copy(),202)
        except (ValueError,KeyError,OSError):
            if acquired:LOCK.release()
            self.respond({'error':'参数无效，请检查帖子选择、日期及参数范围'},400)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    print(f'Local debug workbench: http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
