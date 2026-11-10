"""Thin HTTP adapters and local browser request protection."""
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory
from services.codex import CodexError
from services.configuration import ConfigurationError
from services.database import DatabaseError


def create_app(controller, ingestion=None, management=None):
    root = Path(__file__).parent
    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
    app.config.update(MAX_CONTENT_LENGTH=700_000, TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    token = secrets.token_urlsafe(32)

    @app.before_request
    def protect_local_requests():
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error='Please use the application in its original browser tab.'), 403
            if not secrets.compare_digest(request.headers.get('X-App-Token', ''), token):
                return jsonify(error='Please reload this page and try again.'), 403

    @app.after_request
    def browser_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; frame-src http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/')
    def index():
        return render_template('index.html', token=token)

    @app.get('/listen/')
    def broadcast_display():
        return send_from_directory(root.parent / 'kern_radio_windowed', 'index.html')

    @app.get('/listen/<path:filename>')
    def broadcast_assets(filename):
        if filename not in ('styles.css', 'script.js') and not filename.startswith('assets/'):
            raise LookupError('Display asset does not exist.')
        return send_from_directory(root.parent / 'kern_radio_windowed', filename)

    @app.get('/assets/mascot.png')
    def mascot():
        return send_file(root.parent / 'kern_radio_windowed' / 'assets' / 'mascot.png')

    @app.get('/api/status')
    def status():
        state = controller.status()
        if management is not None:
            state['station'] = management.snapshot(authenticated=state['authenticated'])
        return jsonify(state)

    def station_manager():
        if management is None:
            raise ValueError('Station management is not available.')
        return management

    @app.get('/api/broadcast')
    def broadcast_state():
        programmer = management.programming if management is not None else None
        if programmer is None:
            return jsonify(running=False, phase='idle', current=None, queue=[], dialogue=None, playback='idle', mascot='standard', error=None)
        return jsonify(programmer.public_state())

    @app.post('/api/programming/start')
    def start_programming():
        return jsonify(station_manager().start_programming(authenticated=controller.status()['authenticated']))

    @app.post('/api/programming/stop')
    def stop_programming():
        return jsonify(station_manager().stop_programming())

    @app.post('/api/programming/queue-song')
    def queue_catalog_song():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'suno_song_id', 'position'}:
            raise ValueError('Choose a catalog song and queue position.')
        if not isinstance(data['suno_song_id'], str) or data['position'] not in ('next', 'append'):
            raise ValueError('Choose a catalog song and either Play next or Append to queue.')
        manager = station_manager()
        if manager.programming is None:
            raise ValueError('Programming is not available.')
        status = manager.programming.status()
        if not status['running']:
            raise ValueError('Start Programming before adding a song to the queue.')
        if status['stop_requested']:
            raise ValueError('The station is signing off. Start a new program before adding songs.')
        detail = manager.song_detail(data['suno_song_id'])
        title = detail['song']['title'] or 'Untitled song'
        artist = detail['artist']['preferred_display_alias'] if detail['artist'] else 'Artist not assigned'
        position = 'next' if data['position'] == 'next' else 'at the end of the upcoming queue'
        prompt = (f'Kern selected catalog song {data["suno_song_id"]} ({title} by {artist}) to place {position}. '
            'Read station_programming first and use its current revision. Read this song’s catalog details and write a brief, '
            'accurate introduction and outro, then call queue_station_song with the requested position. Do not interrupt the '
            'current audio or replace other upcoming songs. Kern’s direct selection is an explicit replay request if playback '
            'history shows this song has already started; include repeat_reason only when needed.')
        if controller.station_task(prompt) is None:
            raise CodexError('Your host is busy. Wait for the current response to finish, then try again.')
        return jsonify(accepted=True)

    @app.get('/api/catalog')
    def catalog():
        return jsonify(station_manager().catalog_page(offset=int(request.args.get('offset', 0)),
            query=request.args.get('q', '')))

    @app.get('/api/artists')
    def artists():
        return jsonify(station_manager().artist_page(offset=int(request.args.get('offset', 0))))

    @app.get('/api/youtube')
    def youtube_catalog():
        return jsonify(station_manager().youtube_page(offset=int(request.args.get('offset', 0))))

    @app.get('/api/youtube/<video_id>')
    def youtube_submission(video_id):
        return jsonify(station_manager().youtube_detail(video_id))

    @app.get('/api/catalog/<song_id>')
    def song_detail(song_id):
        return jsonify(station_manager().song_detail(song_id))

    @app.get('/api/catalog/<song_id>/artwork')
    def song_artwork(song_id):
        return send_file(station_manager().artwork_path(song_id))

    @app.get('/api/import-review')
    def import_review():
        batch = request.args.get('batch_id')
        return jsonify(station_manager().import_review(batch_id=int(batch) if batch is not None else None,
            offset=int(request.args.get('offset', 0))))

    @app.get('/api/import-review/<int:item_id>')
    def import_review_entry(item_id):
        return jsonify(station_manager().import_review_entry(item_id))

    @app.post('/api/catalog/<song_id>')
    def save_song(song_id):
        return jsonify(station_manager().save_song(song_id, request.get_json(silent=True)))

    @app.post('/api/catalog-review')
    def review_catalog():
        return jsonify(station_manager().review_catalog())

    @app.post('/api/onboarding/complete')
    def finish_onboarding():
        return jsonify(station_manager().finish_onboarding(authenticated=controller.status()['authenticated']))

    @app.post('/api/policies/action')
    def policy_action():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'action', 'policy_id', 'revision'}:
            raise ValueError('Choose a policy action.')
        return jsonify(controller.policy_action(**data))

    @app.post('/api/connect')
    def connect():
        return jsonify(controller.connect())

    @app.post('/api/messages')
    def send():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Please enter a message.')
        controller.send(data.get('text'))
        return jsonify(accepted=True), 202

    @app.post('/api/import')
    def import_music():
        if ingestion is None:
            return jsonify(error='Music import is not available in this application.'), 503
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Paste some Discord messages first.')
        return jsonify(ingestion.import_text(data.get('text')))

    @app.errorhandler(DatabaseError)
    def database_error(exc):
        return jsonify(error='Changes could not be saved. Some earlier changes may already be saved. Check that local storage is writable and try again.'), 503

    @app.errorhandler(LookupError)
    def missing_record(exc):
        return jsonify(error=str(exc)), 404

    @app.errorhandler(CodexError)
    @app.errorhandler(ConfigurationError)
    def service_error(exc):
        return jsonify(error=str(exc)), 503

    @app.errorhandler(ValueError)
    def input_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(413)
    def too_large(exc):
        return jsonify(error='That message is too long. Please shorten it.'), 413

    @app.errorhandler(500)
    def unexpected_error(exc):
        return jsonify(error='Something went wrong. Please try again.'), 500

    return app
