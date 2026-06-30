# Healthcheck — 集群巡检

`rancher_healthcheck.py` — 巡检 local 集群 + 所有下游集群的健康状态。

## 检查项

| 类别 | 内容 |
|---|---|
| 🔍 集群状态 | state、Ready 条件、Provider 类型、K8s 版本 |
| ⚙️ 控制平面 | etcd、scheduler、controller-manager 健康 |
| 🖧 节点健康 | Ready 状态、Disk/Memory/PID 压力、Kubelet 版本一致性、Runtime 版本 |
| 🌐 CNI 巡检 | CNI 类型识别（Canal/Calico/Flannel/Cilium）、DaemonSet 就绪 |
| 💾 CSI 巡检 | StorageClass、CSI Driver 状态、PVC 是否全部 Bound |
| 🔧 系统组件 | CoreDNS、Ingress Controller、Metrics Server 健康 |
| 📦 工作负载 | Deployment/DaemonSet/StatefulSet 副本匹配 |
| 🔄 Pod 健康 | 高重启 Pod (>20次)、Failed Pod |
| 📋 事件 | 最近 Warning 事件 |
| 🔐 RBAC 概览 | 集群级角色绑定统计 |
| 📊 项目检查 | 项目状态、无成员项目 |

## 使用

```bash
# 全量巡检（含 k8s API 深度检查）
python3 rancher_healthcheck.py

# 导出 Markdown 报告
python3 rancher_healthcheck.py -o report.md

# 只看 Rancher 级别（跳过 k8s API 深度检查，更快）
python3 rancher_healthcheck.py --no-deep

# JSON 格式（可接入监控）
python3 rancher_healthcheck.py --json

# 只查某集群
python3 rancher_healthcheck.py -c poc
```

## 风险等级

| 图标 | 等级 | 含义 | 触发条件 |
|---|---|---|---|
| 🔴 CRITICAL | 严重 | 需立即处理 | 集群/节点 NotReady, 控制平面组件异常 |
| 🟡 WARNING | 警告 | 需关注 | 节点资源压力、版本不一致、高重启、副本不匹配 |
| 🟢 INFO | 提示 | 可优化 | 空项目、无管理员、Warning 事件 |

## 退出码

- `0` — 无严重问题
- `1` — 存在 CRITICAL 级别问题（可接入 CI/CD）

## 参数

| 参数 | 说明 |
|---|---|
| `-o` / `--output` | 输出文件 .md / .json |
| `-c` / `--cluster` | 限定集群 name 或 id |
| `--json` | JSON 格式 |
| `--no-deep` | 跳过 k8s API 深度检查 |
| `-e` / `--env` | env 文件 |
