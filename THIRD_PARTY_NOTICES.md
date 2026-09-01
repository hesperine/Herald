# Third-party notices and acknowledgements

核验日期：2026-09-02。

## Horizon

[Thysrael/Horizon](https://github.com/Thysrael/Horizon) 是 MIT 许可的 Python AI 新闻雷达，Copyright (c) 2026 Thysrael。其 GitHub Actions、每日聚合和 GitHub Pages 产品形态为本项目提供了架构启发。Horizon 当前从 `main/docs` 触发并发布至 `gh-pages`；HERALD 独立实现 `main/state/page` 隔离，以避免用户运行状态影响 fork 与上游同步。

本仓库没有直接复制 Horizon 源码。若将来复制或修改 MIT 代码，必须在分发中保留对应版权和许可文本。

## Weibo projects

- [nghuyong/WeiboSpider](https://github.com/nghuyong/WeiboSpider)：MIT License，Copyright (c) 2019 HuYong。
- [dataabc/weibo-crawler](https://github.com/dataabc/weibo-crawler)：核验时未发现 LICENSE 文件。
- [dataabc/weiboSpider](https://github.com/dataabc/weiboSpider)：核验时未发现 LICENSE 文件。

这些项目用于理解微博采集领域的常见能力与术语。HERALD 的微博适配器为独立实现，没有复制上述项目的源码、配置、注释或测试，也没有把无明确许可证的项目作为依赖或重新分发。“致谢/侵删”不构成授权；若将来需要实质使用其代码，必须先取得适用许可。

## Danbooru Skland extractor

HERALD 的森空岛临时设备身份与请求签名流程基于 [Danbooru](https://github.com/danbooru/danbooru) 的 `Source::Extractor::Skland`（BSD 2-Clause）移植并重新组织为 Python；作者时间线分页、增量游标、HERALD 数据模型和测试为本项目实现。其许可证声明如下：

```text
Copyright (c) 2013~2026, Danbooru Project
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
```

米游社作者时间线接口还通过 `kabegame/crawler-plugins` 与 RSSHub 的公开实现进行过行为核对；前者核验时未发现许可证，后者为 AGPL-3.0。HERALD 没有复制这些项目的源码。`PaiGramTeam/FixMiYouShe`（AGPL-3.0+）仅用于交叉验证森空岛响应行为，没有复制或重新分发其代码。

## Product references

[CSBaoyan DDL](https://ddl.csbaoyan.top/) 的时间信息浏览方式曾作为产品讨论参考。当前前端刻意保持最低可用实现，没有复制其代码、样式或视觉资产。
