# homelab-x-collector

Local-first X/Twitter bookmark archiver MVP.

第一版目标：

- 从导出的 JSON 文件导入收藏数据。
- 使用 SQLite 保存原始数据、分类、标签和导出路径。
- 用本地规则分类，不依赖云端 API。
- 导出每条收藏为独立 HTML，并按分类目录保存。
- 生成全局索引 `archive/_index/index.html`。

## Quick Start

```bash
python3 -m xbookmarks.cli init
python3 -m xbookmarks.cli run --input samples/bookmarks.json
```

默认输出：

- SQLite 数据库：`data/bookmarks.sqlite`
- HTML 归档：`archive/`

查看统计：

```bash
python3 -m xbookmarks.cli stats
```

单步执行：

```bash
python3 -m xbookmarks.cli import samples/bookmarks.json
python3 -m xbookmarks.cli classify
python3 -m xbookmarks.cli export-html
```

## JSON Input

MVP 支持常见字段名：

- `id` / `tweet_id` / `rest_id`
- `url`
- `text` / `full_text` / `content`
- `author` / `screen_name` / `username`
- `created_at`

JSON 顶层可以是数组，也可以是包含 `bookmarks`、`tweets` 或 `data` 的对象。

## Category Rules

默认分类规则在 `config/categories.yaml`。

当前内置分类：

- VMware
- vCenter
- VCF
- Kubernetes
- VKS
- Networking
- DevOps
- Programming
- Linux
- Data
- Tools
- Productivity
- Learning
- Career
- Language
- Finance
- Homelab
- AI
- Security
- Life
- General

可以直接编辑 `config/categories.yaml` 调整分类和关键词。

配置格式：

```yaml
Tools:
  description: Software tools, websites, browser extensions, CLI utilities, online services.
  keywords:
    - tool
    - browser extension
    - singlefile
```

`description` 会进入 Ollama prompt，用来约束分类边界；`keywords` 用于规则分类。

也可以用 CLI 添加或更新分类：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli category add Tools \
  --description "Software tools, websites, browser extensions, CLI utilities." \
  --keyword tool \
  --keyword extension
```

## Ollama Provider

默认 provider 是本地规则：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli classify --provider rules --all
```

如果本机已安装并启动 Ollama，可以只重分类 `General`：

```bash
ollama pull qwen2.5:7b
ollama serve
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite classify \
  --provider ollama \
  --only-category General \
  --ollama-model qwen2.5:7b \
  --ollama-timeout 180 \
  --limit 20
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite export-html \
  --archive-dir archive-real-v4-ollama
```

确认 20 条效果没问题后，再去掉 `--limit` 跑完整 `General`。

如果当前模型较慢，先用更小模型测试，例如 `qwen2.5:3b` 或 `llama3.2:3b`。
