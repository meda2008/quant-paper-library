# -*- coding: utf-8 -*-
"""语义捞回：把机械门槛判成"非金融"、但其实做量化投资的新到货论文挑回来。

背景（2026-09-30 实测）：本轮扩扫到货 3016 篇解析后只有 30 篇进了库，2986 篇被 gate_verdict 判 fail
并移进 _剔除-非金融。抽检里绝大多数确实是传染病模型、队列计数、保险精算、气味分子、细胞迁移，
但里面也混着真货（如 "Statistical properties of market collective responses" 做的是订单流对市场
价格与流动性的冲击，"Special Markowitz" 做的是收益与协方差的联合正则）。机械门槛用词表判金融，
对这类"词不典型但问题域对"的论文就是会漏 —— 只有语义判据能清，这条是我们做批15 清污时定下的规矩。

所以这里不放宽词表（放宽会把真垃圾也放进来），而是把这 2986 篇交给读手按摘要逐篇判，
判 keep 的再移回分类目录。动作完全可逆：rescue_manifest.json 记录每一篇的原路径。

用法:
  python rescue_gate.py export [组数]     -> 生成 <ROOT>/rescue_q<K>.jsonl
  python rescue_gate.py apply             -> 读 rescue_res_q*.jsonl，把 keep 的移回库
  python rescue_gate.py stats             -> 只报当前待判/已判数量
"""
import json, os, re, sys, time, collections

sys.stdout.reconfigure(encoding="utf-8")
def _resolve_root():
    """与 quant_library._resolve_root 同一套优先级：PAPER_ROOT > 同目录 .paper_root > paper_store。
    这里不复用它是因为本脚本在 import quant_library 之前就要用 ROOT。"""
    v = os.environ.get("PAPER_ROOT")
    if v:
        return v
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".paper_root")
    try:
        with open(cfg, encoding="utf-8") as f:
            p = f.read().strip()
        if p:
            return p
    except OSError:
        pass
    return "paper_store"


ROOT = _resolve_root()
CAT = os.path.join(ROOT, "catalog.json")
OUT = os.path.join(ROOT, "rescue_manifest.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant_library as q          # 复用 save_catalog / classify / sanitize


def todo_keys():
    """待语义复核：已解析、没精读过、被门槛判非金融、摘要还在的。"""
    c = json.load(open(CAT, encoding="utf-8"))
    sel = [r for r in c if r.get("status") == "done" and r.get("text_done")
           and r.get("offtopic") and not r.get("ai_summary")
           and not r.get("rescue_verdict")]
    sel.sort(key=lambda r: -int(r.get("rel") or 0))     # 题摘像因子论文的先看
    return c, sel


def export(n=6):
    c, sel = todo_keys()
    if not sel:
        print("没有待复核的条目"); return
    size = int(len(sel) / n) + 1
    made = []
    for i in range(n):
        part = sel[i * size:(i + 1) * size]
        if not part:
            break
        p = os.path.join(ROOT, "rescue_q%d.jsonl" % (i + 1))
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps({"key": r["key"], "title": r.get("title", ""),
                                    "abstract": (r.get("abstract") or "")[:1400],
                                    "source": r.get("source", ""), "primary": r.get("primary", ""),
                                    "date": r.get("date", ""), "rel": r.get("rel"),
                                    "txt": r.get("txt_path", "")}, ensure_ascii=False) + "\n")
        os.replace(p + ".tmp", p)
        made.append((os.path.basename(p), len(part)))
    print("待复核 %d 篇，切成 %s" % (len(sel), made))
    print("抽检最像因子论文的 5 篇:")
    for r in sel[:5]:
        print("   [%2s] %s" % (r.get("rel"), (r.get("title") or "")[:64]))


def apply_verdicts():
    files = sorted(f for f in os.listdir(ROOT) if re.match(r"rescue_res_q\d+\.jsonl$", f))
    if not files:
        print("还没有 rescue_res_q*.jsonl 结果文件"); return
    v = {}
    bad = 0
    for f in files:
        for line in open(os.path.join(ROOT, f), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                bad += 1; continue
            k = d.get("key")
            vv = (d.get("verdict") or "").strip().lower()
            if not k or vv not in ("keep", "drop"):
                bad += 1; continue
            if vv == "keep" or k not in v:      # keep 优先，重复行以 keep 为准
                v[k] = {"verdict": vv, "why": d.get("why") or d.get("reason") or ""}
    c = q.load_catalog()
    orig = q.snapshot(c)
    idx = {r["key"]: r for r in c}
    moved, missing, drop, already = [], 0, 0, 0
    man = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else []
    known = {m["key"] for m in man}
    for k, d in v.items():
        r = idx.get(k)
        if not r:
            missing += 1; continue
        r["rescue_verdict"] = d["verdict"]
        r["rescue_why"] = d["why"][:160]
        if d["verdict"] == "drop":
            drop += 1; continue
        if not r.get("offtopic"):
            already += 1; continue
        primary = r.get("primary") or ""
        if not primary:
            primary, _t = q.classify("%s %s" % (r.get("title", ""), r.get("abstract", "")))
            r["primary"], r["tags"] = primary, {}
        folder = os.path.join(ROOT, q.sanitize(primary, 30))
        os.makedirs(folder, exist_ok=True)
        old = r.get("pdf_path") or ""
        newp = os.path.join(folder, os.path.basename(old)) if old else ""
        if old and os.path.isfile(old) and old != newp:
            try:
                if os.path.exists(newp):
                    os.remove(old)
                else:
                    os.replace(old, newp)
                r["pdf_path"] = newp
            except OSError as e:
                print("  移动失败 %s: %s" % (k, str(e)[:60]))
        elif not old or not os.path.isfile(old):
            print("  PDF 不在盘上，只改标记:", k, old[:60])
        r["offtopic"] = False
        r["gate_level"] = "rescued"
        r["rescued_at"] = time.strftime("%F %T")
        if k not in known:
            man.append({"key": k, "from": old, "to": r.get("pdf_path"),
                        "primary": primary, "at": r["rescued_at"]})
            known.add(k)
        moved.append(k)
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    os.replace(OUT + ".tmp", OUT)
    q.save_catalog(c, orig)
    print("判完: keep 移回库 %d 篇 | drop %d 篇 | 本就在库 %d 篇 | 找不到记录 %d 篇 | 坏行 %d 行"
          % (len(moved), drop, already, missing, bad))
    print("manifest 累计 %d 条 -> %s（可据此逐篇移回 _剔除）" % (len(man), OUT))
    print("下一步: python quant_library.py index 重建索引，再 python deepread.py prepare 发车精读")


def stats():
    c, sel = todo_keys()
    done_v = sum(1 for r in c if r.get("rescue_verdict"))
    print("待复核剩 %d 篇 | 已有判定 %d 篇 | keep 已移回 %d 篇"
          % (len(sel), done_v, sum(1 for r in c if r.get("gate_level") == "rescued")))
    slices = [f for f in os.listdir(ROOT) if re.match(r"rescue_q\d+\.jsonl$", f)]
    res = [f for f in os.listdir(ROOT) if re.match(r"rescue_res_q\d+\.jsonl$", f)]
    print("切片 %d 个 %s | 结果 %d 个 %s" % (len(slices), slices, len(res), res))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "export":
        export(int(sys.argv[2]) if len(sys.argv) > 2 else 6)
    elif cmd == "apply":
        apply_verdicts()
    else:
        stats()
