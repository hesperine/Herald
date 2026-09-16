"""Isolated debug replay; only run-local.py reads private configuration."""
import asyncio
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    if sys.argv[1]=='--collect':
        from herald.community_samples import collect
        from datetime import datetime
        asyncio.run(collect(Path(sys.argv[2]),datetime.fromisoformat(sys.argv[3]),int(sys.argv[5]),datetime.fromisoformat(sys.argv[4])))
        return 0
    if sys.argv[1]!='--child':
        root=Path(sys.argv[1]).resolve()
        original=subprocess.run
        def child(command, **kwargs):
            return original([sys.executable,str(Path(__file__).resolve()),'--child',str(root)],**kwargs)
        subprocess.run=child
        sys.argv=[str(ROOT/'scripts/run-local.py'),'--ai-smoke-test']
        runpy.run_path(sys.argv[0],run_name='__main__')
        return 0
    root=Path(sys.argv[2]);options=json.loads((root/'debug-options.json').read_text(encoding='utf-8'))
    from herald import replay, semantic_pipeline
    semantic_pipeline.BATCH_POST_LIMIT=options['batch_size']
    original=replay._make_provider
    def provider(settings,client):
        instance=original(settings,client)
        if instance is None:raise ValueError('AI configuration required')
        if options['model']:instance.model_name=options['model']
        instance.thinking=options['thinking'] or None
        instance.max_tokens=options['max_tokens']
        request=instance._request_payload
        def payload(packet):
            result=request(packet)
            if options['prompt'].strip():result['messages'][0]['content']=options['prompt']
            return result
        instance._request_payload=payload
        if options.get('time_schema') == 'nested':
            import importlib.util
            spec=importlib.util.spec_from_file_location('time_schema_experiment',ROOT/'scripts/time-schema-experiment.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.install(instance)
        return instance
    replay._make_provider=provider
    async def run():
        result=await replay.run(argparse.Namespace(directory=root,mode='bootstrap',now=options['now'],cache_media=False))
        if not result['pending'] and options['mode']=='both':
            result=await replay.run(argparse.Namespace(directory=root,mode='incremental',now=options['now'],cache_media=False))
        return 1 if result['pending'] else 0
    try:
        return asyncio.run(run())
    except Exception:
        return 1


if __name__=='__main__':
    raise SystemExit(main())
