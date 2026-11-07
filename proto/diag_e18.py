"""诊断 E-17 与 E-18 的基线差异（0.8962 vs 0.8781）"""
import os, sys
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np
from retina import get_features
from run_e17_star_control import build_with, l2
from run_e18_perclass import build as build18, Idx

Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
mu = Ftr[:9000].mean(axis=0)
sd = Ftr[:9000].std(axis=0) + 1e-6
zsc = lambda F: l2((F - mu) / sd)

print("=" * 84)
print("E-17 build_with vs E-18 build 的对比")
print("=" * 84)
print(f"{'seed':<6}{'E17 stars':<12}{'E17 macro':<11}{'E18 stars':<12}{'E18 macro':<11}")
print("-" * 84)
for seed in [0, 1, 2, 3, 4]:
    ix17, tests17 = build_with(Ftr, ytr, seed, zsc, 0.705)
    a17 = {t: ix17.accuracy(zsc(tests17[t][0]), tests17[t][1]) for t in "ABC"}
    m17 = float(np.mean(list(a17.values())))

    ix18, tests18 = build18(Ftr, ytr, seed, zsc, 0.705)
    mp = {u: 3 for u in ix18.uniq}
    a18 = {t: ix18.accuracy(zsc(tests18[t][0]), tests18[t][1], mp) for t in "ABC"}
    m18 = float(np.mean(list(a18.values())))

    print(f"{seed:<6}{ix17.M.shape[0]:<12}{m17:<11.4f}{ix18.M.shape[0]:<12}{m18:<11.4f}")

print()
print("E-17 汇总(seeds 0-4) 应 = 0.8962")
print("E-18 汇总(seeds 1-4) 应 = 0.8817/0.9033/0.8492 → 0.8781")
