# 提示词：完整迁移

## 场景 A：跨 Rancher 迁移（不同 SSO）

复制以下内容给 AI：

---

下游集群要从源 Rancher 迁移到目标 Rancher 管理，两个 Rancher SSO 不同。
下游集群已存在、NS 不动，只迁移项目结构和 RBAC 权限。

代码在 https://github.com/kodyssss/rancher-ops，env.txt 已配置。

### 参数
- 源 Rancher: [填 URL]
- 目标 Rancher: [填 URL]
- 集群名: [填集群名]

### 步骤

**Part 1: 从源 Rancher 导出**
```bash
# 切到源 Rancher 的 env.txt
python3 mapping/rancher_mapping.py -c [集群名] -o mapping.csv
python3 rbac/rancher_rbac.py -c [集群名] -o rbac.csv
```

**Part 2: 手动操作**
- 从源 Rancher 删除/分离该下游集群
- 在目标 Rancher UI → Import Existing → 导入集群

**Part 3: 目标端创建项目**
```bash
# 切到目标 Rancher 的 env.txt
python3 project/rancher_create.py create-project -f mapping.csv --dry-run
python3 project/rancher_create.py create-project -f mapping.csv
```

**Part 4: 已有 NS 归入项目**
```bash
python3 project/rancher_create.py move-ns -f mapping.csv --dry-run
python3 project/rancher_create.py move-ns -f mapping.csv
```

**Part 5: RBAC 用户映射 + 绑定**
```bash
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --auto-map-users --auto-create-users --dry-run
python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --auto-map-users --auto-create-users
```

### 注意事项
- `move-ns` 会自动跳过 mapping 输出为 `(unknown)` 的项目
- `--auto-map-users` 按 displayName 匹配目标端用户，SSO 未匹配的需手动处理
- `--auto-create-users` 只创建本地用户，SSO 用户需在新 Rancher 登录后重试
- 自动创建的用户密码在 `user_passwords.txt`
- 先 dry-run 预览，确认无误再执行

---

## 场景 B：同 Rancher 跨集群迁移（相同 SSO）

复制以下内容给 AI：

---

我要从 Rancher 源集群完整迁移到目标集群，包括项目结构、Namespace 和 RBAC 权限。
两个集群在同一个 Rancher 下，SSO 用户 ID 一致。

代码在 https://github.com/kodyssss/rancher-ops，env.txt 已配置。

### 参数
- 源集群: [填集群名]
- 目标集群: [填集群名]

### 步骤

**Part 1: 项目结构**
```bash
python3 mapping/rancher_mapping.py -c [源集群] -o mapping.csv
python3 project/rancher_create.py create-project -f mapping.csv --dry-run
python3 project/rancher_create.py create-project -f mapping.csv
python3 project/rancher_create.py create-ns -f mapping.csv --dry-run
python3 project/rancher_create.py create-ns -f mapping.csv
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
