# Daily Paper Push MVP

English version: [README.md](README.md)

这是一个每日论文推送系统，当前支持：
- 从 `arXiv` 抓取配置领域内的论文，并支持更细粒度主题过滤
- 从 `Hugging Face Daily Papers` 抓取指定日期页
- 使用 LLM 生成中文摘要
- 通过飞书 `webhook` 推送
- 使用本地常驻脚本按固定时间自动执行

## 系统会做什么

整条链路是：
1. 从 `arXiv` 和 `Hugging Face Daily Papers` 抓取候选论文
2. 统一字段格式并去重
3. 按规则打分，生成两个输出栏目
4. 生成中文摘要
5. 推送到飞书

当前输出栏目：
- `For You`：更偏你的兴趣关键词、时效性和趋势分
- `Trending Now`：更偏热度和新鲜度

当前两个数据源的时间行为：
- `arXiv`：抓取窗口是“上一次成功推送时间”到“本次运行时间”
- `Hugging Face Daily Papers`：仍然按当天的 `/papers/date/YYYY-MM-DD` 页面抓取

## 目录结构

```text
config/
  default.yaml        可提交到 Git 的安全默认配置
  local.yaml          你本机使用的私有配置，已加入 .gitignore
scripts/
  run_daily_push.sh   单次执行脚本
  daily_push_loop.sh  后台常驻循环脚本
src/
  connectors/         数据源抓取
  pipeline/           标准化和去重
  ranking/            打分和选取
  summarization/      摘要生成
  delivery/           飞书发送和幂等
  scheduler/          调度与状态管理
tests/
logs/
data/state/
```

## 配置文件说明

配置优先级：
1. 命令行显式传入 `--config <path>`
2. 如果存在 `config/local.yaml`，默认优先用它
3. 否则使用 `config/default.yaml`

建议用法：
- `config/default.yaml` 只保留安全、通用的默认值
- `config/local.yaml` 放你自己的真实 `webhook`、`API key` 和偏好设置
- 不要提交 `config/local.yaml`

### `scheduler`

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `timezone` | 后台循环使用的时区 | `Asia/Shanghai` |
| `daily_time` | 每天触发时间，格式 `HH:MM` | `09:00` |
| `state_path` | 调度状态文件，记录上次成功推送时间和上次已发论文 ID | `data/state/scheduler_state.json` |

说明：
- `arXiv` 的增量抓取依赖这个状态文件
- 首次成功运行后会写入“上次成功推送时间”
- 后续运行时会按这个时间窗抓 `arXiv`

### `ranking`

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `ranking_version` | 规则版本标记 | `v1` |
| `keywords` | 用于 `For You` 的关键词，逗号分隔 | `reinforcement learning,agent,reasoning,llm,alignment` |
| `weights.interest` | `For You` 中兴趣相关性的权重 | `0.65` |
| `weights.freshness` | `For You` 中新鲜度的权重 | `0.20` |
| `weights.trending` | `For You` 中趋势分的权重 | `0.15` |
| `for_you_n` | `For You` 推送条数 | `5` |
| `trending_n` | `Trending Now` 推送条数 | `5` |

说明：
- `keywords` 主要影响 `For You`
- `Trending Now` 仍然主要由热度和新鲜度决定

### `source`

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `source_success_mode` | 数据源成功判定方式 | `strict_both` |
| `arxiv_categories` | arXiv 一级领域，逗号分隔 | `cs.AI,cs.LG,cs.CL,cs.CV` |
| `arxiv_focus_terms` | 更细粒度主题词，作用于 arXiv 标题/摘要查询 | `reinforcement learning,agent,reasoning` |
| `arxiv_focus_mode` | `any` 表示命中任一主题词即可，`all` 表示必须全部命中 | `any` |
| `hf_fallback_days` | 如果当天 HF 页面为空，最多向前回退几天 | `3` |

说明：
- `arxiv_categories` 是硬边界
- `arxiv_focus_terms` 是硬边界内部的细分主题过滤
- HF 仍按日期页抓取，不做窗口化增量

### `fetch`

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `max_results_per_source` | 每个数据源最多抓多少条原始候选论文 | `50` |

### `delivery`

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `max_msg_chars` | 单条飞书消息的最大字符数 | `18000` |
| `max_entries_per_chunk` | 单条飞书消息最多包含几篇论文 | `8` |
| `webhook_url` | 飞书 webhook 地址 | `https://...` |
| `webhook_timeout_sec` | webhook HTTP 超时秒数 | `15` |

说明：
- 如果 `webhook_url` 为空，流程仍可跑通，但不会真的发消息
- 推送幂等状态保存在 `data/state/delivery_idempotency.jsonl`

### `summary`

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `use_llm` | 是否启用 LLM 摘要 | `1` |
| `language` | 摘要输出语言 | `zh-CN` |
| `mode` | `strict` 只看标题/摘要，`enhanced` 会额外抓取 arXiv HTML 正文片段 | `enhanced` |
| `enhanced_max_chars` | `enhanced` 模式下送入 LLM 的 HTML 文本上限 | `8000` |
| `api_key` | LLM API key | 提交配置里应为空 |
| `base_url` | OpenAI 兼容接口地址或 API 根路径 | 提交配置里应为空 |
| `model` | 模型名 | `gpt-4o-mini` |

说明：
- `enhanced` 主要对 arXiv 论文有效，因为系统会把 arXiv `abs` URL 转成 `html` URL 抓正文
- 如果 LLM 不可用，会自动退回到“从原摘要抽取”的保底摘要

## `config/local.yaml` 示例

建议你在本机创建 `config/local.yaml`：

```yaml
scheduler:
  timezone: Asia/Shanghai
  daily_time: "09:00"
  state_path: "data/state/scheduler_state.json"

ranking:
  ranking_version: v1
  keywords: "reinforcement learning,agent,reasoning,llm,alignment"
  weights:
    interest: 0.65
    freshness: 0.20
    trending: 0.15
  for_you_n: 5
  trending_n: 5

source:
  source_success_mode: strict_both
  arxiv_categories: "cs.AI,cs.LG,cs.CL,cs.CV"
  arxiv_focus_terms: "reinforcement learning,agent,reasoning"
  arxiv_focus_mode: "any"
  hf_fallback_days: 3

fetch:
  max_results_per_source: 50

delivery:
  max_msg_chars: 18000
  max_entries_per_chunk: 8
  webhook_url: "https://your-feishu-webhook"
  webhook_timeout_sec: 15

summary:
  use_llm: 1
  language: "zh-CN"
  mode: "enhanced"
  enhanced_max_chars: 8000
  api_key: "your-api-key"
  base_url: "https://your-openai-compatible-endpoint"
  model: "your-model-name"
```

## 手动运行

直接按默认配置选择逻辑运行：

```bash
cd /path/to/daily-paper-push-mvp
conda run -n paper python -m src.scheduler.daily_job --run-live-today
```

强制指定某个配置文件：

```bash
conda run -n paper python -m src.scheduler.daily_job --config config/local.yaml --run-live-today
```

只检查当前是否到触发时间：

```bash
conda run -n paper python -m src.scheduler.daily_job --check-schedule
```

用 fixture 测试整条链路：

```bash
conda run -n paper python -m src.scheduler.daily_job --run-once-fixtures
```

## 后台运行

单次执行脚本：

```bash
./scripts/run_daily_push.sh
```

后台常驻循环：

```bash
./scripts/daily_push_loop.sh
```

当前行为：
- 自动优先使用 `config/local.yaml`
- 从当前有效配置中读取 `scheduler.daily_time` 和 `scheduler.timezone`
- 循环日志写到 `logs/daily_push_loop.log`
- 单次任务日志写到 `logs/daily_push.log`

## 环境变量覆盖

脚本支持这些环境变量覆盖：

| 变量名 | 含义 |
| --- | --- |
| `DAILY_PAPER_PUSH_CONFIG` | 显式指定配置文件路径 |
| `CONDA_BIN` | 显式指定 `conda` 可执行文件 |
| `CONDA_ENV_NAME` | `run_daily_push.sh` 使用的 conda 环境名 |
| `PYTHON_BIN` | `daily_push_loop.sh` 用于读配置的 Python 路径 |

可参考 [.env.example](.env.example)。

## 日志与状态文件

常见本地文件：
- `logs/daily_push.log`：单次任务日志
- `logs/daily_push_loop.log`：后台循环日志
- `data/state/delivery_idempotency.jsonl`：飞书推送幂等状态
- `data/state/scheduler_state.json`：上次成功推送时间和上次已发论文 ID

其中 `scheduler_state.json` 很关键，因为它决定了下一次 `arXiv` 的抓取窗口。

## 测试

```bash
conda run -n paper pytest -q
```

## Git 提交约定

- [config/default.yaml](config/default.yaml) 可以提交
- `config/local.yaml` 不会进入 Git
- `logs/`、`data/state/`、`.pytest_cache/`、`__pycache__/` 都已经加入 `.gitignore`

## 常见问题

飞书没有收到消息：
- 检查当前生效配置里的 `delivery.webhook_url`
- 查看 `logs/daily_push.log`
- 检查机器是否能访问 Feishu 和上游数据源

摘要退回到原文抽取：
- 检查 `summary.api_key`、`summary.base_url`、`summary.model`
- 确认你的接口是 OpenAI 兼容格式

后台循环启动后立刻退出：
- 先运行 `bash -n scripts/daily_push_loop.sh`
- 再直接执行一次 `./scripts/daily_push_loop.sh` 看即时报错

`arXiv` 结果出现重复：
- 检查 `data/state/scheduler_state.json`
- 只有成功推送后才会推进时间窗，失败运行不会推进
