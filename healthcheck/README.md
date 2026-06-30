# Healthcheck — 集群巡检

`rancher_healthcheck.py` — 巡检 local 集群 + 所有下游集群的健康状态。

## 检查项

| 类别 | 内容 |
|---|---|
| 集群状态 | state、conditions (Ready/Connected/AgentDeployed 等) |
| 节点健康 | Ready 状态、CPU/内存/Pods、Kubelet 版本一致性 |
| 项目巡检 | 项目状态、无成员项目 |
| RBAC 概览 | 集群级角色绑定统计 |

## 使用

```bash
# 终端报告
python3 rancher_healthcheck.py

# 导出 Markdown
python3 rancher_healthcheck.py -o report.md

# JSON 格式（可接入监控）
python3 rancher_healthcheck.py --json

# 只查某集群
python3 rancher_healthcheck.py -c poc
```

## 风险等级

| 图标 | 等级 | 含义 |
|---|---|---|
| 🔴 CRITICAL | 严重 | 集群/节点 NotReady |
| 🟡 WARNING | 警告 | 节点压力、版本不一致 |
| 🟢 INFO | 提示 | 空项目、无管理员 |

## 退出码

- `0` — 无严重问题
- `1` — 存在 CRITICAL 级别问题（可接入 CI/CD）

## 参数

| 参数 | 说明 |
|---|---|
| `-o` / `--output` | 输出文件 .md / .json |
| `-c` / `--cluster` | 限定集群 name 或 id |
| `--json` | JSON 格式 |
| `-e` / `--env` | env 文件 |
