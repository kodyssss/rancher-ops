# rbac — 角色管理

## 脚本

| 脚本 | 功能 |
|------|------|
| `rancher_rbac.py` | 导出源端 RBAC (global/cluster/project 三层) |
| `rancher_user.py` | 获取目标端所有用户清单 |
| `rancher_rbac_bind.py` | 单用户绑定 (命令行传参) |
| `rbac_batch.py` | 批量调用 bind.py (读 CSV 逐行绑定) |
| `test_user.py` | 快速验证用户 API |

## 完整工作流

```
源 Rancher                          目标 Rancher
─────────                           ─────────

① rancher_rbac.py                     → rbac.csv
   rancher_mapping.py                 → mapping.csv
       │
② 源 Rancher 下线集群
       │
③ 修改 cleanup job → apply
       │
                                   ④ Import 集群
                                   ⑤ create-project + move-ns
       │
                                   ⑥ rancher_user.py
                                      查看用户清单
       │
                                   ⑦ rbac_batch.py --csv rbac.csv
                                      逐条调用 rancher_rbac_bind.py
```

### 命令汇总

```bash
# === 源端 ===
python3 rbac/rancher_rbac.py -c [集群名] -o rbac.csv
python3 mapping/rancher_mapping.py -c [集群名] -o mapping.csv

# === 目标端 ===
python3 project/rancher_create.py create-project -f mapping.csv
python3 project/rancher_create.py move-ns -f mapping.csv

# 查看用户
python3 rbac/rancher_user.py
python3 rbac/rancher_user.py -o users.csv

# 批量绑定 (先预览)
python3 rbac/rbac_batch.py --csv rbac.csv --dry-run
python3 rbac/rbac_batch.py --csv rbac.csv
```

## rancher_rbac.py

```bash
python3 rancher_rbac.py                     # 终端表格
python3 rancher_rbac.py -c 集群名 -o rbac.csv  # CSV
```

## rancher_user.py

```bash
python3 rancher_user.py                     # 终端表格
python3 rancher_user.py -o users.csv        # CSV
```

## rancher_rbac_bind.py

```bash
# 项目角色
python3 rancher_rbac_bind.py -c poc -u "用户名" -p Default --role Owner

# 集群角色
python3 rancher_rbac_bind.py -c poc -u "admin" --clusterrole "Cluster Owner"
```

## rbac_batch.py

```bash
python3 rbac_batch.py --csv rbac.csv --dry-run
python3 rbac_batch.py --csv rbac.csv
python3 rbac_batch.py --csv rbac.csv --skip-clusterrole  # 只绑项目角色
```

读取 rbac.csv，逐行调用 `rancher_rbac_bind.py`。
