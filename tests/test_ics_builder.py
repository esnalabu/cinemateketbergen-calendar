from datetime import date, datetime, timezone

import pytest
from icalendar import Calendar

import update_calendar as uc


def screening(
    start: datetime = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc),
    ticket_url: str | None = (
        "https://cinemateketbergen.ticketco.events/no/nb/e/testfilm__111_kl_1600"
    ),
) -> uc.Screening:
    return uc.Screening(
        title="TESTFILM",
        description="Ein testfilm med komma, semikolon og æøå.",
        detail_url="https://www.cinemateketbergen.no/arrangementer/testfilm",
        ticket_url=ticket_url,
        start=start,
        duration_minutes=120,
    )


def find_event(content: str):
    calendar = Calendar.from_ical(content)
    events = [item for item in calendar.walk() if item.name == "VEVENT"]
    assert len(events) == 1
    return events[0]


def test_uid_is_deterministic_for_the_same_screening():
    first = uc.uid_for(screening())
    second = uc.uid_for(screening())

    assert first == second
    assert first == "cinemateket-testfilm__111_kl_1600-2026@cinemateketkalender.no"


def test_uid_changes_for_a_different_year():
    first = uc.uid_for(screening())
    second = uc.uid_for(
        screening(start=datetime(2027, 11, 1, 15, 0, tzinfo=timezone.utc))
    )

    assert first != second


def test_uid_falls_back_to_hash_without_ticket_url():
    ticketless = screening(ticket_url=None)
    first = uc.uid_for(ticketless)
    second = uc.uid_for(ticketless)

    assert first == second
    assert first.startswith("cinemateket-")
    assert first.endswith("@cinemateketkalender.no")
    assert "testfilm__111" not in first


def test_builds_valid_ics_with_utc_times_and_duration():
    content = uc.build_calendar(
        [screening()],
        now=datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),
    )
    event = find_event(content)

    assert content.startswith("BEGIN:VCALENDAR\r\n")
    assert content.endswith("END:VCALENDAR\r\n")
    assert str(event["UID"]) == (
        "cinemateket-testfilm__111_kl_1600-2026@cinemateketkalender.no"
    )
    assert event.decoded("dtstart") == datetime(
        2026, 11, 1, 15, 0, tzinfo=timezone.utc
    )
    assert event.decoded("dtend") == datetime(
        2026, 11, 1, 17, 0, tzinfo=timezone.utc
    )
    assert str(event["SUMMARY"]) == "TESTFILM"
    assert str(event["LOCATION"]) == (
        "Cinemateket Bergen, USF Verftet, Georgernes verft 12, 5011 Bergen"
    )
    assert event["CATEGORIES"].to_ical().decode("utf-8") == "FILM"
    assert str(event["STATUS"]) == "CONFIRMED"
    assert str(event["TRANSP"]) == "OPAQUE"


def test_description_contains_runtime_ticket_and_detail_url():
    content = uc.build_calendar([screening()])
    event = find_event(content)
    description = str(event["DESCRIPTION"])

    assert "Ein testfilm med komma, semikolon og æøå." in description
    assert "Spilletid: 2t" in description
    assert "Billetter: https://cinemateketbergen.ticketco.events/" in description
    assert "Mer info: https://www.cinemateketbergen.no/arrangementer/testfilm" in description


def test_escapes_text_and_roundtrips_special_characters():
    item = screening()
    item.title = "A, B; C\\D æøå"
    content = uc.build_calendar([item])
    event = find_event(content)

    assert str(event["SUMMARY"]) == "A, B; C\\D æøå"


def test_folds_long_lines_at_75_utf8_bytes():
    item = screening()
    item.title = "Æ" * 100
    content = uc.build_calendar([item])

    for line in content.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_write_atomic_keeps_existing_file_on_invalid_content(tmp_path):
    target = tmp_path / "calendar.ics"
    target.write_text("gammalt innhald", encoding="utf-8")

    with pytest.raises(ValueError):
        uc.write_atomic(target, "ikkje ein gyldig kalender")

    assert target.read_text(encoding="utf-8") == "gammalt innhald"


def test_write_atomic_replaces_file_with_valid_content(tmp_path):
    target = tmp_path / "calendar.ics"
    content = uc.build_calendar([screening()])

    uc.write_atomic(target, content)

    assert target.read_bytes() == content.encode("utf-8")


def test_calendar_contains_refresh_hints():
    content = uc.build_calendar([screening()])

    assert "X-PUBLISHED-TTL:P1W" in content
    assert "REFRESH-INTERVAL;VALUE=DURATION:P1W" in content
    assert "X-WR-TIMEZONE:Europe/Oslo" in content


def test_builds_all_day_event():
    item = screening(ticket_url=None)
    item.all_day_date = date(2026, 11, 21)
    content = uc.build_calendar([item])
    event = find_event(content)

    assert event.decoded("dtstart") == date(2026, 11, 21)
    assert event.decoded("dtend") == date(2026, 11, 22)
    assert "Heildag" in str(event["DESCRIPTION"])
    assert "DTSTART;VALUE=DATE:20261121" in content
    assert "DTEND;VALUE=DATE:20261122" in content


def test_ticketless_uid_is_stable_when_time_is_corrected():
    original = screening(ticket_url=None)
    original.source_start = original.start

    corrected = screening(
        start=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
        ticket_url=None,
    )
    corrected.source_start = original.source_start

    assert uc.uid_for(original) == uc.uid_for(corrected)


def test_ticketless_uid_is_stable_for_all_day_event():
    timed = screening(ticket_url=None)
    timed.source_start = timed.start

    all_day = screening(ticket_url=None)
    all_day.source_start = timed.source_start
    all_day.all_day_date = date(2026, 11, 1)

    assert uc.uid_for(timed) == uc.uid_for(all_day)
