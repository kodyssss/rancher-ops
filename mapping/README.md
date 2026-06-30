# Mapping — 集群结构导出

`rancher_mapping.py` — 导出 `集群 → 项目 → Namespace → Labels` 四级映射。

## 使用

```bash
# 全量导出（终端表格）
python3 rancher_mapping.py

# CSV
python3 rancher_mapping.py -o mapping.csv

# JSON
python3 rancher_mapping.py -o mapping.json

# 限定集群
python3 rancher_mapping.py -c poc
```

## 输出

```
CLUSTER    PROJECT    NAMESPACE    LABELS
poc        基础服务    default      env=prod,team=sre
poc        基础服务    monitoring   env=prod
```

## 参数

| 参数 | 说明 |
|---|---|
| `-o` / `--output` | 输出文件 .csv / .json |
| `-c` / `--cluster` | 限定集群 name 或 id |
| `-e` / `--env` | env 文件路径 |
