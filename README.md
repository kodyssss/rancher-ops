# rancher-ops

Rancher v3 运维脚本工具集 — 纯 Python，不依赖 kubectl，直接调 Rancher API。

所有脚本输出均使用 UTF-8 编码（CSV 带 BOM），Windows/Mac/Linux 中文无乱码。

## 目录

```
rancher-ops/
├── rbac/          → 角色导出 + 应用（支持 global/cluster/project 三层）
├── mapping/       → 集群结构导出（项目/NS/Labels 映射）
├── project/       → 项目/Namespace 管理（创建/迁移）
├── healthcheck/   → 集群巡检（控制平面/节点/CNI/CSI/事件/RBAC）
├── prompts/       → AI 提示词模板
├── env.txt.example
└── .gitignore
```

## 快速开始

```bash
cp env.txt.example env.txt
# 编辑 env.txt 填入 Rancher URL 和 Token
```

## 典型场景

### 场景 1：跨 Rancher 迁移（不同 SSO）

下游集群换一个 Rancher 管理，两个 Rancher SSO 不同。

```
源 Rancher                       目标 Rancher
─────────                        ─────────
 ① 导出结构 + RBAC
    mapping + rbac export
    │
 ② 从源 Rancher 删除下游集群
    (detach → agent 被清理)
    │
                              ③ 导入集群 (Import Existing)
                                 自动发现已有 NS
    │
                              ④ 创建项目
                                 create-project -f
    │
                              ⑤ NS 归入项目 (move-ns)
                                 已有 NS 不需重建
    │
                              ⑥ 用户映射 + RBAC 绑定
                                 --auto-map-users --auto-create-users
```

```bash
# Part 1: 从源端导出
python3 mapping/rancher_mapping.py -c poc -o mapping.csv
python3 rbac/rancher_rbac.py -c poc -o rbac.csv

# --- 手动: 源 Rancher 删除集群 + 目标 Rancher 导入集群 ---

# Part 2: 目标端创建项目
python3 project/rancher_create.py create-project -f mapping.csv --dry-run
python3 project/rancher_create.py create-project -f mapping.csv

# Part 3: 已有 NS 归入项目 (自动跳过未知项目)
python3 project/rancher_create.py move-ns -f mapping.csv --dry-run
python3 project/rancher_create.py move-ns -f mapping.csv

# Part 4: RBAC 自动映射 + 创建缺失用户
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --auto-map-users --auto-create-users --dry-run
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --auto-map-users --auto-create-users
```

### 场景 2：同 Rancher 跨集群迁移（相同 SSO）

同一 Rancher 内从 A 集群拷贝结构 + 权限到 B 集群。用户组相同，直接映射。

```bash
# 1. 导出源集群结构
python3 mapping/rancher_mapping.py -c poc -o mapping.csv

# 2. 目标集群创建项目 + NS
python3 project/rancher_create.py create-project -f mapping.csv
python3 project/rancher_create.py create-ns -f mapping.csv

# 3. 导出源集群 RBAC
python3 rbac/rancher_rbac.py -c poc -o rbac.csv

# 4. 预检 + 绑定
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --check-principals
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users --dry-run
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users
```

### 场景 3：巡检集群健康

```bash
python3 healthcheck/rancher_healthcheck.py                    # 终端报告
python3 healthcheck/rancher_healthcheck.py -o report.md       # Markdown 报告
python3 healthcheck/rancher_healthcheck.py -c poc --json       # JSON 格式
python3 healthcheck/rancher_healthcheck.py --no-deep           # 跳过 k8s API 深度检查
```

### 场景 4：只导出/查看 RBAC

```bash
python3 rbac/rancher_rbac.py                                   # 终端表格
python3 rbac/rancher_rbac.py -o rbac.csv                       # CSV 文件
python3 rbac/rancher_rbac.py -c poc                            # 限定集群
python3 rbac/rancher_rbac.py --no-global                       # 跳过全局角色
python3 rbac/rancher_rbac.py -o rbac.csv --per-cluster         # 每个集群单独文件
```

## 核心功能

### rbac 模块

| 脚本 | 功能 |
|------|------|
| `rancher_rbac.py` | 三层导出: global/cluster/project |
| `rancher_rbac_apply.py` | 批量绑定 + 预检 + 自动创建用户 + 跨 SSO 映射 |

关键参数:
- `--check-principals` 预检用户/组在目标端是否存在
- `--auto-map-users` 按 displayName 自动匹配目标端用户，更新 PRINCIPAL_ID
- `--auto-create-users` 自动创建缺失的本地用户
- `--map-cluster old=new` 跨集群名映射

跨 SSO 迁移时，`--auto-map-users` 会拉取目标端所有本地用户 + SSO principals，
按 displayName/username/loginName 匹配并替换 CSV 中的 PRINCIPAL_ID 和 TYPE。
配合 `--auto-create-users`，未匹配的本地用户自动创建。

### mapping 模块

| 脚本 | 功能 |
|------|------|
| `rancher_mapping.py` | 导出集群 → 项目 → NS 映射（含 Labels） |

输出格式: `CLUSTER,PROJECT,NAMESPACE,LABELS`，可直接给 project 模块做批量输入。

### project 模块

| 脚本 | 功能 |
|------|------|
| `rancher_create.py` | 三种操作: create-project / create-ns / move-ns |

- **create-project**: 批量创建项目（支持 labels）
- **create-ns**: 批量创建新 NS 并分配至项目
- **move-ns**: 批量将已有 NS 迁移至项目（自动跳过 mapping 输出的 `(unknown)` 项目）

支持单条模式（`-c -p -n`）和批量模式（`-f file.csv`）。

### healthcheck 模块

| 脚本 | 功能 |
|------|------|
| `rancher_healthcheck.py` | 完整集群巡检（Markdown/JSON 输出） |

检查项: 集群状态、控制平面(etcd/apiserver/scheduler/controller-manager) + pod 数量一致性、节点健康 + Kubelet 版本偏移、CNI 类型识别、CSI 驱动/StorageClass/PVC、系统组件(CoreDNS/Ingress/Metrics)、高重启 Pod、非 Running Pod、工作负载副本、控制平面/etcd Warning 事件、RBAC 概览、项目成员。

## 要求

- Python 3.6+
- Rancher API Token
- 网络可访问 Rancher URL（支持自签名证书 + HTTP 代理）

## 安全

- `env.txt` 在 `.gitignore` 中，不会提交到 Git
- `env.txt.example` 是模板，不含真实 token
- 自动创建的用户密码写入 `user_passwords.txt`（也在 `.gitignore`）
