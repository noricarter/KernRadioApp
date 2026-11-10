"""Actual main composition/Flask HTTP on an isolated port; Codex is a fake."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import main as application
from services.web import FlaskWebService
from tests.ui_server import DisconnectedCodex


class StartupTests(TestCase):
    def test_main_starts_with_error_logging_and_recovers_from_a_web_error(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = []

            def server(app):
                @app.get('/test/failure')
                def failure():
                    raise RuntimeError('Isolated startup diagnostic failure')

                web = FlaskWebService(app, port=0)

                def check():
                    with urlopen(web.url + '/api/status', timeout=3) as response:
                        results.append(json.load(response))
                    try:
                        urlopen(web.url + '/test/failure', timeout=3)
                    except HTTPError as error:
                        with error:
                            self.assertEqual(error.code, 500)
                            self.assertNotIn('RuntimeError', error.read().decode())
                    else:
                        self.fail('Expected the fixture error')
                    with urlopen(web.url + '/api/status', timeout=3) as response:
                        results.append(json.load(response))

                web.wait = check
                return web

            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root)}), \
                    patch.object(application, 'CodexService', return_value=DisconnectedCodex()), \
                    patch.object(application, 'FlaskWebService', side_effect=server), \
                    patch.object(application.signal, 'signal'):
                application.main()
            self.assertEqual(len(results), 2)
            self.assertFalse(results[0]['station']['programming']['running'])
            self.assertEqual(results[1]['station']['catalog']['songs'], 0)
            log = (root / 'kernradio' / 'logs' / 'errors.log').read_text()
            self.assertIn('RuntimeError: Isolated startup diagnostic failure', log)
            self.assertNotIn('GET /api/status', log)
