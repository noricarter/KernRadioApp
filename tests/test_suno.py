"""Offline page-parser and request-boundary tests. Live checks are separate."""
import json
from unittest import TestCase
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from services.suno import SunoMetadataError, SunoMetadataService, parse_song_page
from services.suno.urls import song_identity

ID = '12345678-1234-1234-1234-123456789abc'


def page_for(song):
    flight = 'a:' + json.dumps(['$', {'state': {'data': song}}], ensure_ascii=False) + '\n'
    # Exercise flight chunks split in the middle of a JSON token.
    return ''.join('<script>self.__next_f.push(' + json.dumps([1, chunk], ensure_ascii=False) + ')</script>'
                   for chunk in (flight[:25], flight[25:]))


class SunoTests(TestCase):
    def test_observed_page_format_and_unicode(self):
        result = parse_song_page(page_for({'id': ID, 'title': '風 🌙', 'handle': '𝒰𝓈𝑒𝓇',
            'metadata': {'prompt': 'Words\n第二行', 'tags': 'ambient'}}), ID)
        self.assertEqual(result, {'suno_song_id': ID, 'title': '風 🌙', 'lyrics': 'Words\n第二行',
                                  'style': 'ambient', 'suno_username': '𝒰𝓈𝑒𝓇', 'artwork_url': None})

    def test_artwork_uses_song_large_image_then_regular_image_never_avatar(self):
        song = {'id': ID, 'metadata': {}, 'image_large_url': 'https://cdn2.suno.ai/large.jpeg',
                'image_url': 'https://cdn2.suno.ai/small.jpeg', 'avatar_image_url': 'https://cdn1.suno.ai/avatar.webp'}
        self.assertEqual(parse_song_page(page_for(song), ID)['artwork_url'], song['image_large_url'])
        song.pop('image_large_url')
        self.assertEqual(parse_song_page(page_for(song), ID)['artwork_url'], song['image_url'])
        song.pop('image_url')
        self.assertIsNone(parse_song_page(page_for(song), ID)['artwork_url'])

    def test_missing_fields_remain_unavailable(self):
        result = parse_song_page(page_for({'id': ID, 'metadata': {}}), ID)
        self.assertIsNone(result['title'])
        self.assertIsNone(result['lyrics'])
        self.assertIsNone(result['style'])
        self.assertIsNone(result['suno_username'])

    def test_lyrics_reference_uses_utf8_byte_length_and_preserves_newlines(self):
        lyrics = 'Verse 🌳\n日本語 e\u0301\nLast line'
        song = {'id': ID, 'title': 'Title', 'metadata': {'prompt': '$bb'}}
        flight = f'bb:T{len(lyrics.encode("utf-8")):x},' + lyrics + 'bc:' + json.dumps(song) + '\n'
        page = '<script>self.__next_f.push(' + json.dumps([1, flight]) + ')</script>'
        self.assertEqual(parse_song_page(page, ID)['lyrics'], lyrics)

    def test_unresolved_text_reference_is_not_stored_as_lyrics(self):
        self.assertIsNone(parse_song_page(page_for({'id': ID, 'metadata': {'prompt': '$missing'}}), ID)['lyrics'])

    def test_other_songs_or_generic_og_title_do_not_supply_metadata(self):
        for page in (page_for({'id': 'different', 'title': 'Wrong', 'metadata': {}}),
                     '<meta property="og:title" content="Sign in to Suno">',
                     '<script>self.__next_f.push(broken)</script>'):
            with self.assertRaises(SunoMetadataError):
                parse_song_page(page, ID)

    def test_only_targeted_song_urls_are_accepted(self):
        for value in ('https://localhost/song/' + ID, 'https://suno.com.evil/song/' + ID,
                      'https://suno.com/@creator', 'https://suno.com/playlist/other', 'not-a-song'):
            with self.assertRaises(ValueError):
                song_identity(value)
        self.assertEqual(song_identity(ID)[0], ID)
        self.assertEqual(song_identity('https://suno.com/song/' + ID + '?x=1')[0], ID)

    def test_http_and_network_failures_have_clear_feedback(self):
        for failure, expected in ((HTTPError('url', 429, 'rate limit', {}, None), 'slow down'),
                                  (HTTPError('url', 404, 'not found', {}, None), 'unavailable'),
                                  (URLError('offline'), 'connection')):
            service = SunoMetadataService()
            service._opener = Mock()
            service._opener.open.side_effect = failure
            with self.assertRaisesRegex(SunoMetadataError, expected):
                service.fetch_song(ID)
            self.assertEqual(service._opener.open.call_count, 1)

    def test_request_uses_only_canonical_url_and_bounds_response(self):
        service = SunoMetadataService()
        response = Mock()
        response.read.return_value = page_for({'id': ID, 'title': 'Title', 'metadata': {}}).encode()
        service._opener = Mock()
        service._opener.open.return_value.__enter__ = Mock(return_value=response)
        service._opener.open.return_value.__exit__ = Mock(return_value=False)
        original = 'http://www.suno.com/song/' + ID + '?tracking=yes'
        self.assertEqual(service.fetch_song(original)['original_suno_url'], original)
        request = service._opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://suno.com/song/' + ID)
        response.read.assert_called_once_with(4_000_001)

    def test_share_resolution_uses_head_and_full_identity(self):
        service = SunoMetadataService()
        service._request = Mock(return_value=('https://suno.com/song/' + ID + '?sh=code', ''))
        self.assertEqual(service.resolve_song_id('https://suno.com/s/code'), ID)
        service._request.assert_called_once_with('https://suno.com/s/code', method='HEAD')

    def test_share_resolution_must_end_on_a_canonical_song(self):
        service = SunoMetadataService()
        for target in ('https://suno.com/', 'https://suno.com/s/another'):
            service._request = Mock(return_value=(target, ''))
            with self.assertRaises(SunoMetadataError):
                service.resolve_song_id('https://suno.com/s/code')
