# -*- coding: utf-8 -*-
import json, os, sys, collections
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def _resolve_root():
    """PAPER_ROOT 环境变量 > 脚本同目录 .paper_root 文件 > ./paper_store（与 quant_library 一致）"""
    v = os.environ.get("PAPER_ROOT")
    if v:
        return v
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".paper_root")
    try:
        with open(cfg, encoding="utf-8") as f:
            p = f.read().strip()
        if p:
            return p
    except OSError:
        pass
    return "paper_store"


ROOT = _resolve_root()
r = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
st = collections.Counter(x["status"] for x in r)
print("候选", len(r), dict(st))
print("待下载按来源", dict(collections.Counter(x["source"] for x in r if x["status"] == "new")))
print("历史失败按来源", dict(collections.Counter(x["source"] for x in r if x["status"] in ("failed", "bad_pdf"))))
done = [x for x in r if x["status"] == "done"]
off = [x for x in done if x.get("offtopic")]
inlib = [x for x in done if not x.get("offtopic")]
print("done", len(done), "| 非金融", len(off), "| 在库", len(inlib),
      "| text_done", sum(1 for x in r if x.get("text_done")))
dead = [x for x in done if x.get("pdf_path") and not os.path.exists(x["pdf_path"].replace("\\", "/"))]
print("PDF 路径失效:", len(dead))
print("非金融却仍在分类目录:", sum(1 for x in off if "_剔除" not in x["pdf_path"].replace("\\", "/")))
print("在库却躺在剔除目录:", sum(1 for x in inlib if "_剔除" in x["pdf_path"].replace("\\", "/")))
print("分类目录 PDF 与索引不符:", end=" ")
mism = {}
for d in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, d)
    if not os.path.isdir(p) or d in ("_inbox", "text"):
        continue
    disk = len([f for f in os.listdir(p) if f.lower().endswith(".pdf")])
    want = (len(off) if d == "_剔除-非金融"
            else sum(1 for x in inlib if x.get("primary") == d))
    if disk != want:
        mism[d] = (disk, want)
print(mism if mism else "无，全部一致")
print("inbox 残留:", len([f for f in os.listdir(os.path.join(ROOT, "_inbox")) if f.lower().endswith(".pdf")]))
print("INDEX.md mtime:", __import__("time").strftime("%Y-%m-%d %H:%M",
      __import__("time").localtime(os.path.getmtime(os.path.join(ROOT, "INDEX.md")))))
