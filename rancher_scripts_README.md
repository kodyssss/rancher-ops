# Rancher v3 运维脚本使用说明

四个纯 Python 脚本，不依赖 kubectl，直接调 Rancher v3 API。

---

## 前置条件

在脚本同目录放一个 `env.txt`：

```
RANCHER_URL=https://rancher.your-company.com
RANCHER_TOKEN=***
```

或通过 `-e` 指定：

```bash
python3 rancher_mapping.py -e /path/to/env.txt
```

---

## 一、rancher_mapping.py — 集群/项目/NS 映射导出

导出 `集群 → 项目 → namespace → labels` 的四级映射。

```bash
# 终端表格
python3 rancher_mapping.py

# 导出 CSV
python3 rancher_mapping.py -o mapping.csv

# 导出 JSON
python3 rancher_mapping.py -o mapping.json

# 只看某个集群（name 或 id 均可）
python3 rancher_mapping.py -c poc
python3 rancher_mapping.py -c poc -c prod
```

**输出格式：**

```
CLUSTER    PROJECT    NAMESPACE    LABELS
poc        基础服务    default      env=prod,team=sre
poc        基础服务    monitoring   env=prod
```

---

## 二、rancher_create.py — 项目/NS 创建与迁移

三种操作：`create-project` / `create-ns` / `move-ns`。

### 单条操作

```bash
# 创建项目（name 自动解析为 ID）
python3 rancher_create.py create-project -c poc -p "项目名"
python3 rancher_create.py create-project -c poc -p "项目名" -l env=prod -l team=sre

# 创建 namespace
python3 rancher_create.py create-ns -c poc -p "项目名" -n "ns名"

# 迁移 namespace
python3 rancher_create.py move-ns -c poc -p "目标项目" -n "已有ns"
```

### 批量操作

使用 `-f` 自动识别 `.csv` / `.json`：

```bash
# 从 mapping 导出的 CSV 直接创建项目（自动去重）
python3 rancher_mapping.py -c poc -o poc.csv
python3 rancher_create.py create-project -f poc.csv --dry-run
python3 rancher_create.py create-project -f poc.csv

# 从 mapping 导出的 CSV 创建 namespace
python3 rancher_create.py create-ns -f poc.csv --dry-run

# JSON 批量
python3 rancher_create.py create-project -f projects.json
python3 rancher_create.py move-ns -f move.json
```

> `-f` / `--from-file` 是推荐用法。旧参数 `--from-csv` / `--from-json` 仍兼容。
> `create-project` 模式自动去重 + 过滤无效条目。

### 输入格式

**CSV**（也接受 mapping 导出的 `CLUSTER,PROJECT,NAMESPACE,LABELS` 表头）：

```csv
# create-ns / move-ns:
CLUSTER,PROJECT,NAMESPACE
poc,项目A,ns1
poc,项目A,ns2

# create-project（labels 列可选，格式 key=value,key=value）:
CLUSTER,PROJECT,LABELS
poc,项目A,env=prod,team=sre
```

**JSON**（平铺数组或 mapping 嵌套格式均可）：

```json
// 平铺数组
[{"cluster":"poc","name":"项目A","labels":{"env":"prod"}}]

// mapping 嵌套格式也支持
{"clusters":[{"name":"poc","projects":[{"name":"项目A","namespaces":["ns1"]}]}]}
```

---

## 三、rancher_rbac.py — 全层级角色导出

导出 **全局 / 集群 / 项目** 三个层级的用户/组角色绑定，含 displayName 和原始 ID。

```bash
# 终端表格（含 global + cluster + project 三层）
python3 rancher_rbac.py

# 导出 CSV
python3 rancher_rbac.py -o rbac.csv

# 每个集群单独一个文件
python3 rancher_rbac.py -o rbac.csv --per-cluster

# 只看某个集群（name 或 id 均可）
python3 rancher_rbac.py -c poc

# 跳过全局角色
python3 rancher_rbac.py --no-global

# 只看项目级（跳过 global + cluster）
python3 rancher_rbac.py --no-global --no-cluster
```

**输出格式：**

```
LEVEL,CLUSTER,PROJECT,USER_GROUP,TYPE,ROLE,PRINCIPAL_ID,ROLE_ID
global,-,-,admin,User,Admin,user-qlb5m,admin
cluster,poc,-,admin,User,Cluster Owner,user-qlb5m,cluster-owner
project,poc,Default,admin,User,Owner,user-qlb5m,project-owner
project,poc,视频系统,poc222,User,poctest,u-hgc2x,rt-zfftv
project,poc,视频系统,开发组,Group,Member,activedirectory_group://CN=...,project-member
```

| 列 | 说明 |
|---|---|
| `LEVEL` | 绑定层级：`global` / `cluster` / `project` |
| `CLUSTER` | 集群名称（global 为 `-`） |
| `PROJECT` | 项目名称（global / cluster 为 `-`） |
| `USER_GROUP` | 用户/组 displayName（可读） |
| `TYPE` | User / Group |
| `ROLE` | 角色 displayName（通过 API 获取） |
| `PRINCIPAL_ID` | 用户/组原始 ID（apply 脚本直接用） |
| `ROLE_ID` | 角色原始 templateId（apply 脚本直接用） |

**三个层级说明：**

| 层级 | API 资源 | 含义 |
|------|----------|------|
| `global` | GlobalRoleBinding | 谁能登录/管理 Rancher 本身 |
| `cluster` | ClusterRoleTemplateBinding | 谁能访问集群（Cluster Owner/Member/Viewer 等） |
| `project` | ProjectRoleTemplateBinding | 谁能操作项目（Owner/Member/ReadOnly 等） |

---

## 四、rancher_rbac_apply.py — 批量绑定角色

读取 rbac CSV，在目标集群/项目执行角色绑定。支持 project 和 cluster 两个层级。

- 优先使用 `PRINCIPAL_ID` 和 `ROLE_ID` 列（无需名称反查）
- 无 ID 列时回退到 displayName 查找（兼容旧 CSV）
- `global` 层级默认跳过（需手动管理）
- 新版 CSV 的 `LEVEL` 列自动识别，旧 CSV（无 LEVEL 列）默认按 project 处理

```bash
# 预览
python3 rancher_rbac_apply.py --from-csv rbac.csv --dry-run

# 执行
python3 rancher_rbac_apply.py --from-csv rbac.csv

# 跨集群迁移（含集群级绑定）
python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod --dry-run
python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod

# 多个集群映射
python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster "poc=prod,poc23=staging"
```

### 用户缺失处理

跨 Rancher 实例迁移时，目标端可能缺少 CSV 中的用户。提供两种工具：

#### 预检模式 (`--check-principals`)

扫描 CSV 所有用户/组，报告目标端存在/缺失情况：

```bash
python3 rancher_rbac_apply.py --from-csv rbac.csv --check-principals
```

输出示例：
```
  [✓] admin (User, PID=user-qlb5m) — local (by userId)
  [✗] poc222 (User, PID=user-fake88) — 目标端不存在

  缺失本地用户 (可用 --auto-create-users 自动创建):
    - poc222 (user-fake88)
```

#### 自动创建用户 (`--auto-create-users`)

自动创建缺失的**本地用户**（SSO 用户需确保认证源已配置）：

```bash
# 预览（不实际绑定）
python3 rancher_rbac_apply.py --from-csv rbac.csv --auto-create-users --dry-run

# 执行
python3 rancher_rbac_apply.py --from-csv rbac.csv --auto-create-users
```

- 自动生成 16 位随机密码
- 密码保存到 `user_passwords.txt` 文件
- 创建后自动刷新用户缓存，继续绑定角色

---

## 五、组合工作流

### 场景 A：从源集群拷贝 NS 结构到目标集群

```bash
# 1. 导出
python3 rancher_mapping.py -c poc -o structure.csv

# 2. 预览 + 创建项目（自动去重）
python3 rancher_create.py create-project -f structure.csv --dry-run
python3 rancher_create.py create-project -f structure.csv

# 3. 预览 + 创建 namespace
python3 rancher_create.py create-ns -f structure.csv --dry-run
python3 rancher_create.py create-ns -f structure.csv
```

### 场景 B：用 CSV 批量迁移 NS

```bash
python3 rancher_mapping.py -o all.csv
# Excel 编辑 all.csv，修改 PROJECT 列为目标项目名
python3 rancher_create.py move-ns -f all.csv --dry-run
python3 rancher_create.py move-ns -f all.csv
```

### 场景 C：批量创建带标签的项目

```csv
# projects.csv:
CLUSTER,PROJECT,LABELS
poc,前端服务,env=prod,team=fe
poc,后端服务,env=prod,team=be
```

```bash
python3 rancher_create.py create-project -f projects.csv --dry-run
python3 rancher_create.py create-project -f projects.csv
```

### 场景 D：跨集群迁移 RBAC 权限（项目级 + 集群级）

```bash
# 1. 导出源集群全层级 RBAC
python3 rancher_rbac.py -c poc -o rbac_poc.csv

# 2. 预览（项目级 + 集群级绑定一起迁移）
python3 rancher_rbac_apply.py --from-csv rbac_poc.csv --map-cluster poc=prod --dry-run

# 3. 执行
python3 rancher_rbac_apply.py --from-csv rbac_poc.csv --map-cluster poc=prod
```

### 场景 E：完整迁移（结构 + 权限）

```bash
# Step 1: 导出 NS 结构 + 创建
python3 rancher_mapping.py -c poc -o poc.csv
python3 rancher_create.py create-project -f poc.csv
python3 rancher_create.py create-ns -f poc.csv

# Step 2: 导出 RBAC + 应用
python3 rancher_rbac.py -c poc -o rbac.csv
python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod
```

---

## 六、通用参数一览

| 参数 | 脚本 | 说明 |
|------|------|------|
| `-e /path/to/env.txt` | 全部 | 指定 env 文件（默认同目录） |
| `-c` / `--cluster` | mapping / rbac / create | 集群 name 或 id，支持多次 |
| `-p` / `--project` | create | 项目 name 或 id |
| `-n` / `--namespace` | create | namespace 名称 |
| `-l key=value` | create | 标签，可多次使用（create-project） |
| `-o` / `--output` | mapping / rbac | 输出文件 .csv / .json |
| `-f` / `--from-file` | create | 批量输入，自动识别 .csv / .json |
| `--from-csv` | create / rbac_apply | CSV 批量输入（兼容旧参数） |
| `--from-json` | create | JSON 批量输入（兼容旧参数） |
| `--map-cluster` | rbac_apply | 集群名映射 `旧=新,旧2=新2` |
| `--per-cluster` | rbac | 每个集群单独输出文件 |
| `--no-global` | rbac | 跳过全局角色绑定 |
| `--no-cluster` | rbac | 跳过集群级角色绑定 |
| `--check-principals` | rbac_apply | 预检目标端用户/组是否存在 |
| `--auto-create-users` | rbac_apply | 自动创建缺失的本地用户 |
| `--dry-run` | create / rbac_apply | 只预览不执行 |

---

## 注意事项

- 集群和项目均支持 **name**（如 `poc`、`基础服务`），自动解析为 ID
- 自签名证书默认跳过验证
- Token 过期直接报错退出
- 创建已存在的资源打印 `[SKIP]`，不报错
- 分页拉取全量数据，大规模集群无压力
- rbac CSV 的 `PRINCIPAL_ID` + `ROLE_ID` 列让 apply 零名称反查，精确可靠
- `rancher_rbac_apply.py` 中 `global` 层级绑定默认跳过不自动应用
