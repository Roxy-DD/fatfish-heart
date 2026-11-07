"""测量 search_scoped 各阶段真实延迟，找出注入层 4 秒从哪来"""
import json, subprocess, sys, os, time

d = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem"
db = os.path.join(d, "dsh_mem.db")

env = {**os.environ, "PYTHONIOENCODING": "utf-8", "KMP_DUPLICATE_LIB_OK": "TRUE",
       "HF_HOME": r"C:\Users\xuee\Desktop\肥鱼之心\.hf"}

proc = subprocess.Popen([sys.executable, "-u", os.path.join(d, "kernel_server.py"), db],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8",
                        bufsize=1, env=env)

# 等预热完成
time.sleep(12)


def call(i, cmd, payload):
    t0 = time.time()
    proc.stdin.write(json.dumps({"id": i, "cmd": cmd, "payload": payload},
                                ensure_ascii=False) + "\n")
    proc.stdin.flush()
    r = json.loads(proc.stdout.readline())
    return r, (time.time() - t0) * 1000


print("=" * 74)
print("各操作真实延迟（稳态）")
print("=" * 74)

q = "以实验数据为准"

for i in range(3):
    r, dt = call(10 + i, "search", {"query": q, "top": 5, "scoped": False})
    print(f"  search (普通)      #{i+1}: {dt:7.1f} ms  hits={len(r.get('results',[]))}")

for i in range(3):
    r, dt = call(20 + i, "search", {"query": q, "top": 5, "scoped": True})
    print(f"  search (scoped)    #{i+1}: {dt:7.1f} ms  hits={len(r.get('results',[]))}")

for i in range(2):
    r, dt = call(30 + i, "search", {"query": "", "top": 3, "fixed_slots": 3, "min_cost": 0.7})
    print(f"  search (固定席位)  #{i+1}: {dt:7.1f} ms  hits={len(r.get('results',[]))}")

for i in range(2):
    r, dt = call(40 + i, "add", {"text": f"性能测试条目 {i} 号 abcxyz{i}"})
    print(f"  add                #{i+1}: {dt:7.1f} ms  deduped={r.get('deduped',False)}")

r, dt = call(50, "stats", {})
print(f"  stats                  : {dt:7.1f} ms  total={r.get('total')}")

proc.stdin.close()
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()
