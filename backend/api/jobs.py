"""Prevent concurrent generators from overwriting one novel's chapter state."""
import threading

_active = set()
_lock = threading.Lock()


def is_writing(novel_id):
    with _lock:
        return novel_id in _active


async def stream_job(novel_id, generator):
    with _lock:
        accepted = novel_id not in _active
        if accepted:
            _active.add(novel_id)
    if not accepted:
        await generator.aclose()
        yield {"type": "error", "message": "这部作品已有生成任务，请等待当前章节完成"}
        return
    try:
        async for event in generator:
            yield event
    finally:
        try:
            await generator.aclose()
        finally:
            with _lock:
                _active.discard(novel_id)
