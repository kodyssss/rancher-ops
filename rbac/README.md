# rbac — 角色管理

## 脚本

| 脚本 | 功能 |
|------|------|
| `rancher_rbac.py` | 导出 global/cluster/project 三层 RBAC |
| `rancher_rbac_apply.py` | 批量绑定角色 + 预检 + 自动映射 + 自动创建用户 |

## rancher_rbac.py

导出 Rancher 角色绑定为 CSV。

```bash
python3 rancher_rbac.py                         # 终端表格
python3 rancher_rbac.py -o rbac.csv             # CSV 输出（utf-8-sig）
python3 rancher_rbac.py -c poc                  # 限定集群
python3 rancher_rbac.py --no-global             # 跳过全局角色
python3 rancher_rbac.py --no-cluster            # 跳过集群级角色
python3 rancher_rbac.py -o rbac.csv --per-cluster  # 每集群单独文件
```

**输出格式**: `LEVEL,CLUSTER,PROJECT,USER_GROUP,TYPE,ROLE,PRINCIPAL_ID,ROLE_ID`

- `LEVEL`: global / cluster / project
- `USER_GROUP`: 用户或组的 displayName
- `TYPE`: User / Group
- `PRINCIPAL_ID`: 全局唯一标识（user-xxx / u-xxx / LDAP DN）
- `ROLE_ID`: 角色 API ID

## rancher_rbac_apply.py

读取 RBAC CSV，在目标集群/项目中执行绑定。

```bash
# 预检用户是否存在
python3 rancher_rbac_apply.py --from-csv rbac.csv --check-principals

# 跨集群迁移（相同 Rancher）
python3 rancher_rbac_apply.py --from-csv rbac.csv \
    --map-cluster old=new --auto-create-users

# 跨 SSD 迁移（不同 Rancher）
python3 rancher_rbac_apply.py --from-csv rbac.csv \
    --auto-map-users --auto-create-users

# 预览不执行
python3 rancher_rbac_apply.py --from-csv rbac.csv --auto-map-users --dry-run
```

### 参数

| 参数 | 说明 |
|------|------|
| `--from-csv` | RBAC CSV 文件路径 |
| `--check-principals` | 预检模式：检查所有用户/组在目标端是否存在 |
| `--auto-map-users` | 拉取目标端所有用户，按 displayName 自动匹配并替换 PRINCIPAL_ID |
| `--auto-create-users` | 自动创建缺失的本地用户，密码写入 `user_passwords.txt` |
| `--map-cluster old=new` | 集群名映射（逗号分隔多组） |
| `--dry-run` | 只预览不执行 |

### 跨 SSO 迁移流程

```
源 Rancher 导出 rbac.csv
       ↓
目标 Rancher --auto-map-users
       ├── displayName 匹配 → 替换 PID（直接可用）
       └── 未匹配
            ├── 本地用户 → --auto-create-users 自动创建
            └── SSO 用户 → 标出，需手动登录后再跑
       ↓
执行绑定
```

### roleTemplateId 解析

脚本内置了常见角色名 ↔ API ID 的映射：

**Project 级**: owner → project-owner, member → project-member, readonly → read-only

**Cluster 级**: cluster owner → cluster-owner, cluster member → cluster-member, cluster admin → cluster-admin, cluster viewer → cluster-viewer, nodes view → nodes-view, nodes manage → nodes-manage, projects create → projects-create, projects view → projects-view, storage manage → storage-manage

**Global 级**: 全局角色绑定默认跳过（需手动管理）

自定义角色会通过 API 查询 displayName，结果缓存在内存中。
