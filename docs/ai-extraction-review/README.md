# AI 提取样本审核稿 v3

状态：`review_required`。未接入 Prompt，未调用真实模型。

## 工作流边界

本稿中的每个 `input` 必须与生产代码实际传给 AI Provider 的 `ExtractionInput` 完全相等：

1. `WeiboTimelineClient` 通过 `https://m.weibo.cn/api/container/getIndex` 分页抓取正文和图片 URL。
2. 遇到原创或转发微博的 `isLongText` 时，爬虫先通过 `https://m.weibo.cn/statuses/extend?id={post_id}` 取得 `longTextContent` 和完整外链结构，再构建 Observation。
3. `build_extraction_input()` 同时供正式 `ObservationPipeline` 和历史样本采集器使用；当前版本固定生成纯文本输入，`ocr_text` 与 `media_urls` 均为空。
4. 历史采集器把该对象原样写入 `.herald-work/ai-sample-candidates-v4.json` 的 `extraction_input`，并把爬虫图片 URL 与去重摘要另存于同条记录的 `source_media`。
5. 本稿只复制选中对象，并由人工添加 `expected_output`；不允许用浏览器正文替换、补全或清洗 `input`。

`source_url` 使用 `https://weibo.com/{uid}/{bid}` 是爬虫生成的规范化证据链接；它与实际抓取传输接口 `m.weibo.cn` 不是同一个概念。

## 本次真实采集

- 中国时区日期范围：2026-07-18 至 2026-08-31，结束日期包含整天。
- 官号数量：4。
- 爬取材料：381 条。
- 规则候选：26 条。
- 抓取警告：0。
- 每官号默认页数上限：90（45 个自然日 × 2）。
- 原始文件：`.herald-work/ai-sample-candidates-v4.json`（Git 忽略）。

大白兔微博的生产输入现已取得完整正文，不再包含 `...全文`：正文明确给出 `@微博抽奖平台`、10 位获奖者和随机角色周边。原创长微博与转发中的长微博都有离线测试覆盖；扩展请求失败时只抛出脱敏的 `SourceAccessError`。

## 两条 Few-shot

选择原则是正文短、教学目标互补。两条来源微博均有 1 张图片，但图片不属于 AI Few-shot 输入。

| ID | Campaign | 正文字数 | 教学目标 | 同 Campaign 排除范围 |
| --- | --- | ---: | --- | --- |
| `fewshot-arknights-national-library-2026-07` | 明日方舟 × 国家图书馆 | 42 | 只有“合作筹备中”时保持保守，不虚构 Activity、Action、日期或地点 | `weibo-5324662127202110` |
| `fewshot-hsr-white-rabbit-2026-08` | 崩坏：星穹铁道 × 大白兔 | 179 | 从同一公告中拆出联名上线和微博转发抽奖，同时保留缺失时间 | `weibo-5331915584046294` |

## 图片如何进入详情页

图片暂不进入模型。实测公开新浪直链在没有微博 Referer 时可能返回 403，因此也不让详情页直接引用远端 URL：

- 页面生成阶段以公开的 `https://m.weibo.cn/` Referer 下载当前可见 Campaign 的图片；
- 校验 Content-Type 和 10 MiB 大小上限；
- 按图片内容 SHA-256 命名，写入生成式 `page/assets/media`；
- `page/data/media-index.json` 保存原 URL、站内路径、Content-Type、字节数和内容摘要；
- 详情页使用站内图片，并保留“查看原图”链接；
- 二进制不进入 `main` 或 `state`，下载失败只记录脱敏计数，不阻断文本提取和页面发布。

## 当前 Schema 限制

1. 日期只有月日而没有年份和具体时刻时，当前字段不能无猜测地构造完整带时区 datetime，因此 `at` 保持 `null`。
2. 当前 AI 输入不含图片；图片独有事实暂不写成结构化 claim。
3. 获奖人数和奖品在大白兔正文中是已知事实，但当前输出 Schema 没有对应字段；Few-shot 不把它们硬塞进其他字段。

## Evaluation Campaign 分区草案

- 明日方舟：终末地 × 优衣库 UTme!：正文 179 字、9 张切片图，保留为多图 Evaluation；同 Campaign 的 `weibo-5335046212553907`、`weibo-5335756425396596`、`weibo-5336224156095445` 作为一组，不进入 Few-shot。
- 原神 × 美团丨大众点评：多个只有日期、没有具体时刻的节点。
- 原神「千星创作赛」：负例，“与官方合作的机会”不是已官宣联动。
- 明日方舟 × 女神异闻录3 Reload：筹备预告与后续服装帖属于同一 Campaign。
- 崩坏：星穹铁道 × 绝区零：只有“2026 年冬季”，不得补具体日期。
- 明日方舟：终末地「向渊行」主题快闪开奖帖：当前 Schema 还不能表达首次官宣、更新和开奖后续帖，暂不纳入自动通过率。

审核通过前，不会把这些内容接入 Prompt 或运行 AI 提取测试。
