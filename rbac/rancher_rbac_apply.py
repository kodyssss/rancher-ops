#!/usr/bin/env python3
"""
rancher_rbac_apply.py — 批量角色绑定

读取 rbac CSV，按 USER_GROUP 精确匹配目标端用户，执行绑定。
用户/角色/项目任一不存在则跳过。

用法:
  python3 rancher_rbac_apply.py --from-csv rbac.csv --dry-run
  python3 rancher_rbac_apply.py --from-csv rbac.csv

CSV 列: LEVEL,CLUSTER,PROJECT,USER_GROUP,TYPE,ROLE,PRINCIPAL_ID,ROLE_ID
"""

import os, sys, json, csv, re, time, ssl

try:
    from urllib.request import Request, urlopen, HTTPError
    from urllib.parse   import quote
except ImportError:
    from urllib2 import Request, urlopen, HTTPError
    from urllib import quote

MAX_RETRIES = 3
RETRY_BACKOFF = 2.0
TIMEOUT = 60

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

ROLE_MAP = {
    "owner": "project-owner", "member": "project-member", "readonly": "read-only",
    "cluster owner": "cluster-owner", "cluster member": "cluster-member",
    "cluster admin": "cluster-admin", "cluster viewer": "cluster-viewer",
}


def load_env(env_path=None):
    if env_path is None:
        d = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(d, "env.txt"), os.path.join(d, "env"), os.path.join(os.getcwd(), "env.txt")]:
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
    return env_vars.get("RANCHER_URL", "").rstrip("/"), env_vars.get("RANCHER_TOKEN", "")


def api(url, token, method, path, body=None):
    full = url + "/" + path.lstrip("/")
    data = json.dumps(body).encode("utf-8") if body else None
    for a in range(MAX_RETRIES):
        try:
            req = Request(full, data=data)
            req.add_header("Authorization", "Bearer {}".format(token))
            req.add_header("Accept", "application/json")
            if data:
                req.add_header("Content-Type", "application/json")
            req.get_method = lambda m=method: m.upper()
            resp = urlopen(req, timeout=TIMEOUT, context=SSL_CTX)
            raw = resp.read().decode("utf-8")
            return resp.getcode(), (json.loads(raw) if raw.strip() else {})
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:300]
            except:
                pass
            if e.code == 401:
                print("ERROR: Token 无效", file=sys.stderr)
                sys.exit(1)
            if e.code in (404, 422, 409):
                return e.code, {"error": body}
            if a < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** a)
                continue
            return e.code, {"error": body}
        except Exception as e:
            if a < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** a)
                continue
            return 0, {"error": str(e)}
    return 0, {"error": "max retries"}


def paginated(url, token, path):
    items, marker = [], None
    sep = "&" if "?" in path else "?"
    while True:
        p = "{}{}limit=1000".format(path, sep)
        if marker:
            p += "&continue={}".format(marker)
        code, data = api(url, token, "GET", p)
        if code != 200:
            break
        batch = data.get("data", [])
        if not batch:
            break
        items.extend(batch)
        pag = data.get("pagination", {})
        if pag.get("next"):
            marker = pag["next"]
        elif len(batch) < 1000:
            break
        else:
            marker = batch[-1].get("id", "")
    return items


def find_user_by_name(url, token, name):
    """按 displayName 精确查找用户，返回 {id, type} 或 None"""
    nl = name.strip().lower()
    if not nl:
        return None
    # 查本地用户
    code, data = api(url, token, "GET",
                     "v3/users?displayName={}".format(quote(name, safe="")))
    if code == 200:
        for u in data.get("data", []):
            if (u.get("displayName") or "").lower() == nl:
                return {"id": u["id"], "type": "local"}
    # 查 principal
    code, data = api(url, token, "GET",
                     "v3/principals?name={}".format(quote(name, safe="")))
    if code == 200:
        for p in data.get("data", []):
            for f in ("displayName", "loginName", "name"):
                if (p.get(f) or "").lower() == nl:
                    t = "group" if p.get("principalType") == "group" else "sso"
                    return {"id": p["id"], "type": t}
    return None


def resolve_cluster(url, token, name_or_id):
    code, data = api(url, token, "GET", "v3/clusters/{}".format(name_or_id))
    if code == 200 and data.get("id"):
        return data["id"]
    code, data = api(url, token, "GET", "v3/clusters")
    if code == 200:
        for it in data.get("data", []):
            if it.get("name") == name_or_id or it.get("id") == name_or_id:
                return it["id"]
    return None


def resolve_project(url, token, cid, name_or_id):
    if ":" in name_or_id:
        return name_or_id
    code, data = api(url, token, "GET", "v3/projects?clusterId={}".format(cid))
    if code == 200:
        for it in data.get("data", []):
            if it.get("name") == name_or_id or it.get("id") == name_or_id:
                return it["id"]
    return None


def resolve_role(url, token, role_name, role_id, level):
    if role_id and role_id != "-":
        code, _ = api(url, token, "GET", "v3/roleTemplates/{}".format(role_id))
        if code == 200:
            return role_id
        return None

    if not role_name or role_name == "-":
        return None

    mapped = ROLE_MAP.get(role_name.lower(), role_name)
    code, _ = api(url, token, "GET", "v3/roleTemplates/{}".format(mapped))
    if code == 200:
        return mapped
    # 也尝试查 globalRoles
    code, _ = api(url, token, "GET", "v3/globalRoles/{}".format(mapped))
    if code == 200:
        return mapped
    return None


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--from-csv", required=True, help="rbac CSV 文件")
    p.add_argument("-e", "--env", help="env 文件路径")
    p.add_argument("--dry-run", action="store_true", help="预览不执行")
    args = p.parse_args()

    url, token = load_env(args.env)

    with open(args.from_csv, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print("# {}".format(url), file=sys.stderr)

    ok = 0
    skip_u = skip_r = skip_c = skip_p = skip_o = 0

    for i, row in enumerate(rows, 1):
        level = (row.get("LEVEL", "") or "").strip().lower() or "project"
        cl = (row.get("CLUSTER", "") or "").strip()
        proj = (row.get("PROJECT", "") or "").strip()
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
        role = (row.get("ROLE", "") or "").strip()
        role_id = (row.get("ROLE_ID", "") or "").strip()

        if not cl or cl == "-" or not ug or ug in ("(无成员)", "-") or not role or role == "-":
            continue
        if level == "global":
            continue

        # 1. 用户
        user = find_user_by_name(url, token, ug)
        if not user:
            skip_u += 1
            print("  ⏭ 用户: {} (行{})".format(ug, i))
            continue

        # 2. 角色
        rt = resolve_role(url, token, role, role_id, level)
        if not rt:
            skip_r += 1
            print("  ⏭ 角色: {} (行{})".format(role, i))
            continue

        # 3. 集群
        cid = resolve_cluster(url, token, cl)
        if not cid:
            skip_c += 1
            print("  ⏭ 集群: {} (行{})".format(cl, i))
            continue

        # 4. 项目
        pid = None
        if level == "project" and proj and proj != "-":
            pid = resolve_project(url, token, cid, proj)
            if not pid:
                skip_p += 1
                print("  ⏭ 项目: {}/{} (行{})".format(cl, proj, i))
                continue

        # 5. 绑定
        if user["type"] == "local" or user["id"].startswith("user-"):
            k, v = "userId", user["id"]
        elif user["type"] == "group":
            k, v = "groupPrincipalId", user["id"]
        else:
            k, v = "userPrincipalId", user["id"]

        if level == "cluster":
            path = "v3/clusterRoleTemplateBindings"
            body = {"clusterId": cid, "roleTemplateId": rt, k: v,
                    "type": "clusterRoleTemplateBinding"}
        else:
            path = "v3/projectroletemplatebindings"
            body = {"projectId": pid, "roleTemplateId": rt, k: v,
                    "type": "projectRoleTemplateBinding"}

        if args.dry_run:
            print("[DRY] {} as {} → {}/{}".format(ug, role, cl, proj or "-"))
            ok += 1
            continue

        code, data = api(url, token, "POST", path, body)
        if code in (200, 201):
            print("  ✅ {} as {} → {}/{}".format(ug, role, cl, proj or "-"))
            ok += 1
        elif code == 409:
            print("  ⏭ 已存在: {} as {} → {}/{}".format(ug, role, cl, proj or "-"))
            ok += 1
        else:
            msg = data.get("message", data.get("error", str(data)))[:100]
            print("  ❌ {} as {}: {}".format(ug, role, msg))

    print()
    print("✅ {} 成功, ⏭ 用户{} 角色{} 集群{} 项目{}".format(
        ok, skip_u, skip_r, skip_c, skip_p))


if __name__ == "__main__":
    main()
