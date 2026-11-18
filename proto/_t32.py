import re
src = open("iterate.py", encoding="utf-8").read()
i = src.find("def run_regression")
print(src[i:i+2600])
