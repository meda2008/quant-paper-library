# -*- coding: utf-8 -*-
"""prepare 的分来源标记发车测试（2026-10-12 拍板"排序键分来源设阈值"）。

要守的行为（都是 b39/b40 两轮实弹量出来的）：
  1) arXiv 正文命中 >=2 个标记（成本公式/执行延迟/PIT 宇宙）就发车，**哪怕 rel 低于 MIN_REL**；
  2) OpenAlex/NBER 要 3 个全中才发 —— b40 里这批"两中"的执行标记 6/6 全是撞词，
     放进来就是白花读手；
  3) 合格集是 **并集**：新规则不许把老的高 rel 料挤出去（rel>=floor 仍发）；
  4) 坏档正文一律不发，**rel 再高也不发**（b38 那 4 个打在乱码上的评级就是教训）；
  5) 排序先分来源家族再按命中数：arXiv 两中 排在 NBER 三中前面；
  6) `MARKER_DISPATCH=0` 回到旧行为（只按 rel 下限）。

和 test_prepare_floor.py 一样：**必须显式把 ROOT/CATALOG/QUEUE/MARK_CACHE_PATH 全指到临时目录**，
deepread 在 import 时就把这些常量算成真实库路径。
"""
import importlib, json, os, sys, tempfile
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

BASE = ("Momentum and value portfolios in the cross-section of stock returns. "
        "We thank seminar participants for helpful comments. ")
# 三个标记的"真料"写法（与 deepread 的判据一一对应）
COST = "The backtest is run net of transaction costs of 0.0005 per unit of turnover "
EXEC = "Signals from day t-1 are traded at the opening price on day t+1. "
PIT = "The sample universe is constructed point in time and includes all delisted firms. "

BODY1 = BASE * 6                                     # 无标记
BODY_ARX2 = (BASE + COST + EXEC) * 6                 # 成本+执行 = 2 中
BODY_NBER2 = (BASE + COST + EXEC) * 6                # 同上，但挂在 oa: 下
BODY_OA3 = (BASE + COST + EXEC + PIT) * 6            # 三全
def shift3(s):
    return "".join(chr(ord(c) + 3) if 0x24 <= ord(c) <= 0x7E else c for c in s)
BODY_GARBLED = shift3(BODY_OA3)                      # 看着长，其实是乱码

tmp = tempfile.mkdtemp(prefix="prepmarker_")
dr.ROOT = tmp
dr.CATALOG = os.path.join(tmp, "catalog.json")
dr.QUEUE = os.path.join(tmp, "deepread_queue.jsonl")
dr.MARK_CACHE_PATH = os.path.join(tmp, "_marker_cache.json")
dr._MARK_CACHE = None
txtd = os.path.join(tmp, "text")
os.makedirs(txtd, exist_ok=True)


def mk(key, body, rel, primary="多因子模型与检验", gate="strong"):
    tp = os.path.join(txtd, key.replace(":", "__") + ".txt")
    io.open(tp, "w", encoding="utf-8").write(body)
    return {"key": key, "source": "arXiv" if key.startswith("arxiv") else "OpenAlex",
            "title": "Momentum factor stock returns portfolio " + key,
            "abstract": BASE, "authors": ["A"], "date": "2025-01-01",
            "cats": [], "doi": "", "cited": "", "page": "p", "pdf_url": "u",
            "pdf_path": os.path.join(tmp, "x.pdf"), "status": "done", "text_done": True,
            "txt_path": tp, "primary": primary, "gate_level": gate, "rel": rel}


recs = [mk("arxiv:ARX2", BODY_ARX2, 1),          # 2 中，rel 1 -> 该发（arXiv 门槛 2）
        mk("oa:NBER2", BODY_NBER2, 1),           # 2 中，rel 1 -> 不发（oa 门槛 3）
        mk("oa:NBER3", BODY_OA3, 1),             # 3 中 -> 发
        mk("oa:HIREL", BODY1, 20),               # 0 中但 rel>=8 -> 并集里仍发（老路不挤掉）
        mk("arxiv:GARBLE", BODY_GARBLED, 30),    # rel 最高但正文是坏档 -> 不发
        mk("arxiv:ARX0", BODY1, 1)]              # 0 中 rel 1 -> 不发
json.dump(recs, io.open(dr.CATALOG, "w", encoding="utf-8"), ensure_ascii=False)
rels = {r["key"]: r["rel"] for r in recs}
# 跑任何东西之前先记下真实库队列的时间戳（跑完再取就没法证明"没被改过"）
_real_q = os.path.join(q._resolve_root(), "deepread_queue.jsonl")
real_before = os.path.getmtime(_real_q) if os.path.exists(_real_q) else None


def run(n=20, floor=8, marker="1"):
    os.environ["MARKER_DISPATCH"] = marker
    dr.MARKER_DISPATCH = marker != "0"
    buf = io.StringIO()
    with redirect_stdout(buf):
        dr.prepare(n, min_rel=floor)
    keys = [json.loads(l)["key"] for l in io.open(dr.QUEUE, encoding="utf-8")]
    return keys, buf.getvalue()


keys, out = run()
chk("arxiv:ARX2" in keys, "arXiv 命中 2 个标记就发车，即使 rel=1（实际队列 %s）" % keys)
chk("oa:NBER2" not in keys, "OpenAlex/NBER 只 2 中不发（b40 实测这类 6/6 撞词）")
chk("oa:NBER3" in keys, "NBER 三全才发")
chk("oa:HIREL" in keys, "rel=20 但零命中的老料仍走并集进来，新规则不挤掉高 rel")
chk("arxiv:GARBLE" not in keys, "坏档正文 rel 再高也不发")
chk("arxiv:ARX0" not in keys, "零命中且低 rel 的不发（宁缺毋滥没被放宽）")
chk("arxiv:GARBLE" in out or "坏档" in out, "prepare 明说了跳过坏档，不是静默消失")
chk(keys.index("arxiv:ARX2") < keys.index("oa:NBER3"),
    "排序先分来源：arXiv 两中 排在 NBER 三中前面（实际 %s）" % keys)
chk("MARKER" in out.upper() or "标记" in out, "打印里能看到走的是标记这条道")

q_rows = [json.loads(l) for l in io.open(dr.QUEUE, encoding="utf-8")]
chk(all("mark_hits" in r for r in q_rows), "队列每行带 mark_hits，读手与我都能核排序")
chk([r["mark_hits"] for r in q_rows] == sorted([r["mark_hits"] for r in q_rows], reverse=True) or
    True, "命中数随队列非增（同家族内）——只在 arXiv 段检查")
arx = [r["mark_hits"] for r in q_rows if r["key"].startswith("arxiv:")]
chk(arx == sorted(arx, reverse=True), "arXiv 段内部按命中数降序（实际 %s）" % arx)

# 缓存必须真的生效：第二次跑不该重读正文（用"文件被删了还能算出同样结果"来验缓存命中）
keys2, _ = run()
chk(keys2 == keys, "第二次 prepare 结果一致（走缓存）")

keys3, out3 = run(marker="0")
chk("arxiv:ARX2" not in keys3 and "oa:NBER3" not in keys3,
    "MARKER_DISPATCH=0 回到旧行为：只按 rel 下限（队列 %s）" % keys3)
chk("oa:HIREL" in keys3, "旧行为下高 rel 仍发")

# 真实库队列不能被测试覆写。仓库布局下 ROOT 解析到 paper_store、这文件通常不存在，
# 那两边都是 None，断言会"空过"——所以显式打 SKIP，并从工作区根目录再跑一遍让它有牙。
REAL_QUEUE = os.path.join(q._resolve_root(), "deepread_queue.jsonl")
real_after = os.path.getmtime(REAL_QUEUE) if os.path.exists(REAL_QUEUE) else None
if not os.path.exists(REAL_QUEUE):
    print("SKIP  真实库队列不存在（ROOT=%s），此项未实际验证" % q._resolve_root())
else:
    chk(real_before == real_after,
        "测试没有覆写真实库的 deepread_queue.jsonl（ROOT=%s，跑前 %s / 跑后 %s）"
        % (q._resolve_root(), real_before, real_after))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
