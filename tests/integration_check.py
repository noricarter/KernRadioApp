"""Opt-in real App Server checks. Run from the repository root as a module.

A private temporary Codex home isolates all login, cancellation, refresh, and
session writes. --reuse-auth copies an existing Codex auth file ONLY into this
short-lived test home; no application credentials are stored or managed here.
The original authentication/configuration is never written or logged.
"""
import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
import json

from services.codex import CodexService
from services.configuration import ConfigurationService
from services.web import FlaskWebService
from radio.controller import ApplicationController
from radio.web import create_app


def run(reuse_auth=False):
    with tempfile.TemporaryDirectory(prefix='kernradio-check-') as temporary:
        root = Path(temporary)
        home = root / 'codex'
        home.mkdir(mode=0o700)
        service = CodexService(root / 'workspace', codex_home=home)
        try:
            service.start()
            assert not service.account()['authenticated']
            login = service.begin_login()
            assert login['auth_url'].startswith('https://')
            assert service.login_status()['pending']
            service._transport.request('account/login/cancel', {'loginId': service._login['loginId']})
            print('PASS real initialize, signed-out account detection, browser login initiation, cancellation')
        finally:
            service.close()
        if not reuse_auth:
            print('SKIP authenticated conversation (use --reuse-auth to test an isolated copy of existing Codex auth)')
            return
        auth = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'
        if not auth.is_file():
            print('BLOCKED no file-backed Codex authentication available')
            return
        shutil.copy2(auth, home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        settings = root / 'settings.json'
        for iteration in range(2):
            service = CodexService(root / 'workspace', codex_home=home)
            controller = ApplicationController(ConfigurationService(settings), service)
            web = FlaskWebService(create_app(controller), port=0)
            try:
                controller.start()
                web.start()
                with urlopen(web.url) as response:
                    page = response.read().decode()
                token = re.search(r'name="app-token" content="([^"]+)"', page)[1]
                with urlopen(web.url + '/api/status') as response:
                    state = json.load(response)
                assert state['authenticated'] and state['setup_completed'], state['error']
                if iteration == 0:
                    req = Request(web.url + '/api/messages', data=json.dumps({'text': 'Reply with exactly: Radio connection confirmed'}).encode(), headers={'Content-Type': 'application/json', 'X-App-Token': token})
                    with urlopen(req) as response:
                        assert response.status == 202
                    controller._future.result(timeout=200)
                    with urlopen(web.url + '/api/status') as response:
                        state = json.load(response)
                    assert state['messages'][-1]['role'] == 'agent', state['messages'][-1]
                    print('PASS real authenticated HTTP chat:', state['messages'][-1]['text'])
                else:
                    print('PASS restart preserves setup and reuses authentication')
                service._transport._process.terminate()
                service._transport._process.wait(timeout=5)
                assert controller.status()['error']
                print('PASS real process loss produces reconnect feedback')
            finally:
                web.stop()
                controller.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-auth', action='store_true')
    run(parser.parse_args().reuse_auth)
