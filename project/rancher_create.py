#!/usr/bin/env python3
"""
rancher_create.py — 创建/迁移
============================
纯 Rancher v3 API 实现，不依赖 kubectl。

支持三种操作:
  create-project   创建项目（支持 --label 标签）
  create-ns        创建 namespace 并分配给项目
  move-ns          将已有 namespace 迁移到指定项目

用法:
  # 创建项目（支持 name，自动解析为 ID）
  python3 rancher_create.py create-project -c poc -p "项目名"

  # 创建项目并添加标签
  python3 rancher_create.py create-project -c poc -p "项目名" -l env=prod -l team=sre

  # 创建 namespace 并加入项目
  python3 rancher_create.py create-ns -c poc -p "项目名" -n "ns名"

  # 迁移已有 namespace 到另一个项目
  python3 rancher_create.py move-ns -c poc -p "目标项目" -n "已有ns"

  # ── 批量模式（自动识别 .csv / .json）──

  # 从文件批量（推荐 -f，自动识别格式）
  python3 rancher_create.py create-project -f projects.csv
  python3 rancher_create.py create-ns -f namespaces.json
  python3 rancher_create.py move-ns -f move.csv

  # 兼容旧参数
  python3 rancher_create.py create-project --from-csv projects.csv
  python3 rancher_create.py create-project --from-json projects.json

  # 预览不执行
  python3 rancher_create.py create-project -f poc.csv --dry-run

JSON 格式:
  // projects.json (支持 labels 字段)
  [{"cluster":"poc","name":"项目A","labels":{"env":"prod"}}]

  // namespaces.json / move.json
  [{"cluster":"poc","project":"项目A","namespace":"ns1"}]

  // 也支持 mapping 导出的嵌套格式

CSV 格式:
  # create-ns / move-ns 用:
  #   CLUSTER,PROJECT,NAMESPACE
  #   poc,项目A,ns1
  #   poc,项目A,ns2
  #
  # create-project 用 (labels 列可选, 格式 key=value,key=value):
  #   CLUSTER,PROJECT,LABELS
  #   poc,项目A,env=prod,team=sre
  #   poc,项目B,
  #
  # 也支持 mapping 导出的 CLUSTER,PROJECT,NAMESPACE,LABELS 格式

通用参数:
  -e /path/to/env.txt      指定 env 文件（默认同目录）
  -c                       集群用 name 就行（如 poc），脚本自动查 ID
  -p                       项目也用 name
  -l key=value             项目标签，可多次使用（create-project 用）
  -f / --from-file         批量输入文件，自动识别 .csv / .json
  --dry-run                只预览不执行
"""

import os, sys, json, csv, re, time, ssl

try:
    from urllib.request import Request, urlopen, HTTPError
except ImportError:
    from urllib2 import Request, urlopen, HTTPError

MAX_RETRIES     = 3
RETRY_BACKOFF   = 2.0
REQUEST_TIMEOUT = 60
HTTP_PROXY      = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def load_env(env_path=None):
    if env_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(script_dir, "env.txt"),
                  os.path.join(script_dir, "env"),
                  os.path.join(os.getcwd(), "env.txt")]:
            if os.path.isfile(p):
                env_path = p
                break
    if env_path is None:
        die("找不到 env.txt")
    env_vars = {}
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(?:export\s+)?(\w+)=["\']?(.*?)["\']?\s*$', line)
            if m:
                env_vars[m.group(1)] = m.group(2).rstrip('"\'')
    url = env_vars.get("RANCHER_URL", "").rstrip("/")
    token = env_vars.get("RANCHER_TOKEN", "")
    if not url or not token:
        die("env.txt 缺少 RANCHER_URL / RANCHER_TOKEN")
    return url, token


def die(msg):
    print("ERROR: {}".format(msg), file=sys.stderr)
    sys.exit(1)


def parse_labels_arg(label_args):
    """将 -l key=value 列表解析为 dict"""
    labels = {}
    if label_args:
        for l in label_args:
            if "=" in l:
                k, v = l.split("=", 1)
                labels[k.strip()] = v.strip()
            else:
                print("WARN: 忽略无效 label 格式 (需要 key=value): {}".format(l),
                      file=sys.stderr)
    return labels


def labels_to_str(labels):
    """将 labels dict 转为紧凑字符串 key1=val1,key2=val2"""
    if not labels:
        return ""
    return ",".join("{}={}".format(k, v) for k, v in sorted(labels.items()))


def str_to_labels(s):
    """将紧凑字符串转回 labels dict: key1=val1,key2=val2 → {key1: val1, key2: val2}"""
    if not s or not s.strip():
        return {}
    labels = {}
    for part in s.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            labels[k.strip()] = v.strip()
    return labels


def api(url, token, method, path, body=None):
    """通用 API 请求，带重试"""
    full_url = url + "/" + path.lstrip("/")
    data = json.dumps(body).encode("utf-8") if body else None

    for attempt in range(MAX_RETRIES):
        try:
            req = Request(full_url, data=data)
            req.add_header("Authorization", "Bearer {}".format(token))
            req.add_header("Accept", "application/json")
            if data:
                req.add_header("Content-Type", "application/json")
            req.get_method = lambda m=method: m.upper()
            if HTTP_PROXY:
                req.set_proxy(HTTP_PROXY, "https")
            resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=SSL_CTX)
            raw = resp.read().decode("utf-8")
            code = resp.getcode()
            if code in (200, 201, 204):
                return (code, json.loads(raw) if raw else {})
            return (code, json.loads(raw) if raw else {"error": "empty"})
        except HTTPError as e:
            body_err = ""
            try:
                body_err = e.read().decode("utf-8")[:200]
            except:
                pass
            if e.code == 401:
                die("Token 无效 (401)")
            if e.code == 409:
                print("  [SKIP] 已存在: {}".format(path), file=sys.stderr)
                return (409, {"error": "already exists"})
            if e.code in (404, 422, 400):
                return (e.code, {"error": body_err})
            if attempt < MAX_RETRIES - 1:
                print("  [RETRY {}/{}] HTTP {}: {}".format(
                    attempt + 1, MAX_RETRIES, e.code, body_err), file=sys.stderr)
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return (e.code, {"error": body_err})
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return (0, {"error": str(e)})
    return (0, {"error": "max retries"})


def resolve_cluster_id(url, token, name_or_id):
    """通过 name 或 id 解析集群 id"""
    # 直接匹配 id
    path = "v3/clusters/{}".format(name_or_id)
    code, data = api(url, token, "GET", path)
    if code == 200 and data.get("id"):
        return data["id"]

    # 搜索 name
    code, data = api(url, token, "GET", "v3/clusters")
    if code == 200:
        for item in data.get("data", []):
            if item.get("name") == name_or_id or item.get("id") == name_or_id:
                return item["id"]
    die("集群不存在: {}".format(name_or_id))


def resolve_project_id(url, token, cluster_id, name_or_id):
    """通过 name 或 id 解析项目完整 id (cluster:project)"""
    # 已经是完整 id 格式 "cluster:p-xxx"
    if ":" in name_or_id:
        return name_or_id

    # 搜索 cluster 下的项目
    code, data = api(url, token, "GET",
                     "v3/projects?clusterId={}".format(cluster_id))
    if code == 200:
        for item in data.get("data", []):
            if item.get("name") == name_or_id or item.get("id") == name_or_id:
                return item["id"]
    die("项目不存在于集群 {}: {}".format(cluster_id, name_or_id))


def do_create_project(url, token, cluster_name, project_name, labels=None):
    """创建项目 (支持 labels)"""
    cid = resolve_cluster_id(url, token, cluster_name)
    body = {
        "clusterId": cid,
        "name": project_name,
        "type": "project",
    }
    if labels:
        body["labels"] = labels
    code, data = api(url, token, "POST", "v3/projects", body)
    if code in (200, 201):
        label_info = " (labels: {})".format(labels_to_str(labels)) if labels else ""
        print("  [OK] 项目: {} → {}{}".format(project_name, data.get("id", ""), label_info),
              file=sys.stderr)
        return True
    else:
        print("  [FAIL] 创建项目 {}: {}".format(project_name, data.get("message", data)),
              file=sys.stderr)
        return False


def do_create_ns(url, token, cluster_name, project_name, ns_name):
    """创建 namespace 并分配给项目"""
    cid = resolve_cluster_id(url, token, cluster_name)
    pid = resolve_project_id(url, token, cid, project_name)
    body = {
        "name": ns_name,
        "projectId": pid,
        "type": "namespace",
    }
    code, data = api(url, token, "POST",
                     "v3/clusters/{}/namespaces".format(cid), body)
    if code in (200, 201):
        print("  [OK] namespace: {} → {}".format(ns_name, pid), file=sys.stderr)
        return True
    else:
        print("  [FAIL] 创建 ns {}: {}".format(ns_name, data.get("message", data)),
              file=sys.stderr)
        return False


def do_move_ns(url, token, cluster_name, project_name, ns_name):
    """迁移已有 namespace 到指定项目"""
    cid = resolve_cluster_id(url, token, cluster_name)
    pid = resolve_project_id(url, token, cid, project_name)
    body = {"projectId": pid}
    code, data = api(url, token, "POST",
                     "v3/clusters/{}/namespaces/{}?action=move".format(cid, ns_name),
                     body)
    if code in (200, 201, 204):
        print("  [OK] 迁移 {} → {}".format(ns_name, pid), file=sys.stderr)
        return True
    else:
        print("  [FAIL] 迁移 {}: {}".format(ns_name, data.get("message", data)),
              file=sys.stderr)
        return False


# ──────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    if action not in ("create-project", "create-ns", "move-ns"):
        die("未知操作: {} (可用: create-project, create-ns, move-ns)".format(action))

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action_ignored", nargs="*")
    parser.add_argument("-c", "--cluster", help="集群 name 或 id")
    parser.add_argument("-p", "--project", help="项目 name 或 id")
    parser.add_argument("-n", "--namespace", help="namespace 名称")
    parser.add_argument("-l", "--label", action="append",
                        help="项目标签 (格式: key=value, 可多次使用)")
    parser.add_argument("-e", "--env", help="env 文件路径")
    parser.add_argument("-f", "--from-file", help="批量输入文件 (.csv 或 .json，自动识别格式)")
    parser.add_argument("--from-json", help="从 JSON 文件批量操作 (兼容旧参数)")
    parser.add_argument("--from-csv", help="从 CSV 文件批量操作 (兼容旧参数)")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    args = parser.parse_args(sys.argv[1:])

    url, token = load_env(args.env)

    # ── 批量模式 ──
    from_file = args.from_file or args.from_json or args.from_csv
    if from_file:
        ext = os.path.splitext(from_file)[1].lower()
        use_json = bool(args.from_json) or (not args.from_csv and ext == ".json")

        items = []  # 统一格式: [{"cluster":..., "name":..., "namespace":..., "labels":{...}}]

        if use_json:
            # JSON 模式
            try:
                with open(from_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                if ext == ".csv" and not args.from_json:
                    print("WARN: JSON 解析失败，文件是 .csv，自动切换 CSV 模式", file=sys.stderr)
                    use_json = False
                else:
                    die("JSON 解析失败: {}".format(e))

            if use_json:
                # 兼容两种 JSON 格式
                if isinstance(raw, dict) and "clusters" in raw:
                    for cluster in raw["clusters"]:
                        cname = cluster["name"]
                        for project in cluster.get("projects", []):
                            pname = project["name"]
                            p_labels = project.get("labels", {})
                            if isinstance(p_labels, str) and p_labels.strip():
                                p_labels = str_to_labels(p_labels)
                            elif not isinstance(p_labels, dict):
                                p_labels = {}
                            nss = project.get("namespaces", [])
                            if nss:
                                for ns in nss:
                                    items.append({
                                        "cluster": cname,
                                        "name": pname,
                                        "namespace": ns,
                                        "labels": dict(p_labels),
                                    })
                            else:
                                # 项目没有 namespace（create-project 场景）
                                items.append({
                                    "cluster": cname,
                                    "name": pname,
                                    "namespace": None,
                                    "labels": dict(p_labels),
                                })
                else:
                    # 平铺数组
                    for item in raw:
                        il = item.get("labels", {})
                        if isinstance(il, str) and il.strip():
                            il = str_to_labels(il)
                        elif not isinstance(il, dict):
                            il = {}
                        items.append({
                            "cluster": item["cluster"],
                            "name": item.get("project") or item.get("name"),
                            "namespace": item.get("namespace"),
                            "labels": il,
                        })

        if not use_json:
            # CSV 模式
            with open(from_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                raw_rows = list(reader)
            for row in raw_rows:
                cl = (row.get("cluster", "") or row.get("CLUSTER", "")).strip()
                pn = (row.get("project", "") or row.get("PROJECT", "")).strip()
                ns = (row.get("namespace", "") or row.get("NAMESPACE", "")).strip()
                csv_labels = (row.get("labels", "") or row.get("LABELS", "")).strip()
                il = str_to_labels(csv_labels) if csv_labels else {}
                if not cl or not pn:
                    continue
                items.append({
                    "cluster": cl,
                    "name": pn,
                    "namespace": ns or None,
                    "labels": il,
                })

        # ── 统一处理批量条目 ──
        # create-project 模式: 过滤无效条目 + 自动去重
        if action == "create-project":
            # 过滤 (unknown) 伪项目
            items = [it for it in items if it["name"] and it["name"] != "(unknown)"]
            # 去重（同一个集群+项目只创建一次）
            seen = set()
            deduped = []
            for item in items:
                key = (item["cluster"], item["name"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(item)
            if len(deduped) < len(items):
                print("# 去重: {} → {} 条".format(len(items), len(deduped)), file=sys.stderr)
            items = deduped

        ok = 0
        fail = 0
        skip = 0
        for item in items:
            cl = item["cluster"]
            pn = item["name"]
            ns = item["namespace"]
            labels = item["labels"] if item["labels"] else None

            # 命令行 -l 标签合并，命令行优先
            if args.label:
                cmd_labels = parse_labels_arg(args.label)
                if labels:
                    labels.update(cmd_labels)
                else:
                    labels = cmd_labels

            # move-ns: 跳过未知项目（mapping 输出 (unknown) 等）
            if action == "move-ns" and pn.lower() in ("(unknown)", "unknown", "", "-"):
                print("  [SKIP] 项目未知,跳过: cluster={} ns={}".format(cl, ns), file=sys.stderr)
                skip += 1
                continue

            if args.dry_run:
                if action == "create-project":
                    label_str = " labels={}".format(labels_to_str(labels)) if labels else ""
                    print("[DRY] create-project: cluster={} name={}{}".format(cl, pn, label_str))
                elif action == "create-ns":
                    print("[DRY] create-ns: cluster={} project={} namespace={}".format(cl, pn, ns))
                elif action == "move-ns":
                    print("[DRY] move-ns: cluster={} project={} namespace={}".format(cl, pn, ns))
                ok += 1
                continue
            try:
                if action == "create-project":
                    ok += do_create_project(url, token, cl, pn, labels)
                elif action == "create-ns":
                    ok += do_create_ns(url, token, cl, pn, ns)
                elif action == "move-ns":
                    ok += do_move_ns(url, token, cl, pn, ns)
            except SystemExit:
                fail += 1
            except Exception as e:
                print("  [ERR] {}: {}".format(item, e), file=sys.stderr)
                fail += 1
        print("# 完成: {} 成功, {} 跳过, {} 失败".format(ok, skip, fail), file=sys.stderr)
        return

    # ── 单条模式 ──
    if not args.cluster:
        die("缺少 -c/--cluster")
    if not args.project:
        die("缺少 -p/--project")
    if action in ("create-ns", "move-ns") and not args.namespace:
        die("缺少 -n/--namespace")

    # 解析 labels
    labels = parse_labels_arg(args.label) if args.label else None

    if args.dry_run:
        label_str = " labels={}".format(labels_to_str(labels)) if labels else ""
        print("[DRY-RUN] {} cluster={} project={} namespace={}{}".format(
            action, args.cluster, args.project, args.namespace, label_str))
        return

    if action == "create-project":
        do_create_project(url, token, args.cluster, args.project, labels)
    elif action == "create-ns":
        do_create_ns(url, token, args.cluster, args.project, args.namespace)
    elif action == "move-ns":
        do_move_ns(url, token, args.cluster, args.project, args.namespace)


if __name__ == "__main__":
    main()
