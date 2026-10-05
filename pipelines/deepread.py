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
        # 门槛只管"是不是金融"，rel 管"是不是能拿来做选股/因子/组合"。
        # 批15 的教训是门槛 strong 里塞满了保险精算/央行/电网/加密，光靠门槛排队照样浪费读手，
        # 所以同层内按 rel 从高到低排。
        return (tier, -_rel_of(r), pr, no_abs, "0" if r.get("date", "") >= "2024" else "1")
    n_before = len(cand)
    low_rel = [r for r in cand if _rel_of(r) < floor]
    cand = [r for r in cand if _rel_of(r) >= floor]
    cand.sort(key=lambda r: (rank(r), r.get("date", "")), reverse=False)
    sel = cand[:n]
    print("rel 下限 MIN_REL=%d：合格 %d 篇，被挡下 %d 篇（不删，留在库里等以后重估）"
          % (floor, len(cand), len(low_rel)))
    if len(sel) < n:
        print("  注：本批只凑到 %d 篇（要 %d 篇）——宁缺毋滥，不拿低相关料补数"
              % (len(sel), n))
    with open(QUEUE, "w", encoding="utf-8") as f:
        for r in sel:
            f.write(json.dumps({"key": r["key"], "title": r["title"], "txt": r["txt_path"],
                                "abstract": (r.get("abstract") or "")[:1200], "primary": r["primary"],
                                "date": r.get("date", ""), "state": "pending"},
                               ensure_ascii=False) + "\n")
    print(f"队列已生成: {len(sel)} 篇 -> {QUEUE}")
    print("其中无摘要(仅靠正文判级): %d" % sum(1 for r in sel if not (r.get("abstract") or "").strip()))
    from collections import Counter
    print("分类分布:", dict(Counter(r['primary'] for r in sel)))
    print("门槛分层:", dict(Counter(r.get('gate_level') or '(无)' for r in sel)))

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
