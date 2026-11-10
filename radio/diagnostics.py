"""Bounded application error logs, including uncaught background-thread errors."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading


def start_diagnostics(path):
    """Install error-only local diagnostics; return a cleanup callable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    handler = RotatingFileHandler(path, maxBytes=1_048_576, backupCount=2, encoding='utf-8')
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s'))
    path.chmod(0o600)
    root = logging.getLogger()
    root.addHandler(handler)
    access_log = logging.getLogger('werkzeug')
    previous_access_level = access_log.level
    access_log.setLevel(logging.WARNING)
    previous = threading.excepthook

    def thread_error(arguments):
        if arguments.exc_type is not SystemExit:
            logging.getLogger(__name__).error('Background thread stopped unexpectedly.',
                exc_info=(arguments.exc_type, arguments.exc_value, arguments.exc_traceback))
        previous(arguments)

    threading.excepthook = thread_error

    def close():
        if threading.excepthook is thread_error:
            threading.excepthook = previous
        root.removeHandler(handler)
        handler.close()
        access_log.setLevel(previous_access_level)

    return close
