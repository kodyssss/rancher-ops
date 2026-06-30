# 提示词：拷贝集群结构

## 复制以下内容给 AI

---

我需要从 Rancher 集群拷贝项目/Namespace 结构到另一个集群。

代码在 https://github.com/kodyssss/rancher-ops，env.txt 已配置。

### 参数
- 源集群: [填集群名]
- 目标集群: [填集群名]

### 步骤

1. 导出源集群结构：
   ```bash
   python3 mapping/rancher_mapping.py -c [源集群名] -o structure.csv
   ```

2. 预览要创建的项目（自动去重）：
   ```bash
   python3 project/rancher_create.py create-project -f structure.csv --dry-run
   ```

3. 创建项目：
   ```bash
   python3 project/rancher_create.py create-project -f structure.csv
   ```

4. 预览要创建的 Namespace：
   ```bash
   python3 project/rancher_create.py create-ns -f structure.csv --dry-run
   ```

5. 创建 Namespace：
   ```bash
   python3 project/rancher_create.py create-ns -f structure.csv
   ```

### 可选
- 如果项目需要映射到不同的集群名，编辑 structure.csv 的 CLUSTER 列
- 如果需要带标签创建项目，编辑 structure.csv 添加 LABELS 列（格式: key=value,key=value）
