# -*- coding: utf-8 -*-
"""AI 精读流水线：prepare 选题打包 / merge 合并结果
用法: python deepread.py prepare [N]   -> 生成 <ROOT>/deepread_queue.jsonl（N 篇，默认 120）
      python deepread.py merge <part...> -> 把 jsonl 结果并入 catalog.json 的 ai_summary/grade 字段
选题优先级: 中国A股 > 机器学习选股 > 多因子模型与检验 > 其他；年份新 > 旧；必须有摘要。
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant_library as q          # 复用安全写

ROOT = q._resolve_root()
CATALOG = os.path.join(ROOT, "catalog.json")
QUEUE = os.path.join(ROOT, "deepread_queue.jsonl")
PRIO = ["中国A股", "机器学习选股", "多因子模型与检验", "动量与反转", "价值与基本面因子",
        "组合优化与配置", "另类数据与文本挖掘", "低波动与风险因子"]

# 发车下限：rel 管"是不是能拿来做选股/因子/组合"。
# 三批同尺对照（同一条按 rel 排序的队列上的相邻切片，rel 区间互不重叠）：
#   b26 rel 中位 2  -> A+B 15.5%   b30 中位 6 -> 17.5%   b29 中位 17 -> 36.0%
# 即 3~6 这一段与"不设门槛"没有区别，抬到 8 以上才有质量增益，故下限取 8。
# （早先我按历史分布算过"rel>=10 有 60%"，那是拿早期精选批次估的、带幸存者偏差，
#  实测只有 36%，已改用上面三批对照为准。）
# **rel 只能用来排序挑料，不能当删除过滤器**：已判级的 A+B 里有 17.1% rel<3、
# A 档自己的第 10 百分位只有 2 —— 按它删会连真能用的论文一起砍掉（2026-10-03 实测）。
# 低于下限的条目不删，只是暂不发，等高相关的料读完再回头看。
MIN_REL = int(os.environ.get("MIN_REL", "8"))


# ---- 正文三标记：分来源的发车判据（2026-10-12 拍板）----
# 由来：rel>=8 的可发料实测清零（prepare 合格 0 篇），而未读池 3126 篇全在低 rel；
# 同时两轮实弹给出了"标记按来源分族"的证据——
#   b39（未读∩三命中，rel 4~7，15 篇）：arXiv 5/5 出 B，OA/NBER 10/10 全 C，A+B 33.3%；
#   b40（OCR 重抽后的 NBER 存量，15 篇）：执行标记命中 6 篇**全撞词**、宇宙 4 命中 3 撞词、
#        成本 4 命中只有 1 篇是可部署费率，结果 A0/B1/C14。
# 所以判据有牙但**不能不分来源地用**：arXiv 上 2 个标记就值得发，NBER/OpenAlex 要 3 个才算。
# 必须记住的偏置：ai_grade 本身就是按这三查打的，标记与评级同义反复的成分存在，
# 这张表只配用来排发车顺序，不配当"论文好坏"的独立证据。
MARK_COST = re.compile(
    r"c\s*=\s*0\.\d{1,4}|"
    r"(?:one[- ]way|round[- ]trip|proportional)\s+(?:commission|fee|cost|trading\s+cost)|"
    r"\b\d+(?:\.\d+)?\s*(?:bps|bp|basis points)\b|"
    r"transaction[- ]cost(?:s)?\s+(?:of|are|at|to)\s+|"
    r"cost[- ]adjusted\s+(?:sharpe|ratio|return|performance|net)|"
    r"net[- ]of\s+(?:transaction\s+)?costs?|after[- ]transaction[- ]costs?|"
    r"bid[- ]ask\s+spread|slippage|fee\s+(?:rate|schedule|structure)|"
    r"(?:trading|turnover)\s+cost(?:s)?\s+(?:model|assumption|deduct)", re.I)
MARK_EXEC = re.compile(
    r"t\s*\+\s*1|next\s+(?:business\s+)?day'?s?\s+(?:open|close|return)|opening\s+price|"
    r"arrival\s+price|delay(?:ed)?\s+(?:execution|trading|return)|"
    r"execution\s+(?:delay|lag)|vol(?:ume)?[- ]weighted\s+average\s+price|vwap", re.I)
# PIT 这一族是 b40 数出来的最大噪声源：`CRSP` 单独出现只说明"用了 CRSP 数据"，
# 不等于"宇宙是时点构造的"。所以把裸 CRSP 从判据里拿掉，要求带构造性说法。
MARK_PIT = re.compile(
    r"delist(?:ed|ings|ing)|survivorship(?:\s+bias)?|point[- ]in[- ]time|pre[- ]indexed|"
    r"universe\s+(?:composition|includes|is\s+constructed|spans)|constituent\s+changes|"
    r"(?:sample|universe)\s+(?:includes|covers)[^.]{0,60}delist", re.I)
MARK_MIN_HITS = {
    "arxiv": int(os.environ.get("MARK_MIN_HITS_ARXIV", "2")),
    "oa": int(os.environ.get("MARK_MIN_HITS_OA", "3")),
    "or": int(os.environ.get("MARK_MIN_HITS_OR", "3")),
}
FAMILY_ORDER = {"arxiv": 0, "oa": 1, "or": 2, "other": 3}
MARKER_DISPATCH = os.environ.get("MARKER_DISPATCH", "1") != "0"
MARK_CACHE_PATH = os.path.join(ROOT, "_marker_cache.json")
_MARK_CACHE = None


def _family(key):
    k = key or ""
    for f in ("arxiv", "oa", "or"):
        if k.startswith(f + ":"):
            return f
    return "other"


def _load_mark_cache():
    global _MARK_CACHE
    if _MARK_CACHE is None:
        try:
            _MARK_CACHE = json.load(open(MARK_CACHE_PATH, encoding="utf-8"))
        except Exception:
            _MARK_CACHE = {}
    return _MARK_CACHE


def _mark_body(path):
    """读正文前 15 万字，返回 (命中数, 明细, 是否仍是坏档)。带磁盘缓存。
    坏档直接判"没命中且不可发"：读手拿到乱码只会产出一篇假结论（b38 那次 4 个评级就是白打的）。"""
    c = _load_mark_cache()
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0, {"cost": 0, "exec": 0, "pit": 0}, True
    head = c.get(path)
    if head and head[0] == size:
        return int(head[1]), {"cost": head[2], "exec": head[3], "pit": head[4]}, bool(head[5])
    try:
        t = open(path, encoding="utf-8", errors="replace").read(150000)
    except Exception:
        return 0, {"cost": 0, "exec": 0, "pit": 0}, True
    garbled = q._is_garbled(t)
    d = {"cost": 1 if MARK_COST.search(t) else 0,
         "exec": 1 if MARK_EXEC.search(t) else 0,
         "pit": 1 if MARK_PIT.search(t) else 0}
    hits = 0 if garbled else sum(d.values())
    c[path] = [size, hits, d["cost"], d["exec"], d["pit"], 1 if garbled else 0]
    return hits, d, garbled


def _save_mark_cache():
    if not _MARK_CACHE:
        return
    tmp = MARK_CACHE_PATH + ".tmp"
    try:
        q._write_json_atomic(_MARK_CACHE, tmp, MARK_CACHE_PATH, tries=6)
    except Exception as e:
        print("  标记缓存没落盘（不影响本次发车）:", type(e).__name__, str(e)[:60])


def _rel_of(r):
    """台账里存过 rel 就用存的，没有就按题摘现算（与 index 里同一把尺子）。"""
    v = r.get("rel")
    if v is None or v == "":
        return q.rel_score(r.get("title"), r.get("abstract"))
    try:
        return int(v)
    except (TypeError, ValueError):
        return q.rel_score(r.get("title"), r.get("abstract"))

def excerpt_path(rec):
    return rec.get("txt_path") or ""

def prepare(n=120, min_rel=None):
    floor = MIN_REL if min_rel is None else int(min_rel)
    c = json.load(open(CATALOG, encoding="utf-8"))
    queued = set()
    import glob as _g
    for qf in _g.glob(os.path.join(ROOT, "deepread_q*.jsonl")) + \
              _g.glob(os.path.join(ROOT, "deepread_b*_q*.jsonl")):
        # _res_ 是结果文件；deepread_queue.jsonl 是本次即将覆写的队列，
        # 把它当"在途批次"会导致每次都跟自己要发的 30 篇自撞（2026-09-30 踩过）
        if "_res_" in qf or os.path.basename(qf) == "deepread_queue.jsonl":
            continue
        for line in open(qf, encoding="utf-8"):
            if line.strip():
                queued.add(json.loads(line)["key"])
    cand = [r for r in c if r.get("status") == "done" and not r.get("offtopic")
            and not r.get("dup_of")
            and not r.get("ai_summary") and r.get("text_done")
            and r["key"] not in queued
            and (r.get("txt_path") or "") and os.path.isfile(r.get("txt_path", ""))]
    def rank(r):
        pr = PRIO.index(r["primary"]) if r.get("primary") in PRIO else 99
        # 缺摘要的排到同类后面，但不排除 —— 旧条件把没摘要的经典文献（NBER/期刊版）静默跳掉了
        no_abs = 0 if (r.get("abstract") or "").strip() else 1
        # 门槛分层：先读"题摘确有强金融词"的，把词碰撞误召回排到最后
        # （批15 实测：无差别排队会让七组里约 92% 的力气花在误召回上）
        tier = {"strong": 0, "body": 1, "fail": 2}.get(r.get("gate_level") or "", 1)
        # 排序键（2026-10-12 改）：先分来源家族，再按正文三标记命中数，
        # 然后才是原来的 门槛层 → rel → 主题优先级。
        # rel 从"第一判据"降级成"同分内的次级排序"：b35/b39 实测它在 OpenAlex 一族里
        # 与成色**反向**（唯一的 B 出在 rel=8 下沿，rel 12/18 是综述），
        # 而分来源的标记命中数在两轮实弹里都单调。
        fam = FAMILY_ORDER.get(_family(r.get("key")), 3)
        hits = _hits.get(r["key"], 0)
        return (fam, -hits, tier, -_rel_of(r), pr, no_abs, "0" if r.get("date", "") >= "2024" else "1")
    n_before = len(cand)
    # 正文标记：命中数与"是否仍是坏档"都要算一次（带磁盘缓存，首趟要读几千个 txt）
    _hits, _garbled = {}, {}
    if MARKER_DISPATCH:
        for i, r in enumerate(cand):
            h, d, g = _mark_body(r.get("txt_path") or "")
            _hits[r["key"]] = h
            if g:
                _garbled[r["key"]] = 1
            if i and i % 1000 == 0:
                print("  正文标记已算 %d/%d（已算过的走缓存）" % (i + 1, len(cand)), flush=True)
        _save_mark_cache()
        if _garbled:
            print("  跳过坏档正文（读了也是假结论）: %d 篇" % len(_garbled))
        cand = [r for r in cand if r["key"] not in _garbled]
    low_rel = [r for r in cand if _rel_of(r) < floor]
    if MARKER_DISPATCH:
        # 合格集 = 分来源标记够数的 ∪ rel 过下限的（并集，不是替换）：
        # 标记是新开的道，不该把原来高 rel 的料挤出去；下限本身仍是"宁缺毋滥"的那道闸。
        by_fam = {}
        def eligible(r):
            f = _family(r.get("key"))
            need = MARK_MIN_HITS.get(f, 3)
            got = _hits.get(r["key"], 0)
            by_fam.setdefault(f, [0, 0])
            by_fam[f][1] += 1
            if got >= need:
                by_fam[f][0] += 1
                return True
            return _rel_of(r) >= floor
        n_before_floor = len(cand)
        cand = [r for r in cand if eligible(r)]
        low_rel = [r for r in cand if _rel_of(r) < floor]
        # "被挡下 N 篇"这句必须留着：test_prepare_floor.py 守的就是
        # "prepare 不许静默变少"，换了发车判据也不许把这句弄丢
        print("分来源标记阈值 " + " ".join(
            "%s>=%d(够格%d/%d)" % (f, MARK_MIN_HITS.get(f, 3), v[0], v[1])
            for f, v in sorted(by_fam.items())) +
              "；并上 rel>=%d 的老路后合格 %d 篇，被挡下 %d 篇（不删，留在库里等以后重估）"
              % (floor, len(cand), n_before_floor - len(cand)))
    else:
        cand = [r for r in cand if _rel_of(r) >= floor]
        print("rel 下限 MIN_REL=%d：合格 %d 篇，被挡下 %d 篇（不删，留在库里等以后重估）"
              % (floor, len(cand), len(low_rel)))
    cand.sort(key=lambda r: (rank(r), r.get("date", "")), reverse=False)
    sel = cand[:n]
    if len(sel) < n:
        print("  注：本批只凑到 %d 篇（要 %d 篇）——宁缺毋滥，不拿低相关料补数"
              % (len(sel), n))
    with open(QUEUE, "w", encoding="utf-8") as f:
        for r in sel:
            f.write(json.dumps({"key": r["key"], "title": r["title"], "txt": r["txt_path"],
                                "abstract": (r.get("abstract") or "")[:1200], "primary": r["primary"],
                                "date": r.get("date", ""), "state": "pending",
                                "rel": _rel_of(r), "mark_hits": _hits.get(r["key"], 0)},
                               ensure_ascii=False) + "\n")
    print(f"队列已生成: {len(sel)} 篇 -> {QUEUE}")
    print("其中无摘要(仅靠正文判级): %d" % sum(1 for r in sel if not (r.get("abstract") or "").strip()))
    from collections import Counter
    print("分类分布:", dict(Counter(r['primary'] for r in sel)))
    print("门槛分层:", dict(Counter(r.get('gate_level') or '(无)' for r in sel)))
    if MARKER_DISPATCH:
        print("家族分布:", dict(Counter(_family(r.get('key')) for r in sel)),
              "命中数分布:", dict(Counter(_hits.get(r['key'], 0) for r in sel)))

def mark(claims):
    pass

def merge(parts):
    c = q.load_catalog()
    orig = q.snapshot(c)
    idx = {r["key"]: r for r in c}
    n = 0
    nograde = []
    for p in parts:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                print("坏行:", line[:80]); continue
            r = idx.get(d.get("key"))
            if not r:
                print("未知 key:", d.get("key")); continue
            # 结果文件历史上有两种字段名：批9及以前 grade/findings，批10起 ai_grade/ai_findings
            r["ai_summary"] = d.get("ai_summary", "")[:800]
            r["ai_grade"] = (d.get("grade") or d.get("ai_grade") or "").strip()[:1]
            fnd = d.get("findings")
            if fnd is None:
                fnd = d.get("ai_findings")
            r["ai_findings"] = (fnd or [])[:5]
            r["ai_read_at"] = d.get("read_at", "")
            n += 1
            if not r["ai_grade"]:
                nograde.append(d.get("key"))
    q.save_catalog(c, orig)
    print(f"合并完成 {n} 篇（空评级 {len(nograde)}，如非零请查字段名）。下次 index 重建时 AI 精读列将进入 INDEX.md/CSV。")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    if cmd == "prepare":
        prepare(int(sys.argv[2]) if len(sys.argv) > 2 else 120)
    elif cmd == "merge":
        merge(sys.argv[2:])
