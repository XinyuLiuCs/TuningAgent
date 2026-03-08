# Terminal-Bench 数据集速查

## 本地已确认有的版本

当前在本地 `bench/terminal-bench` 中，已经确认到以下数据集或版本信息：

- `original-tasks`
  - 本地目录已存在
  - 任务数：241
  - 这是当前最稳妥、最直接可跑的本地任务集

- `terminal-bench-core==0.1.0`
  - 已在本地 `registry.json` 中确认
  - 任务数：71
  - 属于 registry 数据集定义

- `terminal-bench-core==0.1.1`
  - 已在本地 `registry.json` 中确认
  - 任务数：80
  - 属于 registry 数据集定义

- `terminal-bench-core==head`
  - 已在本地 `registry.json` 中确认
  - 当前 `task_id_subset` 为 0
  - 不建议作为稳定基线使用

## 本地未确认有的版本

以下版本我没有在当前本地 clone 中确认已经落地：

- `terminal-bench==2.0`

说明：

- 它是官网正式榜单版本，但当前本地 `registry.json` 没有直接显示这一项。
- 如果要跑它，通常需要依赖更高版本的 Terminal-Bench 或在线 registry 拉取。

## 最常用命令

说明：

- `curated-smoke` 和 `curated-core` 是本项目本地整理的 profile，不是官方榜单名。

### 1. 跑本地全量任务集 `original-tasks`

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset-path original-tasks \
  --no-profile \
  --run-id tb-original-tasks-20260308
```

### 2. 跑本地已确认的官方核心集 `terminal-bench-core==0.1.1`

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench-core==0.1.1 \
  --no-profile \
  --run-id tb-core-0p1p1-20260308
```

### 3. 跑本地已确认的旧版核心集 `terminal-bench-core==0.1.0`

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench-core==0.1.0 \
  --no-profile \
  --run-id tb-core-0p1p0-20260308
```

### 4. 跑当前最常用 smoke 子集

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-smoke \
  --run-id tb-smoke-20260308
```

### 5. 跑当前最常用 5 任务子集

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-core \
  --run-id tb-curated-core-20260308
```

### 6. 跑 oracle 基线

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench-core==0.1.1 \
  --no-profile \
  --agent oracle \
  --run-id tb-core-oracle-20260308
```

## 建议

- 如果你只想确认链路能跑，先用 `curated-smoke`
- 如果你要做稳定内部基线，优先用 `terminal-bench-core==0.1.1`
- 如果你要做本地探索或任务分析，优先用 `original-tasks`
