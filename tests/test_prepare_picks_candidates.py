import unittest

from scripts.prepare_picks_candidates import (
    SourcePageParser,
    normalize_url,
    page_candidates,
)


class PreparePicksCandidatesTests(unittest.TestCase):
    def test_normalize_url_removes_tracking_and_fragment(self):
        self.assertEqual(
            normalize_url('HTTPS://Example.com/story/?utm_source=x&id=3#section'),
            'https://example.com/story?id=3',
        )

    def test_parser_discovers_feed_and_links(self):
        parser = SourcePageParser('https://example.com/')
        parser.feed('''
            <link rel="alternate" type="application/rss+xml" href="/feed.xml">
            <a href="/story">A substantive article title</a>
        ''')
        self.assertEqual(parser.feed_urls, ['https://example.com/feed.xml'])
        candidates = page_candidates(
            parser.links, 'https://example.com/', 'example.com', 'Tech')
        self.assertEqual(candidates[0]['url'], 'https://example.com/story')


if __name__ == '__main__':
    unittest.main()
