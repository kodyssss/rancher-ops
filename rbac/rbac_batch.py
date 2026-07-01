#!/usr/bin/env python3
"""
rbac_batch.py — 从 rbac.csv 批量调用 rancher_rbac_bind.py

逐行读取 RBAC CSV，对每一条调用 rancher_rbac_bind.py 做单用户绑定。
用户/角色/项目任一不存在则跳过。

用法:
  python3 rbac_batch.py --csv rbac.csv --dry-run
  python3 rbac_batch.py --csv rbac.csv                # 执行绑定
  python3 rbac_batch.py --csv rbac.csv --skip-clusterrole  # 跳过集群角色绑定
  python3 rbac_batch.py --csv rbac.csv --skip-projectrole  # 跳过项目角色绑定
"""

import os, sys, csv, subprocess

BIND_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rancher_rbac_bind.py")


def main():
    import argparse
    p = argparse.ArgumentParser(description="批量调用 rancher_rbac_bind")
    p.add_argument("--csv", required=True, help="rbac CSV 文件")
    p.add_argument("--dry-run", action="store_true", help="只预览命令不执行")
    p.add_argument("--skip-clusterrole", action="store_true", help="跳过集群角色绑定")
    p.add_argument("--skip-projectrole", action="store_true", help="跳过项目角色绑定")
    args = p.parse_args()

    if not os.path.isfile(BIND_SCRIPT):
        print("ERROR: 找不到 {}".format(BIND_SCRIPT), file=sys.stderr)
        sys.exit(1)

    with open(args.csv, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    ok = fail = skip = 0

    for i, row in enumerate(rows, 1):
        level = (row.get("LEVEL", "") or "").strip().lower() or "project"
        cl = (row.get("CLUSTER", "") or "").strip()
        proj = (row.get("PROJECT", "") or "").strip()
        ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
        role = (row.get("ROLE", "") or "").strip()

        # 跳过无效行
        if not cl or cl == "-" or not ug or ug in ("(无成员)", "-") or not role or role == "-":
            continue

        # 构建命令
        if level == "global":
            continue

        cmd = [sys.executable, BIND_SCRIPT]

        if level == "cluster":
            if args.skip_clusterrole:
                skip += 1
                continue
            cmd += ["-c", cl, "-u", ug, "--clusterrole", role]
        else:
            if args.skip_projectrole:
                skip += 1
                continue
            cmd += ["-c", cl, "-u", ug, "-p", proj, "--role", role]

        print("[{}/{}] {}".format(i, len(rows), " ".join(cmd)))
        if args.dry_run:
            ok += 1
            continue

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            ok += 1
        else:
            fail += 1
            # 打印错误但继续
            err = result.stderr.strip()
            if err:
                print("  {}".format(err[:120]), file=sys.stderr)

    print("\n✅ {} 成功, ⏭ {} 跳过, ❌ {} 失败".format(ok, skip, fail))


if __name__ == "__main__":
    main()
