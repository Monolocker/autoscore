from autoscore.parsing import looks_js_rendered, parse_html

PAGE_HTML = """
<html>
<head>
  <title> Example AI | Lead enrichment </title>
  <meta name="description" content="Enrich every inbound lead.">
  <style>.hero { color: red; }</style>
</head>
<body>
  <nav>
    <a href="/pricing">Pricing</a>
    <a href="/careers/">Careers</a>
    <a href="https://exampleai.com/pricing#plans">Plans</a>
    <a href="mailto:hi@exampleai.com">Email</a>
    <a href="#top">Top</a>
    <a href="https://twitter.com/exampleai">Twitter</a>
  </nav>
  <h1>Turn   leads    into customers</h1>
  <script>window.track("visit");</script>
  <p>Built for B2B sales teams.</p>
  <p>Built for B2B sales teams.</p>
</body>
</html>
"""


def test_extracts_title_and_description() -> None:
    page = parse_html(PAGE_HTML, "https://exampleai.com")
    assert page.title == "Example AI | Lead enrichment"
    assert page.description == "Enrich every inbound lead."


def test_text_excludes_scripts_and_styles_and_collapses_whitespace() -> None:
    page = parse_html(PAGE_HTML, "https://exampleai.com")
    assert "window.track" not in page.text
    assert "color: red" not in page.text
    assert "Turn leads into customers" in page.text
    assert page.text.count("Built for B2B sales teams.") == 1


def test_links_are_absolute_deduplicated_and_filtered() -> None:
    page = parse_html(PAGE_HTML, "https://exampleai.com")
    assert [link.url for link in page.links] == [
        "https://exampleai.com/pricing",
        "https://exampleai.com/careers",
        "https://twitter.com/exampleai",
    ]


def test_detects_probably_js_rendered_page() -> None:
    js_html = "<html><body><div id='root'></div><script>" + "x" * 30_000 + "</script></body></html>"
    assert looks_js_rendered(js_html, parse_html(js_html, "https://exampleai.com").text)
    assert not looks_js_rendered(PAGE_HTML, parse_html(PAGE_HTML, "https://exampleai.com").text)