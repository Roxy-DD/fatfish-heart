"""
诊断：E-8 的三个漏洞
 1. 基线 max=0.8533，但 E-1 同配置为 0.6767 —— 为什么差这么多？
 2. density 变体归零 —— alpha 量级是否错了（log1p 后量级 vs cos 0.7）
 3. zscore 变体崩 —— 分数尺度与别类不可比
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))
import numpy as np
from retina import get_features
from variant import VBrain

TASKS = {"A": {"0","1","2"}, "B": {"3","4","5"}, "C": {"6","7","8","9"}}

def split(F, y, labels, ntr, nte, seed=0):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i,lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr+nte]], y[idx[ntr:ntr+nte]]

Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

print("="*80)
print("1) 为什么 E-8 基线 0.8533 ≠ E-1 的 0.6767")
print("="*80)
b = VBrain(Ftr.shape[1], threshold=0.705, mode="max")
tests = {}
for name in ["A","B","C"]:
    Xtr,ytr_t,Xte,yte_t = split(Ftr,ytr,TASKS[name],900,300)
    tests[name]=(Xte,yte_t)
    for xi,yi in zip(Xtr,ytr_t): b.memorize(xi,yi)
    ret = {k: b.evaluate(*v) for k,v in tests.items()}
    print(f"  学完{name}: A={ret.get('A',0):.4f} B={ret.get('B',0):.4f} C={ret.get('C',0):.4f} "
          f"星={b.g.n} created={b.n_created} reinforced={b.n_reinforced} mitosis={b.n_mitosis}")

print("\n  对照 E-1 的 core.py 实现（同样的切片）:")
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))
from core import CorpusCallosum, evaluate as core_eval
b2 = CorpusCallosum(threshold=0.705, seed=0)
tests2={}
for name in ["A","B","C"]:
    Xtr,ytr_t,Xte,yte_t = split(Ftr,ytr,TASKS[name],900,300)
    tests2[name]=(Xte,yte_t)
    for xi,yi in zip(Xtr,ytr_t): b2.memorize(xi,yi)
    ret={k: core_eval(b2,*v)["acc"] for k,v in tests2.items()}
    print(f"  学完{name}: A={ret.get('A',0):.4f} B={ret.get('B',0):.4f} C={ret.get('C',0):.4f} "
          f"星={len(b2.all_stars())} root={len(b2.right.galaxy)}")

print("\n  差异来源分析:")
print("    core.py: 命中则'合并向量'（原地改 mass/vector），不新建")
print("    variant: 命中则'合并向量'（我的实现），但分裂阈值不同？")
print(f"    core created={b2.right.n_created} reinforced={b2.right.n_reinforced} mitosis={b2.right.n_mitosis}")
print(f"    var  created={b.n_created} reinforced={b.n_reinforced} mitosis={b.n_mitosis}")

print("\n" + "="*80)
print("2) density 变体的量级问题")
print("="*80)
sizes = {lid: len(v) for lid,v in b.g.idx_by_id.items() if v}
print("  各类星数:", {b.g.id_to_name[k]: v for k,v in sizes.items()})
x = Fte[0]
for lid in list(b.g.idx_by_id)[:10]:
    s = b.g.M[b.g._masks[lid]] @ x if lid in b.g._masks else None
    b.g._ensure(lid); s = b.g.M[b.g._masks[lid]] @ x
    n = len(b.g._masks[lid])
    raw = float(s.max())
    print(f"    {b.g.id_to_name[lid]:>3} n={n:4d} max={raw:.4f} "
          f"den(a=0.5)={raw-0.5*np.log1p(n):+.4f} "
          f"den(a=1.0)={raw-1.0*np.log1p(n):+.4f}")
print("  → log1p(n) 在 n=100 时约 4.6，variance 远大于 cos 的 0.1 量级")
print("  → alpha 必须 ~0.01 量级，不是 0.5/1.0")

print("\n" + "="*80)
print("3) zscore 尺度问题")
print("="*80)
for lid in list(b.g.idx_by_id)[:8]:
    b.g._ensure(lid); s = b.g.M[b.g._masks[lid]] @ x
    print(f"    {b.g.id_to_name[lid]:>3} n={len(s):4d} mean={s.mean():.4f} std={s.std():.4f} "
          f"zscore={(s.max()-s.mean())/(s.std()+1e-6):+.4f}")
print("  → zscore 对大类趋于 0（分布宽），对小类可到 3+，与 cos 量级不可比")
print("  → 需要先候选筛选（取 max 前 N 类），再在候选内比 zscore")
