#!/usr/bin/env python3
"""
rancher_healthcheck.py — Rancher 集群巡检
==========================================
巡检 local 集群 + 所有下游集群的健康状态。

检查项:
  🔍 集群状态      — 是否 active、conditions 是否全部正常
  🔍 节点健康      — 节点 Ready/资源压力/K8s版本一致性
  🔍 项目巡检      — 项目状态、无成员项目、空项目
  🔍 RBAC 概览     — 各角色用户/组数量
  🔍 风险提示      — 汇总所有不健康的项

用法:
  python3 rancher_healthcheck.py              # 终端报告
  python3 rancher_healthcheck.py -o report.md # 输出 Markdown
  python3 rancher_healthcheck.py -c poc       # 只查某集群
  python3 rancher_healthcheck.py --json       # JSON 格式

env 文件: 同目录 env.txt
"""

import os, sys, json, re, time, ssl
from collections import defaultdict, Counter

try:
    from urllib.request import Request, urlopen, HTTPError
    from urllib.parse import quote
except ImportError:
    from urllib2 import Request, urlopen, HTTPError
    from urllib import quote

PAGE_SIZE       = 1000
MAX_RETRIES     = 3
RETRY_BACKOFF   = 2.0
REQUEST_TIMEOUT = 60
HTTP_PROXY      = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ── 风险等级 ──
CRITICAL = "🔴 CRITICAL"
WARNING  = "🟡 WARNING"
INFO     = "🟢 INFO"
OK       = "✅ OK"


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


def api_get(url, token, path):
    full = url.rstrip("/") + "/" + path.lstrip("/")
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(full)
            req.add_header("Authorization", "Bearer {}".format(token))
            req.add_header("Accept", "application/json")
            if HTTP_PROXY:
                req.set_proxy(HTTP_PROXY, "https")
            resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=SSL_CTX)
            return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                return None
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None
    return None


def api_paginated(url, token, path):
    all_items = []
    page = 0
    marker = None
    sep = "&" if "?" in path else "?"
    while True:
        p = "{}{}limit={}".format(path, sep, PAGE_SIZE)
        if marker:
            p += "&continue={}".format(marker)
        data = api_get(url, token, p)
        if not data:
            break
        items = data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        page += 1
        pagination = data.get("pagination", {})
        if pagination.get("next"):
            marker = pagination["next"]
        elif len(items) < PAGE_SIZE:
            break
        else:
            marker = items[-1].get("id", "")
            if page > 1 and len(all_items) >= PAGE_SIZE * page:
                break
    return all_items


def get_rancher_version(url, token):
    data = api_get(url, token, "v3/settings/server-version")
    return data.get("value", "unknown") if data else "unknown"


def get_clusters(url, token):
    items = api_paginated(url, token, "v3/clusters")
    return items


def get_nodes(url, token, cluster_id):
    return api_paginated(url, token, "v3/nodes?clusterId={}".format(cluster_id))


def get_projects(url, token, cluster_id):
    return api_paginated(url, token, "v3/projects?clusterId={}".format(cluster_id))


def get_project_rbac(url, token, project_id):
    p = "v3/projectroletemplatebindings?projectId={}".format(project_id)
    return api_paginated(url, token, p)


def get_cluster_rbac(url, token, cluster_id):
    p = "v3/clusterRoleTemplateBindings?clusterId={}".format(cluster_id)
    return api_paginated(url, token, p)


def check_cluster(url, token, cluster):
    """检查单个集群，返回结果字典"""
    cid   = cluster["id"]
    cname = cluster.get("name", cid)
    state = cluster.get("state", "unknown")
    is_local = cid == "local"

    result = {
        "id": cid,
        "name": cname,
        "local": is_local,
        "state": state,
        "conditions": {},
        "nodes": [],
        "projects": [],
        "rbac": {},
        "issues": [],
    }

    # ── 集群条件 ──
    conditions = cluster.get("conditions", [])
    bad_conditions = []
    for c in conditions:
        ctype = c.get("type", "?")
        cstatus = c.get("status", "Unknown")
        result["conditions"][ctype] = cstatus
        if cstatus not in ("True",):
            bad_conditions.append("{}={}".format(ctype, cstatus))

    # 关键条件检查
    if "Ready" in result["conditions"]:
        if result["conditions"]["Ready"] != "True":
            result["issues"].append({
                "level": CRITICAL,
                "item": "Cluster Ready",
                "detail": "集群 {} 状态: {}".format(cname, result["conditions"]["Ready"]),
            })

    if state not in ("active",):
        result["issues"].append({
            "level": CRITICAL,
            "item": "Cluster State",
            "detail": "集群 {} state={}".format(cname, state),
        })

    # 其他异常条件
    for bc in bad_conditions:
        result["issues"].append({
            "level": WARNING,
            "item": "Cluster Condition",
            "detail": "{}: {}".format(cname, bc),
        })

    # ── 节点检查 ──
    nodes = get_nodes(url, token, cid)
    kubelet_versions = set()
    kernel_versions  = set()

    for n in nodes:
        nid   = n["id"]
        nhost = n.get("hostname", n.get("nodeName", nid.split(":")[-1]))
        nstate = n.get("state", "?")
        info = n.get("info", {})
        cpu = info.get("cpu", {})
        mem = info.get("memory", {})
        os_info = info.get("os", {})
        k8s = info.get("kubernetes", {})
        alloc = n.get("allocatable", {})
        capa  = n.get("capacity", {})

        kubelet_version = k8s.get("kubeletVersion", "?") if isinstance(k8s, dict) else "?"
        kernel_version  = os_info.get("kernelVersion", "?") if isinstance(os_info, dict) else "?"
        docker_version  = os_info.get("dockerVersion", "?") if isinstance(os_info, dict) else "?"

        if kubelet_version and kubelet_version != "?":
            kubelet_versions.add(kubelet_version)
        if kernel_version and kernel_version != "?":
            kernel_versions.add(kernel_version)

        node_info = {
            "id": nid,
            "hostname": nhost,
            "state": nstate,
            "cpu_cores": cpu.get("count", "?"),
            "memory_kib": mem.get("memTotalKiB", "?"),
            "kubelet": kubelet_version,
            "kernel": kernel_version,
            "docker": docker_version,
            "alloc_cpu": alloc.get("cpu", "?"),
            "alloc_mem": alloc.get("memory", "?"),
            "alloc_pods": alloc.get("pods", "?"),
            "capacity_cpu": capa.get("cpu", "?"),
            "capacity_mem": capa.get("memory", "?"),
            "capacity_pods": capa.get("pods", "?"),
            "conditions": {},
        }

        for cond in n.get("conditions", []):
            ct = cond.get("type", "")
            cs = cond.get("status", "Unknown")
            node_info["conditions"][ct] = cs
            if ct not in ("Registered", "Provisioned"):
                if ct == "Ready" and cs != "True":
                    result["issues"].append({
                        "level": CRITICAL,
                        "item": "Node NotReady",
                        "detail": "{} ({})".format(nhost, cname),
                    })
                elif ct in ("MemoryPressure", "DiskPressure", "PIDPressure") and cs == "True":
                    result["issues"].append({
                        "level": WARNING,
                        "item": "Node {}".format(ct),
                        "detail": "{} ({})".format(nhost, cname),
                    })

        if nstate not in ("active",):
            result["issues"].append({
                "level": WARNING,
                "item": "Node State",
                "detail": "{} state={} ({})".format(nhost, nstate, cname),
            })

        result["nodes"].append(node_info)

    # 版本一致性
    if len(kubelet_versions) > 1:
        result["issues"].append({
            "level": WARNING,
            "item": "Kubelet Version Mismatch",
            "detail": "{}: {}".format(cname, ", ".join(sorted(kubelet_versions))),
        })

    # ── 项目检查 ──
    projects = get_projects(url, token, cid)
    result["project_count"] = len(projects)

    for proj in projects:
        pid   = proj["id"]
        pname = proj.get("name", pid)
        pstate = proj.get("state", "?")

        # 检查项目是否存在成员
        bindings = get_project_rbac(url, token, pid)
        member_count = len([b for b in bindings
                          if b.get("userId") or b.get("userPrincipalId")
                          or b.get("groupPrincipalId")])

        proj_info = {
            "id": pid,
            "name": pname,
            "state": pstate,
            "member_count": member_count,
            "system": pname in ("System", "Default"),
        }
        result["projects"].append(proj_info)

        if pstate not in ("active",):
            result["issues"].append({
                "level": WARNING,
                "item": "Project State",
                "detail": "{}/{} state={}".format(cname, pname, pstate),
            })

        if member_count == 0 and not proj_info["system"]:
            result["issues"].append({
                "level": INFO,
                "item": "Empty Project",
                "detail": "{}/{} 无成员绑定".format(cname, pname),
            })

    # ── 集群级 RBAC ──
    crtb = get_cluster_rbac(url, token, cid)
    cluster_roles = Counter()
    for b in crtb:
        rt = b.get("roleTemplateId", "")
        if rt:
            cluster_roles[rt] += 1
    result["rbac"]["cluster"] = dict(cluster_roles)

    # 集群无成员检查
    if not crtb:
        result["issues"].append({
            "level": INFO,
            "item": "No Cluster Admins",
            "detail": "集群 {} 无集群级角色绑定".format(cname),
        })

    return result


def generate_report(results, version, url):
    """生成巡检报告"""
    lines = []
    total_issues = sum(len(r["issues"]) for r in results)
    critical = sum(1 for r in results for i in r["issues"] if i["level"] == CRITICAL)
    warnings = sum(1 for r in results for i in r["issues"] if i["level"] == WARNING)
    infos    = sum(1 for r in results for i in r["issues"] if i["level"] == INFO)

    total_nodes     = sum(len(r["nodes"]) for r in results)
    total_projects  = sum(len(r["projects"]) for r in results)
    total_clusters  = len(results)

    lines.append("## Rancher 集群巡检报告")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|------|----|")
    lines.append("| Rancher URL | {} |".format(url))
    lines.append("| Rancher 版本 | {} |".format(version))
    lines.append("| 巡检时间 | {} |".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("| 集群总数 | {} |".format(total_clusters))
    lines.append("| 节点总数 | {} |".format(total_nodes))
    lines.append("| 项目总数 | {} |".format(total_projects))
    lines.append("")

    # ── 风险摘要 ──
    lines.append("### ⚠️ 风险摘要")
    lines.append("")
    if total_issues == 0:
        lines.append("> 未发现风险项，所有集群运行正常 🎉")
    else:
        lines.append("| 等级 | 数量 |")
        lines.append("|------|------|")
        if critical:
            lines.append("| {} | {} |".format(CRITICAL, critical))
        if warnings:
            lines.append("| {} | {} |".format(WARNING, warnings))
        if infos:
            lines.append("| {} | {} |".format(INFO, infos))
        lines.append("")

        lines.append("#### 详细风险项")
        lines.append("")
        for r in results:
            if r["issues"]:
                lines.append("**集群: {}** (`{}`)".format(r["name"], r["id"]))
                lines.append("")
                for issue in r["issues"]:
                    lines.append("- {} **{}**: {}".format(issue["level"], issue["item"], issue["detail"]))
                lines.append("")

    # ── 集群概览 ──
    lines.append("### 🖥️ 集群概览")
    lines.append("")
    lines.append("| 集群 | 类型 | 状态 | 节点数 | 项目数 | 条件 |")
    lines.append("|------|------|------|--------|--------|------|")
    for r in results:
        ctype  = "local" if r["local"] else "downstream"
        state  = r["state"]
        ncount = len(r["nodes"])
        pcount = len(r["projects"])
        cond_ok = sum(1 for v in r["conditions"].values() if v == "True")
        cond_total = len(r["conditions"])
        cond_str = "{} OK".format(cond_total) if cond_ok == cond_total else "{}/{} OK".format(cond_ok, cond_total)
        state_icon = "✅" if state == "active" else "⚠️"
        lines.append("| {} {} | {} | {} | {} | {} | {} |".format(
            state_icon, r["name"], ctype, state, ncount, pcount, cond_str))
    lines.append("")

    # ── 节点详情 ──
    lines.append("### 🖧 节点详情")
    lines.append("")
    for r in results:
        if r["nodes"]:
            lines.append("#### {}".format(r["name"]))
            lines.append("")
            lines.append("| 节点 | 状态 | CPU | 内存 | Pods | Kubelet | 内核 |")
            lines.append("|------|------|-----|------|------|---------|------|")
            for n in r["nodes"]:
                ready = "✅" if n["conditions"].get("Ready") == "True" else "⚠️"
                lines.append("| {} {} | {} | {}核 | {} | {}/{} | {} | {} |".format(
                    ready, n["hostname"],
                    n["state"],
                    n["cpu_cores"],
                    n["alloc_mem"],
                    n["alloc_pods"], n["capacity_pods"],
                    n["kubelet"],
                    n["kernel"],
                ))
            lines.append("")

    # ── 项目检查 ──
    lines.append("### 📦 项目检查")
    lines.append("")
    for r in results:
        non_system = [p for p in r["projects"] if not p["system"]]
        empty = [p for p in non_system if p["member_count"] == 0]
        if non_system:
            lines.append("#### {} ({} 个非系统项目)".format(r["name"], len(non_system)))
            lines.append("")
            lines.append("| 项目 | 状态 | 成员数 |")
            lines.append("|------|------|--------|")
            for p in non_system:
                state_icon = "✅" if p["state"] == "active" else "⚠️"
                member_icon = "👤" if p["member_count"] > 0 else "❌"
                lines.append("| {} | {} {} | {} {} |".format(
                    p["name"], state_icon, p["state"], member_icon, p["member_count"]))
            lines.append("")

    # ── RBAC 概览 ──
    lines.append("### 🔐 RBAC 概览")
    lines.append("")
    for r in results:
        cr = r["rbac"].get("cluster", {})
        if cr:
            lines.append("#### {} — 集群级角色".format(r["name"]))
            lines.append("")
            lines.append("| 角色 | 绑定数 |")
            lines.append("|------|--------|")
            for role, count in sorted(cr.items()):
                lines.append("| {} | {} |".format(role, count))
            lines.append("")

    return "\n".join(lines)


def generate_json(results, version, url):
    return json.dumps({
        "rancher_url": url,
        "rancher_version": version,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "clusters": results,
    }, indent=2, ensure_ascii=False)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rancher 集群巡检")
    parser.add_argument("-o", "--output", help="输出文件 (.md / .json)")
    parser.add_argument("-e", "--env", help="env 文件路径")
    parser.add_argument("-c", "--cluster", action="append", help="限定集群 (支持 name 或 id)")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    url, token = load_env(args.env)
    version = get_rancher_version(url, token)

    print("# Rancher: {} ({})".format(url, version), file=sys.stderr)
    print("# 正在巡检...", file=sys.stderr)

    clusters = get_clusters(url, token)
    cf = set(args.cluster) if args.cluster else None

    results = []
    for cl in clusters:
        cid   = cl["id"]
        cname = cl.get("name", cid)
        if cf:
            if cid not in cf and cname not in cf:
                continue
        print("  # 检查: {} ({})".format(cname, cid), file=sys.stderr)
        r = check_cluster(url, token, cl)
        results.append(r)

    # 输出
    if args.json or (args.output and args.output.endswith(".json")):
        out = generate_json(results, version, url)
        if args.output:
            with open(args.output, "w") as f:
                f.write(out)
            print("# 已输出: {}".format(args.output), file=sys.stderr)
        else:
            print(out)
    else:
        out = generate_report(results, version, url)
        if args.output:
            with open(args.output, "w") as f:
                f.write(out)
            print("# 已输出: {}".format(args.output), file=sys.stderr)
        else:
            print(out)

    # 退出码
    critical_count = sum(1 for r in results for i in r["issues"] if i["level"] == CRITICAL)
    if critical_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
