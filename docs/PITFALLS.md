# 实战坑

按"会静默出错"的危险程度排序。每条都注明现象、原因和修法。

## 一、会让数据静默丢失或损坏

### catalog 必须是并集，不是重建

用本轮检索结果直接覆盖 catalog.json，会让**本次未被检索命中的历史条目凭空消失**——
PDF 还在盘上，但掉出 INDEX.md，已解析的 `text_done`/`summary`/精读结论全丢，index 每天全量重跑。
修法：`KEEP_ON_MERGE` 白名单 + 旧记录字段回填，保证单调增长。

### 长跑进程拿旧副本写回，会盖掉并发写入

`cmd_catalog` 跑 35 分钟，期间精读 merge 已写入 787 条 AI 结论；catalog 结束时把内存里的
旧副本整份写回，精读全没了。

修法：`save_catalog` 改成**写盘前重读 + 字段级叠加**——只把本进程真正改动过的字段叠回磁盘版本，
并保留别处期间新增的记录。改完抓取链和精读合流就能并发跑。
回归测试：`test_catalog_merge` 里真的模拟了这个交错。

> **教训**：进程跑得越久，它的内存副本越不可信。长跑 + 并发 = 必须字段级叠加。

### 磁盘将满时 `os.replace` 会撕裂大 JSON

NAS/WebDAV 上的 `os.replace` 是**服务端拷贝**。只剩 10GB 而 catalog.json 有 33MB、
拷贝中间还要落临时文件——拷到一半没地方落，目的文件当场半截。

现象极具迷惑性：报 `WinError 32`（文件被占用），重试 8 次全失败。
修法：`MIN_FREE_GB` 空间闸（写盘/下载前先量）+ 原子写自带重试 + 写前备份到 `_bak/`。

### 把 `save_catalog` 的返回值重新绑定，会丢掉 harvest 状态

```python
recs = save_catalog(recs, orig)     # 错：返回的是叠加后的新列表，
                                    # 而 status 是就地改在原列表上的
```

结果 harvest 跑了几个小时，`ok` 计数几百篇，`done` 却纹丝不动。
修法：**永远不要重新绑定**。回归测试 `test_harvest_persist` 专门守这条。

### 空间闸 break 之后 futures 还在跑

`break` 只跳出主循环，`ThreadPoolExecutor` 默认等所有已提交任务完成，
于是主进程"停手"了，后台下载器还在闷头把 PDF 写进 `_inbox`——**日志不再出声，下载一路做到你手动杀进程**。

修法：`ex.shutdown(wait=False, cancel_futures=True)`。
回归测试用忙等桩验证"闸门触发后文件数立刻不再增长"。

## 二、会让结果悄悄失真

### 日志行不能当"上一步完成"的信号

`OA term done` 这类 print 没写 `flush=True`，stdout 重定向到文件时是块缓冲的——
日志滞后于真实进度。盯着"目录完成"grep 会误判成卡死，误以为上一步好了就把下一步放行，
结果下一步读的是合并前的 catalog.json。

修法：**等进程退出，不等日志**。

配套两个 PowerShell 坑：
- `if (Get-Process -Id X ...) { exit 1 }` 的退出码 1 表示**进程还活着**（不是"已退出"）。
- 判活用 `Get-Process -Id X | Select -ExpandProperty Id | grep -q <pid>`。

### OpenAlex 摘要字段名

`abstract_inverted_index`（2026-09-30 实测）——写错会静默拿到空摘要，
表现为"这批论文都没摘要，prepare 全跳过"，看起来像正常现象。

### arXiv 分页总数在 OpenSearch 命名空间

不在 Atom 命名空间里。用 Atom 解析 `totalResults` 会**永远解析不到**，
于是"撞 2000 封顶"这件事完全不可见——而单查询封顶会让 q-fin 全家桶从来没抓全过。
`test_sweep_cap` 守这条。

### 撞词过滤会被语料反咬

`alpha`（AlphaZero、α-淀粉酶、画像 alpha 通道、DAX 重标度指数）、`factor`（消融实验）、
`selection`（自然选择/特征选择）、`sharpe`（人名、陡降）、`momentum`（Adam 优化器动量）、
`portfolio`（算法论文的任务组合、电力机组组合）、`VaR`（葡语增加值）。

更要命的是**参考文献题名注入**：统计词频时命中全来自引用条目，必须先截断 References 段。

反向的例子：`selection` 是裸计数最危险的词（"挑照片的 selection: 28" vs "股票选股: 35"），
必须搭配 stock/equity 才算数，用 ±60 字符邻接窗分开。

### 金融门槛收紧会大面积误杀

第一轮收紧把 Backtesting Expected Shortfall、LIBOR、term structure、多因子风险模型、
预测市场、ESG 特质风险、真精算 mortality 全判成非金融。靠**四轮补词族 + 每轮随机抽 20 条人审**才清零。

> **教训：金融词族远比想象宽。"收紧判据"时"正文兜底 + 人审"缺一必误杀。**

### 机械门槛拦不住语义误召回

清污跑完规则门槛后，仍有 143 篇误召回里 **140 篇的 `gate_verdict` 是 strong**——
标题摘要里确实有 stock/factor/market。只有读过正文的 AI 断言能识别。

### primary 分类在清污前不可用于筛选

挂着"中国A股"的条目里有胆囊结石、宫颈癌筛查（老门槛放的 "risk factor" / "cross-sectional"）。
挂"机器学习选股"的有 UAV 视频异常检测、AlphaZero、假说性矩阵分解。

### PDF 抽取可能整篇损坏

全文几万字符但字母极少、只剩图注（MuPDF 对某些排版解析失败）。
判 D 并标注"需重抽"，别当成"论文没内容"。

## 三、流程与协作

### 同一自动化任务手动 run 后，要确认上一次会话已结束

连续 3 次 `session-failed`（约 18 秒内死于会话启动）看似平台故障，
A/B 对照（同时段手动触发另一任务成功）+ 第 4 次成功起跑才定位到：
**旧执行会话长期滞留，占用同任务槽位挡住新会话**。

### 锁的盲区：加锁前就启动的进程不会持锁

`_acquire` 用 `O_CREAT|O_EXCL`。但**代码改动之前就已启动的进程不会遵守它**。
修法：外部心跳看门狗代持锁（每 100s 重写时间戳，检测到该 pid 退出就删锁）。

判断"加锁是否已保护住当前这一轮"，要看**在跑进程的启动时刻早于还是晚于加锁的代码**。

### kill 只按 PID，绝不按镜像名

`taskkill /IM python.exe` 曾经一次杀掉 5 个 python，其中只有 1 个是自己起的。
先按 CommandLine 筛出目标，再按 PID 杀。

### 收尾时必须回盘核验，不采信自报

子 agent 说"29 篇全部完成"，可能是它数错了、重复写了、或中途掉线后编的。
核验三件套：**键集对照**（缺/多/重复）、**空评级哨兵**、**评级分布**。

### 额度敏感的 agent：探针先行 + 并发 ≤7 + 逐组核盘

一次铺 12+ 个 subagent → "Queuing failed" → 然后 "You've reached your daily usage limit"。
正确形态：先发一个最小切片探针 → 并发控制在 6-7 → 每波结束逐组核盘再放下一波。

### 改了 worker 契约，必须同步改 merge

worker 契约改成写 `ai_grade`，而 merge 只认 `grade` → 评级被静默写成空，
merge 照样打印「合并完成 420 篇」，零报错。
修法：merge 两种字段名都吃 + 「空评级 N 篇」哨兵，N≠0 立刻查。

### prepare 的在途排除 glob 必须覆盖切片实际命名

切片实际叫 `deepread_b<N>_q<K>.jsonl`，而排除 glob 写的是 `deepread_q*.jsonl` → 不匹配 →
把正在精读的 420 篇再排一遍，两份结果互相覆盖。
修法：发车前断言「新队列 ∩ 在途切片 == 0」。

### 测试的白名单护栏：新增可合并字段就加断言

`test_catalog_merge` 原本只打桩 `collect_arxiv`/`collect_openalex`，漏了 `collect_openreview`，
结果测试**真的去爬了 OpenReview 2297 条**，断言全错还白耗一次外呼。
10-01 加了 `collect_nber` 后又犯一次。现已加硬断网 + 检索腿白名单断言。

### 跨盘 `os.replace` 报 errno 18

临时文件必须与目标**同盘**。NAS 场景尤其容易踩。

### SMB 上 mtime 不回显

`getmtime` 可能仍显示旧时间（客户端属性缓存）。
判写入成功看 **size + 读回内容**，别只信 mtime。

### 大 JSON 非原子写会被并发读到半截

下载器"内存收全 → 验 `%PDF-` 头 → 才落盘"的设计就是为了这个。
台账则靠 `_write_json_atomic`（fsync + 自检条数 + 退避重试）。

## 四、OCR 兜底特有的坑

### OCR "没报错"不等于"识别成功"

tesseract 装了，但 `tessdata` 里只有 `chi_sim.traineddata`、**没有 `eng`**：`-l eng` 会
`Failed loading language 'eng' / Could not initialize tesseract`。如果按返回码或异常判断，
会以为"跑了但没结果"，实际是根本没识别成。
OCR 是按页吞掉失败的（不能让一页 OCR 挂掉毁掉整轮 index），所以**唯一的验收办法是看输出长度/字母占比**：
本库用"文本层 < 2000 字才 OCR，且 OCR 结果必须比文本层更长才算生效"，并在记录上打 `ocr=True`。

### OCR 后端优先级要按"真能跑"排，不按"存在"排

探测顺序 RapidOCR > tesseract：RapidOCR 自带中英模型、纯离线；tesseract 依赖语言包齐不齐。
`shutil.which('tesseract')` 找得到 ≠ 能用。

### 合成测试页要用真实页面尺寸

一开始用 `pix.width*0.25` 造"扫描件"，字号被挤小、词边界粘在一起（`andvaluefactorsinthe…`），
OCR 断言因此假失败，误判成兜底能力不行。改成 Letter 612×792 + 13pt + 200dpi 后一次通过。
**测 OCR/解析这类"对图像质量敏感"的路径，测试样张的尺寸和 DPI 要跟真实材料一致。**

## 五、环境相关

- **arXiv 按 TLS 指纹拦 Python**：urllib 一律 403/406，用 `curl.exe` 兜底。
- **OpenAlex 匿名 search 有每 IP 每日预算**，用尽回 429（UTC 零点重置）。
  长跑前查一次，把 NBER 排在 OpenAlex 之前（OpenAlex 挂了还能出一条腿）。
- **OpenReview 验证墙**：`/pdf`、`/attachment`、`api2/pdf`、`/forum` 全 403/307 到 Turnstile，
  换 UA/Referer/Cookie 无效，浏览器内 `fetch` 也 403（WAF 按 `Sec-Fetch-Dest` 区分）。
  唯一过法是真实顶层导航，且可捞回比例只有约 12.5%。
  **根因修法**：`collect_openreview` 不造 pdf_url，note 没有真链接就在 catalog 阶段标 giveup。
  改完产出率从 19%/33%/39% 跳到 **93.4%**。
- **Windows 任务计划与 Qoder cron 不要并存**，会并发写 catalog.json。
- **GBK 控制台**：脚本输出中文要 `PYTHONIOENCODING=utf-8`。
