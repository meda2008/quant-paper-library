# -*- coding: utf-8 -*-
"""数据根目录解析优先级测试。

要守的事故：把默认根目录从一个硬编码的绝对路径改成相对 paper_store 之后，定时任务和
daily_update.cmd 都没设 PAPER_ROOT —— 于是 03:00 那一轮会在仓库里新建一个空的
paper_store 而不是写既有的库，台账看起来"清零了"，等于把整个库弄丢。
所以优先级必须是：PAPER_ROOT 环境变量 > 脚本同目录 .paper_root 文件 > ./paper_store。

用真代码测：把 quant_library.py 复制进临时目录，连同 .paper_root 一起，
再在子进程里打印它解析出的 ROOT（子进程干净环境，不受本进程的 PAPER_ROOT 干扰）。
"""
import os, shutil, subprocess, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = _PKG if os.path.isdir(_PKG) else HERE
SRC = os.path.join(PIPELINE_DIR, "quant_library.py")

ok = True


def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)


def resolve_in(tmp, env_extra=None, with_script_root_file=None):
    """在 tmp 里放一份 quant_library.py（可选放 .paper_root），子进程里打印解析结果。"""
    d = tempfile.mkdtemp(prefix="rootcase_", dir=tmp)
    shutil.copy(SRC, os.path.join(d, "quant_library.py"))
    if with_script_root_file is not None:
        open(os.path.join(d, ".paper_root"), "w", encoding="utf-8").write(with_script_root_file)
    env = dict(os.environ)
    env.pop("PAPER_ROOT", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    code = ("import sys;sys.path.insert(0,%r);sys.argv=['x'];"
            "import quant_library as q;print('ROOT='+q.ROOT)" % d)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=180, cwd=d)
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    got = None
    for ln in out.splitlines():
        if ln.startswith("ROOT="):
            got = ln[5:].strip()
    return got, out


tmp = tempfile.mkdtemp(prefix="rootresolve_")

# 1) 环境变量最高优先，且能覆盖 .paper_root
got, out = resolve_in(tmp, {"PAPER_ROOT": "D:/from_env"}, "Z:/from_file")
chk(got == "D:/from_env", "PAPER_ROOT 环境变量优先于 .paper_root 文件（实际 %r）" % got)

# 2) 没有环境变量时用脚本同目录的 .paper_root —— 这是既有部署不改命令行也能指回原库的关键
got, out = resolve_in(tmp, None, "Z:/from_file")
chk(got == "Z:/from_file", "无环境变量时读脚本同目录 .paper_root（实际 %r）" % got)

# 3) 两者都没有才退到相对 paper_store（全新用户）
got, out = resolve_in(tmp, None, None)
chk(got == "paper_store", "都没有时退到 paper_store（实际 %r）" % got)

# 4) .paper_root 是空行/空白时不能被当成有效路径（否则 ROOT='' 会把数据写到当前目录）
got, out = resolve_in(tmp, None, "   \n  ")
chk(got == "paper_store", ".paper_root 内容为空白时忽略它（实际 %r）" % got)

# 5) .paper_root 读不了（权限/中断）不能抛栈，要静默退到默认
bad = os.path.join(tmp, "unreadable")
os.makedirs(bad, exist_ok=True)
shutil.copy(SRC, os.path.join(bad, "quant_library.py"))
cfg = os.path.join(bad, ".paper_root")
open(cfg, "w").write("Z:/x")
os.chmod(cfg, 0)          # Windows 上 chmod 只改只读位，够制造一次读失败或不——两种都不许崩
env = dict(os.environ); env.pop("PAPER_ROOT", None); env["PYTHONIOENCODING"] = "utf-8"
r = subprocess.run([sys.executable, "-c",
                    "import sys;sys.path.insert(0,%r);sys.argv=['x'];"
                    "import quant_library as q;print('ROOT='+q.ROOT)" % bad],
                   capture_output=True, env=env, timeout=180, cwd=bad)
out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
chk(r.returncode == 0, ".paper_root 异常不应崩进程（rc=%s, %s）" % (r.returncode, out.strip().replace("\n", " ")[:70]))
os.chmod(cfg, 0o600)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
