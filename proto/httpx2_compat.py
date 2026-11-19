"""httpx2 + brotlicffi 兼容补丁

════════════════════════════════════════════════════════════
精确根因（已定位到源码行）
════════════════════════════════════════════════════════════
`httpx2/_decoders.py` BrotliDecoder.decode() 第 192 行：
    decompressed = self._decompress(data, output_buffer_limit=output_buffer_limit)

其中 `self._decompress` 指向：
    brotlicffi.Decompressor.decompress(self, data)   ← 只接受 1 个参数

签名不兼容 → 所有 Brotli 压缩的响应解码失败：
    TypeError: Decompressor.decompress() got an unexpected
               keyword argument 'output_buffer_limit'

这是 httpx2 2.16.x 的自身 bug（它假设 brotli 支持该参数）。
已实测：升级 httpx2 / huggingface_hub 均无效。

════════════════════════════════════════════════════════════
修法
════════════════════════════════════════════════════════════
包住 BrotliDecoder 实例的 `_decompress`，丢弃不认识的 kwargs。

必须在导入 huggingface_hub / datasets / mteb 之前 import。

用法：
    import httpx2_compat  # noqa: F401  ← 最顶部
"""
import functools

_status = "failed"


def _patch():
    from httpx2 import _decoders

    cls = _decoders.BrotliDecoder
    orig_init = cls.__init__

    if getattr(orig_init, "_ffh_patched", False):
        return "already"

    @functools.wraps(orig_init)
    def __init__(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        # 原 __init__ 会设置 self._decompress，这里换掉
        raw = self._decompress

        @functools.wraps(raw) if hasattr(raw, "__name__") else (lambda f: f)
        def safe(data, *a, **kw):
            kw.pop("output_buffer_limit", None)
            kw.pop("max_length", None)
            return raw(data, *a, **kw)

        self._decompress = safe

    __init__._ffh_patched = True
    cls.__init__ = __init__
    return "patched"


try:
    _status = _patch()
except Exception as _e:
    _status = f"failed: {type(_e).__name__}: {_e}"


def status():
    return _status


if __name__ == "__main__":
    print(f"  补丁状态: {_status}")
    # 自测：构造 BrotliDecoder 并模拟 httpx2 的调用
    try:
        from httpx2._decoders import BrotliDecoder
        import brotlicffi
        raw = brotlicffi.compress(b"hello world" * 1000)
        d = BrotliDecoder()
        out = b"".join(d.decode(raw))
        print(f"  ✅ 解压成功 {len(out)} 字节")
    except Exception as e:
        print(f"  ❌ 自测失败: {type(e).__name__}: {e}")
