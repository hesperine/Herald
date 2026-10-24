"""Two-stage public-source routing and stable-entity reconciliation contracts."""
import json
import re
from datetime import datetime, timedelta, timezone
from pydantic import Field
from .ai import AIProviderError, ExtractionResult, MergeResult, ExtractedClaim
from .models import StrictModel


class CampaignRoute(StrictModel):
    phrase: str = Field(min_length=1, max_length=200)
    campaign_id: str | None = None
    partner: str | None = None
    observation_ids: list[str] = Field(min_length=1)


class UnconfirmedSource(StrictModel):
    observation_id: str
    reason: str = Field(min_length=1, max_length=300)


class RoutingResult(StrictModel):
    groups: list[CampaignRoute] = Field(default_factory=list)
    unconfirmed: list[UnconfirmedSource] = Field(default_factory=list)


class ActivityExclusion(StrictModel):
    activity_id: str
    observation_id: str
    quote: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=300)


class CampaignResolution(StrictModel):
    extraction: ExtractionResult
    merge: MergeResult | None = None
    exclusions: list[ActivityExclusion] = Field(default_factory=list)


def complete_explicit_deadlines(extraction, observations):
    """Normalize a fully explicit, cited UTC deadline accidentally left in rules.

    No year, time, or timezone is inferred. Ambiguous prose remains unchanged.
    """
    from .evidence import quote_matches
    pattern = re.compile(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{2})\s*[（(]UTC([+-]\d{1,2})[）)]\s*前(?:登录|领取|报名|预约|购买)')
    for i, activity in enumerate(extraction.activities):
        for j, action in enumerate(activity.actions):
            if action.end_at or action.end_date or not action.rules:
                continue
            prefix = f'activities[{i}].actions[{j}]'
            claim = next((c for c in extraction.claims if c.field_path == prefix + '.rules'
                          and any(quote_matches(c.quote, o.text) for o in observations)), None)
            if claim is None:
                continue
            matches = list(pattern.finditer(claim.quote))
            if len(matches) != 1 or matches[0].group() not in action.rules:
                continue
            year, month, day, hour, minute, offset = map(int, matches[0].groups())
            action.end_at = datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=offset)))
            extraction.claims.append(ExtractedClaim(field_path=prefix + '.end_at', quote=claim.quote, confidence=claim.confidence))


def validate_dates(extraction):
    paths = {c.field_path for c in extraction.claims}
    for i, activity in enumerate(extraction.activities):
        entities = [(f'activities[{i}]', activity)] + [
            (f'activities[{i}].actions[{j}]', action) for j, action in enumerate(activity.actions)]
        for prefix, entity in entities:
            for field in ('start_at', 'at', 'end_at', 'start_date', 'end_date'):
                if getattr(entity, field, None) is not None and prefix + '.' + field not in paths:
                    raise ValueError('date requires field evidence')


def payload(provider, packets, instruction, data, schema):
    result = provider._request_payload(packets[0])
    system = result['messages'][0]['content'] + '\n' + instruction
    result['messages'] = [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps({
        **data, 'sources': [provider._public_packet(p) for p in packets],
        'output_schema': schema.model_json_schema()}, ensure_ascii=False)}]
    if len(json.dumps(result, ensure_ascii=False)) > 180000:
        raise AIProviderError('context budget exceeded')
    return result


async def route_campaigns(provider, packets, catalog):
    request = payload(provider, packets,
        '第一轮只做企划归属，不提取活动细节。catalog为已有企划的语义短语目录，ID只是引用。'
        '每个来源必须归入至少一组或unconfirmed，二者互斥。混合帖允许归多个组。'
        '相同IP与明确合作方共用企划；届次在第二轮区分Activity。已有企划必须复用campaign_id，禁止改名新建。'
        '无合作方的快闪/演出按明确主题匹配，不因标题相近强行合并。新组填campaign_id=null。'
        '每企划只一个组，phrase为IP、合作方与主题的一句短语，不是事实ID。'
        '普通游戏更新、复刻或剿灭作战不能因出现在综合公告里归入联动；第三方抽奖奖品不证明品牌联动。'
        '无法确认的来源写unconfirmed及理由，仍保留原帖候选，不伪造活动。',
        {'catalog': catalog}, RoutingResult)
    result = await provider._complete(request, RoutingResult)
    known = {c['id'] for c in catalog}
    expected = {p.observation_id for p in packets}
    grouped = {i for g in result.groups for i in g.observation_ids}
    unconfirmed = {u.observation_id for u in result.unconfirmed}
    identities = [g.campaign_id or (g.partner, g.phrase) for g in result.groups]
    if grouped | unconfirmed != expected or grouped & unconfirmed or len(identities) != len(set(identities)):
        raise AIProviderError('invalid route coverage')
    if any(g.campaign_id and g.campaign_id not in known for g in result.groups):
        raise AIProviderError('invalid campaign candidate')
    return result


async def resolve_campaign(provider, route, packets, existing):
    request = payload(provider, packets,
        '第二轮：只处理route指定企划，充分比较existing_campaign和本批来源。'
        '返回extraction以及merge。新企划merge=null；已有企划必须merge.decision=update。'
        'extraction仅含本企划事实和逐项claims；同帖其他企划、普通更新不得混入。'
        '已有事实语义未变时复用原值，别重写标题或规则；重复宣传merge所有数组为空。'
        '已有Activity/Action通过稳定ID更新，field_path如activities.<id>.actions.<id>.at。'
        'updates每项必须observation_id及原文quote，缺失不是删除，禁止修改ID，禁止null清空。'
        '已有活动新增参与事项用new_actions；新增地点用new_venues；独立新活动用new_activities，'
        'new_activities的title/kind必须与extraction对应活动一致，其字段由程序复用extraction。'
        '同一活动的日期和规则只存最具体的层级，不要在Activity和Action重复；新届次可新Activity。'
        '日期归各自事项，日期不明留空，不补午夜；status仅可原文明示announced/cancelled/ended。'
        '新行动/地点要提供目标activity_id、observation_id、连续quote。纯来源增加不算事实更新。'
        '审核existing_campaign历史误归属：只有输入原文明确表明某个已有Activity是无关联普通更新时，'
        '才在exclusions列其activity_id、observation_id、quote、reason，程序保留其历史但停止提醒。'
        '未提及、已过期、关系不明都不能排除；不要将排除伪装为取消。没有证据则exclusions为空。',
        {'route': route.model_dump(), 'existing_campaign': provider._public_campaign(existing) if existing else None},
        CampaignResolution)
    result = await provider._complete(request, CampaignResolution)
    validate_dates(result.extraction)
    if existing and (result.merge is None or result.merge.decision != 'update'):
        raise AIProviderError('existing campaign requires reconciliation')
    if not existing and result.merge is not None:
        raise AIProviderError('new campaign cannot update existing entities')
    result.extraction.candidate_campaign_id = existing.id if existing else None
    return result
