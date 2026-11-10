"""Small timed text pages for a character dialogue display. No radio/Flask/AI."""
from threading import RLock
import time
import uuid


class DialogueService:
    def __init__(self, *, page_characters=180, characters_per_second=40, words_per_second=3,
                 minimum_seconds=4, clock=time.monotonic):
        self._size, self._cps, self._wps = page_characters, characters_per_second, words_per_second
        self._minimum, self._clock = minimum_seconds, clock
        self._lock = RLock()
        self._pages, self._index, self._id = [], 0, None
        self._started = 0

    def show(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 600:
            raise ValueError('Host dialogue must contain 1–600 characters.')
        remaining, pages = text.strip(), []
        while len(remaining) > self._size:
            split = remaining.rfind(' ', 0, self._size + 1)
            split = split if split > self._size // 2 else self._size
            pages.append(remaining[:split])
            remaining = remaining[split:].lstrip()
        pages.append(remaining)
        with self._lock:
            self._pages, self._index, self._id = pages, 0, uuid.uuid4().hex
            self._started = self._clock()
            return self._id

    def _duration(self, text):
        return max(self._minimum, len(text) / self._cps + len(text.split()) / self._wps + 1)

    def advance(self):
        """Advance an elapsed page; return True after the final reading interval."""
        with self._lock:
            if not self._pages:
                return True
            while self._clock() - self._started >= self._duration(self._pages[self._index]):
                if self._index == len(self._pages) - 1:
                    return True
                self._started += self._duration(self._pages[self._index])
                self._index += 1
            return False

    def snapshot(self):
        with self._lock:
            if not self._pages:
                return None
            text = self._pages[self._index]
            return {'id': self._id, 'page': self._index, 'pages': len(self._pages), 'text': text,
                    'elapsed_seconds': max(0, self._clock() - self._started),
                    'reveal_seconds': len(text) / self._cps}
