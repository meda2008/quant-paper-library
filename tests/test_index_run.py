# -*- coding: utf-8 -*-
"""index 阶段全流程合成回归：临时目录里造 350 个真 PDF 跑一遍 cmd_index。
验的是 2026-09-30 加的三件事 —— 跨进程锁、每 300 篇增量落盘、解析/归档/索引产出正确。
不碰 Z 盘：ROOT/CATLOG/TXT/INBOX 全部重定向到 tempfile。"""
import io, json, os, sys, tempfile, time
from contextlib import redirect_stdout
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库布局是 tests/ 与 pipelines/ 平级；本地旧布局是脚本与测试同目录。两种都认。
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q
import fitz

ok = True
def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)

tmp = tempfile.mkdtemp(prefix="indextest_")
q.ROOT = tmp
q.CATLOG = os.path.join(tmp, "catalog.json")
q.TXT = os.path.join(tmp, "text")
q.INBOX = os.path.join(tmp, "_inbox")
os.makedirs(q.TXT, exist_ok=True)

BODY = ("Momentum and value factors in the cross-section of stock returns. "
        "We build a diversified momentum portfolio, test factor investing on A-share equities, "
        "and examine transaction costs, rebalancing frequency and Sharpe ratio in an asset pricing model. ")

def make_pdf(path, text):
    d = fitz.open()
    p = d.new_page()
    p.insert_text((50, 80), text[:1500], fontsize=9)
    d.save(path)
    d.close()

recs = []
for i in range(350):
    pp = os.path.join(tmp, "raw_%03d.pdf" % i)
    make_pdf(pp, BODY + " sample %d" % i)
    recs.append({"key": "arxiv:T%03d" % i, "source": "arXiv", "title": "Factor Investing Study %03d" % i,
                 "abstract": BODY, "authors": ["Author A"], "date": "2025-0%d" % (1 + i % 9), "cats": ["q-fin.ST"],
                 "doi": "", "cited": i, "page": "https://arxiv.org/abs/T%03d" % i,
                 "status": "done", "pdf_path": pp, "primary": ""})
# 掺一个坏 PDF：解析必须报错但整批不能崩
open(os.path.join(tmp, "raw_bad.pdf"), "wb").write(b"%PDF-1.4 not really a pdf")
recs.append({"key": "arxiv:BAD", "source": "arXiv", "title": "Broken Paper That Must Not Kill The Run",
             "abstract": BODY, "authors": [], "date": "2024", "cats": [], "doi": "", "cited": "",
             "page": "p", "status": "done", "pdf_path": os.path.join(tmp, "raw_bad.pdf"), "primary": ""})
json.dump(recs, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)

buf = io.StringIO()
t0 = time.time()
try:
    with redirect_stdout(buf):
        q.cmd_index()
    crashed = None
except Exception as e:
    crashed = repr(e)
out = buf.getvalue()
print(out[-900:])
chk(crashed is None, "整轮 index 没崩%s" % ("" if crashed is None else " -> " + crashed))

after = json.load(open(q.CATLOG, encoding="utf-8"))
byk = {r["key"]: r for r in after}
parsed = [r for r in after if r.get("text_done")]
chk(len(parsed) == 350, "350 篇解析成功（实际 %d）" % len(parsed))
chk(all(r.get("primary") for r in parsed), "每篇都有分类目录")
chk(os.path.exists(q._lock_path("index")) is False, "跑完释放了 index 锁")
chk("本轮新解析 300 篇" in out, "增量落盘真的发生了（日志里有本轮新解析 300 篇那条）")

mid_flush = os.path.getmtime(q.CATLOG)
chk(mid_flush - t0 < 600, "catalog 写回时间正常")
bad = byk.get("arxiv:BAD", {})
chk(bad.get("text_done") is False and bad.get("error"), "坏 PDF 记了 error 而不是拖垮整批")
# PDF 被搬进分类目录，且路径回写正确
moved = sum(1 for r in parsed if os.path.exists(r.get("pdf_path", "")))
chk(moved == len(parsed), "归档后的 pdf_path 全部可读（%d/%d）" % (moved, len(parsed)))
chk(all(os.path.dirname(r["pdf_path"]) != tmp for r in parsed), "PDF 已从临时根目录移进分类子目录")
chk(os.path.exists(os.path.join(tmp, "INDEX.md")), "INDEX.md 生成")
chk(os.path.exists(os.path.join(tmp, "catalog.csv")), "catalog.csv 生成")
idx = open(os.path.join(tmp, "INDEX.md"), encoding="utf-8").read()
chk(idx.count("- **") == 350, "INDEX.md 条目数 350（实际 %d）" % idx.count("- **"))

# 并发安全：外部进程在 index 之后写了精读结论，再跑一轮 index 不能把它抹掉
c = json.load(open(q.CATLOG, encoding="utf-8"))
c[0]["ai_summary"] = "外部写进来的精读结论"
c[0]["ai_grade"] = "A"
json.dump(c, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)
with redirect_stdout(io.StringIO()):
    q.cmd_index()
c2 = json.load(open(q.CATLOG, encoding="utf-8"))
chk(c2[0].get("ai_summary") == "外部写进来的精读结论", "第二遍 index 保住了外部并发的 ai_summary")
chk(c2[0].get("ai_grade") == "A", "第二遍 index 保住了 ai_grade")

# dup_of 副本不进 INDEX.md，但仍留在 catalog.csv 全字段表里
c3 = json.load(open(q.CATLOG, encoding="utf-8"))
for r in c3[:5]:
    r["dup_of"] = c3[100]["key"]
json.dump(c3, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)
with redirect_stdout(io.StringIO()):
    q.cmd_index()
idx2 = open(os.path.join(tmp, "INDEX.md"), encoding="utf-8").read()
chk(idx2.count("- **") == 345, "dup_of 的 5 条不再占索引行：350-5=345（实际 %d）" % idx2.count("- **"))
chk("另有 5 条是已在库条目的同文异 key 副本" in idx2, "索引抬头交代了隐藏多少 dup，数字不会凭空少")
import csv as _csv
rows = list(_csv.DictReader(open(os.path.join(tmp, "catalog.csv"), encoding="utf-8")))
chk(len(rows) == 351, "catalog.csv 仍是全字段表、没跟着过滤（实际 %d 行）" % len(rows))

# 链接必须是相对根目录的：原来按字面量 "论文/" 截，换机器就截不出来
sample_line = [ln for ln in idx2.splitlines() if "[原文PDF]" in ln]
chk(bool(sample_line), "索引里有原文PDF链接")
if sample_line:
    chk(tmp.replace("\\", "/") not in sample_line[0],
        "索引里的 PDF 链接不含绝对路径（不泄漏本机根目录）")
    chk("/_inbox/" not in sample_line[0], "链接指向分类目录而不是暂存区")

# 锁互斥：另一进程持活锁时 cmd_index 必须直接跳过，不动盘
other = os.path.join(tmp, "_index.lock")
open(other, "w", encoding="utf-8").write("%d %f" % (os.getppid(), time.time()))
c3 = json.load(open(q.CATLOG, encoding="utf-8"))
c3[0].pop("ai_summary", None)
json.dump(c3, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)
buf2 = io.StringIO()
with redirect_stdout(buf2):
    q.cmd_index()
chk("跳过 index" in buf2.getvalue(), "别人持活锁 -> 本轮 index 跳过（不会双跑）")
chk("ai_summary" not in json.load(open(q.CATLOG, encoding="utf-8"))[0],
    "跳过时确实没写盘（盘上还是外部那份）")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
