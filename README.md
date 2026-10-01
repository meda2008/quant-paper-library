# quant-paper-library

量化因子论文的自动建库 + AI 精读流水线。三段幂等（`catalog` → `harvest` → `index`），
外加一条 AI 精读接力线。全程只用**免登录**的公开渠道。

这个仓库是**可直接移植**的：数据根目录走环境变量 `PAPER_ROOT`，不含任何机器相关的
硬编码路径、账号或密钥。实测跑在 Windows + NAS（WebDAV）上，也可指向本地目录。

---

## 快速开始

```bash
pip install -r requirements.txt

export PAPER_ROOT=/your/data/paper_store        # Windows: set PAPER_ROOT=D:\paper_store
mkdir -p "$PAPER_ROOT"

python pipelines/quant_library.py catalog       # 检索候选，写 catalog.json
python pipelines/quant_library.py harvest --workers 2 --interval 2.0
python pipelines/quant_library.py index          # 抽全文 → 分类 → 归档 → 建索引
python pipelines/quant_library.py status         # 看当前台账
```

根目录按这个优先级解析：`PAPER_ROOT` 环境变量 → 脚本同目录的 `.paper_root` 文件 → `./paper_store`。
中间那层是给已有部署留的——定时任务和命令行不一定设了环境变量，放一个只写路径的
`.paper_root` 就能指回既有的库。默认值是相对路径，所以忘设环境变量的那一轮会在仓库里
新建一个空 `paper_store`，台账看起来像被清空了（`test_root_resolution` 守这个）。

数据根目录会长成：

```
<ROOT>/
  _inbox/            下载暂存（index 消化后清空）
  text/              抽取出的全文 txt
  catalog.json       候选 + 状态台账（幂等主键是 key）
  _bak/              写盘前的滚动快照（保留 8 份）
  INDEX.md           中文总索引，按 14 个分类
  catalog.csv        全字段表
  <分类>/             分类后的 PDF
  _剔除-非金融/        没过金融门槛的淘汰项
```

## 命令

### `quant_library.py` — 主引擎

| 子命令 | 作用 | 关键参数 |
| --- | --- | --- |
| `catalog` | 检索候选并**并集**合并进台账 | `OA_PAGES`、`NBER_PAGES` |
| `harvest` | 下载 PDF 到 `_inbox`，验 `%PDF-` 头才落盘 | `--workers`、`--interval`、`--new-only`、`--attempts` |
| `index` | 抽全文 → 金融门槛 → 分类 → 归档 → 重建 INDEX.md/CSV | 无 |
| `status` | 打印状态分布与分类分布 | 无 |

常用环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PAPER_ROOT` | 见下 | 数据根目录 |
| `MIN_FREE_GB` | `3.0` | 剩余空间低于此值 harvest 主动停手 |
| `OA_PAGES` | `1` | OpenAlex 每词条翻几页 |
| `NBER_PAGES` | `1` | NBER 每词条翻几页 |

### `deepread.py` — 精读选题与合流

```bash
python pipelines/deepread.py prepare 420     # 出队：<ROOT>/deepread_queue.jsonl
python pipelines/deepread.py merge <结果.jsonl> [更多结果.jsonl ...]
```

`merge` 的文件参数**可以直接传多个**，一次合流一整批。

### 其余脚本

| 脚本 | 作用 |
| --- | --- |
| `dupmark.py` | 同文异 key 归并（默认 dry-run，`--apply` 才写） |
| `check_state.py` | 台账一致性体检（路径失效、误归类、孤儿文件） |
| `lib_snapshot.py` | 打印台账基线，用于算增量 |
| `abstract_backfill.py` | 从全文回填缺失摘要 |
| `rescue_gate.py` | 门槛误杀的语义捞回 |
| `purge_semantic.py` | 撞词误召回的语义清污（带 manifest，可还原） |

---

## AI 精读接力线

精读不是脚本能做的，需要 agent 读全文取证。流程是固定的五步：

```
1. prepare      出队，切成 N 篇一批的 JSONL
2. 切片分发      每个 agent 一个 <ROOT>/deepread_<批次>_q<组>.jsonl
3. agent 精读    按 prompts/read_brief.md 的契约，写 <ROOT>/_res_<批次>_q<组>.jsonl
4. 逐组核盘      对照键集 + 空评级哨兵，确认没漏没多没编
5. merge 合流    deepread.py merge，然后 dupmark + index 重建
```

切片每行的字段：`key`（输出必须原样回填）、`title`、`txt`（**全文 txt 的绝对路径**）、
`abstract`、`primary`、`date`、`state`。

结果每行：

```json
{"key": "...", "ai_summary": "≤150字中文，含正文原始数字",
 "ai_grade": "A|B|C|D", "ai_findings": ["≤3条"]}
```

评级口径、三查红线、各分类专项留红都写在 **[`prompts/read_brief.md`](prompts/read_brief.md)**，
那是给 agent 的完整契约，直接连同任务描述一起发出去即可。

### 派发时的三条硬规矩

这三条是踩过坑总结的，别省：

1. **探针先行**。额度敏感的 agent（配额、并发位、速率限制），先发一个最小切片验证，
   确认可用再铺开。一次铺十几个会撞 "Queuing failed"。
2. **并发控制在 6-7 以内**。再高就开始排队失败，配额也烧得快。
3. **逐组核盘再放下一波**。每组结果对照切片键集，确认无缺、无多、无重复、**空评级为 0**。
   报告里的行数要回盘核验，不采信自报。

`merge` 本身会打印「空评级 N 篇」哨兵——**N 不为 0 立刻查字段名**（历史上踩过：
worker 写 `ai_grade`，merge 只认 `grade`，评级被静默写成空而它照样报"合并完成"）。

---

## 在别的 agent 里调用

流水线本身是纯命令行，任何能执行 shell 的 agent 都能跑。精读那半边需要一个能读文件、
能写文件的子 agent，把 `prompts/read_brief.md` 作为契约附在 prompt 里即可。

最小调用示例：

```bash
export PAPER_ROOT=/data/papers
cd pipelines
python quant_library.py catalog
python quant_library.py harvest --new-only --workers 2 --interval 2.0
python quant_library.py index
python deepread.py prepare 420
# → 把 deepread_queue.jsonl 切片分给 N 个 agent，附上 prompts/read_brief.md
python deepread.py merge ../<ROOT>/_res_*.jsonl
python dupmark.py --apply
python quant_library.py index
```

---

## 测试

```bash
cd tests && for t in test_*.py; do python $t; done
```

六个套件，全部离线、全在临时目录里跑，不碰真实数据：

| 套件 | 守的是什么 |
| --- | --- |
| `test_catalog_merge` | 台账并集合并、并发写入不被旧副本盖掉、**离线护栏** |
| `test_locks` | 跨进程锁互斥、活进程占用、死锁可接管、陈旧锁可接管 |
| `test_index_run` | index 增量解析、落盘节流 |
| `test_sweep_cap` | arXiv 单查询 2000 封顶的探测与分片 |
| `test_rel_score` | 撞词过滤（matting alpha、AlphaZero、Sharpe 人名…） |
| `test_harvest_persist` | harvest 状态持久化、**空间闸真会停** |
| `test_root_resolution` | 根目录解析优先级——防"忘设环境变量就写进一个新建的空库" |

`test_catalog_merge` 里有一段白名单护栏：`quant_library` 新增检索腿而测试没打桩时
会当场断言失败，而不是安静地去联网。2026-09-30 和 10-01 各被它抓到过一次真实外呼。

---

## 设计要点

值得单独说明的几条，都是踩坑换来的：

- **台账只做并集，永不重建**。每次 `catalog` 用新检索结果覆盖 catalog.json 会让本次
  未命中的历史条目凭空消失（PDF 还在盘上但掉出索引）。
- **写盘前重读 + 字段级叠加**。`save_catalog` 只把本进程真正改过的字段叠回磁盘版本，
  这样抓取链和精读合流可以并发跑，不会互相覆盖。
- **空间闸**。写大 JSON、下载 PDF 前先量剩余空间；NAS 快满时 `os.replace` 是服务端拷贝，
  拷到一半没地方落会把台账**撕裂**。宁可停手也不能把账写坏。
- **下载用 `curl.exe`**，Python 的 urllib 被 arXiv 按 TLS 指纹拦（403/406）。
- **退让只对真限流**。429/503/截断才指数退让；非 arXiv 域的 rc=22 是永久墙（订阅墙/验证墙），
  快失败快 giveup——否则几百条死链每条烧 45 秒。
- **门槛分级**。`STRONG_GATE` 判断"是不是金融"（故意放宽），正文兜底，
  三档 `strong/body/fail`，只有 fail 才移出。收紧判据时"正文兜底 + 人工抽检"缺一必误杀。

更多实战坑见 [`docs/PITFALLS.md`](docs/PITFALLS.md)。

## 已知限制

- 需要 `curl.exe`（Windows 自带；Linux/macOS 需装 curl 或改 `_curl`）。
- 全文抽取依赖 PyMuPDF，扫描版 PDF（纯图片）抽不出文本，会被判 D。
- OpenAlex 匿名接口有每 IP 每日预算，用尽会回 429（约 UTC 零点重置）。
- 金融门槛是**规则**判定，撞词误召回（portfolio/factor/selection 在非金融语境里）
  只能靠语义清污 `purge_semantic.py` 处理，机械门槛拦不住。

## 数据源

arXiv API、OpenAlex（匿名）、OpenReview、NBER 工作论文——均为免登录公开端点。
不含 SSRN / 知网等需要登录或订阅的渠道。
