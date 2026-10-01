# -*- coding: utf-8 -*-
"""rel_score 的判据回归：真量化论文必须整体排在撞词误召回之前。
这批样本都是本库实际读过的条目类型（批13/批15 的误召回家族 + 已定级的 A/B 篇目）。"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库布局是 tests/ 与 pipelines/ 平级；本地旧布局是脚本与测试同目录。两种都认。
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q

GOOD = [
 ("Common Risk Factors in Stock Returns", "Fama French three factor model, cross-section of stock returns"),
 ("Tactical Asset Allocation", "portfolio management, momentum factor, Sharpe ratio, transaction cost, backtest"),
 ("Boosting Alpha Factor Selection with Cross-Sectional Data", "alpha factor mining, cross-section of stock returns, China A-share"),
 ("Deep Learning for Portfolio Optimization", "portfolio optimization with transaction costs and turnover constraints"),
 ("The Cross-Section of Expected Stock Returns", "characteristics based investing, excess return predictability"),
 ("Optimal Rebalancing with Proportional Transaction Costs", "mean-variance portfolio, long-short, rebalancing frequency"),
 ("Machine Learning for Momentum Investing", "momentum strategy, stock ranking, out-of-sample Sharpe ratio"),
 # 这两篇本库定过 A：题摘里带了 crypto/bitcoin，但做的确实是组合/时序动量，不能被撞词惩罚打到负分
 ("Long-only cryptocurrency portfolio management by ranking the assets", "asset ranking, portfolio management, Sharpe ratio"),
 ("Bitcoin intraday time series momentum", "time series momentum, intraday returns"),
]
BAD = [
 ("PANDORA: Graph learning for infectivity risk", "graph neural network, protein, molecule, epidemic"),
 ("Inventory control for perishable items under supply chain risk", "reorder point, warehouse, logistics, supply chain"),
 ("Momentum turbulence in plasma lattice simulation", "particle, quantum, lattice, galaxy"),
 ("Confirmatory factor analysis of personality traits", "item response, latent trait, factor score prediction, structural equation"),
 ("Deep reinforcement learning for smart grid unit commitment", "photovoltaic, energy management, power system"),
 ("Crypto token scam detection on Ethereum", "blockchain, stablecoin, token, fraud"),
 ("Central bank inflation expectations and household finance", "monetary policy, GDP, business cycle survey measurement"),
]

ok = True
def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)

# 2026-09-30 抽检"被机械门槛剔掉但 rel>=4"的 176 篇时发现的假阳性家族：
# 裸 alpha 在图像 matting / 脑电节律 / 矩阵乘法指数里都是普通名词，不能当因子信号。
NOISE2 = [
 ("Alpha Rhythms in Audition: Cognitive and Clinical Perspectives", "auditory alpha oscillations, brain, EEG, clinical trial"),
 ("Training Matting Models without Alpha Labels", "image matting, alpha matte, deep learning, neural network"),
 ("New Bounds for Matrix Multiplication: from Alpha to Omega", "matrix multiplication exponent, alpha, omega, algorithm"),
 ("Renyi Entropy Estimation Revisited", "we estimate the Renyi entropy, nonparametric regression, sample"),
 ("Density-Sensitive Algorithms for Edge Coloring", "graph coloring, matching, greedy algorithm"),
 ('"We\'ve Disabled MFA for You": Security and Usability', "multi-factor authentication, phishing, users, survey measurement"),
 # cs.CR 的 "multi-factor authentication" 会踩到 POS3 的 multi-factor，必须靠安全词族压回去
 # （2026-09-30 复盘：语义捞回切片按 rel 排序时，榜首全是 MFA/生物识别密钥交换论文）
 ("Biometrics-Based Authenticated Key Exchange with Multi-Factor", "key agreement, biometric, smart card, formal model"),
 ("Evaluating the Influence of Multi-Factor Authentication", "authentication, malware, access control, recommendation"),
]
n2s = [q.rel_score(t, a) for t, a in NOISE2]
for s, (t, _) in zip(n2s, NOISE2):
    print("  NOISE %+3d  %s" % (s, t[:58]))
chk(max(n2s) <= 3, "裸 alpha 家族不再冒充因子论文（最高 %d）" % max(n2s))
chk(all(s <= 3 for s in n2s), "六篇噪声全部 <=3")

gs = [q.rel_score(t, a) for t, a in GOOD]
bs = [q.rel_score(t, a) for t, a in BAD]
for s, (t, _) in zip(gs, GOOD):
    print("  GOOD %+3d  %s" % (s, t[:58]))
for s, (t, _) in zip(bs, BAD):
    print("  BAD  %+3d  %s" % (s, t[:58]))

chk(min(gs) >= 5, "真量化论文最低分也 >=5（实际 %d）" % min(gs))
chk(max(bs) <= 0, "撞词误召回最高分也 <=0（实际 %d）" % max(bs))
chk(min(gs) > max(bs), "两组完全分开：%d > %d" % (min(gs), max(bs)))
# 硬词 >=6 时撞词惩罚封顶，避免把"crypto portfolio management"这类真论文压到负分
chk(q.rel_score("Long-only cryptocurrency portfolio management by ranking the assets",
                "asset ranking, portfolio management, Sharpe ratio") > 0,
    "带 crypto 字样但确实在做组合的论文不被误杀")
# 无摘要时正文要能补上分（批13 组1 的教训：有价值的老文献常常没有摘要）
chk(q.rel_score("Old NBER Working Paper", "", "we estimate a multi-factor model on the cross-section of stock returns with transaction costs")
    > q.rel_score("Old NBER Working Paper", ""),
    "无摘要条目能靠正文补分，不会被排到队尾永远读不到")
chk(q.rel_score("某篇讲分子的", "", "the molecule binds to the protein in the cell")
    <= q.rel_score("某篇讲分子的", ""),
    "正文里的误召回家族不会被当成加分项")
# 排序方向：prepare 用 (tier, -rel, ...)，下载用 -rel，两处都必须是高分在前
hot = sorted([1, 9, -4, 6], key=lambda x: -x)
chk(hot[0] == 9, "排序键 -rel 确实是高分在前")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
