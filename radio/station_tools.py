"""Application-owned agent capabilities. No approval/disable or playback tools."""
from services.catalog import RecordNotFound
from .catalog_review import pending_import_review
from .policy_discovery import INSTRUCTIONS
from .programming.presentation import MASCOTS


def _schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


_STRING = {'type': 'string'}
_INT = {'type': 'integer'}
_MASCOT = {'type': 'string', 'enum': list(MASCOTS)}
_PAGE = {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
         'offset': {'type': 'integer', 'minimum': 0}}


class StationTools:
    def __init__(self, information, policies, configuration, *, programming=None):
        self.information, self.policies, self.configuration = information, policies, configuration
        self.calls = []  # Session-only diagnostic names/arguments; no credentials.
        self.programming = programming
        self._tools = [
            ('station_overview', 'Read actual library counts, artist representation, history counts and configuration readiness. Begin station discovery here.', _schema()),
            ('station_catalog', 'Read catalog pages or one song including paged lyrics. Actions: songs, song, artists, artist, youtube, youtube_track. YouTube entries contain supplied Discord metadata; Suno fields are not required. Durations may be unavailable.',
                _schema({'action': {'type': 'string', 'enum': ['songs', 'song', 'artists', 'artist', 'youtube', 'youtube_track']},
                    'suno_song_id': _STRING, 'video_id': _STRING, 'artist_id': _INT, 'lyrics_offset': _INT, 'lyrics_limit': _INT, **_PAGE}, ['action'])),
            ('station_ingestion', 'Read community music imports. Actions: unresolved (current review queue; excludes groups whose songs are now all in the catalog), batches (historical outcome counts), items (historical failed entries of the latest batch by default), item (original text in character pages). Failed counts are historical, not current unresolved counts. Inspect originals before diagnosing. Read-only: cannot repair or reimport.',
                _schema({'action': {'type': 'string', 'enum': ['unresolved', 'batches', 'items', 'item']},
                    'batch_id': _INT, 'item_id': _INT,
                    'outcome': {'type': 'string', 'enum': ['failed', 'added', 'existing', 'all']},
                    'source_offset': _INT, 'source_limit': _INT, **_PAGE}, ['action'])),
            ('station_playback', 'Read existing playback facts/metrics. Actions: history, song_statistics, artist_statistics, rankings, airtime, representation. History order newest returns recent starts; oldest is the default. Rankings support most_played, least_played, least_recent, catalog. Date ranges use UTC playback starts [start,end).',
                _schema({'action': {'type': 'string', 'enum': ['history', 'song_statistics', 'artist_statistics', 'rankings', 'airtime', 'representation']},
                    'suno_song_id': _STRING, 'artist_id': _INT, 'start': _STRING, 'end': _STRING,
                    'order': _STRING, 'metadata': _STRING, **_PAGE}, ['action'])),
            ('station_policies', 'Review persisted proposals and approvals before creating a subject. Actions: list, get, active. Get shows exact text and rationale.',
                _schema({'action': {'type': 'string', 'enum': ['list', 'get', 'active']}, 'policy_id': _INT, **_PAGE}, ['action'])),
            ('propose_station_policy', 'Save a proposal only when Kern wants a persistent policy considered. Reuse an existing subject; never store a temporary program instruction. This cannot activate anything.',
                _schema({'subject': {'type': 'string', 'pattern': '^[a-z0-9]+(?:-[a-z0-9]+)*$', 'maxLength': 100}, 'title': _STRING, 'instruction': _STRING,
                    'kind': {'type': 'string', 'enum': ['default', 'operational']}, 'rationale': _STRING, 'basis': _STRING},
                    ['subject', 'title', 'instruction', 'kind', 'rationale', 'basis'])),
            ('revise_station_policy', 'Propose a new revision of the same subject after reviewing it. The existing approved revision remains active until Kern approves the replacement.',
                _schema({'policy_id': _INT, 'expected_revision': _INT, 'title': _STRING,
                    'instruction': _STRING, 'rationale': _STRING, 'basis': _STRING},
                    ['policy_id', 'expected_revision', 'title', 'instruction', 'rationale', 'basis'])),
        ]
        if programming is not None:
            expressions = {key: _MASCOT for key in ('introduction_mascot', 'playing_mascot', 'outro_mascot')}
            track = _schema({'suno_song_id': _STRING, 'introduction': _STRING, 'outro': _STRING, 'repeat_reason': _STRING, **expressions}, ['suno_song_id', 'introduction', 'outro'])
            self._tools += [
                ('station_programming', 'Read the live program, current revision, current song, pending queue/scripts, stop request, and failures. Read before editing. Only the backend Start button begins a run.', _schema()),
                ('set_station_program', 'Set or replace 1–6 unique UPCOMING tracks and their brief on-air scripts. The current song continues. Previously started songs require repeat_reason explaining Kern’s explicit replay request or why a repeat is necessary; otherwise choose different songs. Actual history is checked before saving. The first plan requires greeting and signoff. Read actual song contents and approved policies first. Aim for 180 characters per script (maximum 360).',
                    _schema({'expected_revision': _INT, 'tracks': {'type': 'array', 'items': track, 'minItems': 1, 'maxItems': 6},
                             'greeting': _STRING, 'signoff': _STRING, 'greeting_mascot': _MASCOT, 'signoff_mascot': _MASCOT}, ['expected_revision', 'tracks'])),
                ('queue_station_song', 'Put Kern’s requested Suno catalog song next or append it to the end of the upcoming queue, as requested, with a brief introduction and outro. If previously started, include repeat_reason citing her explicit replay request or why repetition is necessary. Interrupt the current song ONLY if Kern explicitly requests immediate interruption. Otherwise it finishes normally.',
                    _schema({'expected_revision': _INT, 'suno_song_id': _STRING, 'introduction': _STRING, 'outro': _STRING,
                             'interrupt_current': {'type': 'boolean'}, 'position': {'type': 'string', 'enum': ['next', 'append']},
                             'repeat_reason': _STRING, **expressions}, ['expected_revision', 'suno_song_id', 'introduction', 'outro'])),
                ('set_host_mascot', 'Switch the public host image without changing dialogue, audio or the queue. Use an approved expression; standard is the default. Can preview while off air. Planned expressions take over at the next scripted stage.',
                    _schema({'mascot': _MASCOT}, ['mascot'])),
                ('say_on_air', 'Change the visible host dialogue during an active program, optionally with a matching mascot expression. Keep it brief and grounded in actual information. This does not interrupt audio.', _schema({'text': _STRING, 'mascot': _MASCOT}, ['text'])),
                ('revise_host_script', 'Rewrite the current song’s upcoming outro or the station signoff. This preserves current audio and the queue. Can update the closing text while a Stop request is draining the current song.',
                    _schema({'expected_revision': _INT, 'target': {'type': 'string', 'enum': ['current_outro', 'signoff']}, 'text': _STRING, 'mascot': _MASCOT}, ['expected_revision', 'target', 'text'])),
            ]

    @property
    def instructions(self):
        if self.programming is not None:
            from .programming.prompts import HOST_INSTRUCTIONS
            return INSTRUCTIONS + HOST_INSTRUCTIONS
        return INSTRUCTIONS

    @property
    def specifications(self):
        return [{'type': 'function', 'name': name, 'description': description, 'inputSchema': schema}
                for name, description, schema in self._tools]

    def dispatch(self, name, arguments):
        specification = next((tool for tool in self._tools if tool[0] == name), None)
        if specification is None:
            raise ValueError('This station capability is not available.')
        schema = specification[2]
        if not isinstance(arguments, dict) or arguments.keys() - schema['properties'].keys() or set(schema['required']) - arguments.keys():
            raise ValueError('Invalid tool arguments. Follow the declared tool schema.')
        for key, value in arguments.items():
            field = schema['properties'][key]
            if ((field['type'] == 'string' and not isinstance(value, str))
                    or (field['type'] == 'integer' and type(value) is not int)
                    or (field['type'] == 'array' and not isinstance(value, list))
                    or (field['type'] == 'boolean' and type(value) is not bool)
                    or ('enum' in field and value not in field['enum'])):
                raise ValueError(f'Invalid {key}.')
        self.calls.append({'tool': name, 'arguments': dict(arguments)})
        if name == 'station_overview':
            return self.information.overview() | {'setup_completed': self.configuration.setup_completed,
                'policies_available': True, 'station_operation_implemented': self.programming is not None}
        if name == 'station_programming':
            return self.programming.status()
        if name == 'set_station_program':
            return self.programming.set_program(**arguments)
        if name == 'queue_station_song':
            return self.programming.queue_song(**arguments)
        if name == 'set_host_mascot':
            return self.programming.set_mascot(**arguments)
        if name == 'say_on_air':
            return self.programming.say(**arguments)
        if name == 'revise_host_script':
            return self.programming.rewrite_script(**arguments)
        if name == 'station_catalog':
            return self._catalog(**arguments)
        if name == 'station_ingestion':
            return self._ingestion(**arguments)
        if name == 'station_playback':
            return self._playback(**arguments)
        if name == 'station_policies':
            return self._policies(**arguments)
        if name == 'propose_station_policy':
            return self._review(self.policies.create_proposal(**arguments))
        if name == 'revise_station_policy':
            return self._review(self.policies.revise(**arguments))
        raise ValueError('Unsupported capability.')

    def conversation_context(self):
        """Fresh application facts for every turn, including an already-open thread."""
        context = {'latest_import': self.information.latest_import(),
                'import_results': 'Use station_ingestion action unresolved for current needs-attention entries. Batch failed counts are historical; successfully imported split groups may now be resolved. Use item for original text. Import failures are separate from artist identity review.'}
        if self.programming is not None:
            program = self.programming.status()
            context['programming'] = program
            if program['running']:
                tracks = ([program['current']] if program['current'] else []) + program['queue']
                context['playback_selection'] = self.information.playback_context(
                    song_ids=[track['suno_song_id'] for track in tracks])
        return context

    def _ingestion(self, action, **arguments):
        if action == 'unresolved':
            return pending_import_review(self.information.catalog, **arguments)
        operations = {'batches': self.information.ingestion_batches,
                      'items': self.information.ingestion_items,
                      'item': self.information.ingestion_item}
        return operations[action](**arguments)

    def _catalog(self, action, **arguments):
        if action == 'youtube':
            self.information.validate_page(arguments.get('limit', 10), arguments.get('offset', 0))
            return self.information.catalog.list_youtube_tracks(**arguments)
        if action == 'youtube_track':
            track = self.information.catalog.get_youtube_track(arguments['video_id'])
            if track is None:
                raise RecordNotFound('YouTube video does not exist.')
            return {'track': track, 'submissions': self.information.catalog.list_youtube_submissions(arguments['video_id'])}
        if action == 'songs':
            return self.information.songs(**arguments)
        if action == 'song':
            return self.information.song(**arguments)
        if action == 'artists':
            return self.information.artists(**arguments)
        if action == 'artist':
            artist = self.information.catalog.get_artist(arguments['artist_id'])
            if artist is None:
                raise RecordNotFound('Artist does not exist.')
            return artist

    def _playback(self, action, **arguments):
        limit, offset = arguments.pop('limit', 10), arguments.pop('offset', 0)
        self.information.validate_page(limit, offset)
        operations = {'history': self.information.history.list_events,
                      'song_statistics': self.information.metrics.song_statistics,
                      'artist_statistics': self.information.metrics.artist_statistics,
                      'rankings': self.information.metrics.song_play_counts,
                      'airtime': self.information.metrics.artist_airtime_distribution,
                      'representation': self.information.metrics.representation}
        if action in ('history', 'rankings'):
            return operations[action](**arguments, limit=limit, offset=offset)
        result = operations[action](**arguments)
        if isinstance(result, list):
            return {'items': result[offset:offset + limit], 'total': len(result)}
        return result

    def _policies(self, action, **arguments):
        if action == 'get':
            policy = self.policies.get_policy(arguments['policy_id'])
            if policy is None:
                raise RecordNotFound('Policy does not exist.')
            return self._review(policy)
        limit, offset = arguments.get('limit', 10), arguments.get('offset', 0)
        self.information.validate_page(limit, offset)
        if action == 'active':
            policies = self.policies.active_policies()
            return {'items': policies[offset:offset + limit], 'total': len(policies)}
        policies = self.policies.list_policies(limit=limit, offset=offset)
        return [{'id': p['id'], 'subject': p['subject'], 'kind': p['kind'], 'enabled': p['enabled'],
            'title': p['latest_revision']['title'], 'revision': p['latest_revision']['revision'],
            'approval_status': p['latest_revision']['approval_status'], 'active': p['active'],
            'declined': bool(p['latest_revision'].get('declined', 0)),
            'approved_revision_number': p['approved_revision']['revision'] if p['approved_revision'] else None}
            for p in policies]

    @staticmethod
    def _review(policy):
        revision = policy['latest_revision']['revision']
        return {'policy': policy,
                'approval_command': f"Approve policy {policy['id']} revision {revision}",
                'disable_command': f"Disable policy {policy['id']} revision {revision}",
                'authority': 'Only Kern can send these commands in the application chat. No agent tool can approve or disable.'}
