# healthcheck — 集群巡检

## 脚本

`rancher_healthcheck.py` — 完整的 Rancher 集群健康检查，支持 Markdown/JSON 输出。

## 用法

```bash
python3 rancher_healthcheck.py                    # 终端报告
python3 rancher_healthcheck.py -o report.md       # Markdown 报告
python3 rancher_healthcheck.py -o report.json     # JSON 输出
python3 rancher_healthcheck.py --json             # JSON 到终端
python3 rancher_healthcheck.py -c poc             # 限定集群
python3 rancher_healthcheck.py --no-deep          # 跳过 k8s API 深度检查
```

## 检查项

| 类别 | 检查内容 |
|------|----------|
| 集群状态 | Active/Ready、K8s 版本、Provider 类型 |
| 控制平面 | etcd/apiserver/scheduler/controller-manager Pod 数量一致性、HA 检测 |
| 节点健康 | Ready 状态、资源压力(Mem/Disk/PID)、Kubelet 版本偏移、容器运行时一致性 |
| CNI | CNI 类型识别(Canal/Calico/Flannel/Cilium 等)、DaemonSet 就绪 |
| CSI | CSI 驱动/StorageClass/PVC Bound 状态 |
| 系统组件 | CoreDNS、Ingress、Metrics-server 就绪 |
| 工作负载 | Deployment/DaemonSet/StatefulSet 副本、高重启 Pod(>20次) |
| 事件 | 控制平面/etcd Warning 事件、普通 Warning 事件 |
| RBAC | 集群级角色绑定概览、项目成员检查 |
| Rancher 组件 | cattle-system/fleet/capi Pod 健康 |

## 风险等级

| 图标 | 等级 | 说明 |
|------|------|------|
| 🔴 CRITICAL | 严重 | 集群不可用/控制平面异常，exit code 1 |
| 🟡 WARNING | 警告 | 节点压力/版本不一致/副本异常 |
| 🟢 INFO | 信息 | 空项目/无管理员等，不影响运行 |
| ✅ OK | 正常 | 一切正常 |

## `--no-deep` 模式

跳过所有 k8s API 调用，仅使用 Rancher API 检查：
- 集群状态、节点（Rancher 层面）、项目/RBAC
- 不检查 Pod、DaemonSet、事件、CNI、CSI 等

适合快速扫描或 k8s API 不可达时使用。

## 说明

- 支持自签名证书（内网 Rancher 常见）
- 支持 HTTP 代理（通过 `HTTPS_PROXY` 环境变量）
- 控制平面组件通过 Pod 名前缀识别（etcd- / kube-apiserver- / kube-controller-manager- / kube-scheduler-）
- PVC 只标记 Not Bound 状态（Pending 等），Loss 状态需人工介入
