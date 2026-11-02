import pytest

from tradingagent.ingestion.feeds import FeedError, parse_feed
from tradingagent.ingestion.normalize import canonical_url, clean_text, parse_datetime, title_key, tokens

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Medio</title>
<item><title><![CDATA[SEC approves spot Ether ETF options & more]]></title>
<link>https://www.example.com/markets/2026/01/01/sec-ether/?utm_source=rss&amp;utm_medium=feed</link>
<guid isPermaLink="false">abc-123</guid>
<description><![CDATA[<p>The <b>SEC</b> approved&nbsp;options.</p><script>evil()</script>]]></description>
<pubDate>Thu, 01 Jan 2026 12:00:00 GMT</pubDate><category>Policy</category></item>
<item><title>Second</title><dc:date>2026-01-01T12:05:00Z</dc:date><link>https://example.com/b</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>EDGAR</title>
<entry><title>8-K - ACME CORP (0000123456) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/123456/x-index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-01-01 &lt;b&gt;AccNo:&lt;/b&gt; 0001</summary>
<updated>2026-01-01T07:30:00-05:00</updated><category term="8-K" label="form type"/><id>urn:tag:sec.gov,2008:accession-number=0001</id>
</entry></feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="x"><title>t</title></channel>
<item rdf:about="https://example.org/1"><title>Fed holds rates steady</title><link>https://example.org/1</link>
<dc:date>2026-01-01T19:00:00Z</dc:date></item></rdf:RDF>"""


def test_rss_con_cdata_html_categorias_y_fechas():
    e = parse_feed(RSS)
    assert len(e) == 2
    assert e[0].title == "SEC approves spot Ether ETF options & more" and e[0].guid == "abc-123"
    assert e[0].categories == ("Policy",)
    assert clean_text(e[0].summary) == "The SEC approved options."             # sin script ni etiquetas
    assert parse_datetime(e[0].published) == 1767268800.0
    assert parse_datetime(e[1].published) == 1767269100.0                       # dc:date ISO


def test_atom_de_edgar_y_rdf():
    a = parse_feed(ATOM)[0]
    assert a.link.endswith("x-index.htm") and a.categories == ("8-K",)
    assert parse_datetime(a.published) == 1767270600.0                          # -05:00 → UTC
    r = parse_feed(RDF)[0]
    assert r.title == "Fed holds rates steady" and r.guid == "https://example.org/1"


def test_xml_hostil_se_rechaza_y_ampersand_suelto_se_tolera():
    bomb = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""
    with pytest.raises(FeedError, match="seguridad"):
        parse_feed(bomb)
    xxe = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&x;</title></item></channel></rss>"""
    with pytest.raises(FeedError, match="seguridad"):
        parse_feed(xxe)
    roto = b"<rss><channel><item><title>AT&T and Coinbase partner</title></item></channel></rss>"
    assert parse_feed(roto)[0].title == "AT&T and Coinbase partner"
    with pytest.raises(FeedError):
        parse_feed(b"<html><body>no es un feed</body></html>")
    with pytest.raises(FeedError):
        parse_feed(b"")


def test_url_canonica_quita_seguimiento_www_y_fragmento():
    u = "HTTPS://WWW.Example.com:443/a/b/?utm_source=x&id=7&fbclid=1&b=2#frag"
    assert canonical_url(u) == "https://example.com/a/b?b=2&id=7"
    assert canonical_url("https://example.com") == "https://example.com/"
    assert canonical_url("not a url") == "not a url"


def test_fechas_variantes_y_basura():
    assert parse_datetime("2026-01-01 12:00:00") == 1767268800.0                 # sin zona = UTC
    assert parse_datetime("2026-01-01T12:00:00.000Z") == 1767268800.0
    assert parse_datetime("Thu, 01 Jan 2026 09:00:00 -0300") == 1767268800.0
    assert parse_datetime("ayer") is None and parse_datetime("") is None


def test_clave_de_titulo_y_tokens():
    assert title_key("SEC Sues Binance — CoinDesk") == title_key("SEC sues Binance!")
    assert "binance" in tokens("The SEC sues Binance") and "the" not in tokens("The SEC sues Binance")
