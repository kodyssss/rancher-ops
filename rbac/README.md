# rbac — 角色管理

## 脚本

| 脚本 | 功能 |
|------|------|
| `rancher_rbac.py` | 导出 global/cluster/project 三层 RBAC |
| `rancher_rbac_apply.py` | 批量绑定（读 CSV 按 displayName 匹配） |
| `rancher_user.py` | **获取目标 Rancher 所有用户清单** |
| `rancher_rbac_bind.py` | **单用户绑定（传命令行参数）** |

## rancher_user.py — 获取用户

```bash
python3 rancher_user.py                 # 终端表格
python3 rancher_user.py -o users.csv    # 导出 CSV
```

输出所有本地用户 + SSO principals 的 displayName、principal_id、类型。

## rancher_rbac_bind.py — 单用户绑定

```bash
# 项目角色
python3 rancher_rbac_bind.py -c poc -u "e-Xiao.Wang4@geely.com" -p Default --role Owner

# 集群角色
python3 rancher_rbac_bind.py -c poc -u "admin" --clusterrole "Cluster Owner"
```

| 参数 | 说明 |
|------|------|
| `-c` | 集群名 |
| `-u` | 用户 displayName（精确匹配） |
| `-p` | 项目名（`--role` 时需要） |
| `--role` | 项目角色: Owner / Member / ReadOnly |
| `--clusterrole` | 集群角色: Cluster Owner / Member / Admin / Viewer |

### 典型工作流

```bash
# 1. 获取目标 Rancher 所有用户
python3 rancher_user.py -o users.csv

# 2. 查看 users.csv，确认用户 displayName

# 3. 逐个绑定（或用脚本循环）
python3 rancher_rbac_bind.py -c poc -u "e-Xiao.Wang4@geely.com" -p Default --role Owner
python3 rancher_rbac_bind.py -c poc -u "e-Boran.Yang@geely.com" -p Default --role Member
python3 rancher_rbac_bind.py -c poc -u "admin" --clusterrole "Cluster Owner"
```

## rancher_rbac.py — 导出

```bash
python3 rancher_rbac.py                         # 终端表格
python3 rancher_rbac.py -o rbac.csv             # CSV（utf-8-sig）
python3 rancher_rbac.py -c 集群名               # 限定集群
```

## rancher_rbac_apply.py — 批量绑定

```bash
python3 rancher_rbac_apply.py --from-csv rbac.csv --dry-run
python3 rancher_rbac_apply.py --from-csv rbac.csv
```

读 CSV 逐行按 displayName 查用户 → 绑定，用户/角色/项目任一缺失则跳过。
