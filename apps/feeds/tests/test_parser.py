"""Parser tests — RSS 2.0 + Atom 1.0, dates, enclosures, leniency. No DB."""

from __future__ import annotations

from datetime import timezone

from apps.feeds.parser import parse_feed

RSS = """<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Example</title>
    <item>
      <title>First post</title>
      <link>https://example.com/1</link>
      <guid>https://example.com/1</guid>
      <description>Hello world</description>
      <pubDate>Mon, 06 Sep 2021 16:20:00 +0000</pubDate>
      <dc:creator>Ada</dc:creator>
      <enclosure url="https://example.com/a.mp3" length="1234" type="audio/mpeg"/>
    </item>
    <item>
      <title>Second</title>
      <link>https://example.com/2</link>
    </item>
  </channel>
</rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example</title>
  <entry>
    <title>Atom entry</title>
    <id>urn:uuid:1</id>
    <link rel="alternate" href="https://example.com/a"/>
    <link rel="enclosure" href="https://example.com/a.mp3" length="99" type="audio/mpeg"/>
    <updated>2021-09-06T16:20:00Z</updated>
    <summary>Body text</summary>
    <author><name>Grace</name></author>
  </entry>
</feed>"""


def test_rss_parses_all_fields():
    items = parse_feed(RSS)
    assert len(items) == 2
    first = items[0]
    assert first.title == "First post"
    assert first.link == "https://example.com/1"
    assert first.guid == "https://example.com/1"
    assert first.summary == "Hello world"
    assert first.author == "Ada"
    assert first.published is not None
    assert first.published.tzinfo is not None
    assert first.published.year == 2021 and first.published.month == 9
    assert first.enclosures == [
        {"url": "https://example.com/a.mp3", "length": "1234", "type": "audio/mpeg"}
    ]


def test_rss_is_lenient_about_missing_fields():
    # Second item has no guid/description/date — guid falls back to link.
    second = parse_feed(RSS)[1]
    assert second.guid == "https://example.com/2"
    assert second.summary == ""
    assert second.published is None


def test_atom_parses_alternate_link_and_enclosure():
    items = parse_feed(ATOM)
    assert len(items) == 1
    entry = items[0]
    assert entry.title == "Atom entry"
    assert entry.link == "https://example.com/a"  # rel=alternate, not the enclosure
    assert entry.guid == "urn:uuid:1"
    assert entry.summary == "Body text"
    assert entry.author == "Grace"
    assert entry.published is not None and entry.published.tzinfo == timezone.utc
    assert entry.enclosures[0]["url"] == "https://example.com/a.mp3"


def test_accepts_bytes_and_str():
    assert len(parse_feed(RSS.encode("utf-8"))) == 2
    assert len(parse_feed(RSS)) == 2


def test_naive_dates_are_made_utc():
    rss = RSS.replace("Mon, 06 Sep 2021 16:20:00 +0000", "2021-09-06T16:20:00")
    published = parse_feed(rss)[0].published
    assert published is not None and published.tzinfo is not None
