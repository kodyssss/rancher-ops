# 提示词：跨集群迁移 RBAC 权限

## 复制以下内容给 AI

---

我有一个 Rancher v3 运维脚本项目，代码在 https://github.com/kodyssss/rancher-ops 。

我需要做跨集群 RBAC 迁移，请按以下步骤操作：

### 环境
- 源集群名: [填集群名]
- 目标集群名: [填集群名]
- env.txt 已配置好目标 Rancher 的 URL 和 Token

### 步骤

1. 用 `rbac/rancher_rbac.py` 从源集群导出 RBAC：
   ```bash
   python3 rbac/rancher_rbac.py -c [源集群名] -o rbac.csv
   ```

2. 预检目标端用户是否存在：
   ```bash
   python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster [源集群名]=[目标集群名] --check-principals
   ```

3. 查看预检结果，确认哪些用户缺失，哪些可以自动创建。

4. 执行迁移（自动创建缺失的本地用户）：
   ```bash
   python3 rbac/rancher_rbac_apply.py --from-csv rbac.csv --map-cluster [源集群名]=[目标集群名] --auto-create-users
   ```

### 注意事项
- 集群角色绑定 (cluster-owner/member 等) 和项目角色绑定会一起迁移
- SSO 用户（Keycloak/AD 等）需要目标端已配置相同认证源
- 自动创建的用户密码会保存在 `user_passwords.txt`
