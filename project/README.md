# project — 项目/Namespace 管理

## 脚本

`rancher_create.py` — 支持三种操作：创建项目、创建 NS、迁移 NS。

## 三种操作

| 操作 | 用途 | 场景 |
|------|------|------|
| `create-project` | 批量创建项目（支持 labels） | 结构迁移 |
| `create-ns` | 批量创建 NS 并分配至项目 | 新建环境 |
| `move-ns` | 批量将已有 NS 迁移至项目 | 跨 Rancher 迁移（NS 已存在） |

## 单条模式

```bash
# 创建项目
python3 rancher_create.py create-project -c poc -p "项目名"
python3 rancher_create.py create-project -c poc -p "项目名" -l env=prod -l team=sre

# 创建 NS
python3 rancher_create.py create-ns -c poc -p "项目名" -n "ns名"

# 迁移已有 NS 到指定项目
python3 rancher_create.py move-ns -c poc -p "目标项目" -n "已有ns"
```

## 批量模式

```bash
# 自动识别文件格式 (.csv / .json)
python3 rancher_create.py create-project -f projects.csv --dry-run
python3 rancher_create.py create-project -f projects.csv

python3 rancher_create.py create-ns -f namespaces.csv --dry-run
python3 rancher_create.py create-ns -f namespaces.csv

python3 rancher_create.py move-ns -f mapping.csv --dry-run
python3 rancher_create.py move-ns -f mapping.csv
```

## 输入格式

### create-project CSV

```csv
CLUSTER,PROJECT,LABELS
poc,项目A,env=prod,team=sre
poc,项目B,
```

### create-ns / move-ns CSV

```csv
CLUSTER,PROJECT,NAMESPACE
poc,项目A,ns-frontend
poc,项目A,ns-backend
poc,项目B,ns-data
```

也支持 mapping 导出的四列格式 `CLUSTER,PROJECT,NAMESPACE,LABELS`。

### JSON

```json
[{"cluster":"poc","project":"项目A","namespace":"ns1"}]
```

也支持 mapping 导出的嵌套格式。

## 智能跳过

- `create-project`: 自动去重（同一集群+项目只创建一次），过滤 `(unknown)` 伪项目
- `move-ns`: 项目名为 `(unknown)` / `unknown` / 空 / `-` 时自动跳过

## 关联

- `mapping/rancher_mapping.py`: 提供 CSV/JSON 输入
- `rbac/rancher_rbac_apply.py`: 项目创建后绑定 RBAC
