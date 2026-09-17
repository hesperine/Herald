"""Opt-in extraction-only time representation experiment; storage/merge unchanged."""
import copy
from datetime import date, datetime, timedelta, timezone
import json
import re

OLD={'at','start_at','end_at','start_date','end_date'}
TIME_SCHEMA={'anyOf':[{'type':'object','additionalProperties':False,
    'properties':{'date':{'type':'string','format':'date','description':'已知日期 YYYY-MM-DD。'},
                  'time':{'anyOf':[{'type':'string','pattern':r'^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$'},{'type':'null'}], 'description':'时刻 HH:MM 或 HH:MM:SS。原文明示该事项时分时必须保留，不能降为仅日期；只有未说明时刻时才填null，不补午夜或23:59。'},
                  'timezone':{'type':'string','enum':['Asia/Shanghai'],'description':'本实验中国区时间，Asia/Shanghai。'}},
    'required':['date','time','timezone']},{'type':'null'}], 'default':None,
    'description':'日期未知则整个对象null；日期已知而时刻未知则只将time填null。'}


TIME_DESCRIPTIONS={
    'ExtractedActivity':{
        'start':'该实际活动自身的开始时间。不能直接用其中某个预约、开票、满赠或折扣的开始时间代替；卡片最近DDL由程序计算，不填在这里。',
        'end':'该实际活动自身的结束时间。不能直接用其中某个预约、开票、满赠或折扣的截止时间代替；售完即止等条件不转换成日期。',
    },
    'ExtractedAction':{
        'start':'这个参与事项自身的发生、开放或生效时间。只取对应渠道、轮次、购买或优惠的时间；不能借用其他Action时间，满赠或折扣开始不等于预售开始。',
        'end':'这个参与事项自身的截止或失效时间。只取对应渠道、轮次、购买或优惠的时间；满赠或折扣截止不等于预售截止。瞬时事项或未公布截止时填null；售完/赠完即止写end_condition。',
    },
}


def entity(d):
    return isinstance(d.get('kind'),str) and isinstance(d.get('title'),str)


def convert_schema(schema):
    schema=copy.deepcopy(schema)
    for name in ('ExtractedAction','ExtractedActivity'):
        definition=schema.get('$defs',{}).get(name)
        if not definition:continue
        props=definition['properties']
        for key in OLD:props.pop(key,None)
        for side in ('start','end'):
            props[side]=copy.deepcopy(TIME_SCHEMA)
            props[side]['description']=TIME_DESCRIPTIONS[name][side]+TIME_SCHEMA['description']+'不借发帖时间补齐，不从版本号推算日期；已知结束不得早于开始。'
    claim=schema.get('$defs',{}).get('ExtractedClaim',{}).get('properties',{}).get('field_path')
    if claim:claim['description']='相对extraction路径，如activities[0].actions[0].start或end.time；数组零起始。'
    return schema


def to_nested(value):
    if isinstance(value,list):return [to_nested(x) for x in value]
    if not isinstance(value,dict):return value
    out={k:to_nested(v) for k,v in value.items()}
    if entity(value):
        for side in ('start','end'):
            instant=value.get('at' if side=='start' and 'actions' not in value else side+'_at')
            instant=instant or value.get(side+'_at')
            day=value.get(side+'_date')
            if instant:
                dt=datetime.fromisoformat(instant.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
                out[side]={'date':dt.date().isoformat(),'time':dt.strftime('%H:%M:%S'),'timezone':'Asia/Shanghai'}
            elif day:out[side]={'date':day,'time':None,'timezone':'Asia/Shanghai'}
            else:out[side]=None
        for key in OLD:out.pop(key,None)
    if isinstance(out.get('field_path'),str):
        out['field_path']=re.sub(r'\.(at|start_at|start_date)$','.start',out['field_path'])
        out['field_path']=re.sub(r'\.(end_at|end_date)$','.end',out['field_path'])
    return out


def from_nested(value):
    mappings={}
    def convert(v,path=''):
        if isinstance(v,list):return [convert(x,f'{path}[{i}]') for i,x in enumerate(v)]
        if not isinstance(v,dict):return v
        out={k:convert(x,f'{path}.{k}' if path else k) for k,x in v.items()}
        if entity(v):
            if OLD.intersection(v):raise ValueError('legacy time fields in nested-time experiment')
            action='.actions[' in path or ('actions' not in v and not path)
            for side in ('start','end'):
                item=out.pop(side,None);instant_key='at' if action and side=='start' else side+'_at'
                out[instant_key]=None;out[side+'_date']=None
                target=side+'_date'
                if item is not None:
                    if not isinstance(item,dict) or set(item)!={'date','time','timezone'}:raise ValueError('invalid time object')
                    day=date.fromisoformat(item['date']) if isinstance(item['date'],str) else None
                    if day is None or item['timezone']!='Asia/Shanghai':raise ValueError('invalid date or timezone')
                    if item['time'] is None:out[side+'_date']=day.isoformat()
                    else:
                        if not isinstance(item['time'],str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?',item['time']):raise ValueError('invalid local time')
                        out[instant_key]=datetime.fromisoformat(day.isoformat()+'T'+item['time']+'+08:00').isoformat()
                        target=instant_key
                mappings[path+'.'+side]=path+'.'+target
        return out
    result=convert(value)
    def claims(v):
        if isinstance(v,list):
            for x in v:claims(x)
        if isinstance(v,dict):
            if isinstance(v.get('field_path'),str):
                p=v['field_path']
                # Batch groups wrap extraction; field_path is relative to extraction.
                for key,target in mappings.items():
                    key=key.split('.extraction.',1)[-1];target=target.split('.extraction.',1)[-1]
                    if p==key or p.startswith(key+'.'):
                        v['field_path']=target;break
            for x in v.values():claims(x)
    # Handle each extraction independently to avoid mapping equal paths across groups.
    if isinstance(value,dict) and 'groups' in value:
        for original,group in zip(value['groups'],result['groups']):group['extraction']=from_nested(original['extraction'])
    else:claims(result)
    return result


def install(provider):
    complete=provider._complete
    response=provider._response_content
    async def wrapped(payload,result_type):
        if result_type.__name__ not in ('ExtractionResult','BatchResult'):
            return await complete(payload,result_type)
        payload=copy.deepcopy(payload)
        for message in payload['messages']:
            if message['role']=='system':
                message['content']=re.sub(r'具体时刻用.*?不重复建截止Action。',
                    'Activity和Action统一使用start/end对象，各含date、time、timezone。例：{"date":"2026-09-08","time":"12:00","timezone":"Asia/Shanghai"}。日期未知则整个对象null；只有日期则time=null。保留原文时分，不补午夜。一个事项的起止放同一Action。',message['content'])
            else:
                data=json.loads(message['content'])
                schema=data.pop('output_schema',None)
                data=to_nested(data)
                if schema is not None:data['output_schema']=convert_schema(schema)
                message['content']=json.dumps(data,ensure_ascii=False)
        def converted(metadata):
            raw=provider._strip_fence(response(metadata))
            return json.dumps(from_nested(json.loads(raw)),ensure_ascii=False)
        provider._response_content=converted
        try:return await complete(payload,result_type)
        finally:provider._response_content=response
    provider._complete=wrapped
