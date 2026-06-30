# RBAC — 角色管理

## 脚本

| 脚本 | 用途 |
|---|---|
| `rancher_rbac.py` | 全层级角色导出 — global / cluster / project → CSV |
| `rancher_rbac_apply.py` | 批量绑定 — 预检 + 自动创建用户 + 跨集群迁移 |

## 快速使用

```bash
# 导出全层级 RBAC
python3 rancher_rbac.py -o rbac.csv

# 只看某个集群
python3 rancher_rbac.py -c poc

# 预检目标端用户情况
python3 rancher_rbac_apply.py --from-csv rbac.csv --check-principals

# 自动创建缺失用户 + 预览绑定
python3 rancher_rbac_apply.py --from-csv rbac.csv --auto-create-users --dry-run

# 执行
python3 rancher_rbac_apply.py --from-csv rbac.csv --auto-create-users

# 跨集群迁移
python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --auto-create-users
```

## 三个层级

| 层级 | API | 说明 |
|---|---|---|
| global | GlobalRoleBinding | 登录/管理 Rancher |
| cluster | ClusterRoleTemplateBinding | 访问集群 |
| project | ProjectRoleTemplateBinding | 操作项目 |

## 参数

| 参数 | rancher_rbac | rancher_rbac_apply |
|---|---|---|
| `-o` / `--output` | CSV 输出 | — |
| `-c` / `--cluster` | 限定集群 | — |
| `-e` / `--env` | env 文件 | env 文件 |
| `--per-cluster` | 每集群单文件 | — |
| `--no-global` | 跳过全局 | — |
| `--no-cluster` | 跳过集群 | — |
| `--from-csv` | — | 输入 CSV |
| `--map-cluster` | — | 集群名映射 |
| `--check-principals` | — | 预检用户 |
| `--auto-create-users` | — | 自动创建用户 |
| `--dry-run` | — | 预览 |
