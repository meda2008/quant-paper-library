# -*- coding: utf-8 -*-
"""Snapshot the paper library state (catalog statuses + on-disk PDF counts per folder)."""
import json, os, sys, collections, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.environ.get("PAPER_ROOT", "paper_store")
CATLOG = os.path.join(ROOT, "catalog.json")
OUT = sys.argv[1] if len(sys.argv) > 1 else "snapshot_before.json"

recs = json.load(open(CATLOG, encoding="utf-8"))
status = collections.Counter(r.get("status") or "new" for r in recs)
done = [r for r in recs if r.get("status") == "done"]
offtopic = sum(1 for r in recs if r.get("offtopic"))
primary = collections.Counter(r.get("primary") or "(未分类)" for r in done if not r.get("offtopic"))
text_done = sum(1 for r in recs if r.get("text_done"))

# 磁盘实际 PDF 数（每个顶层文件夹）
folders = {}
for name in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, name)
    if os.path.isdir(p) and not name.startswith(("_inbox", "text")):
        folders[name] = sum(1 for f in os.listdir(p) if f.lower().endswith(".pdf"))
inbox_pdfs = sum(1 for f in os.listdir(os.path.join(ROOT, "_inbox")) if f.lower().endswith(".pdf")) \
    if os.path.isdir(os.path.join(ROOT, "_inbox")) else 0

snap = {
    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total_candidates": len(recs),
    "status": dict(status),
    "status_by_key": {r["key"]: (r.get("status") or "new") + ("|T" if r.get("text_done") else "")
                      + ("|off" if r.get("offtopic") else "") for r in recs},
    "done": len(done),
    "text_done": text_done,
    "offtopic": offtopic,
    "in_library": sum(primary.values()),
    "primary_by_category": dict(primary),
    "pdf_files_by_folder": folders,
    "inbox_pdfs": inbox_pdfs,
    "keys": sorted(r["key"] for r in recs),
}
json.dump(snap, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[{snap['ts']}] 候选 {len(recs)} | 已下载 {len(done)} | 已解析 {text_done} | "
      f"入库 {snap['in_library']} | 非金融剔除 {offtopic} | _inbox 残留 {inbox_pdfs}")
print("状态:", dict(status))
print("分类分布:", dict(primary))
