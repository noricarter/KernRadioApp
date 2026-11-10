"""Crucible Radio's public M4A/guest-rights/AES playback preparation, isolated."""
import base64
from dataclasses import dataclass
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services.suno import SunoMetadataError, SunoMetadataService


class PlaybackError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedAudio:
    suno_song_id: str
    source: Path | str


class SunoAudioResolver:
    def __init__(self):
        self._temporary = TemporaryDirectory(prefix='kern-suno-audio-')
        self._directory = Path(self._temporary.name)
        self._identities = SunoMetadataService()

    @staticmethod
    def _read(url, *, body=None, timeout=15):
        headers = {'User-Agent': 'Mozilla/5.0'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=headers)
        with urlopen(request, timeout=timeout) as response:
            return response.read()

    @staticmethod
    def _decrypt(rights, encrypted, clip_id):
        guest_key = hashlib.sha256(str(rights['glt']).encode('utf-8')).digest()
        key_blob = base64.b64decode(rights['key'])
        iv_blob = base64.b64decode(rights['iv'])
        aes_gcm = AESGCM(guest_key)
        content_key = aes_gcm.decrypt(key_blob[:12], key_blob[12:], clip_id.encode('utf-8'))
        counter = aes_gcm.decrypt(iv_blob[:12], iv_blob[12:], clip_id.encode('utf-8'))
        decryptor = Cipher(algorithms.AES(content_key), modes.CTR(counter)).decryptor()
        audio = decryptor.update(encrypted) + decryptor.finalize()
        if b'ftyp' not in audio[:64]:
            raise PlaybackError('Suno did not return a playable M4A file.')
        return audio

    def resolve(self, song_id_or_url):
        try:
            song_id = self._identities.resolve_song_id(song_id_or_url)
            cached = self._directory / f'{song_id}.m4a'
            if cached.exists():
                return ResolvedAudio(song_id, cached)
            page = self._read(f'https://suno.com/song/{song_id}').decode('utf-8')
            # Same preference and media patterns as Crucible Radio main.py.
            media = re.search(r'(https://[^"\\]+\.m4a)', page)
            mp3 = re.search(r'(https://cdn\d+\.suno\.ai/[^"\\]+\.mp3)', page)
            if media:
                try:
                    self._prepare_m4a(media[1], cached)
                    return ResolvedAudio(song_id, cached)
                except (PlaybackError, URLError, OSError, HTTPException, ValueError, KeyError, InvalidTag):
                    if mp3 is None:
                        raise
            if mp3:
                return ResolvedAudio(song_id, mp3[1])
            raise PlaybackError('No playable audio was available on this Suno page.')
        except PlaybackError:
            raise
        except (URLError, OSError, HTTPException, ValueError, KeyError, InvalidTag, SunoMetadataError) as exc:
            raise PlaybackError('Suno audio could not be prepared. The song may be unavailable or public playback access may have changed.') from exc

    def _prepare_m4a(self, media_url, cached):
        clip_id = re.search(r'([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})', media_url, re.I)
        if clip_id is None:
            raise PlaybackError('Suno audio did not include a clip identifier.')
        clip_id = clip_id[1].lower()
        body = json.dumps({'content_params': {'content_id': clip_id, 'content_type': 'clip'}}).encode()
        rights = json.loads(self._read('https://studio-api.prod.suno.com/api/mango/rights', body=body))
        encrypted = self._read(media_url, timeout=30)
        audio = self._decrypt(rights, encrypted, clip_id)
        temporary = cached.with_suffix('.tmp')
        temporary.write_bytes(audio)
        temporary.replace(cached)

    def close(self):
        self._temporary.cleanup()
