#!/usr/bin/env python3
"""
rancher_mapping.py — NS 映射导出
================================
纯 Rancher v3 API 实现，不依赖 kubectl，可在任意 Linux 执行。

用法:
  # 终端表格
  python3 rancher_mapping.py

  # 导出 CSV
  python3 rancher_mapping.py -o mapping.csv

  # 导出 JSON
  python3 rancher_mapping.py -o mapping.json

  # 只查某个集群
  python3 rancher_mapping.py -c poc

输出: 集群名 | 项目名 | namespace | labels

通用参数:
  -e /path/to/env.txt  指定 env 文件（默认同目录）
  -c                   集群用 name 就行（如 poc），脚本自动查 ID

env 文件格式:
  export RANCHER_URL=https://rancher.example.com
  export RANCHER_TOKEN=***
"""

import os
import sys
import json
import csv
import re
import time
import ssl
from io import StringIO

# Python 2/3 兼容
try:
    from urllib.request import Request, urlopen, HTTPError, URLError
    from urllib.parse   import urljoin
except ImportError:
    from urllib2 import Request, urlopen, HTTPError, URLError
    from urlparse import urljoin

# ── 可调参数 ───────────────────────────────────
PAGE_SIZE       = 1000       # 每页条数
MAX_RETRIES     = 3          # 失败重试次数
RETRY_BACKOFF   = 2.0        # 重试退避倍数 (1s → 2s → 4s)
REQUEST_TIMEOUT = 60         # 单次 HTTP 超时 (秒)
HTTP_PROXY      = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))
# ────────────────────────────────────────────────


def load_env(env_path=None):
    """从 env 文件加载 RANCHER_URL 和 RANCHER_TOKEN"""
    if env_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(script_dir, "env.txt"),
            os.path.join(script_dir, "env"),
            os.path.join(os.getcwd(), "env.txt"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                env_path = p
                break

    if env_path is None:
        print("ERROR: 找不到 env.txt，请放到脚本同目录或用 -e 指定", file=sys.stderr)
        sys.exit(1)

    env_vars = {}
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(?:export\s+)?(\w+)=["\']?(.*?)["\']?\s*$', line)
            if m:
                env_vars[m.group(1)] = m.group(2).rstrip('"\'')
            else:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    env_vars[parts[0].strip()] = parts[1].strip().strip('"\'')

    url = env_vars.get("RANCHER_URL", "").rstrip("/")
    token = env_vars.get("RANCHER_TOKEN", "")
    if not url or not token:
        print("ERROR: env 文件中缺少 RANCHER_URL 或 RANCHER_TOKEN", file=sys.stderr)
        sys.exit(1)
    return url, token


def api_get(url, token, path):
    """调用 Rancher v3 API，带重试"""
    # 安全的 URL 拼接
    full_url = url.rstrip("/") + "/" + path.lstrip("/")

    for attempt in range(MAX_RETRIES):
        try:
            req = Request(full_url)
            req.add_header("Authorization", "Bearer {}".format(token))
            req.add_header("Accept", "application/json")
            req.add_header("User-Agent", "rancher-mapping/1.0")

            # 支持 HTTP_PROXY
            if HTTP_PROXY:
                req.set_proxy(HTTP_PROXY, "https")

            # 忽略自签名证书（内网 Rancher 常见）
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx)
            return json.loads(resp.read().decode("utf-8"))

        except HTTPError as e:
            if e.code == 401:
                print("ERROR: Rancher Token 无效或已过期 (401)", file=sys.stderr)
                return None
            if e.code == 404:
                print("WARN: 资源不存在 {} (404)".format(path), file=sys.stderr)
                return None
            if e.code == 429:  # Rate limit
                wait = RETRY_BACKOFF ** attempt
                print("WARN: 请求限流 (429)，{} 秒后重试 {}/{}".format(
                    wait, attempt + 1, MAX_RETRIES), file=sys.stderr)
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES - 1:
                print("WARN: HTTP {} on {}, retrying {}/{}".format(
                    e.code, path, attempt + 1, MAX_RETRIES), file=sys.stderr)
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            print("WARN: API 调用失败 (HTTP {}) {}: {}".format(e.code, path, e), file=sys.stderr)
            return None

        except URLError as e:
            if attempt < MAX_RETRIES - 1:
                print("WARN: 网络错误, 重试 {}/{}: {}".format(
                    attempt + 1, MAX_RETRIES, e.reason), file=sys.stderr)
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            print("WARN: 网络不可达 {}: {}".format(path, e.reason), file=sys.stderr)
            return None

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            print("WARN: 解析失败 {}: {}".format(path, e), file=sys.stderr)
            return None

    return None


def api_get_paginated(url, token, path):
    """
    分页获取全量数据。
    Rancher v3 API 使用 limit + continue 游标分页。
    返回所有 data[] 条目的列表。
    """
    all_items = []
    page = 0
    marker = None

    while True:
        paged_path = "{}?limit={}".format(path, PAGE_SIZE)
        if marker:
            paged_path += "&continue={}".format(marker)

        data = api_get(url, token, paged_path)
        if not data:
            break

        items = data.get("data", [])
        if not items:
            break

        all_items.extend(items)
        page += 1

        # 检查是否有下一页
        pagination = data.get("pagination", {})
        if pagination.get("next"):
            # Rancher 用 pagination.next 标记
            marker = pagination.get("next")
        elif pagination.get("partial", False):
            # 兼容部分版本: partial=true + 有 more
            marker = items[-1].get("id", "")
        else:
            # 没有更多了
            break

        # 防止死循环: 如果返回数量不足一页说明已经到底
        if len(items) < PAGE_SIZE:
            break

        # 进度提示
        if page % 5 == 0:
            print("  ... 已拉取 {} 条 ({})".format(len(all_items), path),
                  file=sys.stderr)

    return all_items


def get_clusters(url, token):
    """获取所有集群: {cluster_id: cluster_name}"""
    items = api_get_paginated(url, token, "v3/clusters")
    clusters = {}
    for item in items:
        clusters[item["id"]] = item.get("name", item["id"])
    return clusters


def get_projects(url, token):
    """获取所有项目: {project_full_id: (project_name, cluster_id, labels_dict)}"""
    items = api_get_paginated(url, token, "v3/projects")
    projects = {}
    for item in items:
        pid = item["id"]
        pname = item.get("name", pid)
        cid = item.get("clusterId", "")
        labels = item.get("labels", {})
        projects[pid] = (pname, cid, labels)
    return projects


def get_namespaces(url, token, cluster_id):
    """获取某个集群的所有 namespace → projectId 映射（分页 + 流式）"""
    items = api_get_paginated(
        url, token, "v3/clusters/{}/namespaces".format(cluster_id)
    )
    ns_list = []
    for item in items:
        ns_name = item.get("name", item.get("id", ""))
        project_id = item.get("projectId", "")
        ns_list.append((ns_name, project_id))
    return ns_list


def labels_to_str(labels):
    """将 labels dict 转为紧凑字符串: key1=val1,key2=val2"""
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


def build_mapping(url, token, cluster_filter=None):
    """
    构建 集群 → 项目 → namespace 三级映射。
    这是一个生成器，逐条产出 (cluster, project, namespace, labels_str)。
    """
    clusters = get_clusters(url, token)
    projects = get_projects(url, token)

    proj_index = {}
    for pid, (pname, cid, labels) in projects.items():
        proj_index[pid] = {"name": pname, "cluster_id": cid, "labels": labels}

    # 将 cluster_filter 中的 name 解析为 id（支持 -c poc 这种用法）
    if cluster_filter:
        expanded = set()
        for f in cluster_filter:
            expanded.add(f)  # 保留原值（可能是 id）
            for cid, cname in clusters.items():
                if cname == f:
                    expanded.add(cid)
        cluster_filter = expanded

    for cid in sorted(clusters.keys()):
        if cluster_filter and cid not in cluster_filter:
            continue
        cname = clusters[cid]

        ns_items = get_namespaces(url, token, cid)
        for ns_name, project_id in ns_items:
            pinfo = proj_index.get(project_id, {})
            pname = pinfo.get("name") if pinfo else None
            if not pname:
                pname = project_id or "(unknown)"
            p_labels = pinfo.get("labels", {})
            labels_str = labels_to_str(p_labels)
            yield (cname, pname, ns_name, labels_str)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Rancher 集群-项目-命名空间映射导出 (含 labels)"
    )
    parser.add_argument("-o", "--output", help="输出文件 (.csv 或 .json)")
    parser.add_argument("-e", "--env", help="env 文件路径 (默认同目录 env.txt)")
    parser.add_argument("-c", "--cluster", help="只查询指定集群 (可多次使用)", action="append")
    parser.add_argument("--no-ssl-verify", help="忽略 SSL 证书校验 (自签名证书)",
                        action="store_true", default=True)
    args = parser.parse_args()

    url, token = load_env(args.env)
    cluster_filter = set(args.cluster) if args.cluster else None

    print("# Rancher: {}".format(url), file=sys.stderr)
    print("# 正在查询...", file=sys.stderr)

    # ── 流式收集，按需排序 ──
    if args.output:
        ext = os.path.splitext(args.output)[1].lower()

        if ext == ".json":
            # JSON: 需要全量聚合后一次性写入
            clusters_map = {}
            proj_labels = {}  # (cluster, project) → labels_str
            count = 0
            for cname, pname, nsname, labels_str in build_mapping(url, token, cluster_filter):
                clusters_map.setdefault(cname, {}).setdefault(pname, []).append(nsname)
                proj_labels[(cname, pname)] = labels_str
                count += 1
            result = {
                "rancher_url": url,
                "clusters": [
                    {
                        "name": cn,
                        "projects": [
                            {
                                "name": pn,
                                "namespaces": sorted(ns),
                                "labels": proj_labels.get((cn, pn), ""),
                            }
                            for pn, ns in sorted(projects.items())
                        ]
                    }
                    for cn, projects in sorted(clusters_map.items())
                ]
            }
            with open(args.output, "w") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print("已输出: {} ({} 条映射)".format(args.output, count), file=sys.stderr)

        else:
            # CSV: 流式写入，不占内存
            count = 0
            with open(args.output, "w") as f:
                writer = csv.writer(f)
                writer.writerow(["CLUSTER", "PROJECT", "NAMESPACE", "LABELS"])
                rows = list(build_mapping(url, token, cluster_filter))
                rows.sort(key=lambda x: (x[0] or "", x[1] or "", x[2] or ""))
                writer.writerows(rows)
                count = len(rows)
            print("已输出: {} ({} 行)".format(args.output, count), file=sys.stderr)
    else:
        # 终端: 流式输出
        print("{:<25} {:<25} {:<25} {}".format("CLUSTER", "PROJECT", "NAMESPACE", "LABELS"))
        print("{:<25} {:<25} {:<25} {}".format("-------", "-------", "---------", "------"))
        count = 0
        rows = list(build_mapping(url, token, cluster_filter))
        rows.sort(key=lambda x: (x[0] or "", x[1] or "", x[2] or ""))
        for cname, pname, nsname, labels_str in rows:
            print("{:<25} {:<25} {:<25} {}".format(
                cname or "(unknown)",
                pname or "(unknown)",
                nsname or "(unknown)",
                labels_str or "",
            ))
            count += 1
        print("# 共 {} 条映射".format(count), file=sys.stderr)


if __name__ == "__main__":
    main()
