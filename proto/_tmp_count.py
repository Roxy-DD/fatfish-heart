import re
src = open(r"proto\iterate.py", encoding="utf-8").read()
m = re.search(r"REGRESSION_SUITE = \[(.*?)\n\]", src, re.S)
entries = re.findall(r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"', m.group(1))
print("entries:", len(entries))
for n, f in entries: print("  ", n, "->", f)
