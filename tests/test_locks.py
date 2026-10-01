# -*- coding: utf-8 -*-
"""跨进程锁的行为测试（全在临时目录里跑，不碰 Z 盘、不碰正在运行的 harvest）。
验证四件事：互斥、活进程占用、死进程可接管、超过 6 小时的陈旧锁可接管。"""
import os, sys, tempfile, time, subprocess
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库布局是 tests/ 与 pipelines/ 平级；本地旧布局是脚本与测试同目录。两种都认。
PIPELINE_DIR = os.path.join(os.path.dirname(HERE), "pipelines")
PIPELINE_DIR = PIPELINE_DIR if os.path.isdir(PIPELINE_DIR) else HERE
sys.path.insert(0, PIPELINE_DIR)
import quant_library as q

tmp = tempfile.mkdtemp(prefix="locktest_")
q.ROOT = tmp
ok = True


def chk(cond, msg):
    global ok
    print(("PASS  " if cond else "FAIL  ") + msg)
    ok = ok and bool(cond)


chk(q._acquire("harvest") is True, "首次申请锁成功")
chk(q._acquire("harvest") is True, "同进程重入：pid 就是自己时允许接管（不会自锁死）")

# 另起一个进程申请同一把锁：当前测试进程还活着 -> 必须失败
child = subprocess.run(
    [sys.executable, "-c",
     "import sys,os;sys.path.insert(0,%r);import quant_library as q;q.ROOT=%r;"
     "print('ACQ',q._acquire('harvest'))" % (PIPELINE_DIR, tmp)],
    capture_output=True, timeout=60)
out = (child.stdout + child.stderr).decode("utf-8", errors="replace")
chk("ACQ False" in out, "另一个活着的进程也拿不到锁：%s" % out.strip().replace("\n", " ")[:110])

q._release("harvest")
chk(not os.path.exists(q._lock_path("harvest")), "release 删除锁文件")

# 伪造一个"进程号已死"的锁 -> 必须能接管
with open(q._lock_path("harvest"), "w", encoding="utf-8") as f:
    f.write("999999 %f" % time.time())
chk(q._acquire("harvest") is True, "持锁进程已死 -> 接管成功")
q._release("harvest")

# 伪造一个"进程号活着但锁超过 6 小时"的 -> 必须能接管（防链条被 kill 后卡死）
victim = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
with open(q._lock_path("harvest"), "w", encoding="utf-8") as f:
    f.write("%d %f" % (victim.pid, time.time() - 7 * 3600))
chk(q._acquire("harvest") is True, "锁龄超 6 小时即使是活进程也接管（陈旧锁不留坟）")
q._release("harvest")
victim.kill()

with open(q._lock_path("catalog"), "w", encoding="utf-8") as f:
    f.write("nonsense-not-a-pid")
chk(q._acquire("catalog") is True, "锁文件内容损坏时仍能接管并覆写")
q._release("catalog")

# pid 被 Windows 回收给别的活进程：只看 pid_exists 会误判"有人在跑"，必须靠启动时刻识破
squatter = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
with open(q._lock_path("harvest"), "w", encoding="utf-8") as f:
    f.write("%d %f %f" % (squatter.pid, time.time(), time.time() - 9999))
chk(q._acquire("harvest") is True, "锁里 pid 活着但启动时刻对不上 -> 判为死锁接管（pid 复用不误跳过）")
q._release("harvest")
squatter.kill()

with open(q._lock_path("harvest"), "w", encoding="utf-8") as f:
    f.write("%d %f %f" % (os.getpid(), time.time(), q._proc_start(os.getpid())))
chk(q._acquire("harvest") is True, "本进程自己持锁时重入允许（不自锁死）")
q._release("harvest")

# ---------- _beat：长任务续心跳，三条都不能越权 ----------
lp = q._lock_path("index")
if os.path.exists(lp):
    os.remove(lp)
chk(q._beat("index") is False, "没持锁时 _beat 返回 False")
chk(not os.path.exists(lp), "_beat 绝不凭空造锁（否则会把别人的阶段锁住）")

other = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
with open(lp, "w", encoding="utf-8") as f:
    f.write("%d %f" % (other.pid, time.time() - 100))
chk(q._beat("index") is False, "锁是别人持的 -> _beat 不改写")
pid_after, ts_after = open(lp, encoding="utf-8").read().split()[:2]
chk(int(pid_after) == other.pid and abs(float(ts_after) - (time.time() - 100)) < 5,
    "_beat 没碰别人的锁内容（pid/时间戳原样）")
other.kill()
# 伪造锁留下的 pid 可能已被 Windows 复用给别的活进程，_acquire 会合理地拒绝接管，
# 所以这里手动清掉自己造的假锁，再测"本进程持锁时 _beat 生效"
if os.path.exists(lp):
    os.remove(lp)

chk(q._acquire("index") is True, "_beat 前置：本进程拿到 index 锁")
time.sleep(0.05)
chk(q._beat("index") is True, "自己持锁时 _beat 返回 True")
pid_now, ts_now = open(lp, encoding="utf-8").read().split()[:2]
chk(int(pid_now) == os.getpid() and float(ts_now) > time.time() - 60,
    "_beat 把锁时间戳刷新到现在（跑超 6 小时不会被当死锁抢走）")
q._release("index")
chk(not os.path.exists(lp), "release 后 index 锁清除")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
