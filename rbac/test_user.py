#!/usr/bin/env python3
"""
test_user.py — 快速验证用户 API（本地用户 + SSO principals）

用法:
  python3 test_user.py                        # 查 admin
  python3 test_user.py "e-Xiao.Wang4@geely.com"
"""

import os, sys, json, re, ssl
from urllib.request import Request, urlopen
from urllib.parse import quote

# 读 env.txt
script_dir = os.path.dirname(os.path.abspath(__file__))
for fn in ["env.txt", "env"]:
    p = os.path.join(script_dir, fn)
    if os.path.isfile(p):
        with open(p) as f:
            raw = f.read()
        break
else:
    print("ERROR: 找不到 env.txt", file=sys.stderr)
    sys.exit(1)

cfg = {}
for line in raw.split("\n"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    m = re.match(r'^(?:export\s+)?(\w+)=["\']?(.*?)["\']?\s*$', line)
    if m:
        cfg[m.group(1)] = m.group(2).rstrip('"\'')

url = cfg["RANCHER_URL"].rstrip("/")
token = cfg["RANCHER_TOKEN"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

name = sys.argv[1] if len(sys.argv) > 1 else "admin"
print("Rancher: {}".format(url))
print("查询: {}".format(name))
print()

# 本地用户
req = Request("{}/v3/users?displayName={}".format(url, quote(name, safe="")))
req.add_header("Authorization", "Bearer {}".format(token))
req.add_header("Accept", "application/json")
resp = urlopen(req, timeout=10, context=ctx)
users = json.loads(resp.read().decode("utf-8")).get("data", [])
print("--- 本地用户 ---")
for u in users:
    print("  {}  →  id={}".format(u.get("displayName"), u.get("id")))
if not users:
    print("  (无)")

# SSO principal
req = Request("{}/v3/principals?name={}".format(url, quote(name, safe="")))
req.add_header("Authorization", "Bearer {}".format(token))
req.add_header("Accept", "application/json")
resp = urlopen(req, timeout=10, context=ctx)
data = json.loads(resp.read().decode("utf-8")).get("data", [])
print("\n--- SSO Principals ---")
for p in data[:10]:
    print("  {}  →  id={}  ({})".format(
        p.get("displayName") or p.get("loginName") or "?",
        p.get("id"), p.get("principalType", "?")))
if not data:
    print("  (无)")
