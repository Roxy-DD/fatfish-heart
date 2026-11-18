import json
d = json.load(open("work/last_regression.json", encoding="utf-8"))
items = d.get("items", d)
for k in ("PS1编码","灾难恢复演练","README声称","版本谱系","分支状态","R2指纹"):
    v = items.get(k, {})
    print("="*20, k, v.get("pass"))
    print((v.get("output") or "")[-700:])
