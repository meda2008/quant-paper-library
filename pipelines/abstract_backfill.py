# -*- coding: utf-8 -*-
"""摘要回填：给"在库、没有 abstract、但已有全文 txt"的条目从 txt 里抽 Abstract 段。
为什么必须跑：deepread.py prepare 的选题条件之一是"有摘要"，缺摘要的经典文献
（NBER/RFS/期刊版常只有 arXiv 预印本带 OpenAlex 摘要）会被静默跳过，永远进不了精读队列。
用法: python abstract_backfill.py          -> dry-run，打印候选与抽样
      python abstract_backfill.py --apply -> 写回 catalog.json（原子）
"""
import json, os, re, sys, random

ROOT = os.environ.get("PAPER_ROOT", "paper_store")
CAT = os.path.join(ROOT, "catalog.json")
sys.stdout.reconfigure(encoding="utf-8")

ABS_HEAD = re.compile(r"(?:^|\n)\s*(?:JEL[^A-Za-z]|A B S T R A C T|Abstract|ABSTRACT|Summary)\s*[:\-—]?\s*\n", re.I)
ABS_STOP = re.compile(r"\n\s*(?:JEL|Keywords?|Key words|1\s*\.?\s*Introduction|Introduction\s*\n|I\.?\s*Introduction)", re.I)

def readable(s):
    """质量闸：抽出的是不是人读得懂的英文。mojibake/控制字符/封面页元数据一律不收。"""
    if not s or len(s) < 200:
        return False
    letters = sum(ch.isalpha() for ch in s)
    junk = sum(ch in "\ufffd\u0000" or (ord(ch) < 32 and ch not in "\t\n") for ch in s)
    if letters / len(s) < 0.60 or junk > 2:
        return False
    if re.search(r"(?:\D\s*\?){6,}", s):          # % &  之类乱码串
        return False
    words = re.findall(r"[A-Za-z]{3,}", s)
    return len(words) >= 40 and s.count(".") >= 2

def extract(txt):
    """返回 (摘要文本, 来源标记)。优先显式 Abstract 段，其次文首连续正文。"""
    head = txt[:12000]
    m = ABS_HEAD.search(head)
    if m:
        tail = head[m.end(): m.end() + 1600]
        s = ABS_STOP.split(tail)[0]
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) >= 180 and readable(s):
            return s[:1200], "txt_abstract"
    # 不做"取文首"退化路径：抽到的是封面页/通讯作者脚注，而 abstract 会进 gate_verdict
    # 参与金融门槛判定，灌非摘要文本有把论文误剔或误留的风险。宁可留空。
    return "", ""

def main(apply):
    c = json.load(open(CAT, encoding="utf-8"))
    inlib = lambda r: r.get("status") == "done" and not r.get("offtopic")
    cand = [r for r in c if inlib(r) and not (r.get("abstract") or "").strip()
            and r.get("text_done") and r.get("txt_path")]
    print("候选（在库+无摘要+有txt）:", len(cand))
    filled, srcs = [], {"txt_abstract": 0, "txt_head": 0}
    for r in cand:
        try:
            txt = open(r["txt_path"], encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        s, src = extract(txt)
        if s:
            filled.append((r, s, src))
            srcs[src] = srcs.get(src, 0) + 1
    print("可回填:", len(filled), "来源分布:", srcs)
    for r, s, src in random.Random(7).sample(filled, min(6, len(filled))):
        print("  [%s] %s | %s\n      %s" % (src, r["key"], (r.get("title") or "")[:56], s[:170]))
    if apply:
        for r, s, src in filled:
            r["abstract"] = s
            r["abstract_src"] = src
        tmp = CAT + ".abstmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CAT)
        print("已写回 %d 条摘要" % len(filled))
    else:
        print("dry-run，未写入")

if __name__ == "__main__":
    main("--apply" in sys.argv)
