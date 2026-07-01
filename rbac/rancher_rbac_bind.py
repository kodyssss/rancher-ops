#!/usr/bin/env python3
"""
rancher_rbac_bind.py — 单用户角色绑定

按 displayName 查找用户，在指定集群/项目中执行角色绑定。

用法:
  # 项目角色绑定
  python3 rancher_rbac_bind.py -c poc -u "e-Xiao.Wang4@geely.com" -p Default --role Owner

  # 集群角色绑定
  python3 rancher_rbac_bind.py -c poc -u "e-Xiao.Wang4@geely.com" --clusterrole "Cluster Owner"

  # 指定 env
  python3 rancher_rbac_bind.py -e env.txt -c poc -u user -p project --role Member

角色名 (大小写不敏感):
  --role:       Owner / Member / ReadOnly
  --clusterrole: Cluster Owner / Cluster Member / Cluster Admin / Cluster Viewer
"""

import os, sys, json, re, time, ssl

try:
    from urllib.request import Request, urlopen, HTTPError
    from urllib.parse   import quote
except ImportError:
    from urllib2 import Request, urlopen, HTTPError
    from urllib import quote

MAX_RETRIES = 3
TIMEOUT = 60
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

PROJECT_ROLES = {
    "owner": "project-owner",
    "member": "project-member",
    "readonly": "read-only",
}
CLUSTER_ROLES = {
    "cluster owner": "cluster-owner",
    "cluster member": "cluster-member",
    "cluster admin": "cluster-admin",
    "cluster viewer": "cluster-viewer",
}


def load_env(env_path=None):
    if env_path is None:
        d = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(d, "env.txt"), os.path.join(d, "env"),
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
            body_err = ""
            try:
                body_err = e.read().decode("utf-8")[:300]
            except:
                pass
            if e.code == 401:
                print("ERROR: Token 无效", file=sys.stderr)
                sys.exit(1)
            if e.code in (404, 422, 409):
                return e.code, {"error": body_err}
            if a < MAX_RETRIES - 1:
                time.sleep(2 ** a)
                continue
            return e.code, {"error": body_err}
        except Exception as e:
            if a < MAX_RETRIES - 1:
                time.sleep(2 ** a)
                continue
            return 0, {"error": str(e)}
    return 0, {"error": "max retries"}


def find_user(url, token, name):
    """按 displayName 查找用户，返回 {id, type} 或 None"""
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


def main():
    import argparse
    p = argparse.ArgumentParser(description="单用户角色绑定")
    p.add_argument("-e", "--env", help="env 文件")
    p.add_argument("-c", "--cluster", required=True, help="集群名")
    p.add_argument("-u", "--user", required=True, help="用户 displayName")
    p.add_argument("-p", "--project", help="项目名 (project role 时需要)")
    p.add_argument("--role", help="项目角色: Owner / Member / ReadOnly")
    p.add_argument("--clusterrole", help="集群角色: Cluster Owner / Cluster Member / ...")
    args = p.parse_args()

    if not args.role and not args.clusterrole:
        p.error("需要 --role 或 --clusterrole")
    if args.role and args.clusterrole:
        p.error("--role 和 --clusterrole 不能同时指定")
    if args.role and not args.project:
        p.error("--role 需要 -p/--project")
    if args.clusterrole and args.project:
        p.error("--clusterrole 不需要 -p")

    url, token = load_env(args.env)
    print("# {}".format(url), file=sys.stderr)

    # 1. 查用户
    user = find_user(url, token, args.user)
    if not user:
        print("ERROR: 用户不存在: {}".format(args.user), file=sys.stderr)
        sys.exit(1)
    print("用户: {} → {} ({})".format(args.user, user["id"], user["type"]))

    # 2. 查集群
    cid = resolve_cluster(url, token, args.cluster)
    if not cid:
        print("ERROR: 集群不存在: {}".format(args.cluster), file=sys.stderr)
        sys.exit(1)
    print("集群: {} → {}".format(args.cluster, cid))

    # 3. 绑定
    if args.role:
        # 项目角色
        pid = resolve_project(url, token, cid, args.project)
        if not pid:
            print("ERROR: 项目不存在: {}/{}".format(args.cluster, args.project),
                  file=sys.stderr)
            sys.exit(1)
        print("项目: {} → {}".format(args.project, pid))

        rt = PROJECT_ROLES.get(args.role.lower())
        if not rt:
            print("ERROR: 未知项目角色: {} (可用: Owner/Member/ReadOnly)".format(args.role),
                  file=sys.stderr)
            sys.exit(1)

        if user["type"] == "local" or user["id"].startswith("user-"):
            k, v = "userId", user["id"]
        else:
            k, v = "userPrincipalId", user["id"]

        body = {
            "projectId": pid,
            "roleTemplateId": rt,
            k: v,
            "type": "projectRoleTemplateBinding",
        }
        print("角色: {} → {}".format(args.role, rt))
        code, data = api(url, token, "POST", "v3/projectroletemplatebindings", body)

    else:
        # 集群角色
        rt = CLUSTER_ROLES.get(args.clusterrole.lower())
        if not rt:
            print("ERROR: 未知集群角色: {} (可用: Cluster Owner/Member/Admin/Viewer)".format(
                args.clusterrole), file=sys.stderr)
            sys.exit(1)

        if user["type"] == "local" or user["id"].startswith("user-"):
            k, v = "userId", user["id"]
        elif user["type"] == "group":
            k, v = "groupPrincipalId", user["id"]
        else:
            k, v = "userPrincipalId", user["id"]

        body = {
            "clusterId": cid,
            "roleTemplateId": rt,
            k: v,
            "type": "clusterRoleTemplateBinding",
        }
        print("集群角色: {} → {}".format(args.clusterrole, rt))
        code, data = api(url, token, "POST", "v3/clusterRoleTemplateBindings", body)

    if code in (200, 201):
        print("✅ 绑定成功")
    elif code == 409:
        print("⏭ 已存在")
    else:
        msg = data.get("message", data.get("error", str(data)))[:200]
        print("❌ 失败: {}".format(msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
