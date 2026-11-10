"""Error-only application diagnostics with real isolated log files."""
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from unittest import TestCase
from unittest.mock import patch

from radio.diagnostics import start_diagnostics


class DiagnosticsTests(TestCase):
    def test_error_log_keeps_tracebacks_without_routine_web_traffic(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'logs' / 'errors.log'
            old_level = logging.getLogger('werkzeug').level
            close = start_diagnostics(path)
            try:
                logging.getLogger('werkzeug').info('GET /api/status')
                try:
                    raise ValueError('Isolated failure evidence')
                except ValueError:
                    logging.getLogger('radio.test').exception('Operation failed')
            finally:
                close()
            text = path.read_text()
            self.assertIn('ValueError: Isolated failure evidence', text)
            self.assertNotIn('GET /api/status', text)
            self.assertEqual(logging.getLogger('werkzeug').level, old_level)

    def test_uncaught_thread_error_is_preserved_and_original_hook_restored(self):
        with TemporaryDirectory() as temporary, patch('threading.excepthook') as previous:
            path = Path(temporary) / 'errors.log'
            close = start_diagnostics(path)
            def fail():
                raise RuntimeError('Test background failure')
            try:
                thread = threading.Thread(target=fail)
                thread.start()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
            finally:
                close()
            self.assertIn('RuntimeError: Test background failure', path.read_text())
            previous.assert_called_once()
            self.assertIs(threading.excepthook, previous)
