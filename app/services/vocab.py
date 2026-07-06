import json
from pathlib import Path

_vocab = None
_VOCAB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "vocab.json"


def load_vocab():
    global _vocab
    if _vocab is None:
        with open(_VOCAB_PATH, encoding="utf-8") as f:
            _vocab = json.load(f)
    return _vocab
