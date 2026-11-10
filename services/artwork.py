"""Bounded Suno artwork downloads and a local original-image cache. No catalog/UI."""
import hashlib
from http.client import HTTPException
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ArtworkError(Exception):
    pass


def _validate_url(url):
    if not isinstance(url, str):
        raise ArtworkError('No song artwork URL is available.')
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ArtworkError('The song artwork URL is invalid.') from exc
    if (parsed.scheme != 'https' or parsed.hostname not in ('cdn1.suno.ai', 'cdn2.suno.ai')
            or parsed.username or parsed.password or port not in (None, 443)):
        raise ArtworkError('The song artwork URL is not a supported Suno image address.')


class _ArtworkRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        _validate_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


class ArtworkService:
    """Preserve source bytes; resizing/thumbnails are intentionally left for later."""
    def __init__(self, directory: Path, *, timeout=15):
        self.directory = Path(directory).resolve()
        self.timeout = timeout
        self._opener = build_opener(_ArtworkRedirects())
        self._lock = Lock()

    def path(self, filename):
        if not isinstance(filename, str) or not re.fullmatch(r'[0-9a-f]{64}\.(?:jpg|png|webp)', filename):
            return None
        path = (self.directory / filename).resolve()
        return path if path.parent == self.directory and path.is_file() else None

    def fetch(self, url):
        """Return an opaque cache filename; repeated URLs reuse the original file."""
        _validate_url(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        with self._lock:
            for extension in ('jpg', 'png', 'webp'):
                if self.path(key + '.' + extension):
                    return key + '.' + extension
            temporary = None
            try:
                request = Request(url, headers={'User-Agent': 'KernRadio/0.1 (song artwork)', 'Accept': 'image/jpeg,image/png,image/webp'})
                with self._opener.open(request, timeout=self.timeout) as response:
                    _validate_url(response.geturl())
                    data = response.read(10_000_001)
                if len(data) > 10_000_000:
                    raise ArtworkError('The song artwork exceeds the 10 MB image limit.')
                if data.startswith(b'\xff\xd8\xff'):
                    extension = 'jpg'
                elif data.startswith(b'\x89PNG\r\n\x1a\n'):
                    extension = 'png'
                elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
                    extension = 'webp'
                else:
                    raise ArtworkError('Suno did not return a supported JPEG, PNG, or WebP image.')
                self.directory.mkdir(parents=True, exist_ok=True)
                filename = key + '.' + extension
                with NamedTemporaryFile(dir=self.directory, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(data)
                temporary.replace(self.directory / filename)
                return filename
            except (URLError, OSError, HTTPException) as exc:
                raise ArtworkError('The song artwork could not be saved. You can try fetching it again later.') from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
