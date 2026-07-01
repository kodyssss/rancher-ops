#!/usr/bin/env python3
"""
rancher_rbac.py — Rancher 全层级角色批量导出
==============================================
批量查询 全局/集群/项目 三个层级的用户/组角色绑定映射。

层级说明:
  global  — GlobalRoleBinding (谁能登录/管理 Rancher 本身)
  cluster — ClusterRoleTemplateBinding (谁能访问集群)
  project — ProjectRoleTemplateBinding (谁能操作项目)

用法:
  python3 rancher_rbac.py                          # 终端输出
  python3 rancher_rbac.py -o rbac.csv              # CSV 文件
  python3 rancher_rbac.py -c poc                   # 只查某集群（支持 name）
  python3 rancher_rbac.py -c c-xxxxx               # 也支持 id
  python3 rancher_rbac.py -o rbac.csv --per-cluster  # 每个集群单独文件
  python3 rancher_rbac.py --no-global              # 跳过全局角色
  python3 rancher_rbac.py --no-cluster             # 跳过集群级角色

env 文件: 同目录 env.txt
  export RANCHER_URL=https://rancher.example.com
  export RANCHER_TOKEN=***
"""

import os, sys, json, csv, re, time, ssl

try:
    from urllib.request import Request, urlopen, HTTPError, URLError
    from urllib.parse   import quote
except ImportError:
    from urllib2 import Request, urlopen, HTTPError, URLError
    from urllib import quote

# ── 配置 ─────────────────────────────────────
PAGE_SIZE       = 1000
MAX_RETRIES     = 3
RETRY_BACKOFF   = 2.0
REQUEST_TIMEOUT = 60
HTTP_PROXY      = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))
# ──────────────────────────────────────────────

# 角色映射（project 级内置角色）
ROLE_MAP = {
    "project-owner":  "Owner",
    "project-member": "Member",
    "read-only":      "ReadOnly",
}

# 全局角色映射（常见内置）
GLOBAL_ROLE_MAP = {
    "admin":            "Admin",
    "user":             "User",
    "user-base":        "User Base",
    "restricted-admin": "Restricted Admin",
}

# 集群角色映射（常见内置）
CLUSTER_ROLE_MAP = {
    "cluster-owner":    "Cluster Owner",
    "cluster-member":   "Cluster Member",
    "cluster-admin":    "Cluster Admin",
    "cluster-viewer":   "Cluster Viewer",
    "nodes-view":       "Nodes View",
    "nodes-manage":     "Nodes Manage",
    "projects-create":  "Projects Create",
    "projects-view":    "Projects View",
    "storage-manage":   "Storage Manage",
}

# 角色 displayName 缓存
_role_cache = {}

# SSL context（内网自签名）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def load_env(env_path=None):
    """加载 env.txt"""
    if env_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(script_dir, "env.txt"),
                  os.path.join(script_dir, "env"),
                  os.path.join(os.getcwd(), "env.txt")]:
            if os.path.isfile(p):
                env_path = p
                break
    if env_path is None:
        print("ERROR: 找不到 env.txt", file=sys.stderr)
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
    url = env_vars.get("RANCHER_URL", "").rstrip("/")
    token = env_vars.get("RANCHER_TOKEN", "")
    if not url or not token:
        print("ERROR: 缺少 RANCHER_URL / RANCHER_TOKEN", file=sys.stderr)
        sys.exit(1)
    return url, token


def api_get(url, token, path):
    """HTTP GET，带重试"""
    full_url = url.rstrip("/") + "/" + path.lstrip("/")
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(full_url)
            req.add_header("Authorization", "Bearer {}".format(token))
            req.add_header("Accept", "application/json")
            if HTTP_PROXY:
                req.set_proxy(HTTP_PROXY, "https")
            resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=SSL_CTX)
            return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 401:
                print("ERROR: Token 无效 (401)", file=sys.stderr)
                return None
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
    """分页拉全量 (path 可含已有 query string)"""
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


def get_clusters(url, token):
    items = api_paginated(url, token, "v3/clusters")
    return {it["id"]: it.get("name", it["id"]) for it in items}


def get_projects(url, token, cluster_id):
    items = api_paginated(url, token,
                          "v3/projects?clusterId={}".format(cluster_id))
    return items


def get_bindings(url, token, project_id):
    path = "v3/projectroletemplatebindings?projectId={}".format(project_id)
    return api_paginated(url, token, path)


def get_cluster_bindings(url, token, cluster_id=None):
    """获取集群级角色绑定 (ClusterRoleTemplateBinding)"""
    if cluster_id:
        path = "v3/clusterRoleTemplateBindings?clusterId={}".format(cluster_id)
    else:
        path = "v3/clusterRoleTemplateBindings"
    return api_paginated(url, token, path)


def get_global_bindings(url, token):
    """获取全局角色绑定 (GlobalRoleBinding)"""
    return api_paginated(url, token, "v3/globalRoleBindings")


def get_user_display(url, token, user_id):
    data = api_get(url, token, "v3/users/{}".format(user_id))
    if data:
        return data.get("displayName") or data.get("username") or data.get("name") or user_id
    return user_id


def get_principal_display(url, token, principal_id):
    """查询 principal（外部用户/组）显示名"""
    encoded = quote(principal_id, safe="")
    data = api_get(url, token, "v3/principals/{}".format(encoded))
    if data:
        name = data.get("displayName") or data.get("loginName") or data.get("name")
        if name and name != "null":
            return name
    # 兜底：从 DN 提取 CN=
    m = re.search(r"CN=([^,]+)", principal_id)
    if m:
        return m.group(1)
    return principal_id.rsplit(":", 1)[-1].rsplit("/", 1)[-1]


def get_role_display(url, token, role_id, role_map=None):
    """从 RoleTemplate API 获取角色的 displayName，带缓存"""
    if not role_id:
        return role_id
    if role_id in _role_cache:
        return _role_cache[role_id]
    # 传入的内置映射优先
    if role_map and role_id in role_map:
        _role_cache[role_id] = role_map[role_id]
        return role_map[role_id]
    # 已知项目内置角色
    builtin = ROLE_MAP.get(role_id)
    if builtin:
        _role_cache[role_id] = builtin
        return builtin
    # 自定义角色查 API (先查 roleTemplates)
    data = api_get(url, token, "v3/roleTemplates/{}".format(role_id))
    if data:
        dn = data.get("displayName") or data.get("name") or role_id
        _role_cache[role_id] = dn
        return dn
    # 也尝试 globalRoles
    data = api_get(url, token, "v3/globalRoles/{}".format(role_id))
    if data:
        dn = data.get("displayName") or data.get("name") or role_id
        _role_cache[role_id] = dn
        return dn
    _role_cache[role_id] = role_id
    return role_id


def resolve_binding_identity(url, token, b):
    """从 binding 对象解析用户/组身份，返回 (display, type, principal_id)"""
    user_id = b.get("userId", "")
    user_principal = b.get("userPrincipalId", "")
    group_principal = b.get("groupPrincipalId", "")

    if user_id:
        display = get_user_display(url, token, user_id)
        return (display, "User", user_id)
    elif user_principal:
        display = get_principal_display(url, token, user_principal)
        return (display, "User", user_principal)
    elif group_principal:
        display = get_principal_display(url, token, group_principal)
        return (display, "Group", group_principal)
    else:
        return ("Creator", "User", b.get("id", "unknown"))


def build_rbac(url, token, cluster_filter=None, include_global=True, include_cluster=True):
    """
    生成器: (level, cluster_name, project_name, display_name, type, role, principal_id, role_id)
    level: global / cluster / project
    """
    clusters = get_clusters(url, token)

    if cluster_filter:
        expanded = set()
        for f in cluster_filter:
            expanded.add(f)
            for cid, cname in clusters.items():
                if cname == f:
                    expanded.add(cid)
        cluster_filter = expanded

    # ── 1. 全局角色 (GlobalRoleBinding) ──
    if include_global and not cluster_filter:
        grb = get_global_bindings(url, token)
        if grb:
            for b in grb:
                role_id = b.get("globalRoleId", "")
                role_display = get_role_display(url, token, role_id, GLOBAL_ROLE_MAP)
                display, ptype, pid = resolve_binding_identity(url, token, b)
                yield ("global", "-", "-", display, ptype, role_display, pid, role_id)
        else:
            yield ("global", "-", "-", "(无绑定)", "-", "-", "", "")

    # ── 2. 集群级角色 (ClusterRoleTemplateBinding) ──
    if include_cluster:
        for cid in sorted(clusters):
            if cluster_filter and cid not in cluster_filter:
                continue
            cname = clusters[cid]
            crtbs = get_cluster_bindings(url, token, cid)
            if not crtbs:
                yield ("cluster", cname, "-", "(无成员)", "-", "-", "", "")
                continue
            for b in crtbs:
                role_id = b.get("roleTemplateId", "")
                role_display = get_role_display(url, token, role_id, CLUSTER_ROLE_MAP)
                display, ptype, pid = resolve_binding_identity(url, token, b)
                yield ("cluster", cname, "-", display, ptype, role_display, pid, role_id)

    # ── 3. 项目级角色 (ProjectRoleTemplateBinding) ──
    for cid in sorted(clusters):
        if cluster_filter and cid not in cluster_filter:
            continue
        cname = clusters[cid]
        projects = get_projects(url, token, cid)
        if not projects:
            continue
        for proj in projects:
            pid = proj["id"]
            pname = proj.get("name", pid)
            bindings = get_bindings(url, token, pid)
            if not bindings:
                yield ("project", cname, pname, "(无成员)", "-", "-", "", "")
                continue
            for b in bindings:
                role_id = b.get("roleTemplateId", "")
                role_display = get_role_display(url, token, role_id)
                display, ptype, pid2 = resolve_binding_identity(url, token, b)
                yield ("project", cname, pname, display, ptype, role_display, pid2, role_id)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rancher 全层级角色批量导出")
    parser.add_argument("-o", "--output", help="输出文件 (.csv)")
    parser.add_argument("-e", "--env", help="env 文件路径")
    parser.add_argument("-c", "--cluster", action="append", help="限定集群 (支持 name 或 id)")
    parser.add_argument("--per-cluster", action="store_true", help="每个集群单独输出文件")
    parser.add_argument("--no-global", action="store_true", help="跳过全局角色绑定")
    parser.add_argument("--no-cluster", action="store_true", help="跳过集群级角色绑定")
    args = parser.parse_args()

    url, token = load_env(args.env)
    cf = set(args.cluster) if args.cluster else None
    include_global = not args.no_global
    include_cluster = not args.no_cluster

    print("# Rancher: {}".format(url), file=sys.stderr)
    print("# 正在查询 (global={}, cluster={}, project=True)...".format(
        include_global, include_cluster), file=sys.stderr)

    HEADER = ["LEVEL", "CLUSTER", "PROJECT", "USER_GROUP", "TYPE", "ROLE",
              "PRINCIPAL_ID", "ROLE_ID"]

    if args.per_cluster and args.output:
        base, ext = os.path.splitext(args.output)
        clusters = get_clusters(url, token)
        for cid in sorted(clusters):
            if cf and cid not in cf:
                continue
            outfile = "{}_{}{}".format(base, cid, ext or ".csv")
            rows = list(build_rbac(url, token, {cid},
                                   include_global=include_global,
                                   include_cluster=include_cluster))
            rows.sort(key=lambda r: (
                {"global": 0, "cluster": 1, "project": 2}.get(r[0], 9),
                r[1] or "", r[2] or "", r[3] or ""))
            with open(outfile, "w", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(HEADER)
                w.writerows(rows)
            print("  {}: {} 行".format(outfile, len(rows)), file=sys.stderr)
    elif args.output:
        rows = list(build_rbac(url, token, cf,
                               include_global=include_global,
                               include_cluster=include_cluster))
        rows.sort(key=lambda r: (
            {"global": 0, "cluster": 1, "project": 2}.get(r[0], 9),
            r[1] or "", r[2] or "", r[3] or ""))
        with open(args.output, "w", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            w.writerows(rows)
        print("已输出: {} ({} 行)".format(args.output, len(rows)), file=sys.stderr)
    else:
        print("{:<8} {:<20} {:<25} {:<30} {:<8} {:<16} {:<36} {}".format(
            "LEVEL", "CLUSTER", "PROJECT", "USER/GROUP", "TYPE", "ROLE",
            "PRINCIPAL_ID", "ROLE_ID"))
        print("{:<8} {:<20} {:<25} {:<30} {:<8} {:<16} {:<36} {}".format(
            "------", "-------", "-------", "----------", "----", "----",
            "------------", "-------"))
        rows = list(build_rbac(url, token, cf,
                               include_global=include_global,
                               include_cluster=include_cluster))
        rows.sort(key=lambda r: (
            {"global": 0, "cluster": 1, "project": 2}.get(r[0], 9),
            r[1] or "", r[2] or "", r[3] or ""))
        for row in rows:
            print("{:<8} {:<20} {:<25} {:<30} {:<8} {:<16} {:<36} {}".format(*row))
        print("# 共 {} 行".format(len(rows)), file=sys.stderr)


if __name__ == "__main__":
    main()
