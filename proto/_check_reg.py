import json
p=r"work/last_regression.json"
d=json.load(open(p,encoding="utf-8"))
print(type(d), list(d)[:20] if isinstance(d,dict) else len(d))
print(json.dumps(d, ensure_ascii=False)[:1500])
