# -*- coding: utf-8 -*-
"""坏档判定 + OCR 兜底的门禁测试（2026-10-11 加）。

要守的行为（都是当天在全库普查上踩出来的）：
  1) **有文本层但内容是垃圾**的 PDF 必须走 OCR —— 老条件只看"抽出来字数不够"，
     位移字体/符号字体坏档每页都有几千字，一律放行，于是坏档一路活到被 AI 读手判级。
     实测后果：4 个已判级（w7169/w7170/w7409/w8744）打在乱码正文上。
  2) **非拉丁正文不许拖进 OCR**（中文/俄文/日文论文抽取是正常的）；但 R1"控制字符占比 >0.05"
     要走在非拉丁放行之前 —— w8259 是"45% 西里尔 + 65% 控制符"的混排坏档，顺序错了就漏。
  3) **数学论文的下标字体也带 ~1.3% 控制字符**，但虚词密度正常，不能误判 —— 误判一次的代价
     是对着 34 页好论文白烧一遍 OCR（实测约 10 分钟/50 页）。
  4) 预算用尽 / 没有 OCR 后端 / OCR 认回来还不如原样 —— 三种情况都必须静默保留原文本层，不崩。
  5) `OCR_GARBLED=0` 能整体关掉这条新路径。
"""
import os, sys, io, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q
import fitz

ok = True
def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)

TRUE = ("momentum and value factors in the cross section of stock returns we build a "
        "diversified portfolio and test factor investing on A share equities while "
        "examining transaction costs and the sharpe ratio after fees the sample covers "
        "delistings and the universe is constructed point in time so survivorship bias "
        "is avoided we thank seminar participants for useful comments on earlier drafts ")
TRUE = (TRUE * 12)[:4000]
assert q._stop_per_k(TRUE) > 15, q._stop_per_k(TRUE)      # 阳性对照：这就当"读得通"的基准


def shift_up(s, d=3):
    return "".join(chr(ord(c) + d) if 0x24 <= ord(c) <= 0x7E else c for c in s)


SHIFT3 = shift_up(TRUE)
CHINESE = ("本文研究了动量因子在中国A股市场的有效性。我们构建了多因子模型，"
           "并在控制交易成本之后检验 portfolios 的净夏普比率。样本期内包含退市公司。") * 20
MATHLIKE = TRUE[:1200] + "".join("\x08" if i % 73 == 0 else c for i, c in enumerate(TRUE[1200:3600]))
CYR_CTRL = ("Социальное обеспечение и эквивалентность модели " + "\x01\x02\x03" * 900)[:3000]

print("== 单元层：_is_garbled 的三条判据与两处放行 ==")
chk(q._is_garbled(SHIFT3) is True, "位移+3 坏档判成坏档（虚词/千字 %.2f）" % q._stop_per_k(SHIFT3))
chk(q._is_garbled(TRUE) is False, "正常英文正文不误判（虚词/千字 %.2f）" % q._stop_per_k(TRUE))
chk(q._is_garbled(CHINESE) is False, "中文正文放行（非拉丁占比 %.3f）" % q._nonlatin_share(CHINESE))
chk(q._is_garbled(MATHLIKE) is False,
    "数学页的下标字体污染不误判：ctrl %.4f 虚词 %.2f" % (q._ctrl_ratio(MATHLIKE), q._stop_per_k(MATHLIKE)))
chk(q._is_garbled(CYR_CTRL) is True,
    "西里尔+控制符混排仍判坏档（ctrl %.3f 非拉丁 %.2f）" % (q._ctrl_ratio(CYR_CTRL), q._nonlatin_share(CYR_CTRL)))
chk(q._is_garbled("too short " * 10) is False, "正文过短（<200 字）不判坏档，交给别的分支")
chk(q._is_garbled("") is False, "空文本不判坏档、不抛异常")
chk(q._shifted_gain(SHIFT3) == 3, "位移搜索能认出偏移=3（认出 %s）" % q._shifted_gain(SHIFT3))
chk(q._shifted_gain(TRUE) == 0, "正常文本没有可用位移（不虚报）")

tmp = tempfile.mkdtemp(prefix="garbletest_")
LETTER = (612, 792)
NPG = 3                       # 单页放不下一页正文；OCR_MIN_CHARS=2000，文本层必须真的够长，
                              # 否则测的是"扫描件"老分支而不是坏档分支（上一版就栽在这）


def page_lines(s, i):
    per = len(s) // NPG
    seg = s[i * per:(i + 1) * per]
    return [seg[j:j + 66] for j in range(0, len(seg), 66)]


def make_pdf(path, visible=None, hidden=None, images=None):
    """images=列表：每页先垫一张位图（模拟"看得见真字"）；visible/hidden 用文字叠上去。
    hidden 用白色 = 抽得到、渲染看不见，正好复刻"字体编码坏了但页面是好的"这类坏档。"""
    d = fitz.open()
    for i in range(NPG):
        p = d.new_page(width=LETTER[0], height=LETTER[1])
        if images:
            p.insert_image(p.rect, filename=images[i])
        y = 100
        if visible:
            for line in page_lines(visible, i)[:24]:
                p.insert_text((60, y), line, fontsize=11)
                y += 24
        y = 100
        if hidden:
            for line in page_lines(hidden, i)[:24]:
                p.insert_text((60, y), line, fontsize=11, color=(1, 1, 1))
                y += 24
    d.save(path)
    d.close()


def layer(path):
    d = fitz.open(path)
    t = "\n".join(x.get_text() for x in d)
    d.close()
    return t


# 先做一本"看得见真字"的 PDF，逐页渲染成位图，再把乱码文本层以白色盖在上面
vis = os.path.join(tmp, "vis.pdf")
make_pdf(vis, visible=TRUE)
pngs = []
d = fitz.open(vis)
for i, pg in enumerate(d):
    fp = os.path.join(tmp, "v%d.png" % i)
    pg.get_pixmap(dpi=200).save(fp)
    pngs.append(fp)
d.close()
garbled_pdf = os.path.join(tmp, "garbled.pdf")
make_pdf(garbled_pdf, images=pngs, hidden=SHIFT3)
clean_pdf = os.path.join(tmp, "clean.pdf")
make_pdf(clean_pdf, visible=TRUE)          # 正常 PDF：有可读文本层，不该进任何 OCR 分支

gl = layer(garbled_pdf)
chk(len(gl.strip()) > q.OCR_MIN_CHARS,
    "坏档 PDF 的文本层有 %d 字（>OCR_MIN_CHARS=%d）——老条件会直接放行" % (
        len(gl.strip()), q.OCR_MIN_CHARS))
chk(q._is_garbled(gl) is True, "坏档 PDF 的文本层被判定为坏档")
chk(q._is_garbled(layer(clean_pdf)) is False, "正常 PDF 的文本层判为读得通")

have_ocr = bool(q._ocr_engine())
print("\n== 端到端：坏档应当落进 OCR 并把字认回来 ==")
if not have_ocr:
    print("SKIP  这台机器没有 RapidOCR 也没有可用 tesseract，端到端部分跳过（不算失败）")
else:
    q._OCR_GARBLED_USED[0] = 0
    q.OCR_GARBLED_MAX_PAGES = 400
    full, npg, used = q.extract_pdf_text(garbled_pdf)
    chk(used is True, "坏档 PDF 走了 OCR（used_ocr=%s）" % used)
    chk(q._stop_per_k(full) > 15, "OCR 后虚词/千字 %.1f，读通了" % q._stop_per_k(full))
    chk("momentum" in full.lower() and "portfolio" in full.lower(), "关键实体词认回来了")

    before = q._OCR_GARBLED_USED[0]
    full2, npg2, used2 = q.extract_pdf_text(clean_pdf)
    chk(used2 is False, "正常 PDF 不进坏档分支")
    chk(q._OCR_GARBLED_USED[0] == before, "正常 PDF 一页 OCR 都没烧（计数器没动）")

    q._OCR_GARBLED_USED[0] = 10 ** 6
    full3, npg3, used3 = q.extract_pdf_text(garbled_pdf)
    chk(used3 is False and q._is_garbled(full3) is True,
        "预算用尽时保留坏档原文、不崩（后面由普查复查）")
    q._OCR_GARBLED_USED[0] = 0

    os.environ["OCR_GARBLED"] = "0"
    full4, npg4, used4 = q.extract_pdf_text(garbled_pdf)
    chk(used4 is False, "OCR_GARBLED=0 能整体关掉这条新路径")
    del os.environ["OCR_GARBLED"]

shutil.rmtree(tmp, ignore_errors=True)
print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
