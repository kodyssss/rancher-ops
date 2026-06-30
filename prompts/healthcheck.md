# 提示词：巡检 Rancher 集群健康状态

## 复制以下内容给 AI

---

我要巡检 Rancher 集群的健康状态。

代码在 https://github.com/kodyssss/rancher-ops，env.txt 已配置。

### 步骤

```bash
# 全量巡检
python3 healthcheck/rancher_healthcheck.py

# 如果要做定期巡检，导出 Markdown 报告
python3 healthcheck/rancher_healthcheck.py -o report_$(date +%Y%m%d).md

# 单个集群
python3 healthcheck/rancher_healthcheck.py -c [集群名]
```

### 检查内容

- 🔴 集群/节点是否 Ready
- 🟡 节点资源压力、Kubelet 版本一致性
- 🟢 空项目、无管理员项目
- 📊 节点 CPU/内存/Pods 容量
- 🔐 集群级 RBAC 概览

### 接入 CI/CD

脚本退出码：
- `0` = 一切正常
- `1` = 存在 CRITICAL 问题
