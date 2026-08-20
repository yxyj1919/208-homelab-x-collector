# V1.0 Release Note

V1.0 是 JSON 导入版 MVP，适合作为本地收藏归档工具的第一版基线。

## Scope

已支持：

- 从 X/Twitter 导出的 JSON 文件导入收藏。
- 用 SQLite 保存原始收藏、分类、标签、导出路径和人工分类状态。
- 使用本地规则分类，默认不依赖云端 API。
- 可选使用 Ollama 进行本地或远端模型分类。
- 手工修正单条收藏分类，并避免后续自动分类默认覆盖人工结果。
- 为每条收藏生成独立 HTML 文件，并按分类目录保存。
- 为每条收藏生成带 Obsidian front matter 的独立 Markdown 文件。
- 生成全局索引页面。

未包含：

- Chrome extension GraphQL 导出。
- 直接登录 X/Twitter 账号同步收藏。
- X API / OAuth 集成。
- 定时任务安装脚本。
- 多用户或远端服务部署。

## Quick Start

```bash
python3 -m xbookmarks.cli init
python3 -m xbookmarks.cli run --input samples/bookmarks.json
python3 -m xbookmarks.cli stats
```

首次使用时可以先选择兴趣领域，生成对应的预设分类：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli category presets
PYTHONPATH=src python3 -m xbookmarks.cli init \
  --write-categories \
  --interests virtualization,kubernetes,homelab,ai,security
```

如果没有传 `--interests`，`init --write-categories` 会写入默认技术领域预设：
`virtualization`、`kubernetes`、`homelab`、`ai`、`security`。
已有 `config/categories.yaml` 时默认只合并缺失的分类和关键词；需要重写时再加
`--force-categories`。

默认输出：

- SQLite 数据库：`data/bookmarks.sqlite`
- 原始 JSON 备份：`data/json-backups/raw/`
- HTML 归档：`archive/`
- 分类配置：`config/categories.yaml`

`data/`、`archive/`、`archive-*/` 和 `real-bookmarks.json` 是本地运行数据，默认不进入 Git。
备份时优先保留 SQLite 数据库、原始 JSON 备份、分类配置和需要长期保存的 HTML 归档目录。

## Commands

查看最近一次运行状态和运行日志：

```bash
python3 -m xbookmarks.cli sync-status
python3 -m xbookmarks.cli run-log --limit 10
```

单步执行：

```bash
python3 -m xbookmarks.cli import samples/bookmarks.json
python3 -m xbookmarks.cli classify
python3 -m xbookmarks.cli export-html
python3 -m xbookmarks.cli export-markdown --archive-dir obsidian-archive
```

导入时会按 `tweet_id` 去重，并用内容 hash 检测同一收藏是否发生变化。输出中的
`inserted`、`updated`、`unchanged` 和 `duplicates` 可用于确认增量导入效果。

## Markdown / Obsidian Export

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite export-markdown \
  --archive-dir obsidian-archive
```

输出结构按分类分目录，单条收藏写成
`obsidian-archive/<Category>/<date>_<tweet_id>.md`，索引写入
`obsidian-archive/_index/index.md`。

每个 Markdown 文件包含固定 front matter：

- `tweet_id`
- `url`
- `author`
- `created_at`
- `category`
- `tags`
- `source`
- `provider`
- `confidence`
- `read_state`

正文包含原文、原链接、备注、分类原因，以及可从 `raw_json` 派生出的 media、card、
quoted tweet 链接。

## Search And Review

全文搜索使用 SQLite FTS5，而不是普通 `LIKE`。搜索字段包括 `text`、`author`、
`url`、`category`、`tags` 和 `notes`：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli search "VMware lifecycle"
PYTHONPATH=src python3 -m xbookmarks.cli search Kubernetes --limit 10
```

查看 review queue 统计：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite review-summary
```

更新单条收藏的备注、标签和状态：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite update 1001 \
  --notes "read later" \
  --tags "vcf,homelab" \
  --read-state read \
  --important \
  --no-archived
```
