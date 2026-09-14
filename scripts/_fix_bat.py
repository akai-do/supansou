# -*- coding: utf-8 -*-
"""修复 build.bat 中被退格符损坏的路径。"""
p = r"D:\A0000-caixaioq\200副业\06-我不是盘神\DuPanSou-Archive\native\build.bat"
data = open(p, "rb").read()
bad = b"%HOOKSRC%\x08ooster_hook.cpp"
good = b"%HOOKSRC%\\booster_hook.cpp"
if bad in data:
    data = data.replace(bad, good)
    print("fixed:", data.count(good))
elif good in data:
    print("already ok:", data.count(good))
else:
    print("pattern not found; dump lines:")
    for line in data.splitlines():
        if b"booster_hook" in line or b"hook.cpp" in line:
            print(repr(line[:160]))
open(p, "wb").write(data)
