# AI 提取样本审核稿 v3

状态：`review_required`。未接入 Prompt，未调用真实模型。

## 工作流边界

本稿中的每个 `input` 必须与生产代码实际传给 AI Provider 的 `ExtractionInput` 完全相等：

1. `WeiboTimelineClient` 通过 `https://m.weibo.cn/api/container/getIndex` 分页抓取正文和图片 URL。
2. 遇到原创或转发微博的 `isLongText` 时，爬虫先通过 `https://m.weibo.cn/statuses/extend?id={post_id}` 取得 `longTextContent` 和完整外链结构，再构建 Observation。
3. `build_extraction_input()` 同时供正式 `ObservationPipeline` 和历史样本采集器使用。
4. 历史采集器把该对象原样写入 `.herald-work/ai-sample-candidates-v3.json` 的 `extraction_input`。
5. 本稿只复制选中对象，并由人工添加 `expected_output`；不允许用浏览器正文替换、补全或清洗 `input`。

`source_url` 使用 `https://weibo.com/{uid}/{bid}` 是爬虫生成的规范化证据链接；它与实际抓取传输接口 `m.weibo.cn` 不是同一个概念。

## 本次真实采集

- 中国时区日期范围：2026-07-18 至 2026-08-31，结束日期包含整天。
- 官号数量：4。
- 爬取材料：381 条。
- 规则候选：26 条。
- 抓取警告：0。
- 原始文件：`.herald-work/ai-sample-candidates-v3.json`（Git 忽略）。

大白兔微博的生产输入现已取得完整正文，不再包含 `...全文`：正文明确给出 `@微博抽奖平台`、10 位获奖者和随机角色周边。原创长微博与转发中的长微博都有离线测试覆盖；扩展请求失败时只抛出脱敏的 `SourceAccessError`。

## 两条 Few-shot

选择原则是正文短、图片少、教学目标互补。两条样本均只有 1 张图片。

| ID | Campaign | 正文字数 | 教学目标 | 同 Campaign 排除范围 |
| --- | --- | ---: | --- | --- |
| `fewshot-arknights-national-library-2026-07` | 明日方舟 × 国家图书馆 | 42 | 只有“合作筹备中”时保持保守，不虚构 Activity、Action、日期或地点 | `weibo-5324662127202110` |
| `fewshot-hsr-white-rabbit-2026-08` | 崩坏：星穹铁道 × 大白兔 | 179 | 从同一公告中拆出联名上线和微博转发抽奖，同时保留缺失时间 | `weibo-5331915584046294` |

## 图片如何进入模型

`input.media_urls` 是爬虫产出的公开新浪图片直链。实测这些 URL 在没有 Referer 时返回 403，因此 Provider 不要求模型服务端自行下载：

- 本地运行时以公开的 `https://m.weibo.cn/` Referer 临时读取图片；
- 校验 Content-Type 和 10 MiB 大小上限；
- 只在内存中编码为 `data:image/...;base64` 多模态内容块；
- 不把二进制写入 state、page 或仓库；
- 下载失败产生脱敏 `AIProviderError`，材料留待重试。

## 当前 Schema 限制

1. 日期只有月日而没有年份和具体时刻时，当前字段不能无猜测地构造完整带时区 datetime，因此 `at` 保持 `null`。
2. `ExtractedClaim` 只能引用正文 `quote`，还不能引用 `media_urls[n]` 的画面区域；图片独有事实暂不写成结构化 claim。
3. 获奖人数和奖品在大白兔正文中是已知事实，但当前输出 Schema 没有对应字段；Few-shot 不把它们硬塞进其他字段。

## Evaluation Campaign 分区草案

- 明日方舟：终末地 × 优衣库 UTme!：正文 179 字、9 张切片图，保留为多图 Evaluation；同 Campaign 的 `weibo-5335046212553907`、`weibo-5335756425396596`、`weibo-5336224156095445` 作为一组，不进入 Few-shot。
- 原神 × 美团丨大众点评：多个只有日期、没有具体时刻的节点。
- 原神「千星创作赛」：负例，“与官方合作的机会”不是已官宣联动。
- 明日方舟 × 女神异闻录3 Reload：筹备预告与后续服装帖属于同一 Campaign。
- 崩坏：星穹铁道 × 绝区零：只有“2026 年冬季”，不得补具体日期。
- 明日方舟：终末地「向渊行」主题快闪开奖帖：当前 Schema 还不能表达首次官宣、更新和开奖后续帖，暂不纳入自动通过率。

审核通过前，不会把这些内容接入 Prompt 或运行 AI 提取测试。
