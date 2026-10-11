"""Request-local deadlines and guards; no database or model state is shared."""
import queue
import re
import threading
import time

import sqlglot
from sqlglot import exp


def is_greeting(question):
    text = re.sub(r"[\s，。！？,.!?~～]", "", question).lower()
    return text in {"你好", "您好", "你好呀", "你好啊", "嗨", "哈喽", "hello", "hi", "早上好", "下午好", "晚上好", "谢谢", "谢谢你"}


def require_read_query(sql, dialect=None):
    statements = sqlglot.parse(sql, read=dialect)
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("只允许单条只读查询")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command, exp.Into, exp.Lock)
    if any(isinstance(node, forbidden) for node in statements[0].walk()):
        raise ValueError("查询包含不允许的写入或锁定操作")


def bounded_stream(factory, deadline):
    """Only the model producer runs in this thread; it can never execute SQL.

    The consumer stops at an absolute deadline even if upstream stalls before
    its first token. Socket timeout also bounds the producer's resource lifetime.
    """
    messages = queue.Queue(maxsize=16)
    stopped = threading.Event()

    def put(item):
        while not stopped.is_set():
            try:
                messages.put(item, timeout=.1)
                return
            except queue.Full:
                pass

    def produce():
        stream = None
        try:
            stream = factory()
            for chunk in stream:
                if stopped.is_set():
                    break
                put(("chunk", chunk))
        except Exception as exc:
            put(("error", exc))
        finally:
            if stream is not None and hasattr(stream, "close"):
                stream.close()
            put(("end", None))

    threading.Thread(target=produce, daemon=True, name="sqlbot-model-stream").start()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("模型处理超时，请稍后重试；未开始的查询已取消")
            try:
                kind, value = messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("模型处理超时，请稍后重试；未开始的查询已取消") from None
            if kind == "end":
                return
            if kind == "error":
                raise value
            yield value
    finally:
        stopped.set()
