"""Atomic local configuration storage, independent of web and agent services."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock


class ConfigurationError(Exception):
    pass


class ConfigurationService:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        try:
            self._settings = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(self._settings, dict):
                raise ValueError('Expected an object')
        except (OSError, ValueError) as exc:
            raise ConfigurationError('Saved setup could not be read. Please restore the configuration file.') from exc

    def get(self, key, default=None):
        with self._lock:
            return self._settings.get(key, default)

    def update(self, **settings):
        with self._lock:
            updated = self._settings | settings
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as file:
                    temporary = Path(file.name)
                    json.dump(updated, file, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                temporary.replace(self.path)
                self._settings = updated
            except OSError as exc:
                raise ConfigurationError('Setup could not be saved. Check that local storage is writable.') from exc
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

    @property
    def setup_completed(self):
        return self.get('setup_completed', False) is True
