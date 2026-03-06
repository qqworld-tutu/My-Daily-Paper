# Daily Paper Push MVP

Chinese version: [README.zh-CN.md](README.zh-CN.md)

Daily paper push pipeline for:
- `arXiv` papers filtered by configured categories and focus terms
- `Hugging Face Daily Papers` date pages
- LLM-based Chinese summaries
- Feishu webhook delivery
- local background scheduling with a persistent loop script

## What It Does

The pipeline fetches candidate papers from two sources, normalizes and deduplicates them, scores them into two output tracks, generates Chinese summaries, and pushes the result to Feishu.

Output tracks:
- `For You`: personalized ranking based on configured keywords, freshness, and trend score
- `Trending Now`: popularity-oriented ranking after removing papers already selected for `For You`

Current source behavior:
- `arXiv`: uses configured categories and focus terms; fetch window is from the last successful push time to the current run time
- `Hugging Face Daily Papers`: uses the configured date page for the run date

## Repo Layout

```text
config/
  default.yaml        safe committed defaults
  local.yaml          local machine override, ignored by Git
scripts/
  run_daily_push.sh   one-shot live run
  daily_push_loop.sh  persistent local scheduler loop
src/
  connectors/         source fetchers
  pipeline/           normalization and dedup
  ranking/            scoring and selection
  summarization/      LLM and fallback summarization
  delivery/           Feishu delivery and idempotency
  scheduler/          orchestration and state management
tests/
logs/
data/state/
```

## Config Files

Configuration precedence:
1. `--config <path>` passed to the CLI
2. `config/local.yaml` if it exists
3. `config/default.yaml`

Recommended setup:
- keep `config/default.yaml` safe and generic
- put secrets and personal preferences in `config/local.yaml`
- do not commit `config/local.yaml`

### `scheduler`

| Key | Meaning | Example |
| --- | --- | --- |
| `timezone` | timezone used by the background loop | `Asia/Shanghai` |
| `daily_time` | daily trigger time in `HH:MM` | `09:00` |
| `state_path` | scheduler state file used to remember the last successful push time and last selected paper ids | `data/state/scheduler_state.json` |

Notes:
- `arXiv` incremental fetch depends on `state_path`
- on the first successful run, the scheduler stores the last success time
- on later runs, `arXiv` fetches papers from the previous success time to the current run time

### `ranking`

| Key | Meaning | Example |
| --- | --- | --- |
| `ranking_version` | informational version label | `v1` |
| `keywords` | comma-separated keywords used for `For You` relevance scoring | `reinforcement learning,agent,reasoning,llm,alignment` |
| `weights.interest` | weight of keyword relevance in `For You` score | `0.65` |
| `weights.freshness` | weight of recency in `For You` score | `0.20` |
| `weights.trending` | weight of trend score in `For You` score | `0.15` |
| `for_you_n` | number of papers in `For You` | `5` |
| `trending_n` | number of papers in `Trending Now` | `5` |

Notes:
- `keywords` influences `For You`
- `Trending Now` is still affected by source popularity and recency, not by `keywords` directly

### `source`

| Key | Meaning | Example |
| --- | --- | --- |
| `source_success_mode` | whether both sources must succeed or either source is enough | `strict_both` |
| `arxiv_categories` | comma-separated arXiv categories | `cs.AI,cs.LG,cs.CL,cs.CV` |
| `arxiv_focus_terms` | optional comma-separated subtopic filter applied to arXiv title/abstract query | `reinforcement learning,agent,reasoning` |
| `arxiv_focus_mode` | `any` means any focus term matches, `all` means all terms must match | `any` |
| `hf_fallback_days` | how many days backward to try if the target HF date page is empty | `3` |

Notes:
- `arxiv_categories` is the hard domain boundary
- `arxiv_focus_terms` is the fine-grained filter inside those categories
- HF currently keeps its date-page based behavior

### `fetch`

| Key | Meaning | Example |
| --- | --- | --- |
| `max_results_per_source` | max raw items fetched per source before downstream ranking | `50` |

### `delivery`

| Key | Meaning | Example |
| --- | --- | --- |
| `max_msg_chars` | max characters per Feishu chunk | `18000` |
| `max_entries_per_chunk` | max paper entries per Feishu chunk | `8` |
| `webhook_url` | Feishu webhook URL | `https://...` |
| `webhook_timeout_sec` | HTTP timeout for webhook delivery | `15` |

Notes:
- leave `webhook_url` empty to run the pipeline without sending real messages
- delivery state is written to `data/state/delivery_idempotency.jsonl`

### `summary`

| Key | Meaning | Example |
| --- | --- | --- |
| `use_llm` | whether to use the LLM path | `1` |
| `language` | output language for summaries | `zh-CN` |
| `mode` | `strict` uses title/abstract only, `enhanced` also fetches arXiv HTML context when available | `enhanced` |
| `enhanced_max_chars` | max HTML text context sent to the LLM in enhanced mode | `8000` |
| `api_key` | LLM API key | empty in committed config |
| `base_url` | OpenAI-compatible API endpoint or API root | empty in committed config |
| `model` | model name | `gpt-4o-mini` |

Notes:
- `enhanced` is mainly useful for arXiv papers because it derives `/html/...` from the arXiv URL
- if LLM configuration is missing or the request fails, the summarizer falls back to extractive summary from the abstract

## Local Config Example

Create `config/local.yaml` and keep your personal values there:

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

## Manual Run

Run with the default config resolution:

```bash
cd /path/to/daily-paper-push-mvp
conda run -n paper python -m src.scheduler.daily_job --run-live-today
```

Force a specific config path:

```bash
conda run -n paper python -m src.scheduler.daily_job --config config/local.yaml --run-live-today
```

Check schedule only:

```bash
conda run -n paper python -m src.scheduler.daily_job --check-schedule
```

Run using fixture inputs:

```bash
conda run -n paper python -m src.scheduler.daily_job --run-once-fixtures
```

## Background Run

One-shot script:

```bash
./scripts/run_daily_push.sh
```

Persistent loop:

```bash
./scripts/daily_push_loop.sh
```

The loop:
- chooses `config/local.yaml` automatically if present
- reads `scheduler.daily_time` and `scheduler.timezone` from the active config
- writes loop logs to `logs/daily_push_loop.log`

The one-shot script:
- chooses `config/local.yaml` automatically if present
- writes task logs to `logs/daily_push.log`

## Environment Overrides

The scripts support a few environment overrides:

| Variable | Meaning |
| --- | --- |
| `DAILY_PAPER_PUSH_CONFIG` | explicit config file path |
| `CONDA_BIN` | explicit `conda` executable path |
| `CONDA_ENV_NAME` | conda environment name for `run_daily_push.sh` |
| `PYTHON_BIN` | explicit Python path for `daily_push_loop.sh` |

See [.env.example](.env.example) for the list.

## Logs And State

Main local files:
- `logs/daily_push.log`: one-shot pipeline output
- `logs/daily_push_loop.log`: background loop output
- `data/state/delivery_idempotency.jsonl`: delivery idempotency state
- `data/state/scheduler_state.json`: last successful push timestamp and last selected paper ids

The scheduler state matters because it controls the next `arXiv` fetch window.

## Testing

```bash
conda run -n paper pytest -q
```

## Git Hygiene

- `config/default.yaml` is safe to commit
- `config/local.yaml` is ignored by Git
- `logs/`, `data/state/`, `.pytest_cache/`, and `__pycache__/` are ignored by Git

## Troubleshooting

No papers received in Feishu:
- check `delivery.webhook_url` in your active config
- inspect `logs/daily_push.log`
- make sure the machine can access Feishu and the upstream sources

Summaries fall back to abstract extraction:
- verify `summary.api_key`, `summary.base_url`, and `summary.model`
- confirm your endpoint is OpenAI-compatible

Background loop starts but exits:
- run `bash -n scripts/daily_push_loop.sh`
- run `./scripts/daily_push_loop.sh` directly once to see the immediate error

Unexpected `arXiv` duplicates:
- check `data/state/scheduler_state.json`
- a failed run does not advance the success window
