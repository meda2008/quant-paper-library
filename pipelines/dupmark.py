# -*- coding: utf-8 -*-
"""重复条目标记（只写 dup_of 指针，不搬文件、不删任何东西）
用法: python dupmark.py          -> dry-run，只打印会新增/改动的标记
      python dupmark.py --apply -> 原子写回 catalog.json
规则: 在 status=done 的记录里按规范化标题分组；每组选一个 canonical，
      其余标 dup_of=canonical.key。选主优先级: AI评级A/B > 有doi > 被引高 > 页数多 > key字典序。
"""
import json, os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant_library as q          # 复用安全写：避免长时间读盘后用旧副本覆盖并发写入

ROOT = q._resolve_root()
CAT = os.path.join(ROOT, "catalog.json")
sys.stdout.reconfigure(encoding="utf-8")

def norm(t):
    t = (t or "").lower()
    t = re.sub(r"\(.*?\)", " ", t)              # 去掉 "(2021)" / "(arXiv:2106.07103)" 之类尾注
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()

GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "": 4}

def ver_num(key):
    m = re.search(r"v(\d+)$", key or "")
    return int(m.group(1)) if m else 0

def same_arxiv_id(group):
    ids = {re.sub(r"v\d+$", "", r["key"].split(":", 1)[1]) for r in group if r["key"].startswith("arxiv:")}
    return len(ids) == 1 and len(group) == len(ids) and len(group) > 1

def _num(v):
    """cited/pages 在台账里可能是 int、可能是数字串（CSV 重建回来的）、可能是 ""。
    排序键一律先转 int，别让 '-' 号作用在字符串上炸掉整轮 dupmark。"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0

def pick(group, inlib):
    # 同 arXiv 号多版本：取版本号最大的（最新稿）为 canonical
    if same_arxiv_id(group):
        cand = sorted(group, key=lambda r: -ver_num(r["key"]))
    else:
        cand = sorted(group, key=lambda r: (
            GRADE_RANK.get(r.get("ai_grade", ""), 4),
            0 if r.get("doi") else 1,
            -_num(r.get("cited")),
            -_num(r.get("pages")),
            r["key"],
        ))
    # 指针要指向在库的那一份；全组都不在库时保持原选择
    return next((r for r in cand if inlib(r)), cand[0])

def content_clusters(records):
    """按 txt 内容哈希找同文异名（标题规范化抓不到的那种）。
    先按文件大小分桶，只对尺寸碰撞的文件算 md5，避免全库读盘。"""
    bysize = collections.defaultdict(list)
    for r in records:
        p = r.get("txt_path")
        if not p:
            continue
        try:
            bysize[os.path.getsize(p)].append(r)
        except Exception:
            pass
    import hashlib
    groups = []
    for sz, g in bysize.items():
        if len(g) < 2:
            continue
        h = collections.defaultdict(list)
        for r in g:
            try:
                h[hashlib.md5(open(r["txt_path"], "rb").read()).hexdigest()].append(r)
            except Exception:
                pass
        groups += [v for v in h.values() if len(v) > 1]
    return groups

def clusters(title_groups, done, use_content):
    """把标题分组与内容分组做并查集合并：同文异名的论文也要归到一份 canonical。"""
    parent = {r["key"]: r["key"] for r in done}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    members = {}
    for g in title_groups.values():
        for r in g[1:]:
            union(g[0]["key"], r["key"])
        for r in g:
            members[r["key"]] = r
    if use_content:
        for g in content_clusters(done):
            for r in g[1:]:
                union(g[0]["key"], r["key"])
            for r in g:
                members.setdefault(r["key"], r)
    out = collections.defaultdict(list)
    for k, r in members.items():
        out[find(k)].append(r)
    return sorted(out.values(), key=lambda g: g[0]["key"])

def main(apply, use_content=False, rebuild=False):
    c = q.load_catalog()
    orig = q.snapshot(c)
    done = [r for r in c if r.get("status") == "done"]

    def inlib(r):
        return r.get("status") == "done" and not r.get("offtopic")

    groups = collections.defaultdict(list)
    for r in done:
        k = norm(r.get("title"))
        if k:
            groups[k].append(r)
    changes, kept_orphan = [], 0
    for g in clusters(groups, done, use_content):
        if len(g) < 2:
            for r in g:
                # 单条标题组里仍带 dup_of —— 那是内容聚类(--content)认下的同文异名，
                # 不带 --content/--rebuild 时绝不能清，否则每轮都会抹掉上一轮的成果。
                if r.get("dup_of") and not (use_content or rebuild):
                    kept_orphan += 1
            continue
        lead = [r for r in g if not r.get("dup_of")]
        lead = lead[0] if len(lead) == 1 else pick(g, inlib)   # 历史已定主条不动，避免整批翻案
        if not inlib(lead):
            lead = pick(g, inlib)   # 主条被门槛剔掉了，指针改指还在库里的那一份
        for r in g:
            want = None if r["key"] == lead["key"] else lead["key"]
            if r.get("dup_of") != want:
                changes.append((r["key"], want, " | ".join([norm(x.get("title"))[:40] for x in g][:2])))
                r["dup_of"] = want
    if kept_orphan:
        print("保留内容聚类来源的孤立标记 %d 条（要重算全集请加 --content）" % kept_orphan)
    marked = sum(1 for r in c if r.get("dup_of"))
    print("重复簇=%d  本次改动=%d  标记后 dup_of 总数=%d" %
          (sum(1 for g in groups.values() if len(g) > 1), len(changes), marked))
    keep = [r for r in c if r.get("status") == "done" and not r.get("offtopic")]
    dup_keep = [r for r in keep if r.get("dup_of")]
    ok = {x["key"] for x in keep}
    lead_ok = sum(1 for r in dup_keep if r["dup_of"] in ok)
    print("在库内被标重复=%d（其中 canonical 也在库=%d）" % (len(dup_keep), lead_ok))
    for row in changes[:25]:
        print("  改:", row[0], "->", row[1])
    if len(changes) > 25:
        print("  ...共 %d 条改动，仅显示前 25" % len(changes))
    if apply:
        q.save_catalog(c, orig)
        print("已安全写回 catalog.json（只叠加本次改动的字段）")
    else:
        print("dry-run，未写入")

if __name__ == "__main__":
    main("--apply" in sys.argv, use_content="--content" in sys.argv, rebuild="--rebuild" in sys.argv)
