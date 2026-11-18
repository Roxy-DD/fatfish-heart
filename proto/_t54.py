import os, re
# E-530: 找到 0 个 .ps1 —— 是不是因为脚本被移到 D:\itsaplan-docs 了
src = open("check-ps1-bom.mjs", encoding="utf-8").read()
m = re.findall(r"glob|readdir|scripts|\.ps1|ROOT|walk", src)
print("mentions:", sorted(set(m)))
i = src.find("scripts")
print(src[max(0,i-600):i+400])
