"""Local Flask hosting; accepts an assembled web application."""
import socket
from threading import Thread
from werkzeug.serving import make_server


class FlaskWebService:
    def __init__(self, app, *, port=5000):
        if port:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(('127.0.0.1', port))
                except OSError:
                    # Preserve the familiar URL when available; let the OS
                    # choose a free loopback port when it is already occupied.
                    port = 0
        self._server = make_server('127.0.0.1', port, app, threaded=True)
        self._thread = None

    @property
    def url(self):
        return f'http://127.0.0.1:{self._server.server_port}'

    def start(self):
        self._thread = Thread(target=self._server.serve_forever, name='local-web')
        self._thread.start()

    def wait(self):
        self._thread.join()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=5)
        self._server.server_close()
