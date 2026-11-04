from datetime import date, datetime, time as dt_time, timezone
from pathlib import Path

import update_calendar as uc


FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_events_and_removes_duplicate_screenings():
    html = (FIXTURES / "program_sample.html").read_text(encoding="utf-8")
    events = uc.ProgramScraper(fetch_detail_durations=False).parse_program(html)

    assert len(events) == 3

    testfilm = next(event for event in events if event.title == "TESTFILM")
    assert (
        testfilm.detail_url
        == "https://www.cinemateketbergen.no/arrangementer/testfilm"
    )
    assert len(testfilm.screenings) == 2
    assert testfilm.screenings[0].start == datetime(
        2026, 11, 1, 15, 0, tzinfo=timezone.utc
    )
    assert (
        testfilm.screenings[0].ticket_url
        == "https://cinemateketbergen.ticketco.events/no/nb/e/testfilm__111_kl_1600"
    )


def test_includes_screening_without_ticket_link():
    html = (FIXTURES / "program_sample.html").read_text(encoding="utf-8")
    events = uc.ProgramScraper(fetch_detail_durations=False).parse_program(html)

    gratis = next(event for event in events if event.title == "GRATISFILM")
    assert len(gratis.screenings) == 1
    assert gratis.screenings[0].ticket_url is None
    assert gratis.screenings[0].start == datetime(
        2026, 11, 22, 12, 0, tzinfo=timezone.utc
    )


def test_parses_duration_with_hours_and_minutes():
    assert uc.parse_duration_minutes("Spilletid: 2t 10min") == 130
    assert uc.parse_duration_minutes("Spilletid: 1 t 45 min") == 105
    assert uc.parse_duration_minutes("Spilletid: 2t") == 120
    assert uc.parse_duration_minutes("Spilletid: 95 min") == 95
    assert uc.parse_duration_minutes("Ingen spilletid oppgitt") is None


def test_parses_utc_timestamps_with_z_and_offset():
    assert uc.parse_instant("2026-11-01T15:00:00.000Z") == datetime(
        2026, 11, 1, 15, 0, tzinfo=timezone.utc
    )
    assert uc.parse_instant("2026-11-01T15:00:00+00:00") == datetime(
        2026, 11, 1, 15, 0, tzinfo=timezone.utc
    )
    assert uc.parse_instant("ikkje ei tid") is None


def test_groups_duplicate_articles_by_detail_url():
    html = (FIXTURES / "program_sample.html").read_text(encoding="utf-8")
    events = uc.ProgramScraper(fetch_detail_durations=False).parse_program(html)

    testfilm_events = [event for event in events if event.title == "TESTFILM"]
    assert len(testfilm_events) == 1
    assert len(testfilm_events[0].screenings) == 2


def test_skips_screening_with_invalid_start_time():
    html = """
    <article itemtype="https://schema.org/ScreeningEvent">
        <h1 itemprop="name">TESTFILM</h1>
        <a href="/arrangementer/testfilm">Les mer</a>
        <a href="https://cinemateketbergen.ticketco.events/no/nb/e/testfilm__111">
            <span itemprop="startDate" content="ikkje ei tid"></span>
        </a>
    </article>
    """
    warnings = []
    events = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    ).parse_program(html)
    assert events == []
    assert len(warnings) == 1
    assert "TESTFILM" in warnings[0]


def test_fetch_duration_falls_back_on_error():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    )

    def fail(_url):
        raise RuntimeError("nettverksfeil")

    scraper.fetch_text = fail
    assert scraper.fetch_duration_minutes("https://example.com") == 120
    assert len(warnings) == 1


def test_fetch_duration_uses_plausible_detail_duration():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=True,
        on_warning=warnings.append,
    )
    scraper.fetch_text = lambda _url: (
        "<html><body><p>Spilletid: 95 min</p></body></html>"
    )

    assert scraper.fetch_duration_minutes("https://example.com") == 95
    assert warnings == []


def test_fetch_duration_rejects_implausible_detail_duration():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=True,
        on_warning=warnings.append,
    )
    scraper.fetch_text = lambda _url: (
        "<html><body><p>Spilletid: 5000 min</p></body></html>"
    )

    assert scraper.fetch_duration_minutes("https://example.com") == 120
    assert len(warnings) == 1
    assert "ugyldig" in warnings[0]


def test_parses_time_ranges_from_description():
    assert uc.parse_time_range("Ingen tid oppgitt") is None
    assert uc.parse_time_range("drop-in fra kl. 12 til 15") == (
        dt_time(12, 0),
        dt_time(15, 0),
    )
    assert uc.parse_time_range("kl. 12.00–15.30") == (
        dt_time(12, 0),
        dt_time(15, 30),
    )
    assert uc.parse_time_range("kl. 23:00-01:00") == (
        dt_time(23, 0),
        dt_time(1, 0),
    )


def test_local_range_uses_local_date():
    start, end = uc.local_range_to_utc(
        datetime(2026, 11, 21, 23, 0, tzinfo=timezone.utc),
        dt_time(12, 0),
        dt_time(15, 0),
    )

    assert start == datetime(2026, 11, 22, 11, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 22, 14, 0, tzinfo=timezone.utc)


def test_local_range_uses_local_date_in_summer_time():
    start, end = uc.local_range_to_utc(
        datetime(2026, 7, 1, 22, 0, tzinfo=timezone.utc),
        dt_time(12, 0),
        dt_time(15, 0),
    )

    assert start == datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 2, 13, 0, tzinfo=timezone.utc)


def test_ticket_ics_url_appends_suffix_once():
    assert uc.ticket_ics_url("https://example.com/e/abc") == (
        "https://example.com/e/abc.ics"
    )
    assert uc.ticket_ics_url("https://example.com/e/abc.ics") == (
        "https://example.com/e/abc.ics"
    )


def test_fetch_ticket_times_parses_and_caches_ics():
    calls = 0
    warnings = []

    def fetch_text(_url):
        nonlocal calls
        calls += 1
        return """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1214683
DTSTAMP:20261103T201357Z
DTSTART:20261201T190000Z
DTEND:20261201T205400Z
SUMMARY:THE DEVILS
END:VEVENT
END:VCALENDAR
"""

    scraper = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    )
    scraper.fetch_text = fetch_text

    result = scraper.fetch_ticket_times(
        "https://cinemateketbergen.ticketco.events/no/nb/e/the_devils__112_kl_1600"
    )

    assert result == (
        datetime(2026, 12, 1, 19, 0, tzinfo=timezone.utc),
        datetime(2026, 12, 1, 20, 54, tzinfo=timezone.utc),
    )
    assert scraper.fetch_ticket_times(
        "https://cinemateketbergen.ticketco.events/no/nb/e/the_devils__112_kl_1600"
    ) == result
    assert calls == 1
    assert warnings == []


def test_fetch_ticket_times_reports_invalid_ics():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    )
    scraper.fetch_text = lambda _url: "<html>ikkje ein kalender</html>"

    assert (
        scraper.fetch_ticket_times(
            "https://cinemateketbergen.ticketco.events/no/nb/e/testfilm"
        )
        is None
    )
    assert len(warnings) == 1


def test_resolve_screening_uses_ticketco_times():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    )
    scraper.fetch_ticket_times = lambda _url: (
        datetime(2026, 12, 1, 19, 0, tzinfo=timezone.utc),
        datetime(2026, 12, 1, 20, 54, tzinfo=timezone.utc),
    )
    event = uc.EventInfo(
        title="THE DEVILS",
        description="Skrekkfilm.",
        detail_url="https://www.cinemateketbergen.no/arrangementer/the-devils",
    )
    info = uc.ScreeningInfo(
        ticket_url=(
            "https://cinemateketbergen.ticketco.events/no/nb/e/"
            "the_devils__112_kl_1600"
        ),
        start=datetime(2026, 12, 1, 19, 0, tzinfo=timezone.utc),
    )

    result = scraper.resolve_screening(event, info)

    assert result.start == info.start
    assert result.duration_minutes == 114
    assert result.all_day_date is None
    assert warnings == []


def test_resolve_screening_rejects_implausible_ticketco_duration():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=True,
        on_warning=warnings.append,
    )
    scraper.fetch_ticket_times = lambda _url: (
        datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 11, 21, 9, tzinfo=timezone.utc),
    )
    scraper.fetch_duration_minutes = lambda _url: 189
    event = uc.EventInfo(
        title="JFK",
        description="Film.",
        detail_url="https://www.cinemateketbergen.no/arrangementer/jfk",
    )
    info = uc.ScreeningInfo(
        ticket_url=(
            "https://cinemateketbergen.ticketco.events/no/nb/e/"
            "huff_jfk__911_kl_1900"
        ),
        start=datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc),
    )

    result = scraper.resolve_screening(event, info)

    assert result.duration_minutes == 189
    assert any("ugyldig varigheit" in warning for warning in warnings)


def test_resolve_screening_corrects_ticketless_time_range():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    )
    event = uc.EventInfo(
        title="FAMILIEDAG",
        description="Gratis inngang. Drop-in fra kl. 12 til 15.",
        detail_url="https://www.cinemateketbergen.no/arrangementer/familiedag",
    )
    info = uc.ScreeningInfo(
        ticket_url=None,
        start=datetime(2026, 11, 21, 23, 0, tzinfo=timezone.utc),
    )

    result = scraper.resolve_screening(event, info)

    assert result.start == datetime(2026, 11, 22, 11, 0, tzinfo=timezone.utc)
    assert result.duration_minutes == 180
    assert result.all_day_date is None
    assert any("Korrigerer starttid" in warning for warning in warnings)


def test_resolve_screening_uses_all_day_for_midnight_without_range():
    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    )
    event = uc.EventInfo(
        title="MIDNATTSARRANGEMENT",
        description="Gratis inngang.",
        detail_url="https://www.cinemateketbergen.no/arrangementer/midnatt",
    )
    info = uc.ScreeningInfo(
        ticket_url=None,
        start=datetime(2026, 11, 21, 23, 0, tzinfo=timezone.utc),
    )

    result = scraper.resolve_screening(event, info)

    assert result.all_day_date == date(2026, 11, 22)
    assert result.duration_minutes == 0
    assert any("2026-11-22" in warning for warning in warnings)
    assert any("heildagsevent" in warning for warning in warnings)


def test_scrape_prefetches_ticketco_ics():
    program_html = """
    <article itemtype="https://schema.org/ScreeningEvent">
        <h1 itemprop="name">TESTFILM</h1>
        <a href="/arrangementer/testfilm">Les mer</a>
        <div itemprop="description">Ein testfilm.</div>
        <a href="https://cinemateketbergen.ticketco.events/no/nb/e/testfilm__111_kl_1600">
            <span itemprop="startDate" content="2026-11-01T15:00:00.000Z"></span>
        </a>
    </article>
    """
    ticket_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test
DTSTAMP:20261103T201357Z
DTSTART:20261101T150000Z
DTEND:20261101T170000Z
SUMMARY:TESTFILM
END:VEVENT
END:VCALENDAR
"""
    calls = []

    def fetch_text(url):
        calls.append(url)
        if url == uc.PROGRAM_URL:
            return program_html
        if url.endswith(".ics"):
            return ticket_ics
        raise AssertionError(f"Uventa URL: {url}")

    warnings = []
    scraper = uc.ProgramScraper(
        fetch_detail_durations=False,
        on_warning=warnings.append,
    )
    scraper.fetch_text = fetch_text

    result = scraper.scrape()

    assert len(result) == 1
    assert result[0].start == datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)
    assert result[0].duration_minutes == 120
    assert any(url.endswith(".ics") for url in calls)
    assert warnings == []
