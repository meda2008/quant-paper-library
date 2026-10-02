# -*- coding: utf-8 -*-
"""离线验证 cmd_catalog 的增量合并语义（不联网、不碰 Z 盘）。
三个 collect_* 必须全部打桩：2026-09-30 这测试漏了 collect_openreview，
结果真的去爬 OpenReview 捞回 2297 条，断言全错还白耗一次外呼。"""
import json, os, re, sys, tempfile, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库布局是 tests/ 与 pipelines/ 平级；本地旧布局是脚本与测试同目录。两种都认。
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q

tmp = tempfile.mkdtemp(prefix="catmerge_")
q.ROOT = tmp
q.CATLOG = os.path.join(tmp, "catalog.json")

old = [
    {"key": "arxiv:A", "source": "arXiv", "title": "OLD A", "pdf_url": "u", "abstract": "", "authors": [],
     "date": "", "cats": [], "doi": "", "cited": "", "status": "done", "pdf_path": "Z:/论文/多因子模型与检验/A.pdf",
     "text_done": True, "txt_path": "Z:/论文/text/A.txt", "primary": "多因子模型与检验", "tags": {"momentum": 3},
     "summary": "旧的总结", "offtopic": False, "pages": 42, "retries": 2, "gate_level": "strong",
     "ai_summary": "已精读", "ai_grade": "B"},
    {"key": "oa:B", "source": "OpenAlex", "title": "OLD B", "pdf_url": "u", "abstract": "", "authors": [],
     "date": "", "cats": [], "doi": "", "cited": "", "status": "failed", "pdf_path": "", "primary": "",
     "error": "404"},
    {"key": "arxiv:C", "source": "arXiv", "title": "OLD C 本次检索未命中", "pdf_url": "u", "abstract": "",
     "authors": [], "date": "", "cats": [], "doi": "", "cited": "", "status": "done",
     "pdf_path": "Z:/论文/机器学习选股/C.pdf", "text_done": True, "primary": "机器学习选股",
     "summary": "C 总结", "offtopic": True, "pages": 7},
    {"key": "or:E", "source": "OpenReview", "title": "OLD E", "pdf_url": "u5", "abstract": "旧摘要很完整",
     "authors": ["W"], "date": "2024-01", "cats": [], "doi": "d-e", "cited": 7, "status": "done",
     "pdf_path": "Z:/论文/组合优化与配置/E.pdf", "text_done": True, "primary": "组合优化与配置",
     "dup_of": "arxiv:A", "retries": 3,
     "rebound_at": "2026-10-02", "ocr": True, "ocr_pending": True},
]
json.dump(old, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)

# 本次检索命中：A(元数据刷新) + B + D(全新) + E(来源本轮没返回摘要)
q.collect_arxiv = lambda: [
    {"key": "arxiv:A", "source": "arXiv", "title": "NEW A", "pdf_url": "u2", "abstract": "a", "authors": ["X"],
     "date": "2026-09-24", "cats": ["q-fin.ST"], "doi": "", "cited": "", "page": "p"},
    {"key": "arxiv:D", "source": "arXiv", "title": "NEW D", "pdf_url": "u3", "abstract": "a", "authors": ["Y"],
     "date": "2026-09-24", "cats": [], "doi": "", "cited": "", "page": "p"},
]
q.collect_openalex = lambda: [
    {"key": "oa:B", "source": "OpenAlex", "title": "NEW B", "pdf_url": "u4", "abstract": "a", "authors": ["Z"],
     "date": "2026-09-23", "cats": [], "doi": "b", "cited": 5, "page": "p"},
]
# collect_openreview 现在返回 (记录, 被验证墙挡住的 key 集合)
def _interleave_and_or(*a, **kw):
    """模拟精读 merge 在 cmd_catalog 读盘之后、写回之前落盘：
    安全写必须保住这份外部改动，而不是用 35 分钟前的旧副本盖掉。"""
    cur = json.load(open(q.CATLOG, encoding="utf-8"))
    for r in cur:
        if r["key"] == "arxiv:A":
            r["ai_summary"] = "外部并发写入的精读结论"
            r["ai_grade"] = "A"
    cur.append({"key": "or:Z", "source": "OpenReview", "title": "别的进程刚加的记录",
                "pdf_url": "https://arxiv.org/pdf/9999.0001", "status": "new", "abstract": "z"})
    json.dump(cur, open(q.CATLOG, "w", encoding="utf-8"), ensure_ascii=False)
    return (
        [
            {"key": "or:E", "source": "OpenReview", "title": "NEW E",
             "pdf_url": "https://openreview.net/pdf?id=E", "abstract": "", "authors": [],
             "date": "", "cats": [], "doi": "", "cited": "", "page": "p"},
            {"key": "or:F", "source": "OpenReview", "title": "NEW F 有 arXiv 直链",
             "pdf_url": "https://arxiv.org/pdf/1234.5678", "abstract": "f", "authors": ["V"],
             "date": "2026-09-20", "cats": [], "doi": "", "cited": "", "page": "p"},
            {"key": "or:G", "source": "OpenReview", "title": "NEW G 只有被墙链接",
             "pdf_url": "https://openreview.net/pdf?id=G", "abstract": "g", "authors": ["U"],
             "date": "2026-09-21", "cats": [], "doi": "", "cited": "", "page": "p"},
        ],
        {"or:G"},
    )

q.collect_openreview = _interleave_and_or

# NBER 是后加的检索腿，同样必须打桩——2026-10-01 漏打桩导致本测试真的去爬了
# nber.org，断言全错还挂在网络超时上。
q.collect_nber = lambda: []

# 白名单护栏：上面四个 collect_* 必须覆盖 quant_library 里实际调用的全部检索腿。
# 以后再加一条腿而忘了打桩，这里会当场炸，而不是安静地去联网。
_called = set(re.findall(r"collect_\w+\(", open(
    os.path.join(sys.modules["quant_library"].__file__), encoding="utf-8").read()))
_called = {c[:-1] for c in _called}
_stubbed = {"collect_arxiv", "collect_openalex", "collect_openreview", "collect_nber"}
_missing = _called - _stubbed
assert not _missing, (
    "quant_library 新增了检索腿 %s，但本测试没打桩 -> 会真的联网。"
    "请在上面的白名单里补一条 lambda: []。" % sorted(_missing))

# 硬断网：任何真实出网都会抛，而不是悄悄跑完再让断言报错。
def _no_net(*a, **kw):
    raise AssertionError("测试期间发生真实网络访问（本应完全离线）")

urllib.request.urlopen = _no_net
q.urllib.request.urlopen = _no_net
q._curl = _no_net

q.cmd_catalog()

recs = {r["key"]: r for r in json.load(open(q.CATLOG, encoding="utf-8"))}
ok = True


def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)


chk(len(recs) == 8, f"合并后 8 条 (实际 {len(recs)})：三个来源都打桩，不得联网")
chk(recs["arxiv:A"].get("ai_summary") == "外部并发写入的精读结论" and recs["arxiv:A"].get("ai_grade") == "A",
    "并发 merge 的 ai_* 没被 35 分钟前的旧内存副本盖掉（2026-09-30 事故回归）")
chk("or:Z" in recs, "别的进程在跑盘期间新增的记录被保留，没被整份写回抹掉")
chk("arxiv:C" in recs, "未被检索命中的历史条目 C 保留(不从库中删除)")
chk(recs["arxiv:C"]["status"] == "done" and recs["arxiv:C"]["primary"] == "机器学习选股", "C 的 status/primary 不变")
chk(recs["arxiv:C"]["text_done"] is True and recs["arxiv:C"]["offtopic"] is True, "C 的 text_done/offtopic 不变")
chk(recs["arxiv:A"]["title"] == "NEW A", "A 的元数据被本次检索刷新")
chk(recs["arxiv:A"]["status"] == "done", "A 状态仍为 done(harvest 会跳过=不重复下载)")
chk(recs["arxiv:A"]["text_done"] is True, "A 的 text_done 继承(index 不重复解析)")
chk(recs["arxiv:A"]["summary"] == "旧的总结", "A 的 summary 继承")
chk(recs["arxiv:A"]["tags"] == {"momentum": 3}, "A 的 tags 继承")
chk(recs["arxiv:A"]["offtopic"] is False, "A 的 offtopic=False 未被丢弃")
chk(recs["arxiv:A"]["pdf_path"].endswith("A.pdf"), "A 的 pdf_path 继承")
chk(recs["arxiv:A"].get("retries") == 2, "A 的 retries 继承(否则死链重试次数被清零)")
chk(recs["arxiv:A"].get("gate_level") == "strong", "A 的 gate_level 继承")
chk(recs["oa:B"]["status"] == "failed", "B 保留 failed(本轮会重试)")
chk(recs["arxiv:D"]["status"] == "new", "D 新条目 status=new(会被下载)")
chk(recs["or:E"]["abstract"] == "旧摘要很完整", "E：来源本轮没给摘要时旧 abstract 补空(2026-09-30 抹掉 546 条的回归)")
chk(recs["or:E"].get("dup_of") == "arxiv:A", "E 的 dup_of 继承(曾被链条抹掉 55 条)")
chk(recs["or:E"].get("retries") == 3, "E 的 retries 继承")
chk(recs["or:E"].get("rebound_at") == "2026-10-02", "E 的 rebound_at 继承（台账救援标记不被每日重写抹掉）")
chk(recs["or:E"].get("ocr") is True, "E 的 ocr 继承（扫描件兜底标记）")
chk(recs["or:E"].get("ocr_pending") is True, "E 的 ocr_pending 继承")
chk(recs["or:E"]["doi"] == "d-e" and recs["or:E"]["cited"] == 7, "E 的 doi/cited 补空保住")
chk(recs["or:E"]["status"] == "done" and recs["or:E"]["pdf_path"].endswith("E.pdf"), "E 的下载状态与路径继承")
chk(recs["or:G"]["status"] == "giveup" and recs["or:G"].get("error") == "or_walled_no_pdf",
    "G 只有 openreview.net/pdf（Turnstile 墙）→ catalog 阶段直接 giveup，不进下载队列")
chk(recs["or:E"]["status"] == "done", "E 是已下载完成的历史条目，被墙判断不得把 done 降级")
chk(recs["or:F"]["status"] == "new", "F 有 arXiv 直链 → 正常进待下载")
n_new = sum(1 for r in recs.values() if r["status"] == "new")
chk(n_new == 3, f"待下载 3 篇（D、F 与并发注入的 or:Z），被墙的 G 不在内 (实际 {n_new})")
print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
