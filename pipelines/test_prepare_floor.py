# -*- coding: utf-8 -*-
"""prepare 的 rel 下限测试（MIN_REL）。

要守的行为：高相关矿脉被抽干时，不许把低 rel 的边缘料照样发满一批。
实测依据：A+B 率 rel>=10 是 60.0%、5-9 是 42.4%、1-4 是 20.8%、0 只有 9.6%；
批26 rel 中位数只有 2，A+B 掉到 15.5%。

**必须显式把 deepread 的 ROOT/CATALOG/QUEUE 指到临时目录**：
deepread 在 import 时就把这三个常量算成了真实库路径，
只改 quant_library.ROOT 或 reload 模块都会让测试写到真实库根目录上去（2026-10-02 差点踩到）。
"""
import json, os, sys, tempfile
from contextlib import redirect_stdout
import io

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q
import deepread as dr

ok = True
def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)

BODY = ("Momentum factors in the cross-section of stock returns: we build a portfolio "
        "and test factor investing with transaction costs. ")

tmp = tempfile.mkdtemp(prefix="prepfloor_")
# 三个常量都要挪走，少一个就会往真实库写
dr.ROOT = tmp
dr.CATALOG = os.path.join(tmp, "catalog.json")
dr.QUEUE = os.path.join(tmp, "deepread_queue.jsonl")
txt_dir = os.path.join(tmp, "text")
os.makedirs(txt_dir, exist_ok=True)

recs = []
for i in range(10):
    rel = i + 1                                    # 1..10
    tp = os.path.join(txt_dir, "arxiv__P%02d.txt" % i)
    open(tp, "w", encoding="utf-8").write(BODY * 30)
    recs.append({"key": "arxiv:P%02d" % i, "source": "arXiv",
                 "title": "Momentum factor stock returns portfolio %02d" % i,
                 "abstract": BODY, "authors": ["A"], "date": "2025-01-0%d" % (1 + i % 9),
                 "cats": [], "doi": "", "cited": "", "page": "p",
                 "pdf_url": "u", "pdf_path": os.path.join(tmp, "x.pdf"),
                 "status": "done", "text_done": True, "txt_path": tp,
                 "primary": "多因子模型与检验", "gate_level": "strong", "rel": rel})
tp = os.path.join(txt_dir, "arxiv__NOREL.txt")
open(tp, "w", encoding="utf-8").write(BODY * 30)
recs.append({"key": "arxiv:NOREL", "source": "arXiv",
             "title": "Momentum factor portfolio returns", "abstract": BODY,
             "authors": [], "date": "2025", "cats": [], "doi": "", "cited": "", "page": "p",
             "pdf_url": "u", "pdf_path": os.path.join(tmp, "y.pdf"), "status": "done",
             "text_done": True, "txt_path": tp, "primary": "多因子模型与检验",
             "gate_level": "strong"})               # 没有 rel，应按题摘现算
tp0 = os.path.join(txt_dir, "arxiv__JUNK.txt")
open(tp0, "w", encoding="utf-8").write("we study a matrix factorization for image matting alpha. " * 30)
recs.append({"key": "arxiv:JUNK", "source": "arXiv", "title": "Alpha matting via factorization",
             "abstract": "image matting, alpha channel", "authors": [], "date": "2025",
             "cats": [], "doi": "", "cited": "", "page": "p", "pdf_url": "u",
             "pdf_path": os.path.join(tmp, "z.pdf"), "status": "done", "text_done": True,
             "txt_path": tp0, "primary": "机器学习选股", "gate_level": "strong", "rel": 0})
json.dump(recs, open(dr.CATALOG, "w", encoding="utf-8"), ensure_ascii=False)
rels = {r["key"]: r.get("rel") for r in recs}


def run(n, floor):
    buf = io.StringIO()
    with redirect_stdout(buf):
        dr.prepare(n, min_rel=floor)
    keys = [json.loads(l)["key"] for l in open(dr.QUEUE, encoding="utf-8")]
    return keys, buf.getvalue()


keys, out = run(20, 4)
below = [k for k in keys if rels.get(k) is not None and rels[k] < 4]
chk(not below, "min_rel=4 时 rel<4 的条目不进队列（实际混入 %s）" % below)
chk("arxiv:JUNK" not in keys, "rel=0 的边缘料被挡在门外")
chk("被挡下" in out, "prepare 报告挡下了多少篇，不是静默变少")
chk("arxiv:NOREL" in keys, "没存 rel 的按题摘现算，因子类题摘不该被误杀")

keys2, out2 = run(20, 8)
chk(len(keys2) < 20, "min_rel=8 时只凑到 %d 篇（要 20 篇）" % len(keys2))
chk("宁缺毋滥" in out2, "凑不满时明确打印'宁缺毋滥'，不拿低相关料补数")

keys3, _ = run(20, 0)
chk("arxiv:JUNK" in keys3, "min_rel=0 恢复旧行为：紧急清库存时仍能全发")

keys4, _ = run(4, 4)
r4 = [rels[k] for k in keys4 if rels.get(k) is not None]
chk(r4 == sorted(r4, reverse=True), "队列内部仍按 rel 从高到低排（实际 %s）" % r4)

chk(os.path.exists(dr.QUEUE), "队列写在临时根目录")
# 真实库队列的时间戳必须没被动过。
# 注意：仓库布局下根目录是 paper_store、这个文件可能压根不存在，
# 那时两边都是 None，断言会"空过"——所以要显式标记它这次没有实际效力，别当成验过了。
REAL_QUEUE = os.path.join(q._resolve_root(), "deepread_queue.jsonl")
real_before = os.path.getmtime(REAL_QUEUE) if os.path.exists(REAL_QUEUE) else None
keys9, _ = run(3, 4)
real_after = os.path.getmtime(REAL_QUEUE) if os.path.exists(REAL_QUEUE) else None
if real_before is None:
    print("SKIP  真实库队列不存在（仓库布局），此项未实际验证")
    chk(True, "临时目录写入成功（真实库检查跳过）")
else:
    chk(real_before == real_after,
        "测试没有覆写真实库的 deepread_queue.jsonl（改前 %s / 改后 %s）" % (real_before, real_after))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
