#!/usr/bin/env python3
"""
rancher_rbac_apply.py — 从 rbac CSV 批量绑定角色 (支持 global/cluster/project)
===========================================================================
读取 rancher_rbac.py 输出的 CSV，在目标集群/项目中执行角色绑定。

支持层级:
  project — ProjectRoleTemplateBinding  (原有行为)
  cluster — ClusterRoleTemplateBinding (新增)
  global  — 默认跳过 (全局角色通常手动管理)

用户缺失处理:
  --check-principals    预检模式：扫描 CSV 所有用户/组，报告目标端存在/缺失
  --auto-create-users   自动创建缺失的本地用户 (随机密码 → user_passwords.txt)
  --auto-map-users      按 displayName 自动匹配目标端用户，更新 CSV 中的 PRINCIPAL_ID
                        (含模糊匹配：忽略 email domain 和 .-_ 分隔符差异)
  --user-mapping FILE   手动用户映射 CSV: source_name,target_name（优先于自动匹配）

角色缺失处理:
  --skip-missing-roles  预检目标端角色，跳过不存在的角色绑定

用法:
  # 源端导出
  python3 rancher_rbac.py -c poc -o rbac.csv

  # 预检用户/组是否存在
  python3 rancher_rbac_apply.py --from-csv rbac.csv --check-principals

  # 跨 SSO 迁移：自动映射 + 模糊匹配 + 跳过缺失角色
  python3 rancher_rbac_apply.py --from-csv rbac.csv \
      --auto-map-users --auto-create-users --skip-missing-roles --dry-run

  # 带手动映射（模糊匹配不了的边缘情况）
  python3 rancher_rbac_apply.py --from-csv rbac.csv \
      --auto-map-users --user-mapping manual_map.csv --auto-create-users

  # 执行绑定
  python3 rancher_rbac_apply.py --from-csv rbac.csv \
      --auto-map-users --auto-create-users --skip-missing-roles

  # 同 SSO 迁移（不需要映射）
  python3 rancher_rbac_apply.py --from-csv rbac.csv

  # 跨集群迁移（含集群级绑定）
  python3 rancher_rbac_apply.py --from-csv rbac.csv --map-cluster poc=prod

CSV 格式 (rancher_rbac.py 输出):
  LEVEL,CLUSTER,PROJECT,USER_GROUP,TYPE,ROLE,PRINCIPAL_ID,ROLE_ID
  global,-,-,admin,User,Admin,user-qlb5m,admin
  cluster,poc,-,admin,User,Cluster Owner,user-qlb5m,cluster-owner
  project,poc,Default,admin,User,Owner,user-qlb5m,project-owner

env 文件: 同目录 env.txt
"""

import os, sys, json, csv, re, time, ssl, secrets, string

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

# project 级角色名 → API 中的 roleTemplateId
ROLE_REVERSE = {
    "owner":    "project-owner",
    "member":   "project-member",
    "readonly": "read-only",
}

# cluster 级角色名 → API 中的 roleTemplateId
CLUSTER_ROLE_REVERSE = {
    "cluster owner":    "cluster-owner",
    "cluster member":   "cluster-member",
    "cluster admin":    "cluster-admin",
    "cluster viewer":   "cluster-viewer",
    "nodes view":       "nodes-view",
    "nodes manage":     "nodes-manage",
    "projects create":  "projects-create",
    "projects view":    "projects-view",
    "storage manage":   "storage-manage",
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
                return (409, {"error": "already exists"})
            if e.code in (404, 422, 400):
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
    """分页获取全量"""
    all_items = []
    page = 0
    marker = None
    sep = "&" if "?" in path else "?"
    while True:
        p = "{}{}limit={}".format(path, sep, 1000)
        if marker:
            p += "&continue={}".format(marker)
        code, data = api(url, token, "GET", p)
        if code != 200:
            break
        items = data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        page += 1
        pagination = data.get("pagination", {})
        if pagination.get("next"):
            marker = pagination["next"]
        elif len(items) < 1000:
            break
        else:
            marker = items[-1].get("id", "")
    return all_items


def load_local_users(url, token):
    """缓存所有本地用户: {displayName_lower: userId, username_lower: userId}"""
    items = api_paginated(url, token, "v3/users")
    users = {}
    for u in items:
        uid = u["id"]
        dn = (u.get("displayName") or "").lower()
        un = (u.get("username") or "").lower()
        if dn:
            users[dn] = uid
        if un and un not in users:
            users[un] = uid
    print("# 已缓存 {} 个本地用户".format(len(items)), file=sys.stderr)
    return users


def load_all_users_raw(url, token):
    """返回所有本地用户的完整对象列表"""
    return api_paginated(url, token, "v3/users")


def search_principal(url, token, name, principal_type=None):
    """按名称搜索 principal（外部用户/组）"""
    params = "?name={}".format(quote(name, safe=""))
    code, data = api(url, token, "GET", "v3/principals" + params)
    if code == 200:
        for p in data.get("data", []):
            pname = (p.get("displayName") or p.get("loginName") or p.get("name") or "").lower()
            if pname == name.lower():
                return p["id"]

    code, data = api(url, token, "GET", "v3/principals")
    if code == 200:
        for p in data.get("data", []):
            pname = (p.get("displayName") or p.get("loginName") or p.get("name") or "").lower()
            if pname == name.lower():
                return p["id"]
    return None


def resolve_cluster_id(url, token, name_or_id):
    """通过 name 或 id 解析集群 id"""
    path = "v3/clusters/{}".format(name_or_id)
    code, data = api(url, token, "GET", path)
    if code == 200 and data.get("id"):
        return data["id"]
    code, data = api(url, token, "GET", "v3/clusters")
    if code == 200:
        for item in data.get("data", []):
            if item.get("name") == name_or_id or item.get("id") == name_or_id:
                return item["id"]
    die("集群不存在: {}".format(name_or_id))


def resolve_project_id(url, token, cluster_id, name_or_id):
    """通过 name 或 id 解析项目完整 id"""
    if ":" in name_or_id:
        return name_or_id
    code, data = api(url, token, "GET", "v3/projects?clusterId={}".format(cluster_id))
    if code == 200:
        for item in data.get("data", []):
            if item.get("name") == name_or_id or item.get("id") == name_or_id:
                return item["id"]
    die("项目不存在于集群 {}: {}".format(cluster_id, name_or_id))


def resolve_principal(url, token, user_group, ptype, users_cache, principal_cache):
    """将 USER_GROUP 解析为 API 所需参数。返回 (key, value) 或 None"""
    name_lower = user_group.lower()
    cache_key = "{}:{}:{}".format(ptype, name_lower, url)

    if cache_key in principal_cache:
        return principal_cache[cache_key]

    result = None

    if ptype.upper() == "USER":
        if name_lower in users_cache:
            result = ("userId", users_cache[name_lower])
        else:
            pid = search_principal(url, token, user_group)
            if pid:
                result = ("userPrincipalId", pid)

    elif ptype.upper() == "GROUP":
        pid = search_principal(url, token, user_group)
        if pid:
            result = ("groupPrincipalId", pid)

    principal_cache[cache_key] = result
    return result


def create_local_user(url, token, username, display_name):
    """创建本地用户，返回 (success, user_id, password)"""
    # 生成随机密码
    password = "".join(secrets.choice(string.ascii_letters + string.digits)
                       for _ in range(16))

    body = {
        "username": username,
        "password": password,
        "name": display_name or username,
        "type": "user",
    }
    code, data = api(url, token, "POST", "v3/users", body)
    if code in (200, 201):
        return (True, data.get("id", ""), password)
    elif code == 409:
        # 用户已存在，直接返回
        return (True, None, "")  # caller 需重新查找 ID
    else:
        return (False, None, data.get("error", str(data)))


def find_local_user_by_name(url, token, name):
    """在本地用户中按 displayName 或 username 查找，返回 user_id 或 None"""
    users = load_all_users_raw(url, token)
    name_lower = name.lower()
    for u in users:
        if (u.get("displayName") or "").lower() == name_lower:
            return u["id"]
        if (u.get("username") or "").lower() == name_lower:
            return u["id"]
    return None


def normalize_name_for_match(name):
    """
    模糊匹配用：去掉 email 域、分隔符，统一小写。
    e.Boran.Yang 和 e-Boran.Yang@geely.com 都会变成 eboranyang。
    """
    name = re.sub(r'@.*$', '', name)     # 去 email domain
    name = name.lower()
    name = re.sub(r'[.\-_\s]', '', name)  # 去分隔符
    return name


def build_target_user_map(url, token):
    """
    构建目标 Rancher 用户/组映射表。
    拉取所有本地用户 + SSO principals，按 displayName/username/loginName 建索引。
    本地用户优先（不需要 SSO 认证即可绑定）。
    返回: (exact_map, normalized_map)
      exact_map: {name_lower: {"type": ..., "principal_id": ..., "user_id": ...}}
      normalized_map: {normalized_name: [...]}  (一对多，处理冲突)
    """
    mapping = {}
    norm_index = {}  # normalized_name → [match_info, ...]

    def _add(name, info):
        nl = name.lower().strip()
        if nl and nl not in mapping:
            info_with_dn = dict(info)
            info_with_dn["display_name"] = name
            mapping[nl] = info_with_dn
            # 归一化索引（模糊匹配用）
            nn = normalize_name_for_match(name)
            if nn:
                if nn not in norm_index:
                    norm_index[nn] = []
                norm_index[nn].append(info_with_dn)

        # 去 domain 索引：e-Xiao.Wang4@geely.com → e-xiao.wang4
        base = re.sub(r'@.*$', '', name).strip()
        bl = base.lower().strip()
        if bl and bl != nl and bl not in mapping:
            info_with_dn = dict(info)
            info_with_dn["display_name"] = name
            mapping[bl] = info_with_dn

    # 1. 本地用户（优先级最高）
    local_users = load_all_users_raw(url, token)
    for u in local_users:
        uid = u["id"]
        info = {"type": "User", "principal_id": uid, "user_id": uid}
        for key in (u.get("displayName"), u.get("username")):
            key = (key or "").strip()
            if key:
                _add(key, info)

    # 2. SSO/外部 principals
    print("# 正在拉取 principals...", file=sys.stderr)
    principals = api_paginated(url, token, "v3/principals")
    for p in principals:
        pid = p["id"]
        raw_type = p.get("principalType", "user")
        ptype = "Group" if raw_type == "group" else "User"
        info = {"type": ptype, "principal_id": pid, "user_id": pid}
        for field in ("displayName", "loginName", "name"):
            name = (p.get(field) or "").strip()
            if name:
                _add(name, info)
                break  # 只用第一个有效字段

    return mapping, norm_index


def auto_map_csv_users(rows, exact_map, norm_map=None):
    """
    按 displayName 匹配，就地更新 CSV 行的 PRINCIPAL_ID 和 TYPE。
    1. 先精确匹配
    2. 未匹配的尝试模糊匹配（去 domain/分隔符）
    返回 (mapped_rows_detail, unmapped_rows_detail, fuzzy_matches) 用于报告。
    """
    mapped = []      # [(ug, old_pid, old_type, new_pid, new_type, src, target_dn)]
    fuzzy = []       # 模糊匹配的子集
    unmapped = []    # [(ug, old_type, old_pid)]

    # 先收集唯一用户（去重）
    seen = set()
    unique_users = []
    for row in rows:
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or row.get("user_group", "")).strip()
        ptype = (row.get("TYPE", "") or row.get("type", "")).strip()
        pid = (row.get("PRINCIPAL_ID", "") or row.get("principal_id", "")).strip()
        if ug in ("(无成员)", "-", "") or not ug:
            continue
        key = ug.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_users.append((ug, ptype, pid))

    # 逐用户匹配
    match_cache = {}   # name_lower → match dict or None

    for ug, old_type, old_pid in unique_users:
        key = ug.lower()
        if key in match_cache:
            match, source = match_cache[key]
        elif key in exact_map:
            match = exact_map[key]
            source = "exact"
            match_cache[key] = (match, source)
        elif norm_map:
            # 模糊匹配
            nn = normalize_name_for_match(ug)
            candidates = norm_map.get(nn, [])
            if len(candidates) == 1:
                match = candidates[0]
                source = "fuzzy"
                match_cache[key] = (match, source)
            elif len(candidates) > 1:
                # 多人归一化后冲突，不自动匹配
                match = None
                source = None
                match_cache[key] = (None, None)
                print("  [WARN] 模糊匹配冲突: {} → {} ({} 个候选人,跳过)".format(
                    ug, nn, len(candidates)), file=sys.stderr)
            else:
                match = None
                source = None
                match_cache[key] = (None, None)
        else:
            match = None
            source = None
            match_cache[key] = (None, None)

        if match:
            target_dn = match.get("display_name", "")
            entry = (ug, old_pid, old_type, match["principal_id"], match["type"], source, target_dn)
            mapped.append(entry)
            if source == "fuzzy":
                fuzzy.append(entry)
        else:
            unmapped.append((ug, old_type, old_pid))

    # 就地更新所有行
    for row in rows:
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
        if not ug or ug in ("(无成员)", "-"):
            continue
        m, _ = match_cache.get(ug.lower(), (None, None))
        if m:
            for k in ("PRINCIPAL_ID", "principal_id"):
                if k in row:
                    row[k] = m["principal_id"]
            for k in ("TYPE", "type"):
                if k in row:
                    row[k] = m["type"]

    return mapped, unmapped, fuzzy


def check_principals(url, token, rows):
    """
    预检所有 CSV 中的用户/组在目标端是否存在。
    返回统计信息。
    """
    # 收集所有唯一用户/组
    entities = {}  # key: (user_group, ptype, principal_id)
    for row in rows:
        level = (row.get("LEVEL", "") or row.get("level", "")).strip().lower()
        # 跳过 global 和占位行
        cl = (row.get("CLUSTER", "") or row.get("cluster", "")).strip()
        if not cl or cl == "-" or level == "global":
            continue
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or row.get("user_group", "")).strip()
        ptype = (row.get("TYPE", "") or row.get("type", "")).strip()
        pid = (row.get("PRINCIPAL_ID", "") or row.get("principal_id", "")).strip()
        role = (row.get("ROLE", "") or row.get("role", "")).strip()

        if ug in ("(无成员)", "-", "") or role in ("-", ""):
            continue
        if not pid or pid == "-":
            continue

        key = (ug, ptype, pid)
        if key not in entities:
            entities[key] = {"roles": set(), "clusters": set()}
        entities[key]["roles"].add(role)
        entities[key]["clusters"].add(cl)

    # 加载目标端所有用户/principal
    local_users = load_all_users_raw(url, token)
    local_ids = {u["id"] for u in local_users}
    local_names = set()
    for u in local_users:
        dn = (u.get("displayName") or "").lower()
        un = (u.get("username") or "").lower()
        if dn:
            local_names.add(dn)
        if un:
            local_names.add(un)

    # 查 principals
    print("# 正在查询目标端 principals...", file=sys.stderr)
    principals_data = api_paginated(url, token, "v3/principals?limit=2000")
    principal_ids = {p.get("id", "") for p in principals_data}
    # 也收集 principal 的 loginName / displayName
    principal_names = set()
    for p in principals_data:
        for field in ("loginName", "displayName", "name"):
            v = p.get(field, "")
            if v:
                principal_names.add(v.lower())

    print()
    print("=" * 70)
    print("  Principal 预检报告 — 目标端: {}".format(url))
    print("=" * 70)

    found_count = 0
    missing_count = 0
    missing_local = []
    missing_sso = []

    for (ug, ptype, pid), info in sorted(entities.items()):
        found = False
        source = ""

        # 本地用户: PID 以 "user-" 开头
        if pid.startswith("user-"):
            if pid in local_ids:
                found = True
                source = "local (by userId)"
            elif ug.lower() in local_names:
                found = True
                source = "local (by name match)"

        # Group principal
        elif ptype.upper() == "GROUP":
            if pid in principal_ids:
                found = True
                source = "group principal"
            elif ug.lower() in principal_names:
                found = True
                source = "group principal (by name)"

        # SSO user principal (如 u-xxx)
        else:
            if pid in principal_ids:
                found = True
                source = "principal"
            elif ug.lower() in local_names:
                found = True
                source = "local (by name)"
            elif ug.lower() in principal_names:
                found = True
                source = "principal (by name)"

        if found:
            found_count += 1
            print("  [✓] {} ({}, PID={}) — {} → 角色: {}".format(
                ug, ptype, pid, source, ", ".join(sorted(info["roles"]))))
        else:
            missing_count += 1
            print("  [✗] {} ({}, PID={}) — 目标端不存在".format(ug, ptype, pid))
            if pid.startswith("user-"):
                missing_local.append((ug, ptype, pid))
            elif ptype.upper() == "GROUP":
                pass  # 组需要配置 auth provider
            else:
                missing_sso.append((ug, ptype, pid))

    print()
    print("  总计: {} 个用户/组".format(len(entities)))
    print("    ✓ 存在: {}".format(found_count))
    print("    ✗ 缺失: {}".format(missing_count))

    if missing_local:
        print("\n  缺失本地用户 (可用 --auto-create-users 自动创建):")
        for name, ptype, pid in missing_local:
            print("    - {} ({})".format(name, pid))

    if missing_sso:
        print("\n  缺失 SSO 用户 (需确保目标端配置了相同的认证源):")
        for name, ptype, pid in missing_sso:
            print("    - {} ({})".format(name, pid))

    if not missing_local and not missing_sso:
        print("\n  所有用户/组均可解析，可以直接执行绑定。")

    return found_count, missing_count, missing_local, missing_sso


def check_roles_exist(url, token, rows):
    """
    检查 CSV 中的 ROLE_ID 在目标 Rancher 是否存在。
    返回 (existing_roles, missing_roles)。
    """
    role_ids = set()
    for row in rows:
        rid = (row.get("ROLE_ID", "") or row.get("role_id", "")).strip()
        if rid and rid != "-":
            role_ids.add(rid)

    if not role_ids:
        return set(), set()

    print("# 正在检查 {} 个角色在目标端是否存在...".format(len(role_ids)), file=sys.stderr)
    existing = set()
    missing = set()
    role_cache = {}  # cache role lookup results

    for rid in sorted(role_ids):
        if rid in role_cache:
            if role_cache[rid]:
                existing.add(rid)
            else:
                missing.add(rid)
            continue

        # 先查 roleTemplates (project/cluster 级)
        code, data = api(url, token, "GET", "v3/roleTemplates/{}".format(rid))
        if code == 200:
            existing.add(rid)
            role_cache[rid] = True
            continue

        # 再查 globalRoles (global 级)
        code, data = api(url, token, "GET", "v3/globalRoles/{}".format(rid))
        if code == 200:
            existing.add(rid)
            role_cache[rid] = True
            continue

        missing.add(rid)
        role_cache[rid] = False

    print()
    print("=" * 60)
    print("  角色预检报告")
    print("=" * 60)
    print("  总计: {} 个角色".format(len(role_ids)))
    print("    ✓ 存在: {}".format(len(existing)))
    print("    ✗ 缺失: {}".format(len(missing)))

    if existing:
        print("\n  存在的角色:")
        for rid in sorted(existing):
            print("    - {}".format(rid))
    if missing:
        print("\n  缺失的角色 (可用 --skip-missing-roles 跳过):")
        for rid in sorted(missing):
            print("    - {}".format(rid))
    print("=" * 60)
    print()

    return existing, missing


def auto_create_users(url, token, missing_local):
    """自动创建缺失的本地用户，返回 {display_name: user_id} 和密码列表"""
    created = {}
    passwords = []

    for name, ptype, pid in missing_local:
        # 生成用户名：优先用 displayName 的拼音混淆，否则用原始名
        # displayName 可能的格式：如 "poc" "poc2" 这类简短名直接用作 username
        username = name.lower().replace(" ", "_")
        # 清理非字母数字字符
        username = re.sub(r'[^a-z0-9_]', '', username)
        if not username:
            username = "user_imported_" + str(len(created) + 1)

        print("  [创建] {} (username={})...".format(name, username), file=sys.stderr, end=" ")
        ok, uid, password = create_local_user(url, token, username, name)
        if ok:
            if uid:
                created[name.lower()] = uid
                passwords.append((username, name, password, uid))
                print("OK (id={})".format(uid), file=sys.stderr)
            else:
                # 用户已存在，重查 ID
                uid = find_local_user_by_name(url, token, name)
                if uid:
                    created[name.lower()] = uid
                    print("已存在 (id={})".format(uid), file=sys.stderr)
                else:
                    print("FAIL (已存在但查不到ID)", file=sys.stderr)
        else:
            print("FAIL: {}".format(password), file=sys.stderr)  # password 此时是错误信息

    # 写密码文件
    if passwords:
        pwd_file = "user_passwords.txt"
        with open(pwd_file, "w", encoding="utf-8") as f:
            f.write("# 自动创建的用户密码 — {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S")))
            f.write("# 格式: username,display_name,password,user_id\n")
            f.write("# 首次登录后请立即修改密码\n\n")
            for uname, dname, pwd, uid in passwords:
                f.write("{},{},{},{}\n".format(uname, dname, pwd, uid))
        print("# 密码已保存到: {} ({} 个用户)".format(pwd_file, len(passwords)),
              file=sys.stderr)

    return created


def apply_binding(url, token, level, cluster, project, user_group, ptype, role,
                  users_cache, principal_cache, cluster_map, dry_run,
                  principal_id=None, role_id=None, created_users=None):
    """创建一条角色绑定，根据 LEVEL 路由到不同 API"""
    if not cluster or cluster == "-":
        return True
    if level == "global":
        print("  [SKIP] 全局角色绑定需手动处理: {} ({} → {})".format(
            user_group, ptype, role), file=sys.stderr)
        return True

    target_cluster = cluster_map.get(cluster, cluster)
    cid = resolve_cluster_id(url, token, target_cluster)

    if level == "cluster":
        return apply_cluster_binding(url, token, cid, target_cluster,
                                     user_group, ptype, role,
                                     users_cache, principal_cache,
                                     dry_run, principal_id, role_id,
                                     created_users)
    else:
        pid = resolve_project_id(url, token, cid, project)
        return apply_project_binding(url, token, cid, pid, target_cluster, project,
                                     user_group, ptype, role,
                                     users_cache, principal_cache,
                                     dry_run, principal_id, role_id,
                                     created_users)


def resolve_role_template(role_display, role_id, level):
    """解析 roleTemplateId：优先 role_id 列，否则从 display name 推断"""
    if role_id and role_id != "-":
        return role_id
    if not role_display or role_display == "-":
        return None

    role_lower = role_display.lower()

    if level == "cluster":
        rt = CLUSTER_ROLE_REVERSE.get(role_lower)
        if rt:
            return rt
        rt = ROLE_REVERSE.get(role_lower)
        if rt:
            return rt
        return role_display

    rt = ROLE_REVERSE.get(role_lower)
    if rt:
        return rt
    return role_display


def apply_cluster_binding(url, token, cluster_id, cluster_name,
                          user_group, ptype, role,
                          users_cache, principal_cache,
                          dry_run, principal_id=None, role_id=None,
                          created_users=None):
    """创建 ClusterRoleTemplateBinding"""
    role_template = resolve_role_template(role, role_id, "cluster")
    if not role_template:
        print("  [SKIP] 无法解析集群角色: {}".format(role), file=sys.stderr)
        return False

    key, value = resolve_principal_with_create(
        url, token, user_group, ptype, principal_id,
        users_cache, principal_cache, created_users)

    if not key:
        print("  [SKIP] 未找到用户/组: {} ({}), 集群={} — 建议用 --check-principals 预检".format(
            user_group, ptype, cluster_name), file=sys.stderr)
        return False

    body = {
        "clusterId": cluster_id,
        "roleTemplateId": role_template,
        key: value,
        "type": "clusterRoleTemplateBinding",
    }

    if dry_run:
        print("[DRY] 集群绑定: cluster={} {}={} role={} → {}".format(
            cluster_name, key, value, role_template, role), file=sys.stderr)
        return True

    code, data = api(url, token, "POST", "v3/clusterRoleTemplateBindings", body)
    if code in (200, 201):
        print("  [OK] cluster:{} {} {} → {} as {}".format(
            cluster_name, ptype, user_group, cluster_id, role), file=sys.stderr)
        return True
    elif code == 409:
        print("  [SKIP] 集群绑定已存在: {} → {} ({})".format(
            user_group, cluster_name, role), file=sys.stderr)
        return True
    else:
        msg = data.get("message", data.get("error", str(data)))
        print("  [FAIL] cluster:{} {} → {} ({}): {}".format(
            cluster_name, user_group, cluster_id, role, msg), file=sys.stderr)
        return False


def apply_project_binding(url, token, cluster_id, project_id, cluster_name, project_name,
                          user_group, ptype, role,
                          users_cache, principal_cache,
                          dry_run, principal_id=None, role_id=None,
                          created_users=None):
    """创建 ProjectRoleTemplateBinding"""
    role_template = resolve_role_template(role, role_id, "project")
    if not role_template:
        print("  [SKIP] 无法解析项目角色: {}".format(role), file=sys.stderr)
        return False

    key, value = resolve_principal_with_create(
        url, token, user_group, ptype, principal_id,
        users_cache, principal_cache, created_users)

    if not key:
        print("  [SKIP] 未找到用户/组: {} ({}), 项目={} — 建议用 --check-principals 预检".format(
            user_group, ptype, project_name), file=sys.stderr)
        return False

    body = {
        "projectId": project_id,
        "roleTemplateId": role_template,
        key: value,
        "type": "projectRoleTemplateBinding",
    }

    if dry_run:
        print("[DRY] 绑定: cluster={} project={} {}={} role={} → {}".format(
            cluster_name, project_name, key, value, role_template, role), file=sys.stderr)
        return True

    code, data = api(url, token, "POST", "v3/projectroletemplatebindings", body)
    if code in (200, 201):
        print("  [OK] {} {} → {} as {}".format(ptype, user_group, project_id, role),
              file=sys.stderr)
        return True
    elif code == 409:
        print("  [SKIP] 绑定已存在: {} → {} ({})".format(
            user_group, project_name, role), file=sys.stderr)
        return True
    else:
        msg = data.get("message", data.get("error", str(data)))
        print("  [FAIL] {} → {} ({}/{}): {}".format(
            user_group, project_name, cluster_name, role, msg), file=sys.stderr)
        return False


def resolve_principal_with_create(url, token, user_group, ptype, principal_id,
                                  users_cache, principal_cache, created_users):
    """
    解析 principal：
    1. 优先用 PRINCIPAL_ID 直接作为绑定参数
    2. 否则回退到 displayName 查找
    3. 如果用户是 auto-created 的，使用新创建的 userId
    返回 (key, value) 或 (None, None)
    """
    # 如果用户是刚 auto-created 的，直接用新的 userId
    if created_users and user_group.lower() in created_users:
        return ("userId", created_users[user_group.lower()])

    # 优先使用 CSV 中的 PRINCIPAL_ID
    if principal_id and principal_id != "-":
        if ptype.upper() == "GROUP":
            return ("groupPrincipalId", principal_id)
        elif principal_id.startswith("user-"):
            return ("userId", principal_id)
        else:
            return ("userPrincipalId", principal_id)

    # 回退到名称查找
    resolved = resolve_principal(url, token, user_group, ptype,
                                 users_cache, principal_cache)
    if resolved:
        return resolved

    return (None, None)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="从 rbac CSV 批量绑定角色 (global/cluster/project)")
    parser.add_argument("--from-csv", required=True, help="rancher_rbac.py 输出的 CSV 文件")
    parser.add_argument("-e", "--env", help="env 文件路径")
    parser.add_argument("--map-cluster", help="集群名映射: 旧名=新名,旧名2=新名2")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--check-principals", action="store_true",
                        help="预检模式：检查 CSV 中所有用户/组在目标端是否存在")
    parser.add_argument("--auto-create-users", action="store_true",
                        help="自动创建 CSV 中缺失的本地用户（SSO 用户跳过）")
    parser.add_argument("--auto-map-users", action="store_true",
                        help="按 displayName 自动匹配目标端用户，更新 CSV 中的 PRINCIPAL_ID")
    parser.add_argument("--skip-missing-roles", action="store_true",
                        help="跳过目标端不存在的角色绑定")
    parser.add_argument("--user-mapping", help="手动用户映射文件 CSV: source_name,target_name")
    args = parser.parse_args()

    url, token = load_env(args.env)
    print("# Rancher: {}".format(url), file=sys.stderr)

    cluster_map = {}
    if args.map_cluster:
        for pair in args.map_cluster.split(","):
            pair = pair.strip()
            if "=" in pair:
                src, dst = pair.split("=", 1)
                cluster_map[src.strip()] = dst.strip()
        print("# 集群映射: {}".format(cluster_map), file=sys.stderr)

    # 读取 CSV
    with open(args.from_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        die("CSV 为空或表头不匹配")

    # ── 预检模式 ──
    if args.check_principals:
        if args.auto_map_users:
            # 直接用模糊匹配结果做预检，不调旧的 check_principals
            print("# 正在从目标端拉取用户列表...", file=sys.stderr)
            exact_map, norm_map = build_target_user_map(url, token)
            print("# 标识: 精确 {} 个, 归一化 {} 个".format(
                len(exact_map), len(norm_map)), file=sys.stderr)

            if args.user_mapping:
                print("# 加载手动映射: {}...".format(args.user_mapping), file=sys.stderr)
                with open(args.user_mapping, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(",")
                        if len(parts) >= 2:
                            src, dst = parts[0].strip(), parts[1].strip()
                            if src and dst:
                                match = exact_map.get(dst.lower())
                                if not match and norm_map:
                                    nn = normalize_name_for_match(dst)
                                    candidates = norm_map.get(nn, [])
                                    if len(candidates) == 1:
                                        match = candidates[0]
                                if match:
                                    exact_map[src.lower()] = match

            mapped, unmapped, fuzzy = auto_map_csv_users(rows, exact_map, norm_map)
            total = len(mapped) + len(unmapped)
            exact_count = len(mapped) - len(fuzzy)

            # 映射明细报告
            print()
            print("=" * 75)
            print("  用户映射明细 (--check-principals) — {}".format(url))
            print("=" * 75)
            total = len(mapped) + len(unmapped)
            exact_count = len(mapped) - len(fuzzy)
            print("  绑定条目: {} | 精确: {} | 模糊: {} | 未匹配: {}".format(
                total, exact_count, len(fuzzy), len(unmapped)))
            print()

            if mapped:
                print("  {:<3} {:<25} {:<25} {:<18} {:<18} {:<8}".format(
                    "", "源 displayName", "→ 目标 displayName", "旧 PID", "新 PID", "方式"))
                print("  {:<3} {:<25} {:<25} {:<18} {:<18} {:<8}".format(
                    "", "─"*25, "─"*25, "─"*18, "─"*18, "─"*8))
                for ug, old_pid, _, new_pid, new_type, src, target_dn in mapped:
                    icon = "✓" if src == "exact" else "~"
                    short_old = (old_pid[:16] + "..") if len(old_pid) > 17 else old_pid
                    short_new = (new_pid[:16] + "..") if len(new_pid) > 17 else new_pid
                    print("  {:<3} {:<25} {:<25} {:<18} {:<18} {}".format(
                        icon, ug[:25], target_dn[:25], short_old, short_new,
                        "精确" if src == "exact" else "模糊"))

            if unmapped:
                print()
                unmapped_local = [(ug, pt, pid) for ug, pt, pid in unmapped if pid and pid.startswith("user-")]
                unmapped_sso = [(ug, pt, pid) for ug, pt, pid in unmapped if pid and not pid.startswith("user-") and pid != "-"]
                print("  未匹配 ({})：".format(len(unmapped)))
                for ug, pt, pid in unmapped:
                    print("    ✗ {:<25} {:<18} {}".format(ug[:25], pid, pt))
                if unmapped_local:
                    print("\n  → 本地用户 {} 个，可用 --auto-create-users 创建".format(len(unmapped_local)))
                if unmapped_sso:
                    print("  → SSO 用户 {} 个，需在新 Rancher 登录后重试".format(len(unmapped_sso)))
            else:
                print("  ✅ 所有用户均已映射，可以直接执行绑定。")
            print("=" * 75)
            print()
        else:
            check_principals(url, token, rows)
        return

    # ── 加载用户缓存 ──
    users_cache = {}
    principal_cache = {}
    created_users = {}

    # ── 用户映射 (--auto-map-users) ──
    if args.auto_map_users:
        print("# 正在从目标端拉取用户列表...", file=sys.stderr)
        exact_map, norm_map = build_target_user_map(url, token)
        print("# 精确标识: {} 个, 模糊标识: {} 个 (去 domain/分隔符)".format(
            len(exact_map), len(norm_map)), file=sys.stderr)

        # 手动映射文件
        manual_map = {}
        if args.user_mapping:
            print("# 加载手动映射文件: {}...".format(args.user_mapping), file=sys.stderr)
            with open(args.user_mapping, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(",")
                    if len(parts) >= 2:
                        src = parts[0].strip()
                        dst = parts[1].strip()
                        if src and dst:
                            manual_map[src.lower()] = dst
            print("# 手动映射: {} 条".format(len(manual_map)), file=sys.stderr)

        # 将手动映射注入到 exact_map
        if manual_map:
            for src_name, dst_name in manual_map.items():
                # 在目标端查找 dst_name
                match = exact_map.get(dst_name.lower())
                if not match and norm_map:
                    nn = normalize_name_for_match(dst_name)
                    candidates = norm_map.get(nn, [])
                    if len(candidates) == 1:
                        match = candidates[0]
                if match:
                    exact_map[src_name] = match
                else:
                    print("  [WARN] 手动映射目标未找到: {} → {}".format(
                        src_name, dst_name), file=sys.stderr)

        mapped, unmapped, fuzzy = auto_map_csv_users(rows, exact_map, norm_map)

        # 映射报告
        print()
        print("=" * 75)
        print("  用户映射明细 — {}".format(url))
        print("=" * 75)
        total = len(mapped) + len(unmapped)
        exact_count = len(mapped) - len(fuzzy)
        print("  绑定条目: {} | 精确: {} | 模糊: {} | 未匹配: {}".format(
            total, exact_count, len(fuzzy), len(unmapped)))
        print()

        if mapped:
            print("  {:<3} {:<25} {:<25} {:<18} {:<18} {:<8}".format(
                "", "源 displayName", "→ 目标 displayName", "旧 PID", "新 PID", "方式"))
            print("  {:<3} {:<25} {:<25} {:<18} {:<18} {:<8}".format(
                "", "─"*25, "─"*25, "─"*18, "─"*18, "─"*8))
            for ug, old_pid, _, new_pid, new_type, src, target_dn in mapped:
                icon = "✓" if src == "exact" else ("~" if src == "fuzzy" else "M")
                # 截断长 PID
                short_old = (old_pid[:16] + "..") if len(old_pid) > 17 else old_pid
                short_new = (new_pid[:16] + "..") if len(new_pid) > 17 else new_pid
                print("  {:<3} {:<25} {:<25} {:<18} {:<18} {}".format(
                    icon, ug[:25], target_dn[:25], short_old, short_new,
                    "精确" if src == "exact" else "模糊"))

        if unmapped:
            print()
            unmapped_local = [(ug, pt, pid) for ug, pt, pid in unmapped if pid and pid.startswith("user-")]
            unmapped_sso = [(ug, pt, pid) for ug, pt, pid in unmapped if pid and not pid.startswith("user-") and pid != "-"]
            if unmapped:
                print("  未匹配 ({})：".format(len(unmapped)))
                for ug, pt, pid in unmapped:
                    print("    ✗ {:<25} {:<18} {}".format(ug[:25], pid, pt))
            if unmapped_local:
                print("\n  → 本地用户 {} 个，可用 --auto-create-users 创建".format(len(unmapped_local)))
            if unmapped_sso:
                print("  → SSO 用户 {} 个，需在新 Rancher 登录后重试".format(len(unmapped_sso)))
        print("=" * 75)
        print()

        # 如果也指定了 --auto-create-users，自动创建未匹配的本地用户
        if args.auto_create_users and unmapped_local:
            print("# 自动创建 {} 个未匹配本地用户...".format(len(unmapped_local)),
                  file=sys.stderr)
            created_users = auto_create_users(url, token, unmapped_local)
            # 更新对应行的 PRINCIPAL_ID
            for row in rows:
                ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
                if ug.lower() in created_users:
                    row["PRINCIPAL_ID"] = created_users[ug.lower()]
                    row["TYPE"] = "User"

        users_cache = load_local_users(url, token)

    elif args.auto_create_users:
        # 非映射模式：原逻辑
        users_cache = load_local_users(url, token)
        print("# 正在检查缺失的本地用户...", file=sys.stderr)
        _, _, missing_local, _ = check_principals(url, token, rows)
        if missing_local:
            print("# 发现 {} 个缺失本地用户，开始自动创建...".format(len(missing_local)),
                  file=sys.stderr)
            created_users = auto_create_users(url, token, missing_local)
            users_cache = load_local_users(url, token)
            print("# 刷新用户缓存完成", file=sys.stderr)
        else:
            print("# 所有用户均存在，无需创建", file=sys.stderr)
    else:
        users_cache = load_local_users(url, token)

    # ── 角色预检 ──
    missing_roles = set()
    if args.skip_missing_roles:
        existing_roles, missing_roles = check_roles_exist(url, token, rows)

    # ── 执行绑定 ──
    ok = 0
    fail = 0
    skip = 0

    for i, row in enumerate(rows, 1):
        level = (row.get("LEVEL", "") or row.get("level", "")).strip().lower()
        cl = (row.get("CLUSTER", "") or row.get("cluster", "")).strip()
        proj = (row.get("PROJECT", "") or row.get("project", "")).strip()
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or row.get("user_group", "")).strip()
        ptype = (row.get("TYPE", "") or row.get("type", "")).strip()
        role = (row.get("ROLE", "") or row.get("role", "")).strip()
        pid_col = (row.get("PRINCIPAL_ID", "") or row.get("principal_id", "")).strip()
        role_id_col = (row.get("ROLE_ID", "") or row.get("role_id", "")).strip()

        if not level:
            level = "project"

        if not cl or cl == "-":
            if level == "global":
                skip += 1
                continue
            skip += 1
            continue
        if ug in ("(无成员)", "-", "") and role in ("-", ""):
            skip += 1
            continue
        if role in ("-", "") or not ug:
            print("  [SKIP] 行{}: 缺少用户或角色 ({} / {})".format(i, ug, role),
                  file=sys.stderr)
            skip += 1
            continue

        # --skip-missing-roles: 跳过不存在的角色
        if missing_roles and role_id_col in missing_roles:
            print("  [SKIP] 行{}: 角色不存在 {} ({})".format(i, role_id_col, role),
                  file=sys.stderr)
            skip += 1
            continue

        try:
            rc = apply_binding(url, token, level, cl, proj, ug, ptype, role,
                               users_cache, principal_cache, cluster_map, args.dry_run,
                               principal_id=pid_col or None,
                               role_id=role_id_col or None,
                               created_users=created_users)
            if rc:
                ok += 1
            else:
                fail += 1
        except SystemExit:
            fail += 1
        except Exception as e:
            print("  [ERR] 行{} ({} / {}): {}".format(i, ug, proj, e), file=sys.stderr)
            fail += 1

    print("\n# 完成: {} 成功, {} 跳过, {} 失败".format(ok, skip, fail), file=sys.stderr)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
