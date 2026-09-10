# Agent 操作指南

本文件供继续维护 HERALD 的 Codex 或其他代码 Agent 使用。先读完整文件，再修改项目。

## 不可破坏的产品约束

1. 项目必须保持无常驻服务器、无数据库、每日一次 GitHub Actions 的平民化部署方式。
2. 使用者自行承担 AI 与 SMTP 账号；项目维护者不得内置、转发或代付 API Key。
3. `main` 只保存可同步的上游代码。用户配置只来自 Repository Variables/Secrets；运行状态只写 `state`；静态产物只写 `page`。
4. 默认只处理中国区、内置 IP 的官方来源。未知 IP 不进行自动官号发现；合作品牌账号暂不自动加入长期监控。
5. 信息必须先来源去重，再按 `Campaign → Activity → Action` 合并。证据不足时保守进入 `pending-review`，不得为了减少条目而硬合并。
6. 当天任务同时包含新公告/更新和历史日期队列中的提醒。
7. 过期活动保留在 `state`，但必须从 `page` 的首页、日历和详情产物中清除。
8. 前端现阶段只保证可用。未经用户明确进入视觉设计阶段，不增加框架、复杂 CSS、配色系统或大规模布局。

## 隐私与安全边界

- `ORIGIN_CITY`、`REACHABLE_CITIES`、邮箱、Cookie、SMTP 凭据和 AI Key 不得进入日志、异常、测试 fixture、`state` 或 `page`。
- 只有公开公告文字、公开图片 URL、公开链接、IP 提示和 JSON schema 可以发送给 AI。
- 默认 `PUBLISH_REACHABILITY=false`。公开页面不得根据 Secret 泄露“本地/可达”推断；只有使用者显式选择后才可发布分类，仍不得发布配置字段本身。
- 本地真实调试的 Secret 只允许进入被 Git 忽略的 `local.env`。HERALD 包代码和 CLI 不得读取该文件；只有跨平台的 `scripts/run-local.py` 可以把它注入独立子进程环境。

## 模块地图

- `config.py`：Variables/Secrets 解析和隐私指纹。
- `registry.py`、`data/ip_sources.json`：内置 IP 与官方来源。
- `sources/`：来源适配器。米游社、森空岛和可选微博每天读取最新页，以 `latest_post_id` 做边界；不要把 `next_offset`、`pageToken`、`since_id` 等分页令牌用作增量游标。
- `dedupe.py`、`merge.py`：来源材料去重与 Campaign 保守合并。
- `candidate_stage.py`：生产运行与历史测试采集共用的来源去重、CandidateFilter 阶段；不得在采集入口另加内容预筛。
- `ai.py`、`assembly.py`：公开材料结构化和稳定 ID 组装。
- `storage.py`：原子 JSON、日期目录和索引。
- `scheduler.py`、`notifications.py`：即时变更、未来提醒和防重回执。
- `reachability.py`、`site.py`：隐私保护的可达性推断与最低可用静态页。
- `pipeline.py`：单个 IP 的观察材料处理。
- `runner.py`：一次完整每日运行。
- `cli.py`：环境变量到运行器的薄适配层。
- `local.env.example`、`scripts/run-local.py`：本地调试配置样例与跨平台环境注入器；远端工作流不读取它们。
- `.github/workflows/daily.yml`：三分支运行与提交。

## 修改顺序

严格采用“一模块、一组测试”的节奏：

1. 先写或更新目标模块的离线测试。
2. 只实现该模块所需最小变化。
3. 运行目标测试。
4. 再`compileall`。
5. 检查生成页面和 state 中没有已知 Secret。

标准验证命令：

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

测试不得调用真实微博、AI 或 SMTP。使用 `httpx.MockTransport`、`MockAIProvider` 和内存邮件发送器。

## AI 适配

- 第一版通用接口是 OpenAI-compatible `/chat/completions`。
- 无 Key 时必须保存 `pending-extraction`；以后有 Key 时必须能从索引加载旧观察记录重试。
- 当前可运行版本固定使用纯文本 AI 输入；`AI_VISION` 是预留配置，不得把图片 URL、OCR 或二进制附加到生产 AI 请求。图片只进入生成式 `page` 详情产物。
- 模型失败必须产生脱敏错误并保留重试状态，不能阻断历史提醒和静态页发布。

## 分支与提交

- 不把用户配置示例的真实值提交到任何分支。
- 工作流不得对 `main` 执行生成式 commit。
- `state/page` 首次运行可自动创建；后续只提交各自目录变化。
- 不从 `state/page` 反向合并到 `main`。
- 上游同步发生冲突时，优先保持 `main` 与上游代码一致，因为用户状态不在 `main`。