# rbac — 角色管理

## 脚本

| 脚本 | 功能 |
|------|------|
| `rancher_rbac.py` | 导出 global/cluster/project 三层 RBAC |
| `rancher_rbac_apply.py` | 批量绑定 + 预检 + 模糊匹配 + 自动创建用户 + 角色预检 |

## rancher_rbac.py

```bash
python3 rancher_rbac.py                         # 终端表格
python3 rancher_rbac.py -o rbac.csv             # CSV（utf-8-sig）
python3 rancher_rbac.py -c 集群名               # 限定集群
python3 rancher_rbac.py --no-global             # 跳过全局角色
python3 rancher_rbac.py --per-cluster           # 每集群单独文件
```

## rancher_rbac_apply.py

### 跨 SSO 迁移（最常用）

```bash
python3 rancher_rbac_apply.py --from-csv rbac.csv \
    --auto-map-users --auto-create-users --skip-missing-roles --dry-run

# 确认后去掉 --dry-run
python3 rancher_rbac_apply.py --from-csv rbac.csv \
    --auto-map-users --auto-create-users --skip-missing-roles
```

### 模糊匹配逻辑

跨 SSO 场景下源端和目标端 displayName 格式不同时自动处理：

```
源 CSV:   e.Boran.Yang        → 归一化: eboranyang
目标端:   e-Boran.Yang@geely.com → 归一化: eboranyang  ← 匹配 ✅
```

归一化规则：去掉 `@` 及之后内容 → 小写 → 去掉 `. - _` 分隔符。
模糊匹配仅在精确匹配失败后才触发，不影响同 SSO 场景。

归一化冲突（多个目标用户归一化后相同）→ 标 WARN 跳过，不会错绑。

### 用户未匹配的四种情况

| 用户类型 | 自动匹配 | 自动创建 | 需要手动 |
|----------|----------|----------|----------|
| 本地用户（精确匹配） | ✅ | — | — |
| 本地用户（模糊匹配） | ✅ | — | — |
| 本地用户（未匹配） | ❌ | ✅ `--auto-create-users` | — |
| SSO 用户（已登录） | ✅ 精确/模糊 | — | — |
| SSO 用户（未登录） | ❌ | ❌ | 登录后重跑 |
| 边缘情况（手动映射） | — | — | `--user-mapping file.csv` |

### 角色缺失处理

```bash
# 预检（不执行绑定）
python3 rancher_rbac_apply.py --from-csv rbac.csv --skip-missing-roles --dry-run

# 报告中显示哪些角色缺失，绑定时会跳过
```

### 完整参数

| 参数 | 说明 |
|------|------|
| `--from-csv` | RBAC CSV 文件 |
| `--check-principals` | 预检：检查所有用户/组是否存在 |
| `--auto-map-users` | 精确+模糊匹配，更新 PRINCIPAL_ID |
| `--auto-create-users` | 自动创建未匹配的本地用户 |
| `--skip-missing-roles` | 跳过目标端不存在的角色 |
| `--user-mapping FILE` | 手动映射 CSV：`源名,目标名`（优先） |
| `--map-cluster old=new` | 集群名映射 |
| `--dry-run` | 只预览不执行 |
