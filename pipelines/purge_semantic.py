# -*- coding: utf-8 -*-
"""按"AI 精读语义"清污染（收紧门槛的补充路，专治词对题不对的误召回）。
用法: python purge_semantic.py dry          -> 只出报告与抽检清单，不动文件
      python purge_semantic.py apply        -> 移动 PDF + 标 offtopic + 写可逆清单
      python purge_semantic.py apply --scope-ai-C   -> 只处理 ai_grade=C 的条目（默认）

三层判据，任一层不过就不动（防误杀）：
  L1 门槛重算：gate_verdict(标题,摘要,正文头) 不是 strong（strong 说明题摘确有强金融词）
  L2 AI 语义：ai_summary/ai_findings 里有明确的"非金融/误召回/文不对题"断言
  L3 金融豁免：同一句话里若还出现真正的金融对象（定价/波动率溢价/做市/期权/组合…），
     判为"金融但无实证"——那是 C 不是污染，留在库里，不进移动清单。
"""
import json, os, re, shutil, sys, random, collections, datetime
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant_library as q

ROOT = q._resolve_root()
REPORT = os.path.join(ROOT, "purge_semantic_report.md")
MANIFEST = os.path.join(ROOT, "purge_semantic_manifest.json")

OFF = re.compile(
    r"误召回|非金融|不属(?:于)?金融|与量化[^。；]{0,12}无关|文不对题|零金融|"
    r"金融核心词[^。；]{0,10}(?:0|仅|只有)|无[^。，；]{0,6}金融对[象象]|跨领域|"
    r"推荐系统|矩阵分解|因子分析[^。；]{0,8}(?:心理|测量|CFA)|心理测量|量表|问卷|"
    r"脑电|脑成像|核磁|影像组|临床|流行病|患者|癌|基因|蛋白|医学|"
    r"雷达|声呐|散射|超声|光谱|粒子|宇宙学|天文|凝聚态|材料|化学键|"
    r"语音|语言模型评测|机器翻译|图像|视频|视觉|"
    r"电力|能[源耗]|电网|光伏|风电|水库|水文|农业|大麦|作物|"
    r"机器人|自动驾驶|无人机|仓储|调度|交通|出行|"
    r"密码|隐私|联邦学习|入侵|恶意软件|钓鱼|数据库|编译|分布式系统|云计算|流处理|"
    r"教育公[平]|健康公[平]|公[平]分配|投票|抽签|博弈机制|"
    r"人口|犯罪|招聘|社交网络分析|网红|直播|游戏|玩家|球迷|体育", re.I)

# 汇总否决：标题或证据里出现投资/投资者/基金/证券等语境 -> 属"金融但没用"，不在本次清除范围
INVEST_VETO = re.compile(r"投资|散户|投资者|基金|证券|资本市场|养老|保险|财富|理财|投顾|"
                         r"investment|investor|mutual fund|securities|portfolio choice|"
                         # 加密/稳定币是本库正式分类（加密与新兴资产），协议经济学不算污染
                         r"加密|区块链|比特币|以太坊|稳定币|代币|DeFi|mining pool|"
                         r"crypto|blockchain|bitcoin|ethereum|stablecoin|token", re.I)

# 主判据：AI 精读里明确的"这篇不是金融"断言（比泛词表更严）
OFF_STRONG = re.compile(
    r"误召回|非金融|不属(?:于)?金融|与量化[^。；]{0,14}无关|文不对题|零金融|"
    r"无任何?金融对[象象]|金融[^。；]{0,6}(?:计数|核心词)[^。；]{0,8}(?:0|仅|为 0)|"
    r"属分类污染|分类污染|污染", re.I)

# 否决项：真正的金融对象（出现即视为"金融相关"，不移）
FIN_KEEP = re.compile(
    r"资产定价|定价因子|因子溢[价]|风险溢价|波动率溢[价]|横截面收益|预期收益|"
    r"选股|因子模型|多因子|Fama|French|Carhart|q-?factor|"
    r"组合优化|有效前沿|再平衡|夏普|Sharpe|回测|样本外收益|"
    r"做市|订单簿|买卖价差|流动性|成交|高频|"
    r"期权定价|隐含波动|期货|展期|套利|配对交易|"
    r"股票收益|股价预测|信用利差|债券|汇率|利率曲线|"
    r"A股|沪深|上证|深证|涨停|龙虎榜|北向|可转债", re.I)

# 只在这些批次的语义证据上做（默认全库，但按 read_at 分层报告）
def sem_ok(r):
    blob = (r.get("ai_summary") or "") + " ｜ " + " ；".join(r.get("ai_findings") or [])
    return blob.strip() != "｜" and OFF.search(blob) and not FIN_KEEP.search(blob)

def main(apply, only_c=True, scope_date=None):
    recs = q.load_catalog()
    orig = q.snapshot(recs)
    active = [r for r in recs if r.get("status") == "done" and not r.get("offtopic")]
    pool = active
    if scope_date:                     # 只处理指定精读日的批次，先小范围验证判据
        active = [r for r in active if (r.get("ai_read_at") or "").startswith(scope_date)]
        print("限定批次 ai_read_at=%s：在库 %d / 全库在库 %d" % (scope_date, len(active), len(pool)))
    cands, skip_strong, skip_finkeep, skip_nosem = [], [], [], []
    for r in active:
        if only_c and r.get("ai_grade") not in ("C", "D", None, ""):
            continue                                    # A/B 一律不动
        head = ""
        try:
            head = open(r.get("txt_path", ""), encoding="utf-8", errors="ignore").read()[:4000]
        except Exception:
            head = ""
        v = q.gate_verdict(r.get("title"), r.get("abstract"), head)
        r["gate_level_rec"] = v
        blob = (r.get("ai_summary") or "") + " " + " ".join(r.get("ai_findings") or [])
        # 主判据 = AI 自己的非金融断言（词碰撞污染在标题+摘要层面查不出来，门槛只作记录）
        if not OFF_STRONG.search(blob):
            skip_nosem.append(r); continue
        if FIN_KEEP.search(blob) or INVEST_VETO.search(blob) or INVEST_VETO.search(r.get("title") or ""):
            skip_finkeep.append(r); continue          # 提到真金融对象/投资语境 -> 留下（金融但无实证不是污染）
        if not OFF.search(blob):
            skip_nosem.append(r); continue
        cands.append(r)

    print("在库 %d | 拟移出 %d | 含真金融对象豁免 %d | 无明确非金融断言 %d"
          % (len(active), len(cands), len(skip_finkeep), len(skip_nosem)))
    print("拟移出的门槛记录:", dict(collections.Counter(r.get("gate_level_rec") for r in cands)))
    print("拟移出按分类:", dict(collections.Counter(r.get("primary") for r in cands)))
    print("拟移出按评级:", dict(collections.Counter(r.get("ai_grade") for r in cands)))
    print("拟移出按精读日:", dict(collections.Counter((r.get("ai_read_at") or "-")[:10] for r in cands)))

    lines = ["# 语义清污报告（purge_semantic）",
             "",
             f"- 在库候选 {len(active)}，拟移出 **{len(cands)}**",
             f"- 三层豁免：门槛 strong {len(skip_strong)} ／ 含真金融对象 {len(skip_finkeep)} ／ 无非金融断言 {len(skip_nosem)}",
             f"- 分类分布：{dict(collections.Counter(r.get('primary') for r in cands))}",
             "", "## 随机抽检 25 条", ""]
    for r in random.Random(20260930).sample(cands, min(25, len(cands))):
        lines.append(f"### {r['key']} ｜ [{r.get('primary')}] ｜ ai={r.get('ai_grade')}")
        lines.append(f"- 标题：{(r.get('title') or '')[:150]}")
        lines.append(f"- AI 摘要：{r.get('ai_summary','')}")
        for f in (r.get("ai_findings") or []):
            lines.append(f"  - {f}")
        lines.append("")
    lines.append("## 全量清单")
    for r in cands:
        lines.append(f"- {r['key']} ｜ [{r.get('primary')}] ｜ {(r.get('title') or '')[:100]}")
    open(REPORT, "w", encoding="utf-8").write("\n".join(lines))
    print("报告已写:", REPORT)

    if not apply:
        print("dry-run，未移动任何文件")
        return
    folder = os.path.join(q.ROOT, "_剔除-非金融")
    os.makedirs(folder, exist_ok=True)
    manifest = {"created": datetime.datetime.now().isoformat(timespec="seconds"), "moves": []}
    if os.path.exists(MANIFEST):                       # 追加历史，保证可逆记录不丢
        manifest = json.load(open(MANIFEST, encoding="utf-8"))
    moved = 0
    for r in cands:
        src = r.get("pdf_path") or ""
        if src and os.path.isfile(src):
            dst = os.path.join(folder, os.path.basename(src))
            try:
                shutil.move(src, dst)
                manifest["moves"].append({"key": r["key"], "from": src, "to": dst,
                                          "was_offtopic": False})
                r["pdf_path"] = dst
                moved += 1
            except OSError as e:
                print("move fail:", src, str(e)[:80])
                continue
        r["offtopic"] = True
        r["offtopic_reason"] = "semantic_purge_20260930"
    q.save_catalog(recs, orig)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)
    print(f"已移动 {moved} 个 PDF；可逆清单 {MANIFEST}（按 from/to 逐条可还原）")

if __name__ == "__main__":
    sd = None
    for a in sys.argv:
        if a.startswith("--scope-date="):
            sd = a.split("=", 1)[1]
    main(apply=("apply" in sys.argv), only_c="--all-grades" not in sys.argv, scope_date=sd)
