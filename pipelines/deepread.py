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

def excerpt_path(rec):
    return rec.get("txt_path") or ""

def prepare(n=120):
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
        # 所以同层内按 rel 从高到低排（rel 只排队，不做删除）。
        rel = r.get("rel")
        if rel is None:
            rel = q.rel_score(r.get("title"), r.get("abstract"))
        return (tier, -int(rel), pr, no_abs, "0" if r.get("date", "") >= "2024" else "1")
    cand.sort(key=lambda r: (rank(r), r.get("date", "")), reverse=False)
    sel = cand[:n]
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
