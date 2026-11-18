import json
d = json.load(open("work/last_regression.json", encoding="utf-8"))
items = d.get("items", d)
for k,v in items.items():
    print(f'{k:<12} pass={v.get("pass")}  {str(v.get("summary"))[:60]}')
print("all_pass", d.get("all_pass"))
