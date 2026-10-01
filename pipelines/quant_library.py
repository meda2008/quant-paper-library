# -*- coding: utf-8 -*-
"""量化因子论文自动建库流水线
用法:  python quant_library.py catalog|harvest|index|status
数据根目录: 环境变量 PAPER_ROOT（默认 ./paper_store），下面是它的结构：
  <ROOT>/_inbox/            下载暂存
  <ROOT>/text/              解析全文
  <ROOT>/<分类>/            分类后的 PDF
  <ROOT>/catalog.json       候选+状态清单
  <ROOT>/INDEX.md           中文总索引（按分类）
  <ROOT>/catalog.csv        全字段表（标题/作者/时间/来源/本地路径/分类/标签/简介/总结/DOI/引用数...）
"""
import csv, json, os, re, sys, time, subprocess, urllib.parse, urllib.request
import shutil, glob
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
import fitz

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.environ.get("PAPER_ROOT", "paper_store")
INBOX = os.path.join(ROOT, "_inbox")
TXT = os.path.join(ROOT, "text")
CATLOG = os.path.join(ROOT, "catalog.json")
UA = {"User-Agent": "Mozilla/5.0 (quant paper library builder)"}
ATOM = "{http://www.w3.org/2005/Atom}"
# arXiv API 的分页总数在 OpenSearch 命名空间里，不在 Atom 里
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
# 单条查询最多只能翻到 2000 条（start+max_results 上限），超出的部分 arXiv 不报错、直接不给。
# 撞没撞上限只能靠 totalResults 判断，撞了就记在这里，类目分片扫据此把窗口切细再扫。
CAP = 2000
CAP_HITS = []
SWEEP_PLAN = os.path.join(ROOT, "_sweep_plan.json")
CAP_LOG = os.path.join(ROOT, "_cap_hits.txt")
MAILTO = "quant-library@local.dev"

# ---------- 分类体系 ----------
CATEGORIES = [
    ("机器学习选股", [r"machine learning", r"deep learning", r"neural network", r"\blstm\b", r"transformer",
        r"xgboost", r"gradient boosting", r"lightgbm", r"reinforcement learning", r"attention", r"\bgan\b"]),
    ("中国A股", [r"\bchina\b", r"chinese", r"a-share", r"ashare", r"a股", r"shanghai", r"shenzhen", r"csi\b", r"chinese stock"]),
    ("动量与反转", [r"momentum", r"reversal", r"trend[- ]following", r"time-series trend", r"price trend", r"52-week high"]),
    ("价值与基本面因子", [r"\bvalue factor", r"book-to-market", r"earnings yield", r"dividend",
        r"fundamental (analysis|screening|signal|valuation)", r"free cash flow", r"\bb/m\b"]),
    ("质量与盈利因子", [r"quality factor", r"profitability", r"accrual", r"earnings (quality|surprise|management)", r"gross profit", r"\bq factor\b"]),
    ("低波动与风险因子", [r"low[- ]vol", r"betting against beta", r"\babb\b", r"downside risk", r"tail risk",
        r"value at risk", r"expected shortfall", r"maximum drawdown", r"risk parity"]),
    ("多因子模型与检验", [r"factor model", r"fama[- ]french", r"factor zoo", r"cross-section of (expected )?stock returns",
        r"new factor", r"factor (test|selection|evaluation|pricing|library|model)", r"alphas?\b", r"mispricing", r"spanning", r"five-factor", r"six-factor", r"\bq\^?5\b"]),
    ("组合优化与配置", [r"portfolio (optimi|selection|construction|allocation|rebalanc)", r"mean-variance", r"risk-based allocation", r"\b1/n\b"]),
    ("市场微观结构与交易", [r"microstructure", r"limit[- ]order book", r"market making", r"execution", r"transaction cost",
        r"high[- ]frequency", r"liquidity", r"order flow", r"algo(?:rithmic)? trading"]),
    ("另类数据与文本挖掘", [r"text mining", r"news sentiment", r"\bnlp\b", r"alternative data", r"earnings call",
        r"social media", r"\bllm\b", r"large language model", r"chatgpt", r"finbert", r"twitter", r"analyst report"]),
    ("宏观与跨资产因子", [r"macroeconomic (factor|condition|risk|variable)", r"business cycle", r"\bcarry\b", r"cross-asset",
        r"commodit", r"yield curve", r"term spread", r"exchange rate", r"asset allocation across", r"factor lens"]),
    ("行为金融与情绪", [r"behavioral", r"investor sentiment", r"sentiment", r"overconfidence", r"herding", r"disposition effect", r"attention"]),
    ("加密与新兴资产", [r"crypto", r"bitcoin", r"defi\b", r"tokeni[sz]", r"nft"]),
    ("因子择时与风控", [r"factor timing", r"regime", r"conditional factor", r"factor (crash|decay|crowding)", r"drawdown control"]),
]
FALLBACK = "综合与评论"

# 金融相关性门槛（剔除 arXiv 里 "factor" 撞词的跨领域论文）——仅作正文兜底判据
FIN_GATE = re.compile(r"financ|stock|equit|asset pric|portfolio|trading|trade|return[sd]? (data|series|cross)|"
    r"bond|market (index|microstructure|place)|sharpe|alpha\b|beta\b|dividend|invest(?:or|ment)|"
    r"fama|quantile of return|exchange|crypto|hedge|volatil|credit|risk[- ]prem|cfa|vix|etf|"
    r"factor (model|invest|zoo|premium)|expected return|anomal", re.I)

# 2026-09-27 收紧：主判据=标题+摘要必须命中强金融词。泛词 factor/quantitative/trading/beta 单独出现不放行。
STRONG_GATE = re.compile(
    r"stock|equit(?:y|ies)|shareholder|asset pric|portfolio|dividend|earning|valuation"
    r"|fama|capm|factor (?:model|invest|premium|zoo|timing|analy|librar|lens|score)"
    r"|cross[- ]section(?:al)? (?:of |return|stock|alpha|predict|model|test)|risk premium|expected return"
    r"|(?:stock|asset|market|excess|abnormal|risk-adjusted|portfolio|total|calendar-time|out-of-sample) return"
    r"|return (?:predict|forecast|dispersion|skew|decomposition)"
    r"|market (?:capitaliz|index|abnormal|timing|equilibrium)|order book|microstructure|limit[- ]order"
    r"|(?:implied|realized|stochastic|forex) volatil|volatil(?:ity)? (?:surface|risk|forecast|trading|model|premium)"
    r"|\bmomentum\b[^.]{0,80}(?:stock|asset|market|portfolio|cross-section|price|return|trend|fx|commodit|crypto|equity|invest)"
    r"|(?:stock|asset|market|time-series|currency|commodity|bond) momentum"
    r"|price (?:formation|impact|limit|forecast|predict)|option (?:pricing|market|hedge)|futures (?:market|contract)"
    r"|carry (?:trade|return)|hedge fund|mutual fund|index(?:ing| fund)|\betf\b|bond|credit (?:risk|spread|rating|score|market|default)"
    r"|(?:financial|stock|equity|trading|investment) (?:market|institution|time series|metric|data|model|strategy|decision|agent)"
    r"|invest(?:or|ment|ing)\b|sharpe|mean-variance|drawdown|factor lens|index track"
    r"|(?:earnings|analyst|book-to-market|p/e|eps)|trading (?:strategy|volume|rule|cost|agent|market)"
    r"|(?:chinese|u\.s\.|global|emerging) (?:stock|market|equity)|csi(?:\s?500|\s?300)|sse 50|szse"
    r"|quantitative finance|systematic (?:invest|trad)|anomal(?:y|ies) (?:in|of|and) (?:stock|asset|market|return)"
    # 2026-09-27 二修：补风险度量/利率/加密/回测/保险/资产类别词族（防误杀真金融论文）
    r"|expected shortfall|tail risk|systemic (?:risk|stress)|market risk|coherent risk|risk (?:model|measure|factor|exposure|management)"
    r"|\bVaR\b|\bCVaR\b|libor|sofr|sarb|interest rate|term structure|yield (?:spread|curve)|central bank|monetary policy|inflation"
    r"|cryptocurrenc|blockchain|bitcoin|stablecoin|\bdefi\b"
    r"|insurance|reinsur|actuar|pension"
    r"|(?:housing|house) (?:price|market)|real estate"
    r"|(?:exchange rate|forex)|commodit|gold price"
    r"|sentiment|economic news|news (?:impact|attention)"
    r"|backtest|mispricing|market (?:manipulation|efficiency|failure)|bubble|herd|flash crash"
    r"|econometric|panel (?:data|regression)|factor (?:loading|exposure)"
    r"|betting (?:market|odds)|sports(?:book|betting)|bookmaker"
    r"|(?:moving average|technical (?:analysis|indicator|rule)|candlestick|chart (?:pattern|signal))"
    r"|default (?:risk|model|probability|time|swap)|credit (?:portfolio|derivative)"
    r"|rebalanc(?:e|ing)|stock (?:picker| screener|universe)|alpha (?:decay|engine|mining|generation|capture)"
    # 2026-09-27 三修：预测市场/金融因子选择/基金复制类
    r"|prediction market|financial factor|factor (?:selection|screening|replication)|fund replication"
    r"|(?:toxic|distressed) asset|contagion|systemically important|mortality|longevity"
    # 2026-09-27 四修：公司金融/ESG/特质风险词族（防误杀 CSR-特质风险类论文）
    r"|idiosyncrat|determinants of (?:stock )?return|corporate (?:governance|social responsibility|finance|risk)"
    r"|\besg\b|firm (?:level|size|value|risk)|board (?:of directors|composition)|analyst (?:coverage|forecast|recommendation)",
    re.I)
BODY_FIN_WORDS = re.compile(r"stock|equit|asset pricing|portfolio|return|dividend|earning|volatil|trading|"
    r"invest(?:or|ment)|market capitaliz|bond|option|hedge|sharpe|factor model|cross-section", re.I)

def gate_verdict(title, abstract, body_head):
    """三档判定：strong=标题摘要命中强门槛；body=正文>=6个金融词兜底（边缘保留）；fail=剔除"""
    ta = (title or "") + " " + (abstract or "")
    if STRONG_GATE.search(ta):
        return "strong"
    if len(BODY_FIN_WORDS.findall((body_head or "").lower())) >= 6:
        return "body"
    return "fail"

# ---------- 因子相关度打分（与门槛分开）----------
# 门槛 STRONG_GATE 故意放得很宽（宁可留红不造假绿，怕误杀真金融论文），
# 于是保险精算/央行货币政策/加密/电网调度都能过 strong。批15 实测：按这种"过了门槛"的顺序读，
# 七组里约 92% 的力气花在误召回上。这里另建一个分数只回答一个问题：
# 这篇是不是"能拿来做选股/因子/组合"的。分数只用于排队和下载优先级，不删任何东西。
REL_POS3 = re.compile(
    r"cross[- ]section(?:al)? (?:of )?(?:expected )?(?:stock )?return|stock (?:selection|ranking|screening)"
    r"|(?:fama(?:-french)?|q[- ]?factor|five[- ]?factor)\b|multi[- ]?factor (?:model|asset|pricing|return|stock|equity|portfolio|invest)|"
    r"factor (?:model|investing|zoo|timing|librar|lens|score|exposure|loading)"
    r"|(?:generat|captur|produc|discover|min)[a-z]*(?:ing)?\s+(?:of\s+)?alphas?\b|"
    r"alphas\b|alpha (?:factor|signal|score|premium|decay|capture|generation|mining|miner)|"
    r"alpha[^.]{0,22}(?:return|stock|portfolio|sharpe|cross[- ]section|predic)|(?:return|stock|portfolio)[^.]{0,22}alpha\b|"
    r"sharpe|mean[- ]variance|long[- ]short|characteristics[- ]based invest|anomaly detection in stock"
    r"|(?:stock|equity|cash[- ]?flow|dividend|earnings|book[- ]to[- ]market|accrual|profitability|investment|asset growth) (?:factor|anomal|premium|signal|alpha)"
    r"|momentum (?:factor|strategy|anomal|and value|portfol)|price momentum in (?:the )?(?:stock|equity|cross)"
    r"|(?:time[- ]series|cross[- ]sectional|industry|time series) momentum|momentum (?:and|with) (?:value|reversal|liquidity)",
    re.I)
REL_POS2 = re.compile(
    r"portfolio (?:optimi|construct|selection|allocat|rebalanc|choice|management|design|rule|weight)"
    r"|opti(?:mi[sz]|misation) .*portfolio|asset allocation|\bintraday\b"
    r"|backtest|trading strategy|asset pric(?:ing|e)|equity return|excess return|abnormal return|return predict"
    r"|(?:stock|equity) (?:price )?(?:predic|forecast)|price forecast|market (?:timing|efficien)|mispricing"
    r"|(?:transaction|trading) cost|turnover|order book|market microstructure|limit order|execution"
    r"|(?:chinese|a-share|csi\s?300|csi\s?500|sse|szse|ashare) (?:stock|market|equity|factor)",
    re.I)
# POS1 只留"金融名词"。以前这里还收了 machine learning / deep learning / neural network /
# regression / forecast 这类通用方法词，结果图像 matting、矩阵乘法、图着色的论文靠正文里的
# "alpha + deep learning + regression" 一路加到 +24，把下载和精读队列的顺序都带偏了
# （2026-09-30 抽检 176 篇"被门槛剔但 rel>=4"时发现的全是这类）。
REL_POS1 = re.compile(
    r"\bstock(?:s|holder)?\b|\bequit(?:y|ies)\b|\bfund\b|\bETF\b|index fund|market index|bond|option|futures"
    r"|\bfirm size\b|market capitaliz|\banalyst\b|\bbroker\b|\bdividend\b|\bearnings\b|\breturn\b",
    re.I)
# 撞词大户：这些是批13-15 和批15 扩容里反复出现的具体误召回家族
REL_NEG2 = re.compile(
    r"smart grid|unit commitment|power system|photovoltaic|wind (?:farm|power)|energy management|"
    r"inventory|supply chain|reorder|perishable|warehous|logistic|"
    r"quantum|particle|lattice|turbulence|galax|cosmolog|plasma|"
    r"molecul|protein|gene|genomic|\bcell\b|clinical|drug|disease|epidemi|neuron decoding|brain|"
    r"item response|personality|latent trait|confirmatory factor analysis|structural equation|"
    r"factor score|score predictor|psychometr|construct validity|survey measurement|attitude scale|"
    r"energy[- ]efficient|scheduling problem|travelling salesman|frechet|fréchet distance|graph embedding survey|"
    r"image segmentation|speech|sentiment analysis of (?:movie|tweet\b)|object detection|"
    r"image matting|alpha matte|matting model|splatting|\bEEG\b|electroencephalo|auditory|attention[- ]deficit|"
    # 2026-09-30 复盘：arXiv 的 cs.CR 用 "multi-factor authentication / MFA / biometric key exchange"
    # 一路顶到 rel 榜首（POS3 里的 "multi-factor" 完全是另一个意思），安全类必须算撞词家族
    r"multi[- ]factor auth|authentication|\bMFA\b|biometric|key exchange|key agreement|cryptosystem|"
    r"malware|phishing|intrusion detection|access control|zero[- ]day|vulnerabilit|protocol analysis|"
    r"edge coloring|graph coloring|matrix multiplication|cluster(ing)? algorithm|"
    r"sports? betting|bookmaker|odds (?:compiler|market)|"
    r"cryptocurrenc|blockchain|bitcoin|stablecoin|\bdefi\b|token",
    re.I)
REL_NEG1 = re.compile(
    r"central bank|monetary policy|inflation|GDP|economic growth|business cycle|household (?:survey|finance)|"
    r"reinsur|actuar|solvency|insurance premium|pension fund|life cycle invest",
    re.I)

def _rel_parts(text):
    hard = 3 * len(REL_POS3.findall(text)) + 2 * len(REL_POS2.findall(text))
    return hard + len(REL_POS1.findall(text)), hard, 2 * len(REL_NEG2.findall(text)) + len(REL_NEG1.findall(text))

def _score_one(text, no_cap=False):
    """no_cap=True 时不给撞词惩罚封顶：标题自己就落在误召回家族上的（MFA/生物识别这类）要用这条，
    否则光靠 "multi-factor" 一个字面就能把安全类论文抬到榜首。"""
    pos, hard, neg = _rel_parts(text)
    return pos - (min(neg, 3) if (hard >= 6 and not no_cap) else neg)

def rel_score(title, abstract, body_head=""):
    """因子相关度打分：+3 因子/截面选股硬词，+2 组合与实证口径词，+1 泛金融/泛ML词，
    -2 已知误召回家族（电网/库存/物理/生物/心理测量/加密/赌盘/多因子认证），-1 宏观与保险精算。
    两条防误伤的补丁都是拿本库真读过的条目反查出来的：
    1) 硬词分 >=6 时撞词惩罚封顶 -3，免得"cryptocurrency portfolio management""Quantum Kernels and the
       Cross-Section of Stock Returns"这类确实在做截面/组合的 A 档论文被打成负分（376 篇 A 校准）；
    2) 但标题自己就撞在误召回家族上时不封顶 —— 标题是作者给的第一信号，"Multi-Factor Authentication"
       的 multi-factor 与因子无关，这条不加就会让安全类论文霸占下载与捞回队列的榜首。
    没有摘要的经典文献（NBER/期刊版）用正文开头一起算取高分（打六折），否则会被排到队尾永远读不到 ——
    批13 组1 里恰恰是这批无摘要老文献贡献了最有价值的结论。"""
    t = title or ""
    s = _score_one("%s %s" % (t, abstract or ""), no_cap=bool(REL_NEG2.search(t)))
    if not (abstract or "").strip() and body_head:
        s = max(s, int(round(_score_one("%s %s" % (t, (body_head or "")[:1200]),
                                        no_cap=bool(REL_NEG2.search(t))) * 0.6)))
    return s

# 因子相关性关键词（用于 arXiv/OpenAlex 候选过滤）
RELEVANCE = re.compile(
    r"factor|alpha|stock (selection|return|rank|pred)|cross.section|asset pric|momentum|"
    r"portfolio (construct|optimi|selection|allocat)|sharpe|backtest|equity (return|pred)|"
    r"multi[- ]?classif|excess return|sort(ing)? portfolio|value stock|growth stock|"
    r"expected return|risk premium|idiosyncrat|anomal", re.I)

def sanitize(s, n=70):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:n].rstrip() or "untitled")

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

def _curl(url, timeout):
    """curl 兜底。原来只回 `curl rc=22`，把真正的 HTTP 状态码吞掉了 —— 同一个 rc=22 既可能是
    OpenReview 的验证墙(403)、也可能是 OpenAlex 的限流(429)，还可能是 404 死链，
    三种情况的处置完全相反（giveup / 退让重试 / 换链接），归因时只能靠猜。
    现在把状态码带进异常消息；判定行为与 --fail 一致（非 2xx 一律失败）。
    临时文件按 pid 命名，免得 harvest / catalog / index 并发时互相踩。"""
    tmp = os.path.join(os.environ.get("TEMP", "."), "_curl_%d.bin" % os.getpid())
    r = subprocess.run(["curl.exe", "-sL", "-A", BROWSER_UA, "-o", tmp, "-w", "%{http_code}",
                        "--max-time", str(timeout), url], capture_output=True, text=True)
    code = (r.stdout or "").strip() or "?"
    body = b""
    try:
        if os.path.exists(tmp):
            with open(tmp, "rb") as f:
                body = f.read()
    except OSError:
        body = b""
    if r.returncode == 0 and code[:2] == "20" and body:
        return body
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    raise RuntimeError("curl rc=%s http=%s" % (r.returncode, code))

def http_get(url, timeout=90):
    """先 urllib；被按 TLS 指纹拦(403/406/429/503)或 SSL 层直接断链时改用 curl.exe 兜底"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 406, 408, 429, 500, 502, 503):  # arXiv 按 Python TLS 指纹限流 → curl 兜底
            return _curl(url, timeout)
        raise
    except (urllib.error.URLError, OSError) as e:  # SSL UNEXPECTED_EOF / 证书 / 连接重置 → curl 兜底
        return _curl(url, timeout)

# ---------- 目录阶段 ----------
def arxiv_query(search_query, cap=2000, tag=""):
    out, start = [], 0
    total = 0
    while start < cap:
        url = ("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
            {"search_query": search_query, "start": start, "max_results": 200}))
        for attempt in range(3):
            try:
                data = http_get(url); break
            except Exception as e:
                print("  retry arxiv page:", e); time.sleep(8); data = None
        if data is None:
            break
        root = ET.fromstring(data)
        # totalResults 在 OpenSearch 命名空间里，不在 Atom 里。以前写成 ATOM+"opensearch:..."
        # 解析出来永远是 0，于是"这一档查询到底被 2000 条封顶截断了没有"根本看不出来 —— 漏了多少无从判断。
        try:
            total = int(root.findtext(OPENSEARCH + "totalResults", "0") or 0) or total
        except Exception:
            pass
        entries = root.findall(ATOM + "entry")
        if not entries:
            return out
        for e in entries:
            m = re.search(r"abs/(.+)$", e.findtext(ATOM + "id", ""))
            if not m or "replace" in m.group(1):
                continue
            arxid = m.group(1)
            title = re.sub(r"\s+", " ", e.findtext(ATOM + "title", "") or "").strip()
            abstract = re.sub(r"\s+", " ", e.findtext(ATOM + "summary", "") or "").strip()
            if not RELEVANCE.search(title + " " + abstract):
                continue
            out.append({"key": f"arxiv:{arxid}", "source": "arXiv", "arxiv_id": arxid,
                "pdf_url": f"https://export.arxiv.org/pdf/{arxid}",
                "page": f"https://arxiv.org/abs/{arxid}",
                "title": title,
                "authors": [a.findtext(ATOM + "name", "").strip() for a in e.findall(ATOM + "author")],
                "date": (e.findtext(ATOM + "published", "") or "")[:10],
                "abstract": abstract,
                "cats": [c.get("term") for c in e.findall(ATOM + "category")], "doi": "", "cited": ""})
        start += 200
        time.sleep(3)
    if total >= cap and start >= cap:          # arXiv 那边还有货，是我们自己封了页
        CAP_HITS.append({"tag": tag, "query": search_query, "total": total, "returned": len(out)})
        print(f"  [截断] {tag or search_query[:70]} 命中 {total} 条，本次只取 {len(out)}（cap={cap}）", flush=True)
        _log_cap_hit(tag, search_query, total, len(out))
    return out

def collect_arxiv():
    queries = [
        '(cat:q-fin.PM OR cat:q-fin.ST OR cat:q-fin.CP OR cat:q-fin.TR OR cat:q-fin.EC OR cat:q-fin.MF OR cat:q-fin.RM OR cat:stat.AP OR cat:cs.CE)',
        '(ti:"factor" OR ti:"stock selection" OR ti:"stock ranking" OR ti:"alpha" OR ti:"asset pricing" OR ti:"momentum" OR ti:"portfolio construction") AND (cat:q-fin.* OR cat:stat.AP OR cat:cs.LG OR cat:pc.conm-ph)',
        '(abs:"cross-section of stock returns" OR abs:"multi-factor" OR abs:"factor model" OR abs:"stock selection") AND (cat:q-fin.* OR cat:stat.* OR cat:cs.* OR cat:eess.*)',
        # 2026-09-30 第二批扩容：因子挖掘 / 执行与再平衡 / 风险溢价 / A股机制 / 过拟合检测
        '(abs:"alpha factor mining" OR abs:"factor mining" OR (abs:"genetic programming" AND abs:"alpha") OR (abs:"symbolic regression" AND abs:"return")) AND (cat:q-fin.* OR cat:cs.LG OR cat:cs.AI)',
        '(abs:"transaction cost" AND abs:"rebalancing" OR abs:"optimal execution" AND abs:"portfolio" OR abs:"turnover" AND abs:"portfolio optimization") AND (cat:q-fin.PM OR cat:q-fin.TR OR cat:q-fin.CP OR cat:math.OC)',
        '(abs:"variance premium" OR abs:"volatility risk premium" OR (abs:"tail risk" AND abs:"asset pricing") OR (abs:"downside risk" AND abs:"cross-section")) AND (cat:q-fin.* OR cat:econ.GN)',
        '(abs:"Chinese stock market" AND (abs:"anomaly" OR abs:"factor" OR abs:"return")) OR (abs:"A-share" AND abs:"factor")',
        '(abs:"backtest overfitting" OR abs:"data snooping" OR abs:"deflated Sharpe" OR (abs:"reality check" AND abs:"Sharpe")) AND (cat:q-fin.* OR cat:stat.ML)',
        '(abs:"order flow imbalance" OR (abs:"limit order book" AND abs:"deep learning") OR (abs:"market making" AND abs:"reinforcement learning")) AND (cat:q-fin.TR OR cat:cs.LG)',
        # 2026-09-27 扩容：机器学习选股/异象/截面预测在 cs 大类的投影
        '(abs:"stock selection" OR abs:"stock ranking" OR abs:"factor investing" OR abs:"alpha factor" OR abs:"quantitative trading" OR abs:"financial factor") AND (cat:cs.LG OR cat:cs.AI OR cat:cs.CL OR cat:cs.NE OR cat:stat.ML)',
        '(abs:"anomaly" AND abs:"stock market") OR (abs:"exchange rate predict" AND cat:q-fin.*) OR (abs:"commodity futures" AND abs:"factor") OR (abs:"return predictability" AND (cat:q-fin.* OR cat:econ.*))',
        '(cat:econ.GN OR cat:math.OC) AND (abs:"portfolio" AND (abs:"factor" OR abs:"stock"))',
        # 2026-09-27 第二轮扩容（免费窗口捞书）
        '(abs:"smart beta" OR abs:"factor tilting" OR abs:"index tracking") AND (cat:q-fin.* OR cat:stat.* OR cat:cs.*)',
        '(abs:"reinforcement learning" AND (abs:"portfolio" OR abs:"trading strategy" OR abs:"asset allocation"))',
        '(ti:"stock" OR ti:"equity" OR ti:"portfolio" OR ti:"factor") AND (cat:stat.ML OR cat:cs.LG OR cat:cs.AI)',
        '(abs:"event study" AND abs:"stock price") OR (abs:"earnings announcement" AND abs:"predict")',
        '(abs:"chinese stock market" OR abs:"a-share") AND (abs:"factor" OR abs:"return" OR abs:"anomal")',
        'cat:econ.EM AND (abs:"forecast" AND (abs:"stock return" OR abs:"excess return" OR abs:"business cycle"))',
    ]
    seen, out = set(), []
    for qi, q in enumerate(queries, 1):
        batch = arxiv_query(q, cap=CAP, tag="kw%02d" % qi)
        for r in batch:
            if r["key"] not in seen:
                seen.add(r["key"]); out.append(r)
        print(f"  query done, cumulative: {len(out)}")
    # 2026-09-30：类目 × 年份窗口分片扫。单条查询封顶 2000，
    # 而 q-fin 全类目合起来远超此数——用一条 OR 查询等于一直在漏，分片后才真拿到全量。
    # 但全扫一次要 2 小时以上，每晚重扫既慢又白耗 arXiv 配额 -> 默认 6 天一次，
    # 需要立刻深扫时设环境变量 SWEEP=1。
    if need_sweep():
        n_before = len(out)
        for r in arxiv_sweep(seen):
            out.append(r)
        print(f"  类目分片扫新增 {len(out) - n_before} 条，累计 {len(out)}", flush=True)
        mark_sweep()
    else:
        print("  跳过类目分片扫（上次在 %s 天内；深扫请设 SWEEP=1）" % SWEEP_DAYS, flush=True)
    return out


SWEEP_DAYS = 6
SWEEP_MARK = os.path.join(ROOT, "_last_sweep.txt")

def need_sweep():
    if os.environ.get("SWEEP") == "1":
        return True
    try:
        last = float(open(SWEEP_MARK, encoding="utf-8").read().strip())
    except Exception:
        return True
    return (time.time() - last) / 86400.0 >= SWEEP_DAYS

def mark_sweep():
    try:
        with open(SWEEP_MARK, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _log_cap_hit(tag, query, total, returned):
    """截断记录留盘：下次扫之前能翻着看哪些档一直在漏，避免只活在内存里、跑完就没了。"""
    try:
        with open(CAP_LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\ttotal=%d\treturned=%d\t%s\n"
                    % (time.strftime("%F %T"), tag or "-", total, returned, query[:120]))
    except OSError:
        pass

def _load_plan():
    try:
        return json.load(open(SWEEP_PLAN, encoding="utf-8"))
    except Exception:
        return {}

def _windows_for(cat):
    w = _load_plan().get(cat)
    if not w:
        return list(SWEEP_WINDOWS)
    return sorted((int(a), int(b)) for a, b in w)

def _split_window(cat, a, b):
    """某 cat×年份窗口撞了 2000 上限 -> 把这一档换成三份更小的窗口，下次扫自动用细窗。
    已经细到一年还在截断的，不再往下炸查询数，交给人判断。"""
    plan = _load_plan()
    cur = plan.get(cat) or [list(w) for w in SWEEP_WINDOWS]
    cur = [tuple(x) for x in cur]
    if (a, b) not in cur:
        return
    if b - a <= 1:
        print(f"  [截断-暂不细分] {cat} {a}-{b} 单年仍封顶，先记账不动窗口", flush=True)
        return
    third = max(1, (b - a) // 3)
    parts, s = [], a
    while s < b:
        e = min(b, s + third) if len(parts) < 2 else b
        if e > s:
            parts.append((s, e))
        s = e
    plan[cat] = [w for w in cur if w != (a, b)] + [list(p) for p in parts]
    try:
        tmp = SWEEP_PLAN + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SWEEP_PLAN)
        print(f"  [窗口细分] {cat} {a}-{b} -> {parts}（下次扫生效）", flush=True)
    except OSError:
        pass

SWEEP_CATS = ["q-fin.PM", "q-fin.ST", "q-fin.CP", "q-fin.TR", "q-fin.MF",
              "q-fin.RM", "q-fin.EC", "q-fin.GN", "econ.GN", "stat.AP"]
SWEEP_WINDOWS = [(y, min(y + 3, 2027)) for y in range(1997, 2027, 3)]

def arxiv_sweep(seen):
    """按类目+提交年份窗口分片抓，避开单查询 2000 条上限。"""
    got = []
    for cat in SWEEP_CATS:
        c0 = 0
        for a, b in _windows_for(cat):
            q = f"cat:{cat} AND submittedDate:[{a}01010000 TO {b}01010000]"
            n_hit = len(CAP_HITS)
            rows = arxiv_query(q, cap=CAP, tag="%s %d-%d" % (cat, a, b))
            if len(CAP_HITS) > n_hit:
                _split_window(cat, a, b)
            for r in rows:
                if r["key"] not in seen:
                    seen.add(r["key"]); got.append(r); c0 += 1
            time.sleep(2)   # 分页之外，查询之间也留间隔，防 arXiv 429/406
            _beat("catalog")
        print(f"  sweep {cat} 新增 {c0} 条", flush=True)
    return got

OA_TERMS = ["factor investing", "alpha factor stock returns", "machine learning stock selection",
    "cross-section of expected stock returns", "deep learning stock price prediction",
    "Fama French factor model China", "portfolio optimization stock selection",
    "momentum value profitability factors", "asset pricing machine learning",
    "quantile regression stock returns", "fundamental factor anomaly",
    # 2026-09-27 扩容
    "return predictability stock", "trading strategy backtest equity", "financial machine learning pricing",
    "technical indicators stock", "analyst forecast stock", "distressed default stock", "Chinese stock market factor",
    "exchange-traded fund allocation", "quantitative equity portfolio management",
    # 第二轮扩容
    "smart beta factor", "deep learning alpha signal", "transformer stock return forecast",
    "graph neural network stock", "reinforcement learning trading agent", "event study prediction stock",
    "cointegration pairs trading", "analyst recommendation machine learning", "sentiment china stock",
    "risk parity factor investing", "value momentum growth combination", "explainable machine learning finance",
        # 2026-09-30 第二批扩容：A股机制/因子挖掘/持仓资金流/财报事件/风险溢价/再平衡
    "A-share factor investing",
    "Chinese stock market anomaly",
    "limit up Chinese stocks",
    "dragon-tiger list trading",
    "northbound capital flows China",
    "convertible bond China returns",
    "alpha factor mining",
    "genetic programming alpha",
    "symbolic regression stock prediction",
    "post-earnings announcement drift",
    "analyst forecast revision",
    "profitability factor stock returns",
    "investment factor asset pricing",
    "accruals anomaly stock",
    "short interest stock returns",
    "institutional holdings 13F returns",
    "fund flows stock returns",
    "variance premium asset pricing",
    "volatility risk premium cross-section",
    "downside risk factor pricing",
    "tail risk asset pricing",
    "low risk anomaly equities",
    "order flow imbalance price prediction",
    "optimal rebalancing transaction costs",
    "turnover constrained portfolio",
    "risk parity portfolio out of sample",
    "hierarchical risk parity",
    "cross-asset momentum",
    "carry factor currency commodity",
    "term spread stock returns predictability",
    "supply chain network stock returns",
    "satellite imagery stock returns",
    "machine learning cross-section returns",
    "deep learning alpha factor",
    "ensemble tree asset pricing",
    "regression tree financial machine learning",
]
BAD_DOMAINS = re.compile(r"ssrn\.com|link\.springer|onlinelibrary\.wiley|tandfonline|acm\.org|ieee\.org$|jstor", re.I)

def _abs_inv_of(w):
    """OpenAlex 把摘要存成 inverted index。字段名是 abstract_inverted_index；
    旧代码取的是 inverse_abstract_inverted_index（不存在的那个名字），
    结果 1998 条 OpenAlex 记录里只有 547 条带摘要 —— 大量条目只靠标题进了库，
    而 gate_verdict 与 rel_score 的输入都含摘要，等于把误召回的门开在标题上。"""
    return w.get("abstract_inverted_index") or w.get("inverse_abstract_inverted_index") or {}

def uninvert(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[k] for k in sorted(pos))[:2000]

# NBER 工作论文：免登录直下（实测 w30000 的 PDF 200/application/pdf，无需 cookie）。
# 台账里已有 93 条 NBER 条目是 OpenAlex 顺手带回来的，所以 key 一律沿用 oa:10.3386/wNNNN，
# 同一篇不会变成两条记录。
NBER_SOURCE = "S2809516038"
# OpenAlex 匿名接口按 IP 记**每日**预算（00:00 UTC 重置），与下面 collect_openalex 共用同一个池子。
# 2026-10-01 01:32 实测打满：报错原文 "Insufficient budget. This request has no API key, so it
# counts against the free daily budget shared by everyone on your network's IP address, and that
# budget is used up ($0 remaining; resets at midnight UTC)"。所以两条腿都要省着用：
# OpenAlex 每词条只取第 1 页，NBER 每词条也只取 1 页，谁先跑谁拿条数（catalog 里 NBER 在前）。
NBER_PAGES = 1
OA_PAGES = int(os.environ.get("OA_PAGES", "1"))
NBER_TERMS = ["asset pricing", "factor model stock returns", "stock market anomalies",
    "portfolio allocation", "momentum returns", "credit spreads", "expected returns",
    "volatility risk premium", "institutional investors", "mutual fund flows",
    "household finance", "consumption and asset returns", "short selling constraints",
    "machine learning asset pricing", "replication crisis finance"]

def nber_pdf_url(doi):
    m = re.search(r"10\.3386/(w\d+)", doi or "")
    if not m:
        return ""
    n = m.group(1)
    return "https://www.nber.org/system/files/working_papers/%s/%s.pdf" % (n, n)

def collect_nber():
    out, seen = [], set()
    consec = 0
    for term in NBER_TERMS:
        if consec >= 3:
            print("  NBER 连续失败，判定接口暂不可用，本轮跳过 NBER", flush=True)
            break
        for page in range(1, NBER_PAGES + 1):
            url = "https://api.openalex.org/works?" + urllib.parse.urlencode({
                "filter": "primary_location.source.id:%s,title_and_abstract.search:%s" % (NBER_SOURCE, term),
                "sort": "publication_date:desc", "per-page": 200, "page": page, "mailto": MAILTO})
            try:
                data = json.loads(http_get(url)); consec = 0
            except Exception as e:
                consec += 1
                print(f"  NBER fail [{term}] {str(e)[:70]} (连续 {consec} 次)", flush=True)
                if THROTTLE.search(str(e)):
                    time.sleep(min(60, 12 * (2 ** consec)))
                break
            for w in data.get("results", []):
                doi = (w.get("doi") or "").replace("https://doi.org/", "")
                pdf = nber_pdf_url(doi)
                if not pdf:
                    continue
                absr = uninvert(_abs_inv_of(w))
                title = re.sub(r"\s+", " ", w.get("title") or "").strip()
                ta = title + " " + absr
                if not RELEVANCE.search(ta):
                    continue
                # 下载位很贵（Z: 只剩 10GB），NBER 这一腿只留题摘确有金融信号的
                if not STRONG_GATE.search(ta) and rel_score(title, absr) < 4:
                    continue
                key = "oa:" + doi
                if key in seen:
                    continue
                seen.add(key)
                out.append({"key": key, "source": "NBER", "arxiv_id": "",
                    "pdf_url": pdf, "page": w.get("landing_page_url") or ("https://www.nber.org/papers/" + doi.split("/")[-1]),
                    "title": title,
                    "authors": [a["author"]["display_name"] for a in (w.get("authorships") or [])][:12],
                    "date": w.get("publication_date") or "", "abstract": absr,
                    "cats": [], "doi": doi, "cited": w.get("cited_by_count", "")})
            time.sleep(1)
        print(f"  NBER term done [{term}] cumulative {len(out)}", flush=True)
    return out

def collect_openalex():
    out, seen = [], set()
    consec = 0
    for term in OA_TERMS:
        if consec >= 3:  # 连续失败 → 服务端临时不可用(匿名检索被暂停/503)，不再逐个词条空耗
            print("  OpenAlex 连续失败，判定服务暂不可用，本轮跳过 OpenAlex", flush=True)
            break
        for page in range(OA_PAGES):  # 每词条取的页数（OpenAlex 匿名按 IP 记日预算，见 NBER_PAGES 注释）
            q = urllib.parse.urlencode({"search": term, "filter": "is_oa:true",
                "sort": "relevance_score:desc", "per-page": 200, "cursor": "*" if page == 0 else None,
                "mailto": MAILTO})
            url = "https://api.openalex.org/works?" + q
            if page:  # 游标翻页: 简单做法用 page 参数
                url = ("https://api.openalex.org/works?" +
                       urllib.parse.urlencode({"search": term, "filter": "is_oa:true",
                           "sort": "relevance_score:desc", "per-page": 200, "page": 2, "mailto": MAILTO}))
            try:
                data = json.loads(http_get(url))
                consec = 0
            except Exception as e:
                consec += 1
                print(f"  OA fail [{term}] {str(e)[:70]} (连续 {consec} 次)", flush=True)
                if THROTTLE.search(str(e)):
                    time.sleep(min(60, 12 * (2 ** consec)))
                break
            for w in data.get("results", []):
                if not RELEVANCE.search((w.get("title") or "") + " " + (w.get("display_name") or "")):
                    continue
                doi = (w.get("doi") or "").replace("https://doi.org/", "")
                pdf = w.get("best_oa_location", {}).get("pdf_url") if w.get("best_oa_location") else None
                if not pdf or BAD_DOMAINS.search(pdf):
                    continue
                inv = _abs_inv_of(w)
                absr = uninvert(inv)
                if not RELEVANCE.search(absr or w.get("title", "")):
                    continue
                key = f"oa:{doi or pdf}"
                if key in seen: continue
                seen.add(key)
                auths = [a["author"]["display_name"] for a in (w.get("authorships") or [])][:12]
                out.append({"key": key, "source": "OpenAlex", "arxiv_id": "",
                    "pdf_url": pdf, "page": w.get("landing_page_url") or doi,
                    "title": re.sub(r"\s+", " ", w.get("title") or "").strip(),
                    "authors": auths, "date": w.get("publication_date") or "",
                    "abstract": absr, "cats": [], "doi": doi,
                    "cited": w.get("cited_by_count", "")})
            time.sleep(1)
        print(f"  OA term done [{term}] cumulative {len(out)}")
    return out

# 重新抓目录时要从旧记录继承的字段（下载状态 + 已完成的解析/分类/总结结果）
# 否则 index 阶段每天把全部条目重跑一遍，且"本次未被检索命中"的历史条目会掉出索引
# 这些字段不进白名单（要让来源刷新），但来源本轮返回空时用旧值补，避免越跑越空
FILL_IF_EMPTY = ("abstract", "doi", "cited", "authors", "date", "cats")

KEEP_ON_MERGE = ("status", "pdf_path", "local_done", "text_done", "txt_path",
                 "primary", "tags", "summary", "offtopic", "pages", "error",
                 "ai_summary", "ai_grade", "ai_findings", "ai_read_at", "gate_level",
                 "dup_of", "retries")   # dup_of/retries 2026-09-30 补：漏掉会被每日 catalog 重写抹掉

OR_TERMS = ["stock selection", "cross-section of stock returns", "factor investing",
    "alpha factor stock", "stock price prediction", "empirical asset pricing",
    "portfolio construction", "limit order book", "stock ranking deep learning",
    "trading strategy backtest", "Fama French factors",
    # 2026-09-27 扩容
    "quantitative equity", "financial forecasting", "return forecast transformer",
    "news trading signal", "graph neural stock",
    # 第二轮扩容
    "alpha mining", "factor model deep learning", "quant stock picking",
    "reinforcement learning portfolio", "stock returns transformer", "explainable ai finance",
        # 2026-09-30 第二批扩容
    "alpha mining large language model",
    "factor model deep learning finance",
    "limit order book forecasting",
    "optimal execution reinforcement learning trading",
    "transaction cost portfolio rebalancing",
    "market making deep learning",
    "stock selection graph neural network",
    "temporal graph financial",
    "volatility trading agent",
    "quantitative factor evaluation",
    "distributionally robust portfolio optimization",
    "backtest overfitting detection",
    "regime switching asset allocation",
    "news sentiment trading",
    "large language model trading agent",
    "point-in-time data leakage finance",
]
OR_BAD = re.compile(r"ieeexplore|dl\.acm\.org|sciencedirect|springer|linkinghub", re.I)

def collect_openreview(existing_titles=None):
    """OpenReview v2 检索（免登录）：NeurIPS/ICLR/ICML 等金融 ML 论文
    返回 (记录列表, 被验证墙挡住的 key 集合)——后者直接标 giveup 不参与下载。"""
    have = set(existing_titles or set())
    out, seen, or_walled = [], set(), set()
    for term in OR_TERMS:
        total, got = None, 0
        for offset in range(0, 1000, 250):
            url = ("https://api2.openreview.net/notes/search?" + urllib.parse.urlencode(
                {"term": term, "source": "forum", "content": "all", "group": "all",
                 "limit": 250, "offset": offset}))
            try:
                data = json.loads(http_get(url, timeout=60))
            except Exception as e:
                print("  OR fail", term, offset, str(e)[:60]); break
            notes = data.get("notes", [])
            total = data.get("count", 0)
            for n in notes:
                nid = n.get("id", "")
                if not nid or nid in seen:
                    continue
                c = n.get("content", {})
                get = lambda k: (c.get(k) or {}).get("value") if isinstance(c.get(k), dict) else None
                title = re.sub(r"\s+", " ", str(get("title") or "")).strip()
                abstract = re.sub(r"\s+", " ", str(get("abstract") or "")).strip()
                if not title or not RELEVANCE.search(title + " " + abstract):
                    continue
                nt = re.sub(r"[^a-z0-9]+", "", title.lower())
                if nt in have:  # 与 arXiv/OpenAlex 已有条目去重
                    continue
                seen.add(nt)
                pdf = str(get("pdf") or "")
                if not pdf.lower().startswith("http") or OR_BAD.search(pdf):
                    # note 没有真 PDF 链接：OpenReview 的 /pdf 端点全站挡在 Turnstile 后，
                    # 纯 HTTP 一律 rc=22，让它进 new/failed 只会让 harvest 每轮白耗几小时（2026-09-30 实测 1082 条）
                    pdf = f"https://openreview.net/pdf?id={nid}"
                    or_walled.add(f"or:{nid}")
                cd = n.get("cdate") or n.get("pdate") or 0
                date = time.strftime("%Y-%m-%d", time.gmtime(cd / 1000)) if cd else ""
                out.append({"key": f"or:{nid}", "source": "OpenReview", "arxiv_id": "",
                    "pdf_url": pdf, "page": f"https://openreview.net/forum?id={nid}",
                    "title": title, "authors": [str(a) for a in (get("authors") or [])][:12],
                    "date": date, "abstract": abstract[:2000], "cats": [str(get("venue") or "")],
                    "doi": "", "cited": ""})
                have.add(nt)
            got += len(notes)
            if not notes or got >= min(total or 0, 1000):
                break
            time.sleep(1)
        print(f"  OR term done [{term}] cumulative {len(out)}")
    return out, or_walled

# ---------- catalog 安全写入 ----------
BAK_KEEP = 8          # 33MB × 8 ≈ 270MB，Z: 只剩 10GB，够用且不吃盘

def _bak_dir():
    """BAK 必须在调用时按当前 ROOT 算：离线测试会把 ROOT 指到临时目录，
    模块级常量会让测试把 33MB 快照写进真的 Z:/论文/_bak。"""
    return os.path.join(ROOT, "_bak")

def backup_catalog(tag=""):
    """每轮写链开始前先给台账拍一张快照。
    2026-10-01 那次 catalog.json 被撕裂时 `_bak/` 是空的，只能拿 index 末尾写的
    catalog.csv 反向重建（摘要只剩前 800 字、retries 计数全丢）。从那以后"跑前先备份"
    不再是口头规矩，而是每个写链入口都会真执行的一次 copy。同盘复制，失败不影响主流程。"""
    bak = _bak_dir()
    try:
        os.makedirs(bak, exist_ok=True)
        src = CATLOG
        if not os.path.exists(src):
            return None
        pat = os.path.join(bak, "catalog_*.json")
        newest = sorted(glob.glob(pat), key=os.path.getmtime)[-1:]
        if newest and time.time() - os.path.getmtime(newest[0]) < 6 * 3600:
            return None                      # 6 小时内已有快照，别把 33MB 反复拷
        dst = os.path.join(bak, "catalog_%s%s.json" % (time.strftime("%Y%m%d_%H%M"),
                                                        "_" + tag if tag else ""))
        shutil.copy2(src, dst)
        for p in sorted(glob.glob(pat), key=os.path.getmtime)[:-BAK_KEEP]:
            try:
                os.remove(p)
            except OSError:
                pass
        return dst
    except OSError:
        return None

def load_catalog():
    return json.load(open(CATLOG, encoding="utf-8")) if os.path.exists(CATLOG) else []

def snapshot(recs):
    """在读取时刻留一份深拷贝，写回时用它判断"本次到底改了哪些字段"。"""
    return json.loads(json.dumps(recs))

def save_catalog(recs, orig=None):
    """写前重新读盘，只把本进程真正改动过的字段叠加到磁盘最新版上。
    2026-09-30 事故：cmd_catalog 开头读入、35 分钟后整份写回，
    把期间精读 merge 进去的 300 条 ai_* 用旧内存副本盖掉了。"""
    fresh = load_catalog()
    fmap = {r["key"]: r for r in fresh}
    omap = {r["key"]: r for r in (orig or [])}
    out, seen = [], set()
    for r in recs:
        key = r.get("key")
        base = fmap.get(key)
        if base is None:
            out.append(r)
        else:
            o = omap.get(key) or {}
            for k, v in r.items():
                if k not in o or o.get(k) != v:
                    base[k] = v
            for k in list(o.keys()):          # 本次确实删掉的字段才删
                if k not in r and k in base:
                    base.pop(k)
            out.append(base)
        seen.add(key)
    for k, r in fmap.items():                 # 别处新增的记录保留
        if k not in seen:
            out.append(r)
    tmp = CATLOG + ".wtmp"
    _write_json_atomic(out, tmp, CATLOG)
    return out

def free_gb(path=None):
    """剩余空间（GB）。取不到就返回 +inf，别把抓取卡死。"""
    try:
        return shutil.disk_usage(path or ROOT).free / 1e9
    except Exception:
        return float("inf")

MIN_FREE_GB = float(os.environ.get("MIN_FREE_GB", "3.0"))

def _space_ok():
    """NAS 快满时坚决停手。2026-10-01 那次 catalog.json 被撕裂，最直接的物理解释就是
    Z: 只剩 10GB 而 WebDAV 的 move 是服务端拷贝 —— 拷到一半没地方落，目的文件当场撕裂。
    写大 JSON / 下 PDF 之前先量一眼：宁可不干活，也不能把台账写坏或把盘子塞死。"""
    return free_gb() >= MIN_FREE_GB

def _write_json_atomic(out, tmp, dst, tries=15):
    """Windows/WebDAV 上 os.replace 会因为"另一个程序正在读 catalog.json"直接抛
    WinError 32（2026-09-30 我手写的打分进程就是这么被正在跑的 harvest 撞掉的）。
    这不是数据坏了，是文件被占用，退避重试就能过去；实在不行也绝不留下半截文件。
    tries=15、最长等约 45 秒：25MB 的表被并发读一次就要 8 秒左右，8 次×递增的窗口
    实测会不够用（2026-09-30 23:40 rescue 的最后一次写回被连挡 8 次，PDF 已移但字段没落上）。"""
    last = None
    for i in range(tries):
        try:
            blob = json.dumps(out, ensure_ascii=False, indent=1)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            # 先验货再换手：临时文件必须能独立解析回同样的条数，
            # 否则宁可这次不落盘，也绝不能把半截内容换成正式台账
            with open(tmp, encoding="utf-8") as f:
                chk = json.load(f)
            if len(chk) != len(out):
                raise ValueError("自检条数不一致 %d/%d" % (len(chk), len(out)))
            os.replace(tmp, dst)
            return True
        except (PermissionError, OSError, ValueError, json.JSONDecodeError) as e:
            last = e
            time.sleep(min(1.5 * (i + 1), 4.0))
    print("  [警告] catalog 写回连续 %d 次被占用挡住: %s（本批改动留在内存里，下一次落盘会带上）"
          % (tries, last), flush=True)
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    return False


def cmd_catalog(refresh=False):
    if not _acquire("catalog"):
        return
    try:
        backup_catalog("catalog")
        _cmd_catalog(refresh)
    finally:
        _release("catalog")

def _cmd_catalog(refresh=False):
    os.makedirs(ROOT, exist_ok=True)
    old = {}
    old = {r["key"]: r for r in load_catalog()}
    orig = snapshot(list(old.values()))
    # NBER 放在 OpenAlex 之前：两者共用 api.openalex.org 的匿名 IP 日预算
    # （2026-10-01 01:32 实测该预算已耗尽，报错原文 "Insufficient budget … shared by everyone on
    #  your network's IP address … resets at midnight UTC"，即北京时间 08:00 才恢复）。
    # 谁先跑谁拿到条数，所以把量小、精度高的 NBER 排前面。
    nb = collect_nber()
    ax, oa = collect_arxiv(), collect_openalex()
    old_titles = set()
    for r in old.values():
        nt = re.sub(r"[^a-z0-9]+", "", (r.get("title") or "").lower())
        if nt:
            old_titles.add(nt)
    orw, or_walled = collect_openreview(old_titles)
    allr = {}
    for r in ax + oa + orw + nb:
        base = old.get(r["key"], {})
        r.update({k: v for k, v in base.items() if k in KEEP_ON_MERGE and v is not None and v != ""})
        # 来源本轮没给元数据时，用旧值补空（2026-09-30：链条把 546 条回填摘要覆盖成空）
        for k in FILL_IF_EMPTY:
            if not r.get(k) and base.get(k):
                r[k] = base[k]
        if not r.get("local_done") and r["key"] in old and old[r["key"]].get("status") == "done":
            r["status"], r["pdf_path"] = "done", old[r["key"]]["pdf_path"]
        allr.setdefault(r["key"], r)
    carried = 0
    for k, r in old.items():  # 本次检索未覆盖的历史条目原样保留，不从库中删除
        if k not in allr:
            allr[k] = r; carried += 1
    recs = list(allr.values())
    n_wall = 0
    for r in recs:
        r.setdefault("status", "new"); r.setdefault("pdf_path", ""); r["primary"] = r.get("primary", "")
        r["rel"] = rel_score(r.get("title"), r.get("abstract"))
        # 只有 OpenReview 自托管 PDF 且从未成功下载过的条目才降级，出版社/arXiv 直链不动
        if (r.get("source") == "OpenReview" and "/openreview.net/" in (r.get("pdf_url") or "")
                and r.get("status") in ("new", "failed", "giveup") and not r.get("pdf_path")):
            if r.get("status") != "giveup":
                n_wall += 1
            r["status"], r["error"] = "giveup", "or_walled_no_pdf"
        elif r.get("key") in or_walled and r.get("status") == "new":
            r["status"], r["error"] = "giveup", "or_walled_no_pdf"
    recs = save_catalog(recs, orig)
    n_new = sum(1 for r in recs if r.get("status") == "new")
    print(f"目录完成: 候选 {len(recs)} 篇 (arXiv {len(ax)}, OpenAlex {len(oa)}, OpenReview新增 {len(orw)}, "
          f"本次未命中但保留 {carried}, 待下载 {n_new}, OpenReview验证墙转giveup {n_wall})", flush=True)

# ---------- 跨进程互斥：防止两条链同时打 arXiv / 同时写 catalog ----------
def _lock_path(kind):
    return os.path.join(ROOT, "_%s.lock" % kind)

def _proc_start(pid):
    """进程启动时刻，用来识别"pid 被回收给了别的进程"。取不到就返回 0。"""
    try:
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return 0.0

def _lock_text():
    return "%d %f %f" % (os.getpid(), time.time(), _proc_start(os.getpid()))

def _holder_alive(pid, parts):
    """锁里的 pid 是否真的是当初那个持锁进程。
    Windows 会回收 pid：只看 pid_exists 会把"锁的主人早死了、pid 被别的程序占了"
    误判成有人在跑，于是整条链一晚上都跳过（2026-09-30 测试里就撞到了）。
    所以第三段记了启动时刻，对不上就当作死锁。老格式没有第三段时退回旧判断。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid == os.getpid() or not psutil.pid_exists(pid):
        return False
    if len(parts) >= 3:
        try:
            return abs(_proc_start(pid) - float(parts[2])) < 2.0
        except (TypeError, ValueError):
            return False
    return True

def _acquire(kind):
    """O_EXCL 建锁；持锁进程已死或锁超过 6 小时则抢占。返回 True 表示拿到锁。"""
    lp = _lock_path(kind)
    for _ in range(2):
        try:
            fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, _lock_text().encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                parts = open(lp, encoding="utf-8").read().split()
                pid, ts = parts[0], float(parts[1])
                alive = _holder_alive(pid, parts)
                stale = time.time() - ts > 6 * 3600
                if alive and not stale:
                    print("  跳过 %s：%s 正被 pid=%s 占用" % (kind, kind, pid), flush=True)
                    return False
                print("  接管失效的 %s 锁（旧 pid=%s age=%.0fh 存活=%s）"
                      % (kind, pid, (time.time() - ts) / 3600, alive), flush=True)
                os.remove(lp)
            except Exception:
                os.remove(lp)
    return False

def _release(kind):
    try:
        os.remove(_lock_path(kind))
    except OSError:
        pass

def _beat(kind):
    """给长任务续锁心跳。_acquire 用"时间戳超过 6 小时"判失效锁并接管，
    而 catalog/harvest/index 单轮都可能跑超 6 小时 —— 不续心跳就会被后来的
    进程当成死锁抢走，出现两个进程同时写 catalog / 同时打 arXiv。
    只在自己确实持锁时续写，绝不凭空造锁。"""
    lp = _lock_path(kind)
    try:
        parts = open(lp, encoding="utf-8").read().split()
        if not parts or int(parts[0]) != os.getpid():
            return False
        with open(lp, "w", encoding="utf-8") as f:
            f.write(_lock_text())
        return True
    except (OSError, ValueError, IndexError):
        return False

# ---------- 下载阶段 ----------
import threading
import psutil
from functools import partial

_PACE = threading.Lock()
_PACE_NEXT = [0.0]

def _pace(interval):
    """全局最小请求间隔：arXiv 对突发并发会返回 429/406，限速比加并发更能提高成功率"""
    if interval <= 0:
        return
    with _PACE:
        t = max(time.time(), _PACE_NEXT[0])
        _PACE_NEXT[0] = t + interval
    wait = t - time.time()
    if wait > 0:
        time.sleep(wait)

THROTTLE = re.compile(r"429|503|UNEXPECTED_EOF|CERTIFICATE_VERIFY|IncompleteRead", re.I)
ARXIV_URL = re.compile(r"arxiv\.org", re.I)

def _wait_for(err, attempt, url=""):
    """真限流特征才指数退让；rc=22/40x 对非 arXiv 域基本是永久墙 -> 短睡快失败快 giveup"""
    s = str(err)
    if THROTTLE.search(s) or (ARXIV_URL.search(url) and re.search(r"rc=22|406|403", s)):
        return min(120, 15 * (2 ** attempt))
    return 1 + attempt

def _dl(rec, attempts=3, interval=1.0):
    if rec.get("status") == "done":
        return rec, "skip"
    fname = sanitize(rec["key"], 90).replace(":", "__") + ".pdf"
    path = os.path.join(INBOX, fname)
    err = "unknown"
    for attempt in range(attempts):
        _pace(interval)
        try:
            blob = http_get(rec["pdf_url"], timeout=120)
            if blob[:5] == b"%PDF-" and len(blob) > 15000:
                with open(path, "wb") as f: f.write(blob)
                rec.update(status="done", pdf_path=path)
                rec.pop("error", None)
                return rec, "ok"
            rec["retries"] = rec.get("retries", 0) + 1
            rec["status"] = "giveup" if rec["retries"] >= 5 else "bad_pdf"
            return rec, "bad"
        except Exception as e:
            err = str(e)[:80]
            if attempt + 1 < attempts:
                time.sleep(_wait_for(err, attempt, rec["pdf_url"]))
    rec["retries"] = rec.get("retries", 0) + 1
    rec.update(status="giveup" if rec["retries"] >= 5 else "failed", error=err)
    return rec, "fail"

def _worthwhile(r):
    """下载前的粗筛：题摘过金融门槛、或因子相关度 >=1，或压根没有摘要（判不了就下）。
    为什么要有这一道：2026-09-30 这轮把 2607 篇 arXiv 新条目全下全解析了，index 的门槛判掉 99%，
    抽检 14 篇标题与正文都对得上、确实是推荐系统矩阵分解/动量梯度下降/GNSS/量子因子图这类撞词论文 ——
    门槛没错，错的是我们把带宽和解析预算全花在了注定要被剔掉的条目上。
    这里只是"先不下"，记录仍留 status=new，随时 --no-screen 全量补齐，不删任何东西。"""
    ab = (r.get("abstract") or "").strip()
    if not ab:
        return True
    ta = "%s %s" % (r.get("title") or "", ab[:1500])
    if STRONG_GATE.search(ta):
        return True
    # 门槛只要 1 分：抽检显示被门槛判非金融的条目里 92% 本来就是负分，
    # 而把线提到 4 分会连带拦掉 1.1% 的真论文（含一篇 A 档 Interpretable Factors of Firm Characteristics
    # 和几篇期权/风险度量的数学金融文）—— 宁可多下几百篇，不能把好论文永久留在 new 里。
    return rel_score(r.get("title"), ab) >= 1

def cmd_harvest(workers=3, new_only=False, interval=1.0, attempts=3, screen=True):
    if not _acquire("harvest"):
        return
    try:
        backup_catalog("harvest")
        _cmd_harvest(workers, new_only, interval, attempts, screen)
    finally:
        _release("harvest")

def _cmd_harvest(workers=3, new_only=False, interval=1.0, attempts=3, screen=True):
    os.makedirs(INBOX, exist_ok=True)
    recs = load_catalog()
    orig = snapshot(recs)
    todo = [r for r in recs if r.get("status") == "new"] if new_only \
        else [r for r in recs if r.get("status") not in ("done", "giveup")]
    # 下载排队按因子相关度从高到低：带宽和 arXiv 配额都有限，先把"能拿来做选股/因子/组合"的捞回来，
    # 精读队列才不至于空等。低分的照常会下，只是排在后面（不删不看衰）。
    n_screen = 0
    if screen and new_only:
        # 只在日常"仅新条目"的抓取里粗筛；周一那次全量 harvest 不筛，
        # 把被拦下的条目一次性补齐 —— 这样粗筛不会让任何论文永久留在 new 里。
        keep = [r for r in todo if _worthwhile(r)]
        n_screen = len(todo) - len(keep)
        todo = keep
    elif screen:
        print("  本轮为全量 harvest（含重试），粗筛不生效，被拦过的条目一并补齐", flush=True)
        n_screen = -1
    n_hot = sum(1 for r in todo if int(r.get("rel") or 0) >= 8)
    todo.sort(key=lambda r: -int(r.get("rel") or 0))
    print(f"待下载 {len(todo)} 篇（高相关 rel>=8 的 {n_hot} 篇排最前; "
          + (f"粗筛跳过 {n_screen} 篇题摘无金融信号的，仍留 status=new、周一全量轮会补齐; " if n_screen >= 0 else "")
          + f"workers={workers} interval={interval}s attempts={attempts} "
          f"{'仅新条目' if new_only else '含历史失败重试'})", flush=True)
    cnt = {"ok": 0, "skip": 0, "fail": 0, "bad": 0}
    if not _space_ok():
        print(f"[停] Z: 只剩 {free_gb():.2f} GB < 安全线 {MIN_FREE_GB} GB，不开下载；"
              f"先把 _剔除-非金融 里的非金融 PDF 搬走腾地方", flush=True)
        return
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = [ex.submit(partial(_dl, attempts=attempts, interval=interval), r) for r in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                r, st = fu.result()
            except Exception as e:
                st = "fail"; print("  worker err", e)
                continue
            cnt[st] += 1
            if i % 25 == 0 or st in ("fail", "bad"):
                _beat("harvest")
                print(f"  [{i}/{len(todo)}] {st} ok={cnt['ok']} fail={cnt['fail']} bad={cnt['bad']} "
                      f"elapsed={time.time()-t0:.0f}s", flush=True)
            if i % 100 == 0:                    # 安全写要重读 25MB，降频避免拖慢下载
                # 绝不能写成 recs = save_catalog(...)：futures 与 todo 拿的是最初那批 dict，
                # save_catalog 返回的是"盘上重读+叠加"出来的另一批对象，一回绑，
                # 后续 _dl 的 status/pdf_path 就改在了已经被丢出列表的旧对象上，落不了盘
                # （2026-09-30 踩过：h2 的 harvest 报了 4839 篇 ok，盘上 done 却只涨了一百来篇）。
                save_catalog(recs, orig)
                if not _space_ok():
                    print(f"  [{i}/{len(todo)}] [停] Z: 只剩 {free_gb():.2f} GB，低于安全线 "
                          f"{MIN_FREE_GB} GB，本轮下载到此为止（已下的都已落盘，剩下的下次接着下）",
                          flush=True)
                    break
    finally:
        # 光 break 停不住池子：futures 在 break 之前就已经全部 submit 了，
        # with 语句的隐式 shutdown(wait=True) 会把剩下几千个任务照样跑完 ——
        # 2026-10-01 那次空间闸触发后日志再也不出声，下载却在后台一直做到我把进程按 PID 停掉。
        # 必须 cancel_futures=True，只留已经在跑的那 workers 个。
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:                      # 老版本 Python 没有 cancel_futures
            ex.shutdown(wait=False)
    save_catalog(recs, orig)
    print(f"下载完成 ok={cnt['ok']} fail={cnt['fail']} bad={cnt['bad']} skip={cnt['skip']} "
          f"用时 {time.time()-t0:.0f}s", flush=True)

# ---------- 解析 / 分类 / 总结 / 索引 ----------
def classify(text):
    t = text.lower(); hits = {}
    for name, pats in CATEGORIES:
        c = sum(1 for p in pats if re.search(p, t))
        if c: hits[name] = c
    primary = max(hits, key=lambda k: (hits[k], -order_idx(k))) if hits else FALLBACK
    return primary, hits

_ORDER = [c[0] for c in CATEGORIES] + [FALLBACK]
def order_idx(k): return _ORDER.index(k)

NOISE_LINE = re.compile(r"^\s*(keywords?|jel\b|abstract|arxiv:|working paper|∗|†|\d{1,3}\s*$|\W{0,3}$|this version|corresponding)", re.I)

def _clean_text(full):
    keep = []
    for ln in full.splitlines():
        s = ln.strip()
        if s and not NOISE_LINE.match(s):
            keep.append(s)
    return re.sub(r"\s+", " ", " ".join(keep))

def _pick(sents, pats, n=1):
    out = []
    for s in sents:
        if 60 < len(s) < 420 and re.search(pats, s, re.I) and s not in out:
            out.append(s)
            if len(out) >= n: break
    return out

def summarize(text, abstract):
    """规则式总结: 研究目的/方法数据/结论发现"""
    clean = _clean_text(text)
    abs_c = _clean_text(abstract or "")
    sents = lambda t: [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    aim = _pick(sents(abs_c) + sents(clean[:8000]),
                r"(we (propose|investigat|examin|study|introduce|develop|ask|document)|this paper (i|presents|studies|propos))", 1)
    method = _pick(sents(clean[:25000]),
                r"(our (sample|data|dataset|test portfolio|period)|using (daily|monthly|u\.s\.|chinese) |we (construct|employ|form|train|run)|"
                r"between 19\d\d|from 19\d\d|from 20\d\d|test portfolios?)", 1)
    ci = max(clean.lower().rfind(h) for h in ("conclusion", "concluding remark", "summary and conclusion"))
    concl_zone = clean[ci:ci + 6000] if ci > 2000 else clean[-9000:]
    find = _pick(sents(abs_c) + sents(concl_zone),
                 r"(we (find|show|document|conclude|demonstrate|reveal)|results?( below)? (show|indicate)|"
                 r"outperform|improve[sd]? (the |sharpe|performance)|significant at|suggests)", 2)
    parts = []
    if aim: parts.append("目的: " + aim[0][:300])
    if method: parts.append("方法/数据: " + method[0][:300])
    if find: parts.append("发现: " + " ".join(x[:260] for x in find))
    return " ｜ ".join(parts) if parts else "（未能自动提取，请查看简介/全文）"

def cmd_index():
    """索引阶段要解析上千个 PDF，动辄几小时。加锁是为了防止 03:00 定时任务和
    手动链条同时跑 index —— 两者都会移动 PDF、重写 catalog.csv/INDEX.md。"""
    if not _acquire("index"):
        return
    try:
        backup_catalog("index")
        _cmd_index()
    finally:
        _release("index")

def _cmd_index():
    recs = load_catalog()
    orig = snapshot(recs)
    os.makedirs(TXT, exist_ok=True)
    done = 0          # 已解析（含历史已解析）计数，只用于最后汇报
    fresh = 0         # 本轮真正新解析的篇数：落盘按它触发
    t_beat = time.time()
    flushed = [0]
    for idx, r in enumerate(recs):
        # 心跳按时间而不是按条数：扫到 8000 条"历史已解析"的记录只用几秒，
        # 按条数刷会把 WebDAV 打成每秒几十次小写入（2026-09-30 实测出现过这种风暴）。
        if time.time() - t_beat > 120:
            _beat("index"); t_beat = time.time()
        if r.get("status") != "done" or not r.get("pdf_path"):
            r["primary"] = r.get("primary") or ""; continue
        try:
            if r.get("text_done"):
                if "offtopic" not in r:  # 补跑金融门槛
                    try:
                        head = open(r["txt_path"], encoding="utf-8").read()[:4000]
                    except Exception:
                        head = ""
                    probe = (r.get("title", "") + " " + r.get("abstract", "")[:500] + " " + head).lower()
                    verdict = gate_verdict(r.get("title"), r.get("abstract"), head)
                    off = verdict == "fail"
                    r["gate_level"] = verdict
                    r["offtopic"] = off
                    if off:
                        folder = os.path.join(ROOT, "_剔除-非金融")
                        os.makedirs(folder, exist_ok=True)
                        newp = os.path.join(folder, os.path.basename(r["pdf_path"]))
                        try:
                            os.replace(r["pdf_path"], newp); r["pdf_path"] = newp
                        except OSError:
                            pass
                done += 1; continue
            with open(r["pdf_path"], "rb") as f:
                doc = fitz.open(stream=f.read(), filetype="pdf")
            full = "\n".join(p.get_text() for p in doc)
            r["pages"] = doc.page_count; doc.close()
            if not r.get("title") or len(r.get("title", "")) < 8:
                first = [l.strip() for l in full.splitlines() if len(l.strip()) > 15]
                r["title"] = first[0] if first else r["title"]
            tb = os.path.join(TXT, sanitize(r["key"], 90).replace(":", "__") + ".txt")
            open(tb, "w", encoding="utf-8").write(full)
            r["txt_path"] = tb
            probe = (r.get("title", "") + " " + r.get("abstract", "") + " " + full[:4000]).lower()
            primary, tags = classify(probe)
            r["primary"], r["tags"] = primary, tags
            r["gate_level"] = gate_verdict(r.get("title"), r.get("abstract"), full[:4000])
            # 解析完才拿得到正文，无摘要的条目到这一步才有可靠的 rel（下载时只按题摘排过序）
            r["rel"] = rel_score(r.get("title"), r.get("abstract"), full[:1500])
            r["offtopic"] = r["gate_level"] == "fail"
            r["summary"] = summarize(full, r.get("abstract", ""))
            # 非金融条目直接落到 _剔除-非金融/，不要混进 15 个分类目录
            folder = os.path.join(ROOT, "_剔除-非金融") if r["offtopic"] else os.path.join(ROOT, sanitize(primary, 30))
            os.makedirs(folder, exist_ok=True)
            newp = os.path.join(folder, os.path.basename(r["pdf_path"]))
            if r["pdf_path"] != newp:
                if os.path.exists(newp): os.remove(r["pdf_path"])
                else: os.replace(r["pdf_path"], newp)
                r["pdf_path"] = newp
            r["text_done"] = True
            done += 1; fresh += 1
        except Exception as e:
            r["text_done"] = False; r["error"] = str(e)[:120]
        # 边解析边落盘：Z 盘掉线或进程被杀时，不至于把几小时的解析全丢掉。
        # 触发量用 fresh（本轮真正新解析的篇数）而不是 done —— done 把 8000 条"历史已解析"
        # 也算进去了，一轮 27MB 的整表写回会被白刷几十次（2026-09-30 踩过）。
        # 故意不回绑 recs —— 循环迭代器还指着原列表，回绑后继续改旧字典就落不了盘。
        if fresh and fresh % 300 == 0 and fresh != flushed[0]:
            flushed[0] = fresh
            save_catalog(recs, orig)
            _beat("index")
            print(f"  索引进度: 本轮新解析 {fresh} 篇（累计已解析 {done} 篇）并落盘", flush=True)
    # 输出 CSV + INDEX.md
    fields = ["title","authors","date","source","page","doi","cited","primary","tags","summary",
              "abstract","pdf_path","txt_path","pages","cats","arxiv_id","key","status"]
    with open(os.path.join(ROOT, "catalog.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["标题","作者","日期","来源站","原始链接","DOI","被引数","分类","标签",
                                       "总结","AI精读","AI评级","简介(摘要)","论文原文(本地PDF)","全文txt","页数","arXiv类目","arXivID","主键","状态"])
        for r in recs:
            w.writerow([r.get("title",""), "; ".join(r.get("authors",[])[:6]), r.get("date",""), r.get("source",""),
                r.get("page",""), r.get("doi",""), r.get("cited",""), r.get("primary",""),
                "、".join(sorted((r.get("tags") or {}), key=lambda k:-r.get("tags",{}).get(k,0))[:5]),
                r.get("summary",""), r.get("ai_summary",""), r.get("ai_grade",""),
                (r.get("abstract","") or "")[:800], r.get("pdf_path",""), r.get("txt_path",""),
                r.get("pages",""), "; ".join(r.get("cats",[])[:6]), r.get("arxiv_id",""), r.get("key",""), r.get("status","")])
    groups = {}
    for r in recs:
        if r.get("status") == "done" and not r.get("offtopic"):
            groups.setdefault(r.get("primary", FALLBACK), []).append(r)
    n_off = sum(1 for r in recs if r.get("offtopic"))
    lines = ["# 量化因子论文库 · 总索引", "",
             f"更新: {time.strftime('%Y-%m-%d %H:%M')} ｜ 入库 {sum(len(v) for v in groups.values())} 篇 ｜ "
             f"分类 {len(groups)} 个 ｜ 非金融剔除 {n_off} 篇（见 _剔除-非金融/） ｜ 全字段表见 catalog.csv，全文在 text/", ""]
    for cat in _ORDER:
        if cat not in groups: continue
        lines.append(f"## {cat}（{len(groups[cat])} 篇）\n")
        for r in sorted(groups[cat], key=lambda x: x.get("date",""), reverse=True):
            au = (r["authors"][0] + " 等 " + str(len(r["authors"])) + "人") if len(r.get("authors",[]))>1 else \
                 (r["authors"][0] if r.get("authors") else "-")
            rel = r["pdf_path"].replace("\\", "/")
            i = rel.find("论文/")
            if i >= 0: rel = rel[i + 3:]
            ai = r.get("ai_summary") or ""
            sum_s = (("[AI精读·" + r.get("ai_grade","") + "] " + ai) if ai else r.get("summary",""))[:300]
            lines.append(f"- **{sanitize(r['title'],140)}**  \n  {au} ｜ {r.get('date','-')} ｜ 来源: {r['source']}"
                         + (f" ｜ DOI: {r['doi']}" if r.get('doi') else "") +
                         f" ｜ {r.get('pages','?')}页  \n  简介: {(r.get('abstract') or '')[:180]}…  \n"
                         f"  总结: {sum_s}  \n"
                         f"  [原文PDF](<{rel}>) ｜ [全文txt](<text/{os.path.basename(r.get('txt_path',''))}>) ｜ [来源链接]({r['page']})")
        lines.append("")
    open(os.path.join(ROOT, "INDEX.md"), "w", encoding="utf-8").write("\n".join(lines))
    recs = save_catalog(recs, orig)
    print(f"索引完成: {done} 篇解析, {len(groups)} 个分类 -> {ROOT}/INDEX.md")

def cmd_status():
    # 全新数据根目录还没有台账，status 是新用户第一条命令，不该直接抛栈
    if not os.path.exists(CATLOG):
        print("台账还不存在: %s" % CATLOG)
        print("先跑一次 catalog:  python quant_library.py catalog")
        return
    recs = json.load(open(CATLOG, encoding="utf-8"))
    from collections import Counter
    print("总数:", len(recs))
    print(Counter(r.get("status") for r in recs))
    print(Counter(r.get("primary") for r in recs if r.get("status") == "done"))

def _parse_flags(args):
    """harvest 可选参数: --workers N  --interval S  --attempts N  --new-only  --no-screen"""
    kw, i = {}, 0
    while i < len(args):
        a = args[i]
        if a == "--new-only":
            kw["new_only"] = True; i += 1
        elif a == "--no-screen":
            kw["screen"] = False; i += 1
        elif a in ("--workers", "--interval", "--attempts"):
            key = a[2:].replace("-", "_")
            kw[key] = int(args[i + 1]) if key in ("workers", "attempts") else float(args[i + 1])
            i += 2
        else:
            i += 1
    return kw

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "harvest":
        cmd_harvest(**_parse_flags(sys.argv[2:]))
    else:
        {"catalog": cmd_catalog, "index": cmd_index, "status": cmd_status}[cmd]()
