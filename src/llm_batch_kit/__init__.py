"""Provider batch APIs (half price) without mismatched answers or paying twice.

    from llm_batch_kit import submit, collect, GeminiBatch, parts
"""

from . import manifest, parts
from .batch import Result, collect, collect_pending, submit
from .cost import ledger_hook
from .parts import image, images_of, text, text_of
from .providers import Answer, FakeBatch, GeminiBatch, Status, check_config
from .queue import Queue, close_collected, submit_queued

__all__ = ["submit", "collect", "collect_pending", "Result", "GeminiBatch", "FakeBatch", "Answer", "Status",
           "check_config", "Queue", "submit_queued", "close_collected", "ledger_hook", "manifest", "parts",
           "text", "image", "text_of", "images_of"]
__version__ = "0.1.0"
