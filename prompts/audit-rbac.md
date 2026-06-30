# 提示词：审计 Rancher RBAC 权限

## 复制以下内容给 AI

---

我需要审计 Rancher 集群的 RBAC 权限配置。

代码在 https://github.com/kodyssss/rancher-ops，env.txt 已配置。

### 需求
- 导出所有集群的 global / cluster / project 三层角色绑定
- 帮我分析：哪些用户有超管权限？哪些集群存在无成员的项目？角色分配是否合理？

### 步骤

```bash
# 导出全层级 RBAC
python3 rbac/rancher_rbac.py -o rbac.csv
```

然后请基于 rbac.csv 帮我分析：
1. 哪些用户/组有 global Admin 权限
2. 哪些用户/组是 Cluster Owner（集群级管理员）
3. 哪些项目没有任何成员绑定
4. 是否存在权限过度分配的情况（如所有人都是 Owner）
