#!/usr/bin/env python3
"""
rancher_rbac_apply.py — RBAC 迁移工具（三步工作流）
====================================================

三步:
  export-users  导出目标 Rancher 所有用户清单
  match         对比 rbac.csv 与用户清单，输出映射结果
  apply         在目标集群执行绑定（用户/角色/项目任一缺失则跳过）

用法:
  # 1️⃣ 导出目标 Rancher 用户
  python3 rancher_rbac_apply.py export-users -o target_users.csv

  # 2️⃣ 对比匹配（预览差异）
  python3 rancher_rbac_apply.py match --rbac rbac.csv --users target_users.csv

  # 3️⃣ 执行绑定
  python3 rancher_rbac_apply.py apply --rbac rbac.csv --users target_users.csv --dry-run
  python3 rancher_rbac_apply.py apply --rbac rbac.csv --users target_users.csv

匹配规则:
  - 精确匹配 displayName（忽略大小写）
  - 去 domain 匹配: 目标端 e-Xiao.Wang4@geely.com 也按 e-Xiao.Wang4 匹配
  - 归一化兜底: 去掉 .-_ 分隔符后比较

apply 规则:
  ✅ 用户在目标端存在 + 角色存在 + 项目存在 → 执行绑定
  ⏭️ 任一缺失 → 跳过，不报错

env 文件: 同目录 env.txt
"""

import os, sys, json, csv, re, time, ssl

try:
    from urllib.request import Request, urlopen, HTTPError
    from urllib.parse   import quote
except ImportError:
    from urllib2 import Request, urlopen, HTTPError
    from urllib import quote

MAX_RETRIES     = 3
RETRY_BACKOFF   = 2.0
REQUEST_TIMEOUT = 60
HTTP_PROXY      = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# 角色名 → API roleTemplateId
ROLE_REVERSE = {
    "owner": "project-owner", "member": "project-member", "readonly": "read-only",
}
CLUSTER_ROLE_REVERSE = {
    "cluster owner": "cluster-owner", "cluster member": "cluster-member",
    "cluster admin": "cluster-admin", "cluster viewer": "cluster-viewer",
    "nodes view": "nodes-view", "nodes manage": "nodes-manage",
    "projects create": "projects-create", "projects view": "projects-view",
    "storage manage": "storage-manage",
}


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


def api(url, token, method, path, body=None):
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
            return (code, json.loads(raw) if raw and raw.strip() else {})
        except HTTPError as e:
            body_err = ""
            try:
                body_err = e.read().decode("utf-8")[:500]
            except:
                pass
            if e.code == 401:
                die("Token 无效 (401)")
            if e.code in (404, 422, 400, 409):
                return (e.code, {"error": body_err})
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return (e.code, {"error": body_err})
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return (0, {"error": str(e)})
    return (0, {"error": "max retries"})


def api_paginated(url, token, path):
    all_items = []
    marker = None
    sep = "&" if "?" in path else "?"
    while True:
        p = "{}{}limit=1000".format(path, sep)
        if marker:
            p += "&continue={}".format(marker)
        code, data = api(url, token, "GET", p)
        if code != 200:
            break
        items = data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        pagination = data.get("pagination", {})
        if pagination.get("next"):
            marker = pagination["next"]
        elif len(items) < 1000:
            break
        else:
            marker = items[-1].get("id", "")
    return all_items


def normalize(s):
    """归一化: 去domain → 小写 → 去 .-_"""
    s = re.sub(r'@.*$', '', s).lower()
    return re.sub(r'[.\-_\s]', '', s)


# ═══════════════════════════════════════════
#  子命令: export-users
# ═══════════════════════════════════════════

def cmd_export_users(args):
    url, token = load_env(args.env)
    users = []

    # 本地用户
    for u in api_paginated(url, token, "v3/users"):
        dn = u.get("displayName") or u.get("username") or ""
        if dn:
            users.append({"display_name": dn, "principal_id": u["id"], "source": "local"})

    # SSO principals
    print("# 正在拉取 principals...", file=sys.stderr)
    for p in api_paginated(url, token, "v3/principals"):
        name = p.get("displayName") or p.get("loginName") or p.get("name") or ""
        if not name:
            continue
        ptype = "group" if p.get("principalType") == "group" else "sso"
        users.append({"display_name": name, "principal_id": p["id"], "source": ptype})

    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["display_name", "principal_id", "source"])
        for u in sorted(users, key=lambda x: x["display_name"].lower()):
            w.writerow([u["display_name"], u["principal_id"], u["source"]])

    local_n = sum(1 for u in users if u["source"] == "local")
    sso_n = sum(1 for u in users if u["source"] == "sso")
    group_n = sum(1 for u in users if u["source"] == "group")
    print("导出完成: {} 个用户 (本地: {}, SSO: {}, 组: {}) → {}".format(
        len(users), local_n, sso_n, group_n, args.output), file=sys.stderr)


# ═══════════════════════════════════════════
#  子命令: match
# ═══════════════════════════════════════════

def load_target_users(csv_path):
    """加载目标端用户 CSV → {name_lower: {display_name, principal_id, source}} + 去domain索引 + 归一化索引"""
    exact = {}
    domain_stripped = {}  # name_without_domain → [entries]
    normalized = {}       # norm → [entries]

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dn = (row.get("display_name") or "").strip()
            pid = (row.get("principal_id") or "").strip()
            src = (row.get("source") or "").strip()
            if not dn or not pid:
                continue
            info = {"display_name": dn, "principal_id": pid, "source": src}

            # 精确索引
            nl = dn.lower()
            if nl not in exact:
                exact[nl] = info

            # 去 domain 索引
            base = re.sub(r'@.*$', '', dn).lower().strip()
            if base and base != nl:
                domain_stripped.setdefault(base, []).append(info)

            # 归一化索引
            nn = normalize(dn)
            if nn:
                normalized.setdefault(nn, []).append(info)

    return exact, domain_stripped, normalized


def find_target_user(name, exact, domain_stripped, normalized):
    """
    三级匹配，返回 (level, info) 或 (None, None)
      level: "exact" | "domain" | "fuzzy"
    """
    nl = name.lower().strip()

    # 1. 精确
    if nl in exact:
        return "exact", exact[nl]

    # 2. 去 domain
    base = re.sub(r'@.*$', '', name).lower().strip()
    if base in domain_stripped:
        candidates = domain_stripped[base]
        if len(candidates) == 1:
            return "domain", candidates[0]
        # 多人冲突，用归一化再过滤
        nn = normalize(name)
        filtered = [c for c in candidates if normalize(c["display_name"]) == nn]
        if len(filtered) == 1:
            return "domain", filtered[0]

    # 3. 归一化兜底
    nn = normalize(name)
    if nn in normalized:
        candidates = normalized[nn]
        if len(candidates) == 1:
            return "fuzzy", candidates[0]
        # 多人，去domain再过滤
        base = re.sub(r'@.*$', '', name).lower().strip()
        filtered = [c for c in candidates
                    if re.sub(r'@.*$', '', c["display_name"]).lower().strip() == base]
        if len(filtered) == 1:
            return "fuzzy", filtered[0]

    return None, None


def cmd_match(args):
    url, token = load_env(args.env)  # 仅用于显示 URL
    exact, ds, norm = load_target_users(args.users)

    with open(args.rbac, "r", encoding="utf-8-sig") as f:
        rbac_rows = list(csv.DictReader(f))

    # 收集唯一用户
    seen = set()
    unique = []
    for row in rbac_rows:
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
        pid = (row.get("PRINCIPAL_ID", "") or "").strip()
        if ug in ("(无成员)", "-", "") or not ug or not pid:
            continue
        key = ug.lower()
        if key not in seen:
            seen.add(key)
            unique.append((ug, pid))

    # 匹配
    matched = []
    unmatched = []
    for ug, old_pid in unique:
        level, info = find_target_user(ug, exact, ds, norm)
        if info:
            matched.append((ug, old_pid, info["principal_id"], info["display_name"], level))
        else:
            unmatched.append((ug, old_pid))

    # 报告
    print()
    print("=" * 75)
    print("  映射报告 — {} 条用户".format(len(matched) + len(unmatched)))
    print("=" * 75)
    print("  匹配: {} | 未匹配: {}".format(len(matched), len(unmatched)))
    print()

    if matched:
        level_labels = {"exact": "精确", "domain": "去domain", "fuzzy": "归一化"}
        print("  {:<30} {:<20} {:<20} {}".format("源 displayName", "目标 displayName", "旧 PID → 新 PID", "方式"))
        print("  {:<30} {:<20} {:<20} {}".format("─"*30, "─"*20, "─"*20, "─"*8))
        for ug, old_pid, new_pid, target_dn, level in matched:
            icon = "✓" if level == "exact" else ("~" if level == "domain" else "≈")
            print("  {}{:<29} {:<20} {:<20} {}".format(
                icon, ug[:29], target_dn[:20],
                "{} → {}".format(old_pid[:8], new_pid[:8]),
                level_labels.get(level, level)))

    if unmatched:
        print()
        print("  未匹配:")
        for ug, old_pid in unmatched:
            print("    ✗ {:<30} {}".format(ug[:30], old_pid))
        print()
        local = [u for u in unmatched if u[1].startswith("user-")]
        sso = [u for u in unmatched if u[1] and not u[1].startswith("user-")]
        if local:
            print("  → {} 个本地用户，需手动创建".format(len(local)))
        if sso:
            print("  → {} 个 SSO 用户，需在新 Rancher 登录后重试".format(len(sso)))

    print("=" * 75)
    print()

    # 生成匹配后的 CSV（只含匹配成功的行）
    if matched and args.output:
        match_pid = {}  # name_lower → (new_pid, target_dn)
        for ug, old_pid, new_pid, target_dn, level in matched:
            match_pid[ug.lower()] = (new_pid, target_dn)

        out_rows = []
        for row in rbac_rows:
            ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
            if ug.lower() in match_pid:
                new_pid, _ = match_pid[ug.lower()]
                row = dict(row)
                row["PRINCIPAL_ID"] = new_pid
                out_rows.append(row)

        with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
            if out_rows:
                w = csv.DictWriter(f, fieldnames=rbac_rows[0].keys())
                w.writeheader()
                w.writerows(out_rows)
        print("已输出匹配条目: {} → {} ({} 行)".format(
            args.rbac, args.output, len(out_rows)), file=sys.stderr)

    return matched, unmatched


# ═══════════════════════════════════════════
#  子命令: apply
# ═══════════════════════════════════════════

def resolve_cluster_id(url, token, name_or_id):
    path = "v3/clusters/{}".format(name_or_id)
    code, data = api(url, token, "GET", path)
    if code == 200 and data.get("id"):
        return data["id"]
    code, data = api(url, token, "GET", "v3/clusters")
    if code == 200:
        for item in data.get("data", []):
            if item.get("name") == name_or_id or item.get("id") == name_or_id:
                return item["id"]
    return None


def resolve_project_id(url, token, cluster_id, name_or_id):
    if ":" in name_or_id:
        return name_or_id
    code, data = api(url, token, "GET", "v3/projects?clusterId={}".format(cluster_id))
    if code == 200:
        for item in data.get("data", []):
            if item.get("name") == name_or_id or item.get("id") == name_or_id:
                return item["id"]
    return None


def resolve_role_template(url, token, role_display, role_id, level):
    """解析 roleTemplateId，返回 (role_id, found)"""
    if role_id and role_id != "-":
        # 检查是否存在
        code, _ = api(url, token, "GET", "v3/roleTemplates/{}".format(role_id))
        if code == 200:
            return role_id, True
        code, _ = api(url, token, "GET", "v3/globalRoles/{}".format(role_id))
        if code == 200:
            return role_id, True
        return role_id, False

    if not role_display or role_display == "-":
        return None, False

    role_lower = role_display.lower()
    rev = CLUSTER_ROLE_REVERSE if level == "cluster" else ROLE_REVERSE
    mapped = rev.get(role_lower)
    if not mapped:
        mapped = ROLE_REVERSE.get(role_lower)
    if not mapped:
        mapped = role_display  # 尝试原始值

    # 验证
    code, _ = api(url, token, "GET", "v3/roleTemplates/{}".format(mapped))
    if code == 200:
        return mapped, True
    return mapped, False


def cmd_apply(args):
    url, token = load_env(args.env)
    exact, ds, norm = load_target_users(args.users)

    with open(args.rbac, "r", encoding="utf-8-sig") as f:
        rbac_rows = list(csv.DictReader(f))

    # 缓存匹配结果
    match_cache = {}
    # 缓存角色存在性
    role_cache = {}
    # 缓存集群/项目 ID
    cluster_cache = {}
    project_cache = {}

    ok = 0
    skip_user = 0
    skip_role = 0
    skip_project = 0
    skip_global = 0
    skip_other = 0

    for i, row in enumerate(rbac_rows, 1):
        level = (row.get("LEVEL", "") or row.get("level", "")).strip().lower() or "project"
        cl = (row.get("CLUSTER", "") or row.get("cluster", "")).strip()
        proj = (row.get("PROJECT", "") or row.get("project", "")).strip()
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
        role = (row.get("ROLE", "") or "").strip()
        pid_col = (row.get("PRINCIPAL_ID", "") or "").strip()
        role_id_col = (row.get("ROLE_ID", "") or "").strip()

        # 跳过无效行
        if not cl or cl == "-":
            if level == "global":
                skip_global += 1
            skip_other += 1
            continue
        if not ug or ug in ("(无成员)", "-") or not role or role == "-":
            skip_other += 1
            continue

        # ── 1. 匹配用户 ──
        if ug.lower() not in match_cache:
            _, info = find_target_user(ug, exact, ds, norm)
            match_cache[ug.lower()] = info
        target_user = match_cache[ug.lower()]

        if not target_user:
            if not args.quiet:
                print("  ⏭ 行{}: 用户不存在 — {} ({})".format(i, ug, cl), file=sys.stderr)
            skip_user += 1
            continue

        new_pid = target_user["principal_id"]

        # ── 2. 检查角色 ──
        cache_key = (role_id_col, role, level)
        if cache_key not in role_cache:
            rt, found = resolve_role_template(url, token, role, role_id_col, level)
            role_cache[cache_key] = (rt, found)
        role_tmpl, role_exists = role_cache[cache_key]

        if not role_tmpl or not role_exists:
            if not args.quiet:
                print("  ⏭ 行{}: 角色不存在 — {} ({})".format(i, role, role_id_col), file=sys.stderr)
            skip_role += 1
            continue

        # ── 3. 解析集群 ──
        if cl not in cluster_cache:
            cluster_cache[cl] = resolve_cluster_id(url, token, cl)
        cid = cluster_cache[cl]
        if not cid:
            if not args.quiet:
                print("  ⏭ 行{}: 集群不存在 — {}".format(i, cl), file=sys.stderr)
            skip_project += 1
            continue

        # ── 4. 解析项目（project 级绑定需要） ──
        pid_api = None
        if level == "project" and proj and proj != "-":
            proj_key = "{}:{}".format(cid, proj)
            if proj_key not in project_cache:
                project_cache[proj_key] = resolve_project_id(url, token, cid, proj)
            pid_api = project_cache[proj_key]
            if not pid_api:
                if not args.quiet:
                    print("  ⏭ 行{}: 项目不存在 — {}/{}".format(i, cl, proj), file=sys.stderr)
                skip_project += 1
                continue

        # ── 5. 确定 principal 参数 ──
        ptype = target_user["source"]
        if ptype == "local" or new_pid.startswith("user-"):
            key, value = "userId", new_pid
        elif ptype == "group":
            key, value = "groupPrincipalId", new_pid
        else:
            key, value = "userPrincipalId", new_pid

        # ── 6. 构建绑定 ──
        if level == "cluster":
            body = {
                "clusterId": cid,
                "roleTemplateId": role_tmpl,
                key: value,
                "type": "clusterRoleTemplateBinding",
            }
            api_path = "v3/clusterRoleTemplateBindings"
        else:
            body = {
                "projectId": pid_api,
                "roleTemplateId": role_tmpl,
                key: value,
                "type": "projectRoleTemplateBinding",
            }
            api_path = "v3/projectroletemplatebindings"

        if args.dry_run:
            print("[DRY] {}: {} as {} → {}/{}".format(ug, role, level, cl, proj or "-"))
            ok += 1
            continue

        code, data = api(url, token, "POST", api_path, body)
        if code in (200, 201):
            print("  ✅ {} as {} → {}/{}".format(ug, role, cl, proj or "-"))
            ok += 1
        elif code == 409:
            if not args.quiet:
                print("  ⏭ 已存在: {} as {} → {}/{}".format(ug, role, cl, proj or "-"), file=sys.stderr)
            ok += 1
        else:
            msg = data.get("message", data.get("error", str(data)))[:100]
            print("  ❌ {} as {} → {}/{}: {}".format(ug, role, cl, proj or "-", msg))
            # Don't count as ok

    print()
    print("=" * 60)
    print("  绑定结果")
    print("=" * 60)
    print("  ✅ 已执行: {}".format(ok))
    print("  ⏭ 跳过 (用户不存在): {}".format(skip_user))
    print("  ⏭ 跳过 (角色不存在): {}".format(skip_role))
    print("  ⏭ 跳过 (集群/项目不存在): {}".format(skip_project))
    print("  ⏭ 跳过 (global/其他): {}".format(skip_global + skip_other))
    print("=" * 60)


# ═══════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("export-users", "match", "apply"):
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", nargs="*")
    parser.add_argument("-e", "--env", help="env 文件路径")
    parser.add_argument("-o", "--output", help="输出文件")
    parser.add_argument("--rbac", help="rbac CSV 文件 (match/apply)")
    parser.add_argument("--users", help="目标端用户清单 CSV (match/apply)")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行 (apply)")
    parser.add_argument("--quiet", action="store_true", help="静默模式，不输出跳过原因")
    parser.add_argument("--map-cluster", help="集群名映射: 旧名=新名")
    sys.argv = ["rancher_rbac_apply.py", cmd] + sys.argv[2:]
    args = parser.parse_args()

    if cmd == "export-users":
        if not args.output:
            die("需要 -o/--output")
        cmd_export_users(args)
    elif cmd == "match":
        if not args.rbac or not args.users:
            die("需要 --rbac 和 --users")
        cmd_match(args)
    elif cmd == "apply":
        if not args.rbac or not args.users:
            die("需要 --rbac 和 --users")
        cmd_apply(args)


if __name__ == "__main__":
    main()
