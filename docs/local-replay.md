# 真实材料录制与 AI 回放

## 米游社请求退避

米游社共用客户端在每次列表/详情请求前等待 2 秒。网络错误、HTTP 错误、非 JSON 响应或非零 retcode（包括 1034）触发最多 3 次额外重试，分别等待 4、8、16 秒；连同首次共最多 4 次请求。成功即停止重试，下一个请求恢复 2 秒基础间隔；客户端内请求串行执行。日常和历史采集均生效，采集脚本不再叠加米游社的 1.2 秒等待。其他平台不使用此退避策略。

耗尽重试后抛出脱敏来源错误，当前账号采集仍标记中断，不能视为完整抓取或自动跨过失败帖子。此策略不保证能解除 1034，也没有实现验证码绕过或跨运行的断点续抓。

## 5—6 月系统一致采集

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m herald.community_samples --directory .herald-work/community-may-june-2026 --since 2026-05-01T00:00:00+08:00 --before 2026-07-01T00:00:00+08:00 --pages 122
```

日期上界是排他的，包含北京时间 6 月 30 日整天。适配器遍历新帖子列表但不请求窗口外详情；不按标题或预览筛选。`candidate_stage.prepare_candidates` 是生产 fetch、语义流程、历史采集共同调用的“去重 → CandidateFilter”入口。

最终 `candidates/<ip-slug>.json` 保存同 IP 跨账号去重分组及全部通过分组的标准化帖子（保留各来源证据）。不剔除可能被 AI 排除的边界内容，也不自动删除 Few-shot 重合企划；重合标记供后续评估划分使用。每账号文件用于诊断，以最终跨账号文件为回放导出边界。

`manifest.json` 中 `complete` 表示到达日期下界或接口明确末页；页数上限、缺失分页信息、网络/来源错误均标记未完成。已抓取详情随时落盘；中断结果不是完整测试集。当前不自动循环重试限流，也不把历史分页令牌当成生产增量游标。

## 当前规则与测试边界（优先于下方旧批次说明）

候选范围已扩展至游戏官方漫展、嘉年华、演唱会、音乐会、巡展等线下衍生活动及配套售卖，不要求存在跨品牌联动。关键词放行不等于 AI 确认；纯游戏内嘉年华或纯线上直播不因此自动收录。

`community_samples` 已取消采样专用预筛，使用真实详情 → Adapter → CandidateFilter；新增的 `candidates.json` 原样保存全部 PASS 帖子，作为 AI 流程回放边界，不需要人工放行。Few-shot 同企划排除属于评估隔离，不是业务筛选。原始响应仍可供抓取诊断，不是 AI 回放的必要输入。

下方 2026-09-08 旧批次是在预筛存在时产生的历史记录，不能视为新流程全量输出。本次只修改规则，没有重新抓取，也没有重新运行 AI 或更新旧清单。

## 四 IP 历史候选采样（2026-09-08 新增）

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m herald.community_samples --directory .herald-work/new-community-history --since 2026-06-01T00:00:00+08:00 --pages 12
```

该入口不读取私密配置、不调用 AI、不发邮件。使用生产米游社/森空岛适配器的分页和详情解析，按内置注册表覆盖四个 IP、五个账号；明日方舟目前配置的是朝陇山衍生品账号，不是全部官方账号。

每个账号目录包含 `raw-responses.json`（只录制公开列表/详情响应，不录设备或鉴权接口）、`observations.json`（适配器原样输出）和 `review.json`（命中词、是否入选、Few-shot 排除标记、被跳过的预览）。成功详情立即落盘，后续请求失败不会丢失已成功材料。

采样先从列表预览匹配联动词或乘号，再拉取完整详情，减少请求量；列表可能截断，因此这不是全量历史导出。默认生产抓取不启用这个预览过滤器。生产 AI 之前的 CandidateFilter 现已重新接入语义流程：普通维护/版本消息无联动词不调用 AI，但混合公告含联动词仍入选。`合作`等宽泛词会留下边界候选，并不等于已确认联动。

新批次位于 `.herald-work/community-history-20260908`。先审核材料，不自动生成回放划分或启动真实 AI。

所有材料和回放产物放在 Git 忽略的 `.herald-work` 中。不覆盖 `local-state`；不发送邮件，不下载图片。真实 AI 会使用用户账号额度。

## 已准备材料（2026-09-08）

- `.herald-work/replay-capture-20260908-network/raw-responses.json`：41 次米游社接口响应，未保存请求头、响应头和 Cookie。中途遇到 retcode=1034，不代表完整抓取成功。
- 同目录 `observations.json`：用原适配器和离线 MockTransport 从成功详情中解析的 38 条帖子，失败详情不伪造。
- `.herald-work/replay-genshin-20260908/dataset.json`：初始化为版本更新说明与美团联名公告；增量为后续普通版本活动公告。用于批量筛选，不能用于证明同企划更新正确。
- `.herald-work/replay-endfield-20260908/dataset.json`：此前本地真实抓取的 8 月 23 日优衣库活动介绍、8 月 25 日宣传 PV。用于初始化后的接续宣传；该组无原始响应底稿。只有两条，不是完整评估集。

两组都排除了 Few-shot 中的大白兔、国家图书馆企划。改期、取消、新节点仍缺真实样本。

## 运行顺序

无需激活环境，PowerShell 在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/run-local.py --replay bootstrap --replay-directory .herald-work/replay-endfield-20260908
.\.venv\Scripts\python.exe scripts/run-local.py --replay incremental --replay-directory .herald-work/replay-endfield-20260908
.\.venv\Scripts\python.exe scripts/run-local.py --replay incremental --replay-directory .herald-work/replay-endfield-20260908
```

Linux/macOS 使用 `.venv/bin/python`，参数不变。测试原神组时更换目录名。

第一次初始化成功后才能增量回放；初始化失败可用原命令重试。第三条命令验证相同材料不重复调用 AI、不重复建立提醒。程序直接消费固定帖子，不创建来源 HTTP 客户端。模型请求仍联网。初始化使用 cutoff 作为模拟时刻，增量使用原帖发布时间。

查看该目录的 `traces/<运行时间>/`：模型消息与回答、HTTP 状态、before.json、after.json 和 report.json。`replay-state` 保存企划、待重试材料和日期队列；`bootstrap-complete.json` 标记成功初始化。数据集指纹防止初始化后悄悄换材料。全新重跑请复制 dataset.json 到新的专用目录，不复制旧状态，也不用删除当前 local-state。

当前真实尝试返回 HTTP 404，初始化仍 pending，未进入增量阶段。需检查所选 AI_PROFILE 对应的 AI_BASE_URL 和 AI_MODEL。不能把这次失败当作模型提取质量结论。

## 重新录制原始数据（无需 AI 配置）

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m herald.replay record --directory .herald-work/new-capture --ip genshin-impact --since 2026-08-01T00:00:00+08:00 --pages 1
```

目前录制入口仅支持内置米游社账号。目录必须是新目录。记录的是公开响应正文，包含接口原始返回字段，保持本地忽略，不直接提交到仓库。

抓取中断后可只解析已经收到的成功详情，不再联网：

```powershell
.\.venv\Scripts\python.exe -m herald.replay normalize-recording --directory .herald-work/new-capture --ip genshin-impact
```

选择少量帖子写成 dataset.json，格式为 ip_slug、带时区的 cutoff、observations 数组（完整 SourceObservation）。cutoff 前后必须各有材料；不可修改原文或发布日期来伪造场景。
