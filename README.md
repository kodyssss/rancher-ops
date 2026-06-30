# rancher-ops

Rancher v3 运维脚本工具集 — 纯 Python，不依赖 kubectl，直接调 Rancher API。

## 目录

```
rancher-ops/
├── rbac/          → 角色管理（导出 + 应用）
├── mapping/       → 集群结构导出
├── project/       → 项目/Namespace 管理
├── env.txt.example
└── .gitignore
```

## 快速开始

```bash
cp env.txt.example env.txt
# 编辑 env.txt 填入 Rancher URL 和 Token
```

## 典型场景

### 跨集群迁移 RBAC

```bash
# 导出 → 预检 → 创建用户 → 应用
python3 rbac/rancher_rbac.py -c poc -o rbac.csv
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --check-principals
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users
```

### 拷贝 NS 结构

```bash
python3 mapping/rancher_mapping.py -c poc -o structure.csv
python3 project/rancher_create.py create-project -f structure.csv
python3 project/rancher_create.py create-ns -f structure.csv
```

## 要求

- Python 3.6+
- Rancher API Token
