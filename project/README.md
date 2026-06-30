# Project — 项目/Namespace 管理

`rancher_create.py` — 三种操作：`create-project` / `create-ns` / `move-ns`

## 单条操作

```bash
# 创建项目
python3 rancher_create.py create-project -c poc -p "项目名"
python3 rancher_create.py create-project -c poc -p "项目名" -l env=prod

# 创建 Namespace
python3 rancher_create.py create-ns -c poc -p "项目名" -n "ns名"

# 迁移 Namespace
python3 rancher_create.py move-ns -c poc -p "目标项目" -n "已有ns"
```

## 批量操作

```bash
# 从 CSV / JSON 批量
python3 rancher_create.py create-project -f projects.csv --dry-run
python3 rancher_create.py create-project -f projects.csv

python3 rancher_create.py create-ns -f structure.csv --dry-run
python3 rancher_create.py move-ns -f all.csv --dry-run
```

## 输入格式

**CSV**（兼容 mapping 导出格式）：

```csv
CLUSTER,PROJECT,NAMESPACE
poc,项目A,ns1
poc,项目A,ns2
```

**JSON**：

```json
[{"cluster":"poc","name":"项目A","labels":{"env":"prod"}}]
```

## 参数

| 参数 | 说明 |
|---|---|
| `create-project` / `create-ns` / `move-ns` | 操作类型 |
| `-c` / `--cluster` | 集群 name 或 id |
| `-p` / `--project` | 项目 name 或 id |
| `-n` / `--namespace` | Namespace 名 |
| `-l key=value` | 标签，可多次使用 |
| `-f` / `--from-file` | 批量输入 (.csv/.json) |
| `--dry-run` | 只预览 |
| `-e` / `--env` | env 文件 |
