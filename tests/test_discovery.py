import pytest

from autoscore.discovery import page_type_for_url, select_subpages
from autoscore.parsing import Link


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://exampleai.com", "homepage"),
        ("https://www.exampleai.com/about-us", "about"),
        ("https://exampleai.com/careers/engineer", "careers"),
        ("https://jobs.ashbyhq.com/exampleai", "careers"),
        ("https://blog.exampleai.com", "blog"),
        ("https://exampleai.com/join-waitlist", "other"),
        ("https://twitter.com/exampleai", None),
    ],
)
def test_page_type_for_url(url: str, expected: str | None) -> None:
    assert page_type_for_url(url, "exampleai.com") == expected


def test_select_subpages_picks_one_per_type_in_priority_order() -> None:
    urls = [
        "https://exampleai.com/blog",
        "https://exampleai.com/pricing",
        "https://exampleai.com/blog/launch-week",
        "https://twitter.com/exampleai",
        "https://exampleai.com/careers",
        "https://exampleai.com/login",
    ]
    links = [Link(url=url, text="") for url in urls]
    assert select_subpages(links, "exampleai.com") == [
        ("careers", "https://exampleai.com/careers"),
        ("pricing", "https://exampleai.com/pricing"),
        ("blog", "https://exampleai.com/blog"),
    ]


def test_select_subpages_respects_cap() -> None:
    links = [Link(url=f"https://exampleai.com/{path}", text="") for path in ["about", "pricing", "careers"]]
    assert select_subpages(links, "exampleai.com", max_pages=2) == [
        ("careers", "https://exampleai.com/careers"),
        ("pricing", "https://exampleai.com/pricing"),
    ]