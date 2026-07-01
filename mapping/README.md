# mapping — 集群结构导出

## 脚本

`rancher_mapping.py` — 导出集群 → 项目 → Namespace 三级映射（含 Labels）。

## 用法

```bash
python3 rancher_mapping.py                        # 终端表格
python3 rancher_mapping.py -o mapping.csv         # CSV 输出（utf-8-sig）
python3 rancher_mapping.py -o mapping.json        # JSON 输出
python3 rancher_mapping.py -c poc                 # 限定集群
```

## 输出格式

**CSV**: `CLUSTER,PROJECT,NAMESPACE,LABELS`

```
CLUSTER,PROJECT,NAMESPACE,LABELS
poc,Default,default,
poc,Default,kube-system,
poc,my-app,app-frontend,env=prod,team=sre
poc,(unknown),cattle-system,   ← 未分配项目的 NS
```

**JSON**: 嵌套结构，含 clusters → projects → namespaces + labels。

## 用途

1. 复制项目结构 → 给 `project/rancher_create.py` 做批量输入
2. 审查 NS 归属 → 找出未分配项目的 NS（标记为 `(unknown)`）
3. 迁移参考 → 了解目标结构全貌

## 关联

- `project/rancher_create.py`: 用 mapping 输出批量创建项目和 NS
- `rbac/rancher_rbac.py`: 配合 mapping 了解项目成员分布
