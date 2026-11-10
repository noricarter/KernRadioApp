"""Actual copied Crucible posts and small synthetic parser fixtures."""
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from services.discord_text import DiscordTextParser

A = 'b4d9e1fe-16ab-43f7-a4cb-d02209e573c7'
B = '12345678-1234-1234-1234-123456789abc'


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = DiscordTextParser()

    def test_multiple_links_and_fields_preserve_unicode(self):
        text = f'Title: 𝕁ØɄℝℕ𝔼𝕐 e\u0301 🌲\nGenre: ambient\nAbout: 日本語\nMore description\nCW: loud sounds\nhttps://suno.com/song/{A}\nhttps://suno.com/song/{B}'
        result = self.parser.parse(text)
        self.assertEqual(len(result.submissions), 2)
        self.assertEqual(result.unresolved, [])
        for song in result.submissions:
            self.assertEqual(song.original_message, text)
            self.assertEqual(song.submitted_title, '𝕁ØɄℝℕ𝔼𝕐 e\u0301 🌲')
            self.assertEqual(song.description, '日本語\nMore description')
            self.assertEqual(song.content_warning, 'loud sounds')
            self.assertIsNone(song.discord_handle)

    def test_repeated_link_within_block_and_across_posts(self):
        post = f'https://suno.com/song/{A}\nhttps://suno.com/song/{A}?x=2'
        result = self.parser.parse(post + '\n\n' + post)
        self.assertEqual(len(result.submissions), 2)

    def test_unknown_author_and_unlabeled_title_are_not_guessed(self):
        text = f'𝓤𝓼𝓮𝓻 — unfamiliar timestamp\nAn unlabeled title\nhttps://suno.com/song/{A}'
        record = self.parser.parse(text).submissions[0]
        self.assertIsNone(record.discord_handle)
        self.assertIsNone(record.submitted_title)
        self.assertEqual(record.original_message, text)

    def test_short_links_are_preserved_for_service_resolution(self):
        result = self.parser.parse('https://suno.com/s/shortlink')
        self.assertEqual(result.unresolved, [])
        self.assertIsNone(result.submissions[0].suno_song_id)
        self.assertEqual(result.submissions[0].original_suno_url, 'https://suno.com/s/shortlink')

    def test_actual_crucible_posts_inline_labels_authors_and_embeds(self):
        text = (Path(__file__).parent / 'fixtures/crucible_posts.txt').read_text()
        result = self.parser.parse(text)
        self.assertEqual(result.unresolved, [])
        self.assertEqual([s.discord_handle for s in result.submissions],
                         ['Sickseven [TCBL]', 'Jїпж [TCBL]', 'SicoAnimal [BF]', 'GemNoodles'])
        self.assertEqual([s.submitted_title for s in result.submissions],
                         ['I Went To The Pictures Tomorrow', 'Whistle - with @ℭ𝔯𝔦𝔪𝔰𝔬𝔫𝔟𝔬𝔬𝔱𝔶', 'The Last Sucker', "Mirelle's Lullaby"])
        self.assertEqual([s.genre for s in result.submissions],
                         ['EDM+', 'dark psytrance, uk drill', 'Horrorcore rap', 'Ballad, Folk'])
        self.assertEqual([s.content_warning for s in result.submissions],
                         ['nonsense', 'mild violent and drug imagery', 'None', 'None.'])
        self.assertTrue(all(s.description for s in result.submissions))
        self.assertNotIn('Server Profile', result.submissions[0].description)
        self.assertNotIn('https://', result.submissions[1].content_warning)
        self.assertEqual(''.join(s.original_message for s in result.submissions), text)

    def test_no_link_is_preserved_for_review(self):
        result = self.parser.parse('Unrecognized original post 🌙')
        self.assertEqual(result.unresolved[0].source_text, 'Unrecognized original post 🌙')

    def test_punctuation_and_uuid_case(self):
        record = self.parser.parse(f'<https://suno.com/song/{A.upper()}>.').submissions[0]
        self.assertEqual(record.suno_song_id, A)

    def test_invalid_input(self):
        for value in ('', '  ', None, 22):
            with self.assertRaises(ValueError):
                self.parser.parse(value)

    def test_ambiguous_posts_do_not_share_the_last_posts_fields(self):
        result = self.parser.parse(f'Title: First\nhttps://suno.com/song/{A}\nTitle: Second\nhttps://suno.com/song/{B}')
        self.assertEqual(result.submissions, [])
        self.assertIn('boundaries', result.unresolved[0].reason)

    def test_actual_grouped_posts_split_before_previews_and_preserve_attribution(self):
        text = (Path(__file__).parent / 'fixtures/crucible_grouped_posts.txt').read_text()
        parsed = self.parser.parse(text)
        self.assertEqual(len(parsed.submissions), 7)
        self.assertEqual(parsed.unresolved, [])
        self.assertEqual(parsed.submissions[2].source, 'youtube')
        self.assertEqual(parsed.submissions[2].youtube_video_id, 'EhNeWvGeZQw')
        self.assertEqual([post.discord_handle for post in parsed.submissions],
            ['Seraphym', 'norioku2020 [TCBL]', '𝕄𝕒𝕟𝕚𝕔💜𝔾𝕣𝕚𝕞 [STAR]', 'GrandpaHazard', 'AvaArtist', 'Oneshot', 'a-kai [TCBL]'])
        self.assertEqual(parsed.submissions[0].submitted_title, 'Lycanthropic Fury')
        self.assertEqual(parsed.submissions[1].submitted_title, 'Load Bearing')
        self.assertEqual(parsed.submissions[0].genre, 'Rockabilly')
        self.assertEqual(parsed.submissions[1].genre, 'Classical + Rap')
        self.assertNotIn('norioku2020', parsed.submissions[0].original_message)
        originals = [post.original_message for post in parsed.submissions] + [post.source_text for post in parsed.unresolved]
        self.assertEqual(''.join(sorted(originals, key=text.index)), text)
        self.assertEqual(parsed.submissions[-1].genre, 'Melodic edm?')
        self.assertIn('P̵A͏G̀E̶', parsed.submissions[-2].submitted_title)

    def test_observed_song_labels_and_warning_icon_keep_their_fields(self):
        text = (Path(__file__).parent / 'fixtures/crucible_grouped_posts.txt').read_text()
        grandpa = self.parser.parse(text).submissions[3]
        self.assertTrue(grandpa.description.startswith('First Night'))
        self.assertIn('The song captures', grandpa.description)
        self.assertNotIn('CONTENT WARNING', grandpa.description)
        self.assertTrue(grandpa.content_warning.startswith('Mature romantic themes'))

    def test_partial_previews_and_blank_paragraphs_do_not_swallow_next_author(self):
        text = f'First — 11/1/26, 1:00 PM\nTitle: A\nAbout: First paragraph\n\nSecond paragraph\nLink: https://suno.com/song/{A}\nSuno\nA\n\nSecond — 11/1/26, 1:01 PM\nTitle: B\nGenre: Folk\nLink: https://suno.com/song/{B}\nSuno\nB'
        parsed = self.parser.parse(text)
        self.assertEqual(parsed.unresolved, [])
        self.assertEqual(len(parsed.submissions), 2)
        self.assertEqual(parsed.submissions[0].description, 'First paragraph\n\nSecond paragraph')
        self.assertEqual(parsed.submissions[1].discord_handle, 'Second')
        self.assertEqual(parsed.submissions[1].genre, 'Folk')

    def test_same_post_repeated_fields_are_still_unresolved(self):
        text = f'Author — 1:00 PM\nTitle: A\nhttps://suno.com/song/{A}\nTitle: B\nhttps://suno.com/song/{B}'
        self.assertEqual(self.parser.parse(text).submissions, [])
        self.assertEqual(len(self.parser.parse(text).unresolved), 1)

    def test_actual_relative_and_explicit_dates_and_clean_artist_names(self):
        text = (Path(__file__).parent / 'fixtures/crucible_dated_posts.txt').read_text()
        reference = datetime(2026, 11, 8, 0, 5, tzinfo=ZoneInfo('America/Phoenix'))
        result = self.parser.parse(text, reference_time=reference)
        self.assertEqual(result.unresolved, [])
        salt, snot = result.submissions
        self.assertEqual(salt.preferred_artist_name, 'The Salt Scribe | Beat VS Break')
        self.assertEqual(salt.discord_handle, 'The Salt Scribe | Beat VS Break [TCBL]')
        self.assertEqual(salt.discord_timestamp, 'Yesterday at 7:51 PM')
        self.assertEqual(salt.posted_at, '2026-11-07T19:51:00-07:00')
        self.assertEqual(snot.preferred_artist_name, 'Snotegel')
        self.assertEqual(snot.discord_handle, 'Snotegel [ᛕᛟᛏᚺ]')
        self.assertEqual(snot.posted_at, '2026-11-05T13:51:00-07:00')
        self.assertEqual(snot.submitted_title, 'From Inside the Data Stream')
        self.assertEqual(snot.content_warning, 'None')
        self.assertNotIn('Suno', salt.content_warning)
        self.assertIn('Straight "From Inside', snot.original_message)

    def test_today_yesterday_calendar_boundary_and_am_pm(self):
        reference = datetime(2027, 1, 1, 0, 1, tzinfo=ZoneInfo('America/Phoenix'))
        for label, expected in (('Yesterday at 11:59 PM', '2026-12-31T23:59:00-07:00'),
                                ('12:00 AM', '2027-01-01T00:00:00-07:00'),
                                ('Today at 12:00 PM', '2027-01-01T12:00:00-07:00')):
            record = self.parser.parse(f'Author — {label}\nhttps://suno.com/song/{A}', reference_time=reference).submissions[0]
            self.assertEqual(record.posted_at, expected)

    def test_yesterday_uses_calendar_date_across_daylight_saving(self):
        reference = datetime(2026, 11, 2, 0, 1, tzinfo=ZoneInfo('America/New_York'))
        record = self.parser.parse(f'Author — Yesterday at 12:30 AM\nhttps://suno.com/song/{A}', reference_time=reference).submissions[0]
        self.assertEqual(record.posted_at, '2026-11-01T00:30:00-04:00')

    def test_invalid_explicit_date_is_retained_without_fabricating_post_time(self):
        record = self.parser.parse(f'Author — 2/30/26, 1:51 PM\nhttps://suno.com/song/{A}').submissions[0]
        self.assertIsNone(record.posted_at)
        self.assertEqual(record.discord_timestamp, '2/30/26, 1:51 PM')
        self.assertTrue(record.timestamp_error)
