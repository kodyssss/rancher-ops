# 提示词：完整集群迁移（结构 + 权限）

## 复制以下内容给 AI

---

我要从 Rancher 源集群完整迁移到目标集群，包括项目结构、Namespace 和 RBAC 权限。

代码在 https://github.com/kodyssss/rancher-ops，env.txt 已配置。

### 参数
- 源集群: [填集群名]
- 目标集群: [填集群名]

### 步骤

**Part 1: 项目结构**
```bash
python3 mapping/rancher_mapping.py -c [源集群] -o poc.csv
python3 project/rancher_create.py create-project -f poc.csv --dry-run
python3 project/rancher_create.py create-project -f poc.csv
python3 project/rancher_create.py create-ns -f poc.csv --dry-run
python3 project/rancher_create.py create-ns -f poc.csv
```

**Part 2: RBAC 权限**
```bash
python3 rbac/rancher_rbac.py -c [源集群] -o rbac.csv
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster [源集群]=[目标集群] --check-principals
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster [源集群]=[目标集群] --auto-create-users --dry-run
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster [源集群]=[目标集群] --auto-create-users
```

### 注意事项
- 先 dry-run 预览，确认无误再执行
- 自动创建的用户密码在 `user_passwords.txt`
- 如有报错，先检查目标集群和项目是否存在
