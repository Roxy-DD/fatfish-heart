"""
rerank_v1.py —— cross-encoder 精排模块（ONNX int8，用 onnxruntime 直接加载）

不用 sentence_transformers / optimum，直接用 onnxruntime InferenceSession。

bge-reranker ONNX 的输入：
  input_ids, attention_mask, token_type_ids
输出：
  logits（shape [batch, 1]）

用 tokenizer 把 [query, passage] 对编码，喂给 ONNX，sigmoid 得分数。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

# E-206 实测：int8 在 GPU 上回落 CPU（1830ms），fp16 才是 GPU 原生（52ms，快 35 倍）
DEFAULT_MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models", "bge-reranker-v2-m3-fp16")


class CrossEncoderReranker:
    def __init__(self, model_dir=DEFAULT_MODEL):
        self.model_dir = model_dir
        self._tok = None
        self._sess = None

    def _load(self):
        if self._sess is not None:
            return
        from transformers import AutoTokenizer
        import onnxruntime as ort

        self._tok = AutoTokenizer.from_pretrained(self.model_dir)
        model_path = os.path.join(self.model_dir, "model.onnx")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"缺少 {model_path}")
        # 限制线程，避免 CPU 争抢
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        # E-201 实测：有 GPU 时优先 CUDAExecutionProvider（RTX 3060）
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if "CUDAExecutionProvider" in ort.get_available_providers()
                     else ["CPUExecutionProvider"])
        self._sess = ort.InferenceSession(
            model_path, sess_options=opts, providers=providers)

    def score(self, query, passages):
        """批量打分。query: str, passages: list[str]。返回 list[float] (0~1)"""
        self._load()
        pairs = [[query, p] for p in passages]
        enc = self._tok(
            pairs, padding=True, truncation=True,
            max_length=512, return_tensors="np")

        inputs = {}
        if "input_ids" in enc:
            inputs["input_ids"] = enc["input_ids"].astype(np.int64)
        if "attention_mask" in enc:
            inputs["attention_mask"] = enc["attention_mask"].astype(np.int64)
        if "token_type_ids" in enc:
            inputs["token_type_ids"] = enc["token_type_ids"].astype(np.int64)

        logits = self._sess.run(None, inputs)[0]   # [batch, 1]
        probs = 1.0 / (1.0 + np.exp(-logits[:, 0]))  # sigmoid
        return [float(x) for x in probs]

    def rerank(self, query, passages, top_k=10):
        scores = self.score(query, passages)
        order = np.argsort(-np.array(scores))[:top_k]
        return [int(i) for i in order], [scores[i] for i in order]


if __name__ == "__main__":
    rk = CrossEncoderReranker()
    q = "When did Melanie paint a sunrise?"
    cands = [
        "I painted that lake sunrise last year! It's special to me.",
        "You'd be a great counselor! Your empathy will help people.",
        "I went to a pottery class yesterday, it was fun.",
    ]
    scores = rk.score(q, cands)
    for c, s in zip(cands, scores):
        print(f"  {s:.4f}  {c[:60]}")
