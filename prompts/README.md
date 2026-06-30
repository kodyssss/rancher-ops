# rancher-ops 运维脚本 — AI 提示词

## 项目简介

这是一个 Rancher v3 运维脚本工具集，纯 Python 实现，直接调 Rancher API，不依赖 kubectl。

**GitHub**: https://github.com/kodyssss/rancher-ops

**目录结构**:
```
rancher-ops/
├── rbac/          → 角色管理（导出 + 应用）
├── mapping/       → 集群结构导出
├── project/       → 项目/Namespace 管理
├── prompts/       → AI 提示词（本目录）
├── env.txt.example
└── .gitignore
```

**环境要求**: Python 3.6+，需要 Rancher API Token。

**配置文件**: 复制 `env.txt.example` 为 `env.txt`，填入:
```
export RANCHER_URL=https://rancher.example.com
export RANCHER_TOKEN=***
```

---

## 脚本说明

### rbac/rancher_rbac.py — 角色导出

导出 global / cluster / project 三层 RBAC → CSV。

```bash
python3 rbac/rancher_rbac.py -o rbac.csv       # 全量导出
python3 rbac/rancher_rbac.py -c poc             # 限定集群
python3 rbac/rancher_rbac.py --no-global         # 跳过全局角色
python3 rbac/rancher_rbac.py --no-global --no-cluster  # 只看项目级
```

**CSV 列**: LEVEL, CLUSTER, PROJECT, USER_GROUP, TYPE, ROLE, PRINCIPAL_ID, ROLE_ID

### rbac/rancher_rbac_apply.py — 角色绑定

从 CSV 批量应用角色绑定。

```bash
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --check-principals   # 预检用户
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --auto-create-users --dry-run  # 预览
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --auto-create-users  # 执行
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users  # 跨集群
```

### mapping/rancher_mapping.py — 结构导出

导出 集群→项目→Namespace→Labels 四级映射。

```bash
python3 mapping/rancher_mapping.py -o mapping.csv
python3 mapping/rancher_mapping.py -c poc -o poc_struct.csv
```

### project/rancher_create.py — 项目/NS 管理

```bash
python3 project/rancher_create.py create-project -c poc -p "项目名" -l env=prod
python3 project/rancher_create.py create-ns -c poc -p "项目名" -n "ns名"
python3 project/rancher_create.py move-ns -c poc -p "目标项目" -n "已有ns"
python3 project/rancher_create.py create-project -f projects.csv --dry-run
```

---

## 典型工作流

### 场景 1: 跨集群迁移 RBAC 权限

```bash
# Step 1: 从源集群导出
python3 rbac/rancher_rbac.py -c 源集群名 -o rbac.csv

# Step 2: 预检目标端用户
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster 源集群名=目标集群名 --check-principals

# Step 3: 执行（自动创建缺失的本地用户）
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster 源集群名=目标集群名 --auto-create-users
```

### 场景 2: 拷贝集群结构到新集群

```bash
python3 mapping/rancher_mapping.py -c 源集群 -o structure.csv
python3 project/rancher_create.py create-project -f structure.csv
python3 project/rancher_create.py create-ns -f structure.csv
```

### 场景 3: 完整迁移（结构 + 权限）

```bash
# 结构
python3 mapping/rancher_mapping.py -c 源集群 -o poc.csv
python3 project/rancher_create.py create-project -f poc.csv
python3 project/rancher_create.py create-ns -f poc.csv

# 权限
python3 rbac/rancher_rbac.py -c 源集群 -o rbac.csv
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster 源集群=目标集群 --auto-create-users
```
