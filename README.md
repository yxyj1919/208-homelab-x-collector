# homelab-x-collector

Local-first X/Twitter bookmark archiver MVP.

第一版目标：

- 从导出的 JSON 文件导入收藏数据。
- 使用 SQLite 保存原始数据、分类、标签和导出路径。
- 用本地规则分类，不依赖云端 API。
- 导出每条收藏为独立 HTML，并按分类目录保存。
- 生成全局索引 `archive/_index/index.html`。

## v0.1.0 Scope

v0.1.0 是 JSON 导入版 MVP，适合作为本地收藏归档工具的第一版基线。

已支持：

- 从 X/Twitter 导出的 JSON 文件导入收藏。
- 用 SQLite 保存原始收藏、分类、标签、导出路径和人工分类状态。
- 使用本地规则分类，默认不依赖云端 API。
- 可选使用 Ollama 进行本地或远端模型分类。
- 手工修正单条收藏分类，并避免后续自动分类默认覆盖人工结果。
- 为每条收藏生成独立 HTML 文件，并按分类目录保存。
- 为每条收藏生成带 Obsidian front matter 的独立 Markdown 文件。
- 生成全局索引页面。

未包含在 v0.1.0：

- 直接登录 X/Twitter 账号同步收藏。
- X API / OAuth 集成。
- 定时任务安装脚本。
- 多用户或远端服务部署。

Obsidian / Markdown 导出的 front matter 字段固定为：

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

推荐运行顺序：

```bash
python3 -m xbookmarks.cli init --write-categories
python3 -m xbookmarks.cli run --input samples/bookmarks.json
python3 -m xbookmarks.cli stats
```

测试：

```bash
python3 -m pytest
```

运行数据默认写入：

- SQLite 数据库：`data/bookmarks.sqlite`
- 原始 JSON 备份：`data/json-backups/raw/`
- HTML 归档：`archive/`
- 分类配置：`config/categories.yaml`

`data/`、`archive/`、`archive-*/` 和 `real-bookmarks.json` 是本地运行数据，默认不进入 Git。
备份时优先保留 SQLite 数据库、原始 JSON 备份、分类配置和需要长期保存的 HTML 归档目录。

## Quick Start

```bash
python3 -m xbookmarks.cli init
python3 -m xbookmarks.cli run --input samples/bookmarks.json
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
- HTML 归档：`archive/`

查看统计：

```bash
python3 -m xbookmarks.cli stats
```

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

Markdown / Obsidian 导出：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite export-markdown \
  --archive-dir obsidian-archive
```

输出结构按分类分目录，单条收藏写成
`obsidian-archive/<Category>/<date>_<tweet_id>.md`，索引写入
`obsidian-archive/_index/index.md`。每个 Markdown 文件包含固定 front matter：
`tweet_id`、`url`、`author`、`created_at`、`category`、`tags`、`source`、
`provider`、`confidence`、`read_state`。正文包含原文、原链接、备注、分类原因，以及
可从 `raw_json` 派生出的 media、card、quoted tweet 链接。

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

输出包含总数、pending/accepted 数量，以及 pending review 按 `review_reason` 的分组计数。

更新单条收藏的备注、标签和状态：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite update 1001 \
  --notes "read later" \
  --tags "vcf,homelab" \
  --read-state read \
  --important \
  --no-archived
```

## Local Web UI

第一版本地 Web UI 只消费已经存在的 storage/search/sync 状态能力，不负责
AI provider 配置、OAuth 流程或定时任务安装。

启动：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli web --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765/` 后可以：

- 列表查看收藏。
- 搜索。
- 按分类过滤。
- 按 `active`、`unread`、`read`、`important`、`pending_review`、`archived` 过滤。
- 修改分类、标签、备注。
- 标记 read/unread、important、archive。
- 对 pending review 单条收藏执行 Accept 或临时 Skip。
- 对 accepted 收藏执行 Mark pending，重新放回 review queue。
- 对当前列表中选中的多条收藏执行 bulk accept、bulk archive 或 bulk category update。
- 查看最近同步状态。

Review queue 当前是轻量状态层：新导入记录默认进入 `pending`，低置信度自动分类会保持
`pending` 并记录原因；旧库迁移后的历史记录默认视为 `accepted`，避免升级后全部进入待审核。

## New User Setup

这一节面向首次使用者，推荐用本地 Web UI 加 Chrome extension 完成 X bookmarks 归档。

### 1. 初始化本地项目

在项目目录中执行：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli init --write-categories
```

默认会写入：

- SQLite 数据库：`data/bookmarks.sqlite`
- 分类配置：`config/categories.yaml`

### 2. 启动本地 Web UI

推荐使用 `8766` 端口，方便和 extension 配置保持一致：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite web --host 127.0.0.1 --port 8766
```

然后打开：

```text
http://127.0.0.1:8766/
```

Web UI 可以查看、搜索、过滤、批量分类、归档和编辑 bookmarks。

### 3. 加载 Chrome extension

项目内置 extension 目录：

```text
extension/chrome
```

加载步骤：

1. 打开 `chrome://extensions`。
2. 启用 `Developer mode`。
3. 点击 `Load unpacked`。
4. 选择项目里的 `extension/chrome` 目录。
5. 如果之后更新了 extension 代码或权限，回到 `chrome://extensions` 点击 `Reload`。

### 4. 配置 extension 服务地址

点击 Chrome 工具栏里的 `X Bookmarks Local Helper` 图标。

点击：

```text
Option
```

把 Local UI URL 设置为：

```text
http://127.0.0.1:8766
```

点击 `Save`。

### 5. 捕获 X 会话状态

在同一个 Chrome 里登录 X，然后打开：

```text
https://x.com/i/bookmarks
```

刷新页面一次，再打开 extension popup。确认 `X Session Capture` 中三项都是 captured：

- `User`
- `Auth`
- `Bookmarks queryId`

如果没有 captured，先确认 Chrome 已登录 X，然后刷新 `https://x.com/i/bookmarks`。

### 6. 导出书签到本地应用

确认本地 Web UI 正在运行，并且 extension 的 Local UI URL 指向 `http://127.0.0.1:8766`。

在 extension popup 点击：

```text
导出所有书签到API
```

导出行为：

- extension background 会分页调用 X.com bookmarks GraphQL。
- 抓取过程中 Web UI 的 `Import status` 会绿灯闪烁，并显示页数和已抓取数量。
- 抓取完成后 extension 会一次性提交到本地 `POST /api/extension/bookmarks`。
- 本地服务会导入 SQLite、执行规则分类，并导出 HTML 到 `archive/`。
- 完成后 Web UI 绿灯常亮 12 秒，并显示导入摘要。
- Chrome 会显示导出完成通知。

### 7. 下载 JSON 备份

如果需要单独保存一份 JSON 文件，在 extension popup 点击：

```text
下载导出文件到本地
```

下载文件包含：

- `export_metadata`
- `folders`
- `bookmarks`

### 8. 使用 Web UI 管理 bookmarks

Web UI 常用操作：

- 搜索 bookmarks。
- 按分类过滤。
- 按 `active`、`unread`、`read`、`important`、`pending_review`、`archived` 过滤。
- 单条编辑分类、标签、备注、read、important、archived。
- 选中多条 bookmarks 后批量：
  - `Accept`
  - `Archive`
  - 从分类下拉菜单选择分类并点击 `Apply category`
- 在 `Settings` 中配置 Ollama 地址、模型、AI classify timeout 和分类规则。

### 9. 注意事项

- extension 不负责登录 X；必须先在 Chrome 中登录 X。
- extension 使用当前浏览器会话和 X.com 内部 GraphQL，请只在自己的账号和本机环境使用。
- X.com cookie、header、token 不会写入项目文件、SQLite、运行日志或 HTML archive。
- 如果 extension 权限有变化，需要在 `chrome://extensions` 重新加载 extension。
- `data/`、`archive/`、`obsidian-archive/`、`config/settings.json` 是本地运行数据，不建议提交到 Git。

## Sync Connector

导入和 `run` 流程现在通过 connector 获取收藏记录。当前实现的 connector 是
`json-file` 和 `xarchive-json`。`json-file` 复用通用 JSON 导入能力，并把 connector 名称、源文件路径和文件游标写入
`sync_state`：

```bash
python3 -m xbookmarks.cli import samples/bookmarks.json --connector json-file
python3 -m xbookmarks.cli run --input samples/bookmarks.json --connector json-file
python3 -m xbookmarks.cli sync-status
```

`import` 和 `run` 默认会把本次同步的原始 JSON 保存到
`data/json-backups/raw/`。文件类 connector 会复制输入 JSON，非文件类 connector 会保存
每条收藏的原始记录。可用 `--json-backup-dir <dir>` 改目录，或用 `--no-json-backup`
关闭。

这个抽象用于后续接入 X API / OAuth 或其他同步来源。分类、去重、人工分类保护和 HTML
导出流程不依赖具体 connector。

### xarchive JSON connector

推荐优先使用 xarchive 路线导出完整 bookmarks，再交给本项目做 SQLite、分类、搜索和
HTML 归档。xarchive JSON 顶层包含 `export_metadata`、`folders` 和 `bookmarks`；
每条 bookmark 的 `folders` 会映射为本项目的 tags，第一个 folder 会作为初始
category。已有 `manual` 分类不会被 xarchive folder 覆盖，完整原始对象会保存在
`raw_json`。

```bash
PYTHONPATH=src python3 -m xbookmarks.cli run \
  --connector xarchive-json \
  --input real-bookmarks.json \
  --archive-dir archive
```

只导入不分类/导出：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli import \
  --connector xarchive-json \
  real-bookmarks.json
```

### X API OAuth connector

`x-api` connector 使用 X API v2 的 authenticated-user bookmarks endpoint：
`GET /2/users/{id}/bookmarks`。这是正规同步路线，但依赖 X API 权限、价格计划和
endpoint 可用性。因此完整同步前必须先跑 capability check。
X API 当前按用量计费，具体 endpoint 价格和 credit 消耗应以 Developer Console 为准；
capability check 只验证认证、scope 和 endpoint 是否可用，不替代费用确认。

推荐顺序：

1. 通过 OAuth 2.0 Authorization Code with PKCE 获取 user access token。
2. 如果需要自动刷新 token，授权 scope 必须包含 `offline.access`，并保存
   `refresh_token` 和 `client_id`。
3. 把 OAuth credential 写入本地 secret store。
4. 运行 capability check。
5. capability check 成功后再运行完整同步。

写入本地 secret store：

```bash
mkdir -p ~/.config/xbookmarks
# 把 access token 写入 ~/.config/xbookmarks/access-token.txt
# 把 refresh token 写入 ~/.config/xbookmarks/refresh-token.txt
PYTHONPATH=src python3 -m xbookmarks.cli x-oauth store \
  --client-id '...' \
  --access-token-file ~/.config/xbookmarks/access-token.txt \
  --refresh-token-file ~/.config/xbookmarks/refresh-token.txt
```

默认 secret 文件是 `data/secrets/x-oauth.json`，文件权限会设置为 `0600`。`data/`
默认不进入 Git。token 不会写入 SQLite、`sync_state` 或运行日志。

capability check：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli capability-check \
  --connector x-api \
  --x-user-id 123456789
```

完整同步：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli run \
  --connector x-api \
  --x-user-id 123456789 \
  --archive-dir archive
```

分页相关参数：

- `--x-page-size`：每页数量，范围 1 到 100，默认 100。
- `--x-max-pages`：单次运行最多请求页数，默认 10。

connector 会把 `x-api.cursor`、`x-api.pages_fetched`、`x-api.result_count` 和
`x-api.has_more` 写入 `sync_state`。`x-api.cursor` 有两种格式：

- `page:<token>`：上次运行达到 `--x-max-pages` 上限，下一次从该分页 token 继续。
- `tweet:<tweet_id>`：上次完整同步后的最新收藏水位，下一次从最新开始抓取，遇到该
  tweet 后停止。

`run-log` 会记录 connector、cursor 前后值、页数、源端返回数量、inserted、updated、
unchanged、duplicates、classified、exported 和 `has_more`。UI 的最近同步状态也消费这份
结构化摘要。

capability check 会写入
`x-api.capability.status`、`x-api.capability.endpoint` 和
`x-api.capability.result_count`。

兼容调试方式：如果没有使用 secret store，也可以临时通过 `X_BEARER_TOKEN` 或
`--x-token-file` 提供 access token。但 OAuth credential 和 refresh token 应优先放在
secret store 中；不要把真实 token 写入项目文件或命令行历史。

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
  --keywords "tool,extension,cli"
```

`category add` 可以直接创建新的 `config/categories.yaml`。分类已存在时，默认保留原
`description`，并把新关键词合并进去；关键词大小写不同但内容相同会自动去重。

示例：手工新增一个 Storage 分类：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli category add Storage \
  --description "Storage systems, filesystems, NAS, and backup." \
  --keywords "storage,filesystem,nas,backup,zfs"
PYTHONPATH=src python3 -m xbookmarks.cli category list
```

新增分类后，对已有数据重新分类并导出：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli classify --all
PYTHONPATH=src python3 -m xbookmarks.cli export-html
```

如果需要完全替换已有分类的描述和关键词，添加 `--replace`。

## Manual Category Fixes

分类后可以手工调整单条收藏的分类：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite set-category \
  1001 VCF \
  --tags "vcf,manual" \
  --archive-dir archive
```

`set-category` 会把该条记录标记为人工分类。之后运行 `classify --all` 或
`run --reclassify` 默认不会覆盖人工分类。

如果确认要让自动分类覆盖人工修改，显式添加 `--include-manual`：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli classify --all --include-manual
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

### 调用远端 Ollama

远端机器需要让 Ollama 监听非 loopback 地址。示例：

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
ollama pull qwen2.5:7b
```

客户端先检查连通性和模型是否存在：

```bash
export OLLAMA_BASE_URL=http://192.168.31.10:11434
export OLLAMA_MODEL=qwen2.5:7b
export OLLAMA_TIMEOUT=180
PYTHONPATH=src python3 -m xbookmarks.cli ollama-check
```

确认 `status=available` 后再分类：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite classify \
  --provider ollama \
  --only-category General \
  --limit 20
```

也可以不使用环境变量，直接传参数：

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite classify \
  --provider ollama \
  --ollama-url http://192.168.31.10:11434 \
  --ollama-model qwen2.5:7b \
  --ollama-timeout 180 \
  --only-category General \
  --limit 20
```
