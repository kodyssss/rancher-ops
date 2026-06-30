# rancher-ops

Rancher v3 运维脚本工具集 — 纯 Python，不依赖 kubectl，直接调 Rancher API。

## 脚本一览

| 脚本 | 用途 |
|---|---|
| `rancher_rbac.py` | 全层级角色导出 — global / cluster / project 三层 RBAC → CSV |
| `rancher_rbac_apply.py` | 批量角色绑定 — 支持用户预检、自动创建缺失本地用户 |
| `rancher_mapping.py` | 集群/项目/Namespace 四级映射导出 → CSV/JSON |
| `rancher_create.py` | 项目和 Namespace 批量创建、迁移 |

## 快速开始

```bash
# 1. 配置
cp env.txt.example env.txt
# 编辑 env.txt，填入 Rancher URL 和 Token

# 2. 导出全层级 RBAC
python3 rancher_rbac.py -o rbac.csv

# 3. 导出集群结构
python3 rancher_mapping.py -o mapping.csv
```

## 典型场景

### 跨集群迁移 RBAC

```bash
# 导出 → 预检 → 创建缺失用户 → 应用
python3 rancher_rbac.py -c poc -o rbac.csv
python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --check-principals
python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users
```

### 拷贝 NS 结构到目标集群

```bash
python3 rancher_mapping.py -c poc -o structure.csv
python3 rancher_create.py create-project -f structure.csv
python3 rancher_create.py create-ns -f structure.csv
```

## 要求

- Python 3.6+
- Rancher API Token（有读写权限）

## 详细文档

[rancher_scripts_README.md](./rancher_scripts_README.md)
