#!/usr/bin/env python3
"""
rancher_rbac_apply.py — RBAC 批量绑定
======================================
读取 rbac CSV，按 USER_GROUP (displayName) 在目标 Rancher 查找用户，
匹配后执行角色绑定。

用法:
  python3 rancher_rbac_apply.py --from-csv rbac.csv --dry-run
  python3 rancher_rbac_apply.py --from-csv rbac.csv

CSV 格式 (rancher_rbac.py 输出):
  LEVEL,CLUSTER,PROJECT,USER_GROUP,TYPE,ROLE,PRINCIPAL_ID,ROLE_ID

匹配规则:
  - 精确匹配 displayName（忽略大小写）
  - 去 domain 兜底: e-Xiao.Wang4@geely.com 也尝试匹配 e-Xiao.Wang4
  - 用户/角色/项目任一不存在 → 跳过

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

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

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
            resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=SSL_CTX)
            raw = resp.read().decode("utf-8")
            code = resp.getcode()
            return (code, json.loads(raw) if raw and raw.strip() else {})
        except HTTPError as e:
            try:
                body_err = e.read().decode("utf-8")[:500]
            except:
                body_err = str(e)
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


# ═══════════════════════════════════════════
#  用户查找
# ═══════════════════════════════════════════

def build_user_index(url, token):
    """
    拉取目标 Rancher 所有用户，建 displayName 索引。
    返回 (exact_index, bare_index)
      exact_index:  {name_lower: {id, type}}    完整名索引
      bare_index:   {name_lower: {id, type}}    去 @domain 索引
    """
    exact = {}
    bare = {}

    def _add(name, info):
        nl = name.lower().strip()
        if nl and nl not in exact:
            exact[nl] = info
        # 去 domain 索引
        base = re.sub(r'@.*$', '', name).lower().strip()
        if base and base != nl and base not in bare:
            bare[base] = info

    # 本地用户
    for u in api_paginated(url, token, "v3/users"):
        info = {"id": u["id"], "type": "local"}
        dn = u.get("displayName") or ""
        un = u.get("username") or ""
        if dn:
            _add(dn, info)
        if un and un != dn:
            _add(un, info)

    # SSO principals
    print("# 拉取 principals...", file=sys.stderr)
    for p in api_paginated(url, token, "v3/principals"):
        pid = p["id"]
        ptype = "group" if p.get("principalType") == "group" else "sso"
        info = {"id": pid, "type": ptype}
        for field in ("displayName", "loginName", "name"):
            name = (p.get(field) or "").strip()
            if name:
                _add(name, info)
                break

    return exact, bare


def find_user(name, exact, bare):
    """按 displayName 查找用户。返回 {id, type} 或 None"""
    nl = name.strip().lower()
    if not nl:
        return None
    if nl in exact:
        return exact[nl]
    # 去 domain 兜底
    base = re.sub(r'@.*$', '', name).lower().strip()
    if base in bare:
        return bare[base]
    if base in exact:
        return exact[base]
    return None


# ═══════════════════════════════════════════
#  集群/项目/角色 解析
# ═══════════════════════════════════════════

def resolve_cluster(url, token, name_or_id):
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


def resolve_project(url, token, cluster_id, name_or_id):
    if ":" in name_or_id:
        return name_or_id
    code, data = api(url, token, "GET", "v3/projects?clusterId={}".format(cluster_id))
    if code == 200:
        for item in data.get("data", []):
            if item.get("name") == name_or_id or item.get("id") == name_or_id:
                return item["id"]
    return None


def resolve_role(url, token, role_display, role_id, level):
    """解析 roleTemplateId，返回 (id, exists)"""
    if role_id and role_id != "-":
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
        mapped = role_display

    code, _ = api(url, token, "GET", "v3/roleTemplates/{}".format(mapped))
    if code == 200:
        return mapped, True
    return mapped, False


def create_user(url, token, display_name):
    """创建本地用户，返回 (ok, user_id, password)"""
    import secrets, string
    password = "".join(secrets.choice(string.ascii_letters + string.digits)
                       for _ in range(16))
    username = re.sub(r'[^a-z0-9_]', '', display_name.lower().replace(" ", "_"))
    if not username:
        username = "user_imported"

    body = {"username": username, "password": password, "name": display_name, "type": "user"}
    code, data = api(url, token, "POST", "v3/users", body)
    if code in (200, 201):
        return True, data.get("id", ""), password
    elif code == 409:
        # 已存在，查找 ID
        for u in api_paginated(url, token, "v3/users"):
            if (u.get("displayName") or "").lower() == display_name.lower():
                return True, u["id"], ""
        return True, None, ""
    else:
        return False, None, data.get("error", str(data))


# ═══════════════════════════════════════════
#  主逻辑
# ═══════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="RBAC 批量绑定")
    parser.add_argument("--from-csv", required=True, help="rbac CSV 文件")
    parser.add_argument("-e", "--env", help="env 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--list-users", action="store_true", help="列出目标端所有用户并退出")
    parser.add_argument("--auto-create-users", action="store_true",
                        help="CSV 中不存在的用户自动创建为本地用户（随机密码 → user_passwords.txt）")
    args = parser.parse_args()

    url, token = load_env(args.env)
    print("# {}\n".format(url), file=sys.stderr)

    # 读取 CSV
    with open(args.from_csv, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # 拉取目标端用户
    print("# 拉取目标端用户...", file=sys.stderr)
    exact_idx, bare_idx = build_user_index(url, token)
    print("# 用户索引: 精确 {} 个, 去domain {} 个\n".format(len(exact_idx), len(bare_idx)),
          file=sys.stderr)

    if args.list_users:
        print("目标端用户清单 ({}，精确索引):".format(url))
        print("-" * 60)
        for name in sorted(exact_idx.keys()):
            info = exact_idx[name]
            print("  {:<40} id={:<20} type={}".format(name[:40], info["id"][:20], info["type"]))
        print()
        print("去domain索引 ({}):".format(len(bare_idx)))
        print("-" * 60)
        for name in sorted(bare_idx.keys()):
            info = bare_idx[name]
            print("  {:<40} id={:<20} type={}".format(name[:40], info["id"][:20], info["type"]))
        sys.exit(0)

    # 缓存
    cluster_cache = {}
    project_cache = {}
    role_cache = {}
    user_cache = {}

    # 先匹配一遍，报告汇总
    matched_names = set()
    unmatched_names = set()
    for row in rows:
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
        if not ug or ug in ("(无成员)", "-"):
            continue
        if ug.lower() not in user_cache:
            user_cache[ug.lower()] = find_user(ug, exact_idx, bare_idx)
        if user_cache[ug.lower()]:
            matched_names.add(ug)
        else:
            unmatched_names.add(ug)

    if matched_names:
        print("已匹配用户 ({}):".format(len(matched_names)), file=sys.stderr)
        for n in sorted(matched_names):
            u = user_cache[n.lower()]
            print("  ✓ {:<35} → id={} ({})".format(n[:35], u["id"][:16], u["type"]), file=sys.stderr)
    if unmatched_names:
        print("未匹配用户 ({}):".format(len(unmatched_names)), file=sys.stderr)
        for n in sorted(unmatched_names):
            print("  ✗ {}".format(n), file=sys.stderr)
    print(file=sys.stderr)

    ok = 0
    skip_user = 0
    skip_role = 0
    skip_cluster = 0
    skip_project = 0
    skip_other = 0
    failed = 0
    created_users = []  # [(name, uid, password)]

    for i, row in enumerate(rows, 1):
        level = (row.get("LEVEL", "") or "").strip().lower() or "project"
        cl = (row.get("CLUSTER", "") or "").strip()
        proj = (row.get("PROJECT", "") or "").strip()
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
        role = (row.get("ROLE", "") or "").strip()
        role_id_col = (row.get("ROLE_ID", "") or "").strip()

        # 跳过无效行
        if not cl or cl == "-" or not ug or ug in ("(无成员)", "-") or not role or role == "-":
            skip_other += 1
            continue
        if level == "global":
            skip_other += 1
            continue

        # ── 1. 查用户 ──
        if ug.lower() not in user_cache:
            user_cache[ug.lower()] = find_user(ug, exact_idx, bare_idx)
        user = user_cache[ug.lower()]

        if not user:
            if args.auto_create_users:
                print("  🔧 创建本地用户: {}...".format(ug), end=" ", file=sys.stderr)
                ok_create, uid, pwd = create_user(url, token, ug)
                if ok_create and uid:
                    print("OK ({})".format(uid), file=sys.stderr)
                    user = {"id": uid, "type": "local"}
                    user_cache[ug.lower()] = user
                    # 记录密码
                    created_users.append((ug, uid, pwd))
                else:
                    print("FAIL: {}".format(pwd), file=sys.stderr)
                    skip_user += 1
                    continue
            else:
                skip_user += 1
                print("  ⏭ 用户不存在: {} (行{})".format(ug, i))
                continue

        # ── 2. 查角色 ──
        cache_key = (role_id_col, role, level)
        if cache_key not in role_cache:
            role_cache[cache_key] = resolve_role(url, token, role, role_id_col, level)
        role_tmpl, role_exists = role_cache[cache_key]

        if not role_tmpl or not role_exists:
            skip_role += 1
            print("  ⏭ 角色不存在: {} / {} (行{})".format(role, role_id_col, i))
            continue

        # ── 3. 查集群 ──
        if cl not in cluster_cache:
            cluster_cache[cl] = resolve_cluster(url, token, cl)
        cid = cluster_cache[cl]
        if not cid:
            skip_cluster += 1
            print("  ⏭ 集群不存在: {} (行{})".format(cl, i))
            continue

        # ── 4. 查项目（project 级需要） ──
        pid_api = None
        if level == "project" and proj and proj != "-":
            proj_key = "{}:{}".format(cid, proj)
            if proj_key not in project_cache:
                project_cache[proj_key] = resolve_project(url, token, cid, proj)
            pid_api = project_cache[proj_key]
            if not pid_api:
                skip_project += 1
                print("  ⏭ 项目不存在: {}/{} (行{})".format(cl, proj, i))
                continue

        # ── 5. 确定 principal 参数 ──
        if user["type"] == "local" or user["id"].startswith("user-"):
            key, value = "userId", user["id"]
        elif user["type"] == "group":
            key, value = "groupPrincipalId", user["id"]
        else:
            key, value = "userPrincipalId", user["id"]

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
            print("[DRY] {} as {} → {}/{}".format(ug, role, cl, proj or "-"))
            ok += 1
            continue

        code, data = api(url, token, "POST", api_path, body)
        if code in (200, 201):
            print("  ✅ {} as {} → {}/{}".format(ug, role, cl, proj or "-"))
            ok += 1
        elif code == 409:
            print("  ⏭ 已存在: {} as {} → {}/{}".format(ug, role, cl, proj or "-"))
            ok += 1
        else:
            msg = data.get("message", data.get("error", str(data)))[:120]
            print("  ❌ {} as {} → {}/{}: {}".format(ug, role, cl, proj or "-", msg))
            failed += 1

    print()
    print("=" * 50)
    print("  ✅ 成功: {}".format(ok))
    print("  ⏭ 跳过 (用户不存在): {}".format(skip_user))
    print("  ⏭ 跳过 (角色不存在): {}".format(skip_role))
    print("  ⏭ 跳过 (集群不存在): {}".format(skip_cluster))
    print("  ⏭ 跳过 (项目不存在): {}".format(skip_project))
    print("  ⏭ 跳过 (其他): {}".format(skip_other))
    if created_users:
        print("  🔧 新建用户: {}".format(len(created_users)))
    if failed:
        print("  ❌ 失败: {}".format(failed))
    print("=" * 50)

    # 写密码文件
    if created_users:
        pwd_file = "user_passwords.txt"
        with open(pwd_file, "w", encoding="utf-8") as f:
            f.write("# 自动创建的用户密码 — {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S")))
            f.write("# 格式: display_name,user_id,password\n\n")
            for name, uid, pwd in created_users:
                if pwd:
                    f.write("{},{},{}\n".format(name, uid, pwd))
        print("\n密码已保存: {} ({} 个用户)".format(pwd_file, len(created_users)), file=sys.stderr)


if __name__ == "__main__":
    main()
