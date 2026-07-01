# rancher-ops

Rancher v3 运维脚本工具集 — 纯 Python，不依赖 kubectl，直接调 Rancher API。

所有脚本输出均使用 UTF-8 编码（CSV 带 BOM），Windows/Mac/Linux 中文无乱码。

## 目录

```
rancher-ops/
├── rbac/          → 角色管理 (导出 + 单用户绑定 + 批量)
├── mapping/       → 集群结构导出 (项目/NS/Labels)
├── project/       → 项目/Namespace 管理 (创建/迁移)
├── healthcheck/   → 集群巡检
├── prompts/       → AI 提示词模板
├── env.txt.example
└── .gitignore
```

## 快速开始

```bash
cp env.txt.example env.txt
# 编辑 env.txt 填入 Rancher URL 和 Token
```

## 跨 Rancher 迁移工作流

```
源 Rancher                      目标 Rancher
─────────                       ─────────

① 导出                           ③ 修改 cleanup job
  rbac.py → rbac.csv                 ↓
  mapping.py → mapping.csv      ④ 导入集群 → 创建项目 → move-ns
    │                               ↓
② 源 Rancher 下线集群            ⑤ rancher_user.py 查用户
                                    ↓
                                 ⑥ rbac_batch.py 逐条绑定
```

```bash
# === 源端 ===
python3 rbac/rancher_rbac.py -c [集群名] -o rbac.csv
python3 mapping/rancher_mapping.py -c [集群名] -o mapping.csv
# → 手动: 下线集群

# === 目标端 ===
python3 project/rancher_create.py create-project -f mapping.csv
python3 project/rancher_create.py move-ns -f mapping.csv

python3 rbac/rancher_user.py               # 查看用户清单
python3 rbac/rbac_batch.py --csv rbac.csv --dry-run
python3 rbac/rbac_batch.py --csv rbac.csv   # 执行
```

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

### 场景 2：巡检集群健康

```bash
python3 healthcheck/rancher_healthcheck.py
python3 healthcheck/rancher_healthcheck.py -o report.md
python3 healthcheck/rancher_healthcheck.py --no-deep
```

### 场景 3：只导出/查看 RBAC

```bash
python3 rbac/rancher_rbac.py
python3 rbac/rancher_rbac.py -c poc -o rbac.csv
```

## rbac 模块

| 脚本 | 用法 |
|------|------|
| `rancher_rbac.py` | 导出源端 RBAC → CSV |
| `rancher_user.py` | 查看目标端用户清单 |
| `rancher_rbac_bind.py` | 单用户绑定 `-c -u -p --role` |
| `rbac_batch.py` | 批量调用 bind.py |
| `test_user.py` | 快速验证用户 API |

## project 模块

| 操作 | 用途 |
|------|------|
| `create-project` | 批量创建项目 |
| `move-ns` | 批量迁移已有 NS（跳过 `(unknown)`） |

## healthcheck 模块

10 大类检查：集群状态、控制平面、节点、CNI、CSI、系统组件、事件、RBAC 等。

## 要求

- Python 3.6+
- Rancher API Token

## 安全

- `env.txt` 在 `.gitignore` 中
