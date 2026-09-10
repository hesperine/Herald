# 生产 AI 请求与编辑入口

## 编辑位置

- `src/herald/ai.py` 的 `_request_payload`：日常提取规则、候选选择规则与输出 Schema。
- 同文件 `extract_batch`：同平台同账号的初始化整理规则与 `BatchResult` Schema。
- 同文件 `merge_campaign`：第二轮最小更新规则与 `MergeResult` Schema。
- `src/herald/data/extraction-examples.json`：**实际加载的 Few-shot**。修改 input 和 expected_output 后，下次请求生效。该文件随 Python 包分发。
- `docs/ai-extraction-review/few-shot-draft.json`：原始审核档案，不再作为运行时编辑入口。

示例以 user / assistant 消息对插在 system 与当前材料之间。日常提取仅加载示例文件中的第一组（国家图书馆）；初始化把同样例包装成 BatchResult；合并使用国家图书馆预告展示“已有相同事实，无需变更”。system 明确示例边界，仅处理最后一条 user。第二轮改期、取消等正向示例仍待扩充，现由明确规则和 Schema 约束，尚未验证真实模型准确率。

示例不属于评估集。国家图书馆、大白兔企划及其近重复材料不应拿来报告独立评估效果。真实图片和审核元数据不进入请求。

## 已接入流程

OpenAI-compatible 和智谱 Provider 使用语义流程。历史 pending（notify_immediately=false）按平台和账号归组，最多 2 个代表帖、正文累计 24000 字符一批，串行调用；超过单帖上限的材料保留 pending，不静默截断。今天的帖子走日常逐帖路径。批处理结果必须覆盖每个输入 ID（归组或忽略）；同一帖可以参与多个企划。

日常第一轮接收同 IP 已存企划的精简状态，选择零或一个候选。没有候选直接创建；有候选调用第二轮，允许 update 或 create_new。第二轮支持标量 updates、new_activities、已有活动的 new_actions / new_venues。失败保留 pending，不回退到名称相似度硬合并。

为避免旧的正文缓存错误复用状态相关结果，新流程暂不使用提取缓存。已成功处理且内容未变的帖子不会重复请求；失败的批次可以重试。原 MockAIProvider 和旧的仅 extract 协议保留原离线测试路径，新流程通过 MockTransport 进行独立测试。

所有请求保持纯文本，配置和邮件信息不进入请求。事实和提醒持久化仍是分文件写入，不是数据库事务；不要并发运行两个修改同一 state 的进程。

## 尚需注意

- 当前候选摘要包含同 IP 全部存档企划，尚未增加大规模候选检索上限。
- 引文校验只能确认原文存在，不能证明模型语义解释正确。
- 首次历史整理与当天新消息分开，初始化不会群发全部旧公告。
- 提示词修改不会自动重跑已处理帖子。重建需明确清理运行状态，不要删除 local.env 或本地 Provider 密钥文件。
- 七天通知快照属于后续工作，不在这次 AI 接入变更中。
