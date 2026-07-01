#!/usr/bin/env python3
"""
工具: 给 rbac.csv 的 USER_GROUP 批量追加 @geely.com
      跳过 admin、已有 @ 的用户、占位行
"""

import sys, csv

SRC = sys.argv[1] if len(sys.argv) > 1 else "rbac.csv"
DST = sys.argv[2] if len(sys.argv) > 2 else "rbac_fixed.csv"

EXCLUDE = {"admin", "(无成员)", "-", ""}

with open(SRC, "r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    fieldnames = reader.fieldnames

changed = 0
skipped = 0

for row in rows:
    ug = (row.get("USER_GROUP", "") or row.get("USER/GROUP", "") or "").strip()
    if not ug or ug.lower() in EXCLUDE or "@" in ug:
        skipped += 1
        continue
    # 更新
    new_ug = ug + "@geely.com"
    for k in ("USER_GROUP", "USER/GROUP"):
        if k in row:
            row[k] = new_ug
    changed += 1
    print("  {} → {}".format(ug, new_ug))

with open(DST, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print("\n{} 行修改, {} 行跳过 → {}".format(changed, skipped, DST))
