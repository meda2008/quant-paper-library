# -*- coding: utf-8 -*-
"""arXiv 2000 条封顶的截断检测 + 窗口自动细分，全离线（http_get 打桩，不联网、不碰 Z 盘）。
背景：以前 totalResults 解析写在 Atom 命名空间里，永远返回 0，于是"某一档查询被封顶截断"
这件事根本看不见，漏了多少无从判断。"""
import json, os, sys, tempfile
from urllib.parse import urlparse, parse_qs
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库布局是 tests/ 与 pipelines/ 平级；本地旧布局是脚本与测试同目录。两种都认。
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q

tmp = tempfile.mkdtemp(prefix="sweepcap_")
q.ROOT = tmp
q.SWEEP_PLAN = os.path.join(tmp, "_sweep_plan.json")
q.CAP_LOG = os.path.join(tmp, "_cap_hits.txt")
q.time.sleep = lambda *a: None          # 测试里不等分页间隔
ok = True
def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)

def page(start, n, total):
    ent = "".join(
        "<entry><id>http://arxiv.org/abs/2401.%05dv1</id>"
        "<title>Factor investing and stock selection study %d</title>"
        "<summary>momentum portfolio alpha factor on the cross-section of stock returns</summary>"
        "<published>2024-01-02T00:00:00Z</published><author><name>A B</name></author>"
        "<category term=\"q-fin.ST\"/></entry>" % (start + i, start + i)
        for i in range(n))
    return (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        '<opensearch:totalResults>%d</opensearch:totalResults>%s</feed>' % (total, ent)).encode()

BOX = {"total": 5000}
def fake_get(url, timeout=90):
    qs = parse_qs(urlparse(url).query)
    start = int(qs.get("start", ["0"])[0])
    return page(start, max(0, min(200, BOX["total"] - start)), BOX["total"])
q.http_get = fake_get

# 1) 撞上限：totalResults 5000 > cap 400 -> 必须记一条截断
q.CAP_HITS.clear()
rows = q.arxiv_query("cat:q-fin.PM AND submittedDate:[201801010000 TO 202101010000]", cap=400, tag="q-fin.PM 2018-2021")
chk(len(rows) == 400, "cap=400 时确实只取回 400 条（实际 %d）" % len(rows))
chk(len(q.CAP_HITS) == 1 and q.CAP_HITS[0]["total"] == 5000,
    "totalResults 解析出来了，并判定为截断：%s" % (q.CAP_HITS[:1]))
chk(os.path.exists(q.CAP_LOG), "截断记录落盘了（不只活在内存里）")
chk("q-fin.PM 2018-2021" in open(q.CAP_LOG, encoding="utf-8").read(), "落盘记录里带了是哪一档窗口")

# 2) 没撞上限：total 300 < cap 400 -> 不许误报截断
q.CAP_HITS.clear()
BOX["total"] = 300
rows = q.arxiv_query("cat:q-fin.ST AND submittedDate:[199701010000 TO 199801010000]", cap=400, tag="q-fin.ST 1997")
chk(len(q.CAP_HITS) == 0, "total<cap 不误报截断（拿到 %d 条）" % len(rows))
chk(len(rows) == 300, "小窗口拿全了（300 条）")

# 3) 解析命名空间：找不到 OpenSearch 字段时不能崩，也不能当截断
q.CAP_HITS.clear()
calls = {"n": 0}
def fake_get_no_total(url, timeout=90):
    calls["n"] += 1
    if calls["n"] > 1:            # 第二页起返空，模拟"总共就这一条"
        return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>').encode()
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><id>http://arxiv.org/abs/2401.00001v1</id><title>Stock selection factor momentum</title>'
            '<summary>portfolio alpha cross-section of stock returns</summary>'
            '<published>2024-01-02T00:00:00Z</published></entry></feed>').encode()
q.http_get = fake_get_no_total
rows = q.arxiv_query("cat:q-fin.MF", cap=400, tag="无 total")
chk(len(rows) == 1 and not q.CAP_HITS, "缺 totalResults 时正常返回且不谎报截断（拿到 %d 条）" % len(rows))
q.http_get = fake_get

# 4) 窗口细分：三年档 -> 三个单年档（用默认窗口里真实存在的 (2018,2021)）
BOX["total"] = 5000
q.CAP_HITS.clear()
q._split_window("q-fin.PM", 2018, 2021)
w = q._windows_for("q-fin.PM")
chk((2018, 2019) in w and (2019, 2020) in w and (2020, 2021) in w, "三年档换成了三个单年档: %s" % w[-3:])
chk((2018, 2021) not in w, "旧的大窗口已从计划里去掉")
plan = json.load(open(q.SWEEP_PLAN, encoding="utf-8"))
chk(len(plan["q-fin.PM"]) == len(q.SWEEP_WINDOWS) + 2,
    "其余窗口原样保留（%d 档 = 默认 %d - 1 + 3）" % (len(plan["q-fin.PM"]), len(q.SWEEP_WINDOWS)))

# 5) 已经细到一年还截断 -> 记账但不再炸查询数
before = json.load(open(q.SWEEP_PLAN, encoding="utf-8"))["q-fin.PM"]
q._split_window("q-fin.PM", 2020, 2021)
chk(json.load(open(q.SWEEP_PLAN, encoding="utf-8"))["q-fin.PM"] == before,
    "单年档仍截断时不无限细分（避免查询数指数爆炸）")

# 6) 切一个不在计划里的窗口 -> 空操作，不能污染计划
q._split_window("q-fin.TR", 2031, 2040)
chk("q-fin.TR" not in json.load(open(q.SWEEP_PLAN, encoding="utf-8")),
    "切不存在的窗口不会凭空写进计划")

# 7) 没计划文件时，_windows_for 用默认窗口
os.remove(q.SWEEP_PLAN)
chk(q._windows_for("econ.GN") == list(q.SWEEP_WINDOWS), "无计划文件时用默认三年窗口")

# 8) 接线：arxiv_sweep 遇到截断确实会调用细分
q.CAP_HITS.clear()
seen_queries = []
def stub_query(query, cap=2000, tag=""):
    seen_queries.append(tag)
    q.CAP_HITS.append({"tag": tag, "query": query, "total": 5000, "returned": cap})
    return []
q.arxiv_query = stub_query
q.SWEEP_CATS = ["q-fin.PM"]
q.SWEEP_WINDOWS = [(2020, 2023), (2023, 2026)]
q.arxiv_sweep(set())
w = q._windows_for("q-fin.PM")
chk(seen_queries == ["q-fin.PM 2020-2023", "q-fin.PM 2023-2026"], "sweep 给每档查询打了 tag: %s" % seen_queries)
chk((2020, 2021) in w, "sweep 检测到截断后自动把窗口写进细分计划")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
