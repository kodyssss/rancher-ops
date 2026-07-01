#!/usr/bin/env python3
"""
rancher_user.py — 获取 Rancher 所有用户列表

列出本地用户 + SSO principals 的 displayName 和 principal_id。
可输出 CSV 文件。

用法:
  python3 rancher_user.py                   # 终端表格
  python3 rancher_user.py -o users.csv       # 导出 CSV
  python3 rancher_user.py -e my_env.txt      # 指定 env
"""

import os, sys, json, csv, re, time, ssl

try:
    from urllib.request import Request, urlopen, HTTPError
except ImportError:
    from urllib2 import Request, urlopen, HTTPError

MAX_RETRIES = 3
TIMEOUT = 60
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


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


def api(url, token, method, path):
    full = url + "/" + path.lstrip("/")
    for a in range(MAX_RETRIES):
        try:
            req = Request(full)
            req.add_header("Authorization", "Bearer {}".format(token))
            req.add_header("Accept", "application/json")
            req.get_method = lambda m=method: m.upper()
            resp = urlopen(req, timeout=TIMEOUT, context=SSL_CTX)
            return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 401:
                print("ERROR: Token 无效", file=sys.stderr)
                sys.exit(1)
            if e.code == 404:
                return None
            if a < MAX_RETRIES - 1:
                time.sleep(2 ** a)
                continue
            return None
        except Exception:
            if a < MAX_RETRIES - 1:
                time.sleep(2 ** a)
                continue
            return None
    return None


def paginated(url, token, path):
    items, marker = [], None
    sep = "&" if "?" in path else "?"
    while True:
        p = "{}{}limit=1000".format(path, sep)
        if marker:
            p += "&continue={}".format(marker)
        data = api(url, token, "GET", p)
        if not data:
            break
        batch = data.get("data", [])
        if not batch:
            break
        items.extend(batch)
        pagination = data.get("pagination", {})
        if pagination.get("next"):
            marker = pagination["next"]
        elif len(batch) < 1000:
            break
        else:
            marker = batch[-1].get("id", "")
    return items


def get_users(url, token):
    users = {}

    # 本地用户
    for u in paginated(url, token, "v3/users"):
        name = u.get("displayName") or u.get("username") or ""
        if name and name not in users:
            users[name] = {"id": u["id"], "type": "local"}

    # SSO principals
    for p in paginated(url, token, "v3/principals"):
        name = p.get("displayName") or p.get("loginName") or p.get("name") or ""
        if not name or name in users:
            continue
        ptype = "group" if p.get("principalType") == "group" else "sso"
        users[name] = {"id": p["id"], "type": ptype}

    return users


def main():
    import argparse
    p = argparse.ArgumentParser(description="获取 Rancher 所有用户")
    p.add_argument("-e", "--env", help="env 文件")
    p.add_argument("-o", "--output", help="输出 CSV 文件")
    args = p.parse_args()

    url, token = load_env(args.env)
    print("# {}".format(url), file=sys.stderr)

    users = get_users(url, token)

    if args.output:
        with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["displayName", "principal_id", "type"])
            for name in sorted(users.keys(), key=str.lower):
                u = users[name]
                w.writerow([name, u["id"], u["type"]])
        print("→ {} ({} 个用户)".format(args.output, len(users)), file=sys.stderr)
    else:
        print("=" * 70)
        print("{:<40} {:<22} {}".format("displayName", "principal_id", "type"))
        print("{:<40} {:<22} {}".format("─" * 40, "─" * 22, "─" * 6))
        local_n = sso_n = group_n = 0
        for name in sorted(users.keys(), key=str.lower):
            u = users[name]
            print("{:<40} {:<22} {}".format(name[:40], u["id"][:22], u["type"]))
            if u["type"] == "local":
                local_n += 1
            elif u["type"] == "group":
                group_n += 1
            else:
                sso_n += 1
        print("─" * 70)
        print("共 {} 个用户 (本地: {}, SSO: {}, 组: {})".format(
            len(users), local_n, sso_n, group_n))
        print("=" * 70)


if __name__ == "__main__":
    main()
