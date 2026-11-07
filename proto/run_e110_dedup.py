"""验证去重是否生效"""
import json, subprocess, sys, os

d = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem"
db = os.path.join(d, "e110_dedup.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

env = {**os.environ, "PYTHONIOENCODING": "utf-8", "KMP_DUPLICATE_LIB_OK": "TRUE",
       "HF_HOME": r"C:\Users\xuee\Desktop\肥鱼之心\.hf"}

proc = subprocess.Popen([sys.executable, "-u", os.path.join(d, "kernel_server.py"), db],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8",
                        bufsize=1, env=env)


def call(i, cmd, payload):
    proc.stdin.write(json.dumps({"id": i, "cmd": cmd, "payload": payload},
                                ensure_ascii=False) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


print("=" * 70)
print("E-110 写入去重验证")
print("=" * 70)

t1 = "我电脑的登录密码是 8848"
r1 = call(1, "add", {"text": t1})
print(f"\n第1次写入: ep_id={r1.get('ep_id')} deduped={r1.get('deduped', False)}")

r2 = call(2, "add", {"text": t1})
print(f"第2次写入: ep_id={r2.get('ep_id')} deduped={r2.get('deduped', False)}")
print(f"  → {'✅ 成功去重' if r2.get('deduped') else '❌ 未去重'}")

# 不同文本应正常写入
r3 = call(3, "add", {"text": "家里的门锁密码是 3721"})
print(f"\n不同内容: ep_id={r3.get('ep_id')} deduped={r3.get('deduped', False)}")
print(f"  → {'✅ 正常写入' if not r3.get('deduped') else '❌ 误判重复'}")

# 带空格差异（归一化后应判定为同一句）
r4 = call(4, "add", {"text": "我电脑的登录密码是  8848"})
print(f"\n空格差异: deduped={r4.get('deduped', False)}")
print(f"  → {'✅ 归一化去重生效' if r4.get('deduped') else '（未归一化匹配）'}")

s = call(5, "stats", {})
print(f"\n最终条数: {s.get('total')}（预期 2 条：密码8848 + 门锁3721）")

proc.stdin.close()
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()
