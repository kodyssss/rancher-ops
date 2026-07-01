# rancher-ops

Rancher v3 运维脚本工具集 — 纯 Python，不依赖 kubectl，直接调 Rancher API。

所有脚本输出均使用 UTF-8 编码（CSV 带 BOM），Windows/Mac/Linux 中文无乱码。

## 目录

```
rancher-ops/
├── rbac/          → 角色导出 + 应用（三层 + 模糊匹配 + 角色预检）
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

### 场景 1：跨 Rancher 迁移（不同 SSO）⭐ 最常用

下游集群换一个 Rancher 管理，两个 Rancher SSO 不同，displayName 格式也可能不同。

```
源 Rancher (旧SSO)              目标 Rancher (新SSO)
─────────────────              ─────────────────
 ① 导出
   mapping → mapping.csv
   rbac    → rbac.csv
    │
 ② 从源 Rancher 删除集群
   (detach → agent 清理)
    │
                              ③ 导入集群 (Import Existing)
                                 自动发现已有 NS
    │
                              ④ 创建项目
                                 create-project -f mapping.csv
    │
                              ⑤ NS 归入项目
                                 move-ns -f mapping.csv
                                 (已有 NS 不需重建)
    │
                              ⑥ 预检 + 映射 + 绑定
                                 --auto-map-users       模糊匹配用户
                                 --auto-create-users    创建缺失本地用户
                                 --skip-missing-roles   跳过不存在的角色
    │
                              ⑦ 未匹配 SSO 用户
                                 → 用户在新 Rancher 登录
                                 → 重跑 ⑥
```

```bash
# Part 1: 源端导出（源 Rancher env.txt）
python3 mapping/rancher_mapping.py -c 集群名 -o mapping.csv
python3 rbac/rancher_rbac.py -c 集群名 -o rbac.csv

# --- 手动操作: 源 Rancher 删除集群 + 目标 Rancher 导入集群 ---

# 切换到目标 Rancher env.txt

# Part 2: 创建项目
python3 project/rancher_create.py create-project -f mapping.csv --dry-run
python3 project/rancher_create.py create-project -f mapping.csv

# Part 3: 已有 NS 归入项目
python3 project/rancher_create.py move-ns -f mapping.csv --dry-run
python3 project/rancher_create.py move-ns -f mapping.csv

# Part 4: RBAC 用户映射 + 角色预检 + 绑定（--dry-run 预览）
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv \
    --auto-map-users --auto-create-users --skip-missing-roles --dry-run

# 确认无误后执行
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv \
    --auto-map-users --auto-create-users --skip-missing-roles

# Part 5: 如果还有未匹配的 SSO 用户 → 手动映射文件
# echo "e.Boran.Yang,e-Boran.Yang@geely.com" > user_map.csv
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv \
    --auto-map-users --user-mapping user_map.csv \
    --auto-create-users --skip-missing-roles
```

### 场景 2：同 Rancher 跨集群迁移（相同 SSO）

同一 Rancher 内从 A 集群拷贝到 B 集群，用户 ID 一致。

```bash
# 1. 导出源集群
python3 mapping/rancher_mapping.py -c poc -o mapping.csv
python3 rbac/rancher_rbac.py -c poc -o rbac.csv

# 2. 目标集群创建项目 + NS
python3 project/rancher_create.py create-project -f mapping.csv
python3 project/rancher_create.py create-ns -f mapping.csv

# 3. RBAC 绑定
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --check-principals
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users --dry-run
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users
```

### 场景 3：巡检集群健康

```bash
python3 healthcheck/rancher_healthcheck.py                       # 终端报告
python3 healthcheck/rancher_healthcheck.py -o report.md          # Markdown
python3 healthcheck/rancher_healthcheck.py -c poc --json          # JSON
python3 healthcheck/rancher_healthcheck.py --no-deep              # 快速扫描
```

### 场景 4：只导出/查看 RBAC

```bash
python3 rbac/rancher_rbac.py                                    # 终端表格
python3 rbac/rancher_rbac.py -c poc -o rbac.csv                  # CSV
python3 rbac/rancher_rbac.py --per-cluster -o rbac.csv           # 每集群单独文件
python3 rbac/rancher_rbac.py --no-global                         # 跳过全局角色
```

## 核心功能

### rbac 模块 — `--auto-map-users` 模糊匹配

跨 SSO 时 displayName 格式不同（如 `e.Boran.Yang` vs `e-Boran.Yang@geely.com`），
模糊匹配自动归一化处理：

```
源端: e.Boran.Yang  →  去 .-_  →  eboranyang  ┐
                                                ├ 匹配 ✅
目标: e-Boran.Yang@geely.com                     │
      → 去 @geely.com → 去 .-_ →  eboranyang  ┘
```

| 参数 | 说明 |
|------|------|
| `--auto-map-users` | 精确匹配 + 模糊匹配（去 email 域和 `.-_` 分隔符） |
| `--auto-create-users` | 未匹配的本地用户自动创建 |
| `--skip-missing-roles` | 跳过目标端不存在的角色 |
| `--user-mapping FILE` | 手动映射文件：`源名,目标名`（优先于自动匹配） |
| `--check-principals` | 预检所有用户/组是否存在 |
| `--map-cluster old=new` | 跨集群名映射 |

**SSO 用户处理**：必须在新 Rancher 登录过一次，principal 才会出现在 API 中。
未登录的 SSO 用户 → 模糊匹配找不到 → 未匹配标记 → 绑定跳过（不自动创建）。
用户登录后重跑命令即可。

### mapping 模块

导出 `CLUSTER,PROJECT,NAMESPACE,LABELS` 四列 CSV，可直接给 project 模块做批量输入。

### project 模块

| 操作 | 用途 |
|------|------|
| `create-project` | 批量创建项目（支持 labels），自动去重 |
| `create-ns` | 批量创建新 NS 并分配至项目 |
| `move-ns` | 批量迁移已有 NS（自动跳过 `(unknown)` 项目） |

### healthcheck 模块

10 大类检查：集群状态、控制平面 + pod 数量一致性、节点健康 + Kubelet 版本偏移、CNI、CSI、系统组件、非 Running Pod、高重启、工作负载副本、控制平面 Warning 事件、RBAC 概览。

## 要求

- Python 3.6+
- Rancher API Token
- 网络可访问 Rancher URL（支持自签名证书 + HTTP 代理）

## 安全

- `env.txt` 在 `.gitignore` 中
- 自动创建的用户密码写入 `user_passwords.txt`（也在 `.gitignore`）
