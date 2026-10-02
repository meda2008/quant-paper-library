# -*- coding: utf-8 -*-
"""扫描件 OCR 兜底的端到端测试：造一个"只有图片、没有文本层"的真 PDF，
确认 extract_pdf_text 走 OCR 把字认回来。没装 tesseract 的机器上整组跳过，
不算失败——OCR 是兜底能力，不是硬依赖。"""
import os, shutil, sys, tempfile
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

if not q._ocr_engine():
    print("SKIP  这台机器既没有 RapidOCR 也没有可用 tesseract，OCR 兜底测试跳过（不算失败）")
    print("\nRESULT: ALL PASS")
    sys.exit(0)
print("OCR 后端:", "RapidOCR" if q._OCR_ENGINE != "tesseract" else "tesseract")

TEXT = ("Momentum and value factors in the cross-section of stock returns. "
        "We build a diversified momentum portfolio and test factor investing "
        "on A-share equities while examining transaction costs and Sharpe ratio. ")

tmp = tempfile.mkdtemp(prefix="ocrtest_")

# 1) 先做一页有文本层的 PDF，再把它渲染成位图，塞进另一本新 PDF —— 得到"纯扫描件"
#    页面尺寸按真实论文用 Letter(612x792)，别用缩小过的怪尺寸：
#    上次拿 1/4 缩放页测，字被挤成一团，OCR 把词边界吃掉了，误判成兜底能力不行。
src = os.path.join(tmp, "textlayer.pdf")
LETTER = (612, 792)
d = fitz.open(); p = d.new_page(width=LETTER[0], height=LETTER[1])
y = 120
for line in [TEXT[i:i + 60] for i in range(0, len(TEXT), 60)]:
    p.insert_text((70, y), line, fontsize=13); y += 30
d.save(src); d.close()

pix = fitz.open(src)[0].get_pixmap(dpi=200)
png = os.path.join(tmp, "scan.png")
pix.save(png)
scan = os.path.join(tmp, "scanonly.pdf")
d2 = fitz.open(); pg = d2.new_page(width=LETTER[0], height=LETTER[1])
pg.insert_image(pg.rect, filename=png)
d2.save(scan); d2.close()

# 2) 确认这本"扫描件"确实抽不出文本层（否则测不到 OCR 分支）
dd = fitz.open(scan)
bare = "\n".join(x.get_text() for x in dd)
imgs = sum(len(x.get_images(full=True)) for x in dd)
npages = dd.page_count
dd.close()
chk(len(bare.strip()) < 50, "扫描件确实没有文本层（抽到 %d 字）" % len(bare.strip()))
chk(imgs >= npages, "扫描件每页都有整页图像（图 %d / 页 %d）" % (imgs, npages))

# 3) 走抽取器：应当自动落进 OCR 分支并把字认回来
full, pages, used = q.extract_pdf_text(scan)
chk(used is True, "抽不到文本层时确实走了 OCR 兜底")
low = full.lower()
chk("momentum" in low and "portfolio" in low, "OCR 认回了正文关键词")
chk(len(full.strip()) > 60, "OCR 输出长度合理（%d 字）" % len(full.strip()))
chk(pages == npages, "页数照常返回（%d）" % pages)

# 4) 有文本层的正常 PDF 不许触发 OCR（否则每篇都渲染位图，index 会被拖垮）
full2, pages2, used2 = q.extract_pdf_text(src)
chk(used2 is False, "有文本层的不触发 OCR（省掉整轮渲染）")
chk("momentum" in full2.lower(), "有文本层的照常直接抽到字")

# 5) 既没字也没图的空 PDF：OCR 不该崩，返回原样即可
empty = os.path.join(tmp, "empty.pdf")
d3 = fitz.open(); d3.new_page(); d3.save(empty); d3.close()
try:
    full3, pages3, used3 = q.extract_pdf_text(empty)
    chk(True, "空白 PDF 不抛异常（长度 %d，ocr=%s）" % (len(full3.strip()), used3))
except Exception as e:
    chk(False, "空白 PDF 抛异常 -> " + repr(e)[:80])

# 6) 两个 OCR 后端都不可用时必须静默降级，绝不能把 index 带崩
q._OCR_ENGINE, q._TESS = "", ""
try:
    full4, pages4, used4 = q.extract_pdf_text(scan)
    chk(used4 is False and len(full4.strip()) < 50, "没有 OCR 后端时降级为空结果、不崩")
except Exception as e:
    chk(False, "没有 OCR 后端时抛异常 -> " + repr(e)[:80])

shutil.rmtree(tmp, ignore_errors=True)
print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
