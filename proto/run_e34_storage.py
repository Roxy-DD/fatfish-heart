"""
E-34 存储选型实测：JSON+npy vs SQLite BLOB vs SQLite SQ8

我们当前的存储是「JSON 元数据 + npz 向量」。
文献说 SQLite BLOB 零拷贝、自带 FTS5、WAL 并发，但要用我们自己的数据验证。

测四个量（这才是选型的依据）：
  ① 建库时间（写入 N 条）
  ② 磁盘占用
  ③ 冷启动加载（打开即可查询的时间）
  ④ 随机单条读取延迟
  ⑤ 全量 top-k 扫描延迟（查询主路径）

两种维度：
  128 维（我们的 HOG）
  384 维（典型文本 embedding，为将来的文本记忆做准备）
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

WORK = os.path.join(_HERE, "work", "e34")
RESULTS = os.path.join(_HERE, "results")
os.makedirs(WORK, exist_ok=True)


def make_data(n, dim, n_class=10, seed=0):
    rng = np.random.default_rng(seed)
    c = rng.normal(size=(n_class, dim)).astype(np.float32)
    c /= np.linalg.norm(c, axis=1, keepdims=True)
    lab = rng.integers(0, n_class, n)
    V = c[lab] + rng.normal(0, 0.35, (n, dim)).astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    return V.astype(np.float32), lab.astype(np.int32)


# ---------- 方案 A：JSON + npy（我们现在的做法） ----------
def bench_json_npy(V, L, tag):
    base = os.path.join(WORK, f"{tag}_json")
    d = os.path.dirname(base)
    os.makedirs(d, exist_ok=True)

    t0 = time.time()
    meta = [{"id": i, "label": int(L[i]), "mass": 1} for i in range(len(L))]
    with open(base + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    np.savez_compressed(base + ".vec.npz", V=V)
    write_s = time.time() - t0

    size = os.path.getsize(base + ".meta.json") + os.path.getsize(base + ".vec.npz")

    # 冷启动：读 meta + 读向量
    t0 = time.time()
    with open(base + ".meta.json", "r", encoding="utf-8") as f:
        _ = json.load(f)
    z = np.load(base + ".vec.npz")
    M = z["V"]
    load_s = time.time() - t0

    # 随机单条读（从内存数组里取，这是 npy 的真实路径）
    idx = np.random.default_rng(0).integers(0, len(M), 2000)
    t0 = time.time()
    for i in idx:
        _ = M[i]
    rand_us = (time.time() - t0) / 2000 * 1e6

    # 全量扫描
    q = M[0]
    t0 = time.time()
    for _ in range(20):
        s = M @ q
        _ = np.argmax(s)
    scan_ms = (time.time() - t0) / 20 * 1000

    return {"scheme": "json+npy", "dim": int(V.shape[1]), "n": len(L),
            "write_s": round(write_s, 3), "size_mb": round(size / 1024 / 1024, 2),
            "load_s": round(load_s, 4), "rand_read_us": round(rand_us, 1),
            "scan_ms": round(scan_ms, 3)}


# ---------- 方案 B：SQLite BLOB (float32) ----------
def bench_sqlite_f32(V, L, tag):
    path = os.path.join(WORK, f"{tag}_f32.db")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    t0 = time.time()
    con.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, label INTEGER, mass INTEGER, vec BLOB)")
    con.executemany("INSERT INTO mem (id,label,mass,vec) VALUES (?,?,?,?)",
                    [(i, int(L[i]), 1, V[i].tobytes()) for i in range(len(L))])
    con.commit()
    write_s = time.time() - t0

    size = os.path.getsize(path)
    con.close()

    # 冷启动：打开 + 预热（只取 count，不读全部）
    t0 = time.time()
    con = sqlite3.connect(path)
    _ = con.execute("SELECT COUNT(*) FROM mem").fetchone()
    load_s = time.time() - t0

    # 随机单条读
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(L), 2000)
    t0 = time.time()
    for i in idx:
        b = con.execute("SELECT vec FROM mem WHERE id=?", (int(i),)).fetchone()[0]
        _ = np.frombuffer(b, dtype=np.float32)
    rand_us = (time.time() - t0) / 2000 * 1e6

    # 全量扫描（把全部 BLOB 读出来拼成矩阵 —— SQLite 的真实用法）
    t0 = time.time()
    rows = con.execute("SELECT vec FROM mem ORDER BY id").fetchall()
    M = np.frombuffer(b"".join(r[0] for r in rows), dtype=np.float32).reshape(len(rows), V.shape[1])
    fullread_s = time.time() - t0
    q = M[0]
    t0 = time.time()
    for _ in range(20):
        s = M @ q
        _ = np.argmax(s)
    scan_ms = (time.time() - t0) / 20 * 1000
    con.close()

    return {"scheme": "sqlite-f32", "dim": int(V.shape[1]), "n": len(L),
            "write_s": round(write_s, 3), "size_mb": round(size / 1024 / 1024, 2),
            "load_s": round(load_s, 4), "rand_read_us": round(rand_us, 1),
            "scan_ms": round(scan_ms, 3), "fullread_s": round(fullread_s, 4)}


# ---------- 方案 C：SQLite BLOB (int8 = SQ8) ----------
def bench_sqlite_sq8(V, L, tag):
    lo = V.min(axis=0)
    hi = V.max(axis=0)
    scale = (hi - lo) / 255.0
    scale[scale == 0] = 1e-9
    Q = np.clip((V - lo) / scale, 0, 255).astype(np.uint8)

    path = os.path.join(WORK, f"{tag}_sq8.db")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    t0 = time.time()
    con.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, label INTEGER, mass INTEGER, q BLOB)")
    con.execute("CREATE TABLE quant (dim INTEGER PRIMARY KEY, lo BLOB, scale BLOB)")
    con.execute("INSERT INTO quant VALUES (?,?,?)", (int(V.shape[1]), lo.tobytes(), scale.tobytes()))
    con.executemany("INSERT INTO mem (id,label,mass,q) VALUES (?,?,?,?)",
                    [(i, int(L[i]), 1, Q[i].tobytes()) for i in range(len(L))])
    con.commit()
    write_s = time.time() - t0
    size = os.path.getsize(path)
    con.close()

    t0 = time.time()
    con = sqlite3.connect(path)
    _ = con.execute("SELECT COUNT(*) FROM mem").fetchone()
    load_s = time.time() - t0

    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(L), 2000)
    t0 = time.time()
    for i in idx:
        b = con.execute("SELECT q FROM mem WHERE id=?", (int(i),)).fetchone()[0]
        _ = np.frombuffer(b, dtype=np.uint8)
    rand_us = (time.time() - t0) / 2000 * 1e6

    t0 = time.time()
    rows = con.execute("SELECT q FROM mem ORDER BY id").fetchall()
    Qm = np.frombuffer(b"".join(r[0] for r in rows), dtype=np.uint8).reshape(len(rows), V.shape[1])
    fullread_s = time.time() - t0
    q = Qm[0]
    t0 = time.time()
    for _ in range(20):
        s = Qm.astype(np.int16) @ q.astype(np.int16)
        _ = np.argmax(s)
    scan_ms = (time.time() - t0) / 20 * 1000
    con.close()

    return {"scheme": "sqlite-sq8", "dim": int(V.shape[1]), "n": len(L),
            "write_s": round(write_s, 3), "size_mb": round(size / 1024 / 1024, 2),
            "load_s": round(load_s, 4), "rand_read_us": round(rand_us, 1),
            "scan_ms": round(scan_ms, 3), "fullread_s": round(fullread_s, 4)}


def main():
    rows = []
    for dim in (128, 384):
        for n in (20000, 100000):
            V, L = make_data(n, dim)
            tag = f"d{dim}_n{n}"
            print(f"\n--- dim={dim} n={n} ---")
            for fn in (bench_json_npy, bench_sqlite_f32, bench_sqlite_sq8):
                r = fn(V, L, tag)
                rows.append(r)
                extra = f" fullread={r.get('fullread_s','-')}s" if "fullread_s" in r else ""
                print(f"  {r['scheme']:<12} write={r['write_s']:>7.2f}s  "
                      f"size={r['size_mb']:>7.2f}MB  load={r['load_s']:>7.4f}s  "
                      f"rand={r['rand_read_us']:>8.1f}us  scan={r['scan_ms']:>8.2f}ms{extra}")

    print("\n" + "=" * 96)
    print("汇总")
    print("=" * 96)
    print(f"{'维度':<6}{'N':<8}{'方案':<14}{'写s':<9}{'MB':<9}{'冷启s':<9}{'随机读us':<11}{'扫描ms':<10}")
    print("-" * 96)
    for r in rows:
        print(f"{r['dim']:<6}{r['n']:<8}{r['scheme']:<14}{r['write_s']:<9.2f}"
              f"{r['size_mb']:<9.2f}{r['load_s']:<9.4f}{r['rand_read_us']:<11.1f}"
              f"{r['scan_ms']:<10.2f}")

    with open(os.path.join(RESULTS, "e34_storage.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-34", "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e34_storage.json')}")

    # 清理大文件
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
