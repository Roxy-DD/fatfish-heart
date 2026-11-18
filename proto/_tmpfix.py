"""临时诊断助手：把 tempfile.mkdtemp 换成默认权限建目录的版本。

原因：本会话的 Windows 文件沙箱下，mkdtemp 用 mode=0o700 建出的目录，
后续对该目录的写入一律 PermissionError（sqlite 也打不开）。
这里改成 os.makedirs(..., mode=0o777)，只为判断"回归失败是不是环境造成"。
"""
import os
import tempfile
import uuid


def _mkdtemp(suffix="", prefix="tmp", dir=None):
    d = dir or tempfile.gettempdir()
    name = os.path.join(d, (prefix or "tmp") + uuid.uuid4().hex[:12] + (suffix or ""))
    os.makedirs(name, mode=0o777, exist_ok=True)
    return name


tempfile.mkdtemp = _mkdtemp
