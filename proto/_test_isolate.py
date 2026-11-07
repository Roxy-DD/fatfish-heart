"""测试隔离：确保测试永远不碰生产 DB

用法：
    from _test_isolate import test_store
    st = test_store("e999")   # 返回独立 DB 的 store，自动清理
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

_PROD_DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
_TEST_DIR = os.path.join(_HERE, "work", "test_db")


def test_store(name: str, clean: bool = True):
    """创建隔离的测试 store（绝不影响生产库）"""
    from store_v2 import MemoryStore
    os.makedirs(_TEST_DIR, exist_ok=True)
    db = os.path.join(_TEST_DIR, f"{name}.db")
    if clean:
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                os.remove(p)
    st = MemoryStore(db)
    # 双重保险：确认不是生产库
    assert os.path.abspath(st.path) != os.path.abspath(_PROD_DB), \
        "测试不能使用生产库！"
    return st


def cleanup(name: str):
    db = os.path.join(_TEST_DIR, f"{name}.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)
