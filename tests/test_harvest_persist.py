# -*- coding: utf-8 -*-
"""harvest 增量落盘的对象身份回归测试（全离线：http_get 打桩，临时目录，不碰 Z 盘）。
要防的 bug：_cmd_harvest 里把 `recs = save_catalog(recs, orig)` 回绑了一次。
save_catalog 返回的是"重读盘 + 字段叠加"得到的**另一批 dict 对象**，而 futures/todo 拿着的
是最初那批 dict。一回绑，后面 _dl 写的 status/pdf_path 就落在了已被踢出列表的旧对象上，
盘上永远看不到 —— 表现就是日志里 ok 一路涨、catalog 里的 done 却不动。
批2026-09-30 的 h2 下载就中招了：4839 篇 ok，盘上只认出最先落盘的几百篇。
"""
import io, json, os, sys, tempfile, time
from contextlib import redirect_stdout
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库布局是 tests/ 与 pipelines/ 平级；本地旧布局是脚本与测试同目录。两种都认。
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q

tmp = tempfile.mkdtemp(prefix="harvesttest_")
q.ROOT = tmp
q.INBOX = os.path.join(tmp, "_inbox")
q.CATLOG = os.path.join(tmp, "catalog.json")
os.makedirs(q.INBOX, exist_ok=True)
# 空间闸量的是模块级 ROOT；仓库布局下它默认指向不存在的 paper_store，
# free_gb 会静默返回 +inf，闸门形同虚设。测试里必须显式钉住。
q.MIN_FREE_GB = 0.0
q.time.sleep = lambda *a: None          # 不打真网络节拍

N = 250
recs = []
for i in range(N):
    recs.append({"key": "arxiv:H%04d" % i, "source": "arXiv", "title": "Momentum factor stock returns %d" % i,
                 "abstract": "cross-section of stock returns, factor model", "authors": [], "date": "2024-01-01",
                 "cats": [], "doi": "", "cited": "", "page": "p",
                 "pdf_url": "https://export.arxiv.org/pdf/H%04d" % i, "status": "new", "pdf_path": "", "rel": 10})
json.dump(recs, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)

# 假装下载成功：返回一个以 %PDF- 开头、长度过 15000 的字节串，走 _dl 的正常成功分支
q.http_get = lambda url, timeout=120: b"%PDF-" + b"x" * 20000

buf = __import__("io").StringIO()
try:
    with redirect_stdout(buf):
        q.cmd_harvest(workers=2, new_only=True, interval=0.0, attempts=1)
    crashed = None
except Exception as e:
    crashed = repr(e)
out = buf.getvalue()
print(out[-600:])
ok = not crashed
def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)

chk(crashed is None, "harvest 整轮没崩%s" % ("" if crashed is None else " -> " + crashed))
after = json.load(open(q.CATLOG, encoding="utf-8"))
done = [r for r in after if r.get("status") == "done"]
# 这是核心断言：不止前 100 篇，全部 250 篇的状态都必须落到盘上
chk(len(done) == N, "全部 %d 篇的 status=done 都落到了盘上（实际 %d 篇）" % (N, len(done)))
chk(all(r.get("pdf_path") and os.path.isfile(r["pdf_path"]) for r in done),
    "每篇的 pdf_path 都写了真实文件")
chk(sum(1 for r in after if r.get("status") == "new") == 0, "没有记录被留在 status=new")
# 文件真的落在 _inbox 里且数量对得上
files = [f for f in os.listdir(q.INBOX) if f.endswith(".pdf")]
chk(len(files) == N, "_inbox 里 %d 个 PDF 与 done 记录一一对应" % len(files))

# ---------------- 空间闸（第二轮，独立临时目录） ----------------
# 要防的是：break 出消费循环后，已经 submit 的几千个任务被 with 的隐式
# shutdown(wait=True) 照样全部跑完 —— 2026-10-01 那次磁盘告警"停了"，
# 日志再也不出声，下载却在后台一路做到我把进程按 PID 掐掉。
tmp2 = tempfile.mkdtemp(prefix="harvestspace_")
q.ROOT = tmp2
q.INBOX = os.path.join(tmp2, "_inbox")
q.CATLOG = os.path.join(tmp2, "catalog.json")
os.makedirs(q.INBOX, exist_ok=True)
M = 260
recs2 = []
for i in range(M):
    recs2.append({"key": "arxiv:S%04d" % i, "source": "arXiv", "title": "Momentum factor stock returns %d" % i,
                  "abstract": "cross-section of stock returns, factor model", "authors": [], "date": "2024",
                  "cats": [], "doi": "", "cited": "", "page": "p",
                  "pdf_url": "https://export.arxiv.org/pdf/S%04d" % i, "status": "new", "pdf_path": "", "rel": 10})
json.dump(recs2, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)
def slow_get(url, timeout=120):
    t0 = time.time()
    while time.time() - t0 < 0.25:      # 每个任务真花 250ms：break 时队列里还剩一大片没跑
        pass
    return b"%PDF-" + b"x" * 20000
q.http_get = slow_get
calls = {"n": 0}
real_free = q.free_gb


def fake_free_low(path=None):
    return 0.4                                   # 一开头就不够


q.MIN_FREE_GB = 5.0
q.free_gb = fake_free_low
with redirect_stdout(io.StringIO()):
    q.cmd_harvest(workers=2, new_only=True, interval=0.0, attempts=1)
after2 = json.load(open(q.CATLOG, encoding="utf-8"))
chk(sum(1 for r in after2 if r.get("status") == "new") == M, "盘不够时一轮都不开下（status 全留 new）")
chk(len([f for f in os.listdir(q.INBOX) if f.endswith(".pdf")]) == 0, "盘不够时没往 _inbox 写任何文件")


def fake_free_drops(path=None):
    calls["n"] += 1
    return 999.0 if calls["n"] <= 1 else 0.4    # 开工够用，第一次落盘检查就爆盘


q.free_gb = fake_free_drops
t1 = time.time()
with redirect_stdout(io.StringIO()):
    q.cmd_harvest(workers=2, new_only=True, interval=0.0, attempts=1)
el = time.time() - t1
# 立刻数文件：晚一秒，被取消之外那几个在途任务就又多下几篇，断言会变得飘忽
made = len([f for f in os.listdir(q.INBOX) if f.endswith(".pdf")])
after3 = json.load(open(q.CATLOG, encoding="utf-8"))
got = sum(1 for r in after3 if r.get("status") == "done")
chk(0 < got < M, "中途爆盘时真的停了：台账只认 %d/%d 篇" % (got, M))
chk(made < M, "_inbox 里的文件也确实没被继续灌满（%d 个 < %d）" % (made, M))
chk(el < 120, "停手是即时的（%.1fs 返回，没把剩下 %d 个排队任务跑完）" % (el, M - got))
q.free_gb = real_free

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
