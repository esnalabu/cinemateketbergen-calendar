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


def find_events(content: str):
    calendar = Calendar.from_ical(content)
    return {
        str(item["UID"]): item
        for item in calendar.walk()
        if item.name == "VEVENT"
    }


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


def test_unchanged_event_keeps_dtstamp_and_output_is_identical():
    first_time = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    first = uc.build_calendar([screening()], now=first_time)
    second = uc.build_calendar(
        [screening()], now=second_time, previous_content=first
    )

    event = find_event(second)
    assert event.decoded("dtstamp") == first_time
    assert int(event["SEQUENCE"]) == 0
    assert second == first


def test_changed_event_gets_new_dtstamp_and_incremented_sequence():
    first_time = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    first = uc.build_calendar([screening()], now=first_time)
    changed = screening()
    changed.title = "ENDRET FILM"

    second = uc.build_calendar(
        [changed], now=second_time, previous_content=first
    )
    event = find_event(second)

    assert event.decoded("dtstamp") == second_time
    assert int(event["SEQUENCE"]) == 1


def test_sequence_increments_on_each_change():
    first_time = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    third_time = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    first = uc.build_calendar([screening()], now=first_time)
    changed = screening()
    changed.title = "ENDRET FILM"
    second = uc.build_calendar(
        [changed], now=second_time, previous_content=first
    )
    changed_again = screening()
    changed_again.title = "ENDRET IGJEN"

    third = uc.build_calendar(
        [changed_again], now=third_time, previous_content=second
    )
    event = find_event(third)

    assert event.decoded("dtstamp") == third_time
    assert int(event["SEQUENCE"]) == 2


def test_new_event_gets_current_dtstamp_and_sequence_zero():
    first_time = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    previous = uc.build_calendar(
        [
            screening(
                ticket_url=(
                    "https://cinemateketbergen.ticketco.events/"
                    "no/nb/e/annafilm__222_kl_1800"
                )
            )
        ],
        now=first_time,
    )

    content = uc.build_calendar(
        [screening()], now=second_time, previous_content=previous
    )
    event = find_event(content)

    assert event.decoded("dtstamp") == second_time
    assert int(event["SEQUENCE"]) == 0


def test_only_changed_event_gets_new_dtstamp():
    first_time = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    other = screening(
        ticket_url=(
            "https://cinemateketbergen.ticketco.events/"
            "no/nb/e/annafilm__222_kl_1800"
        )
    )
    first = uc.build_calendar([screening(), other], now=first_time)
    changed = screening()
    changed.title = "ENDRET FILM"

    content = uc.build_calendar(
        [changed, other], now=second_time, previous_content=first
    )
    events = find_events(content)

    assert events[uc.uid_for(changed)].decoded("dtstamp") == second_time
    assert events[uc.uid_for(other)].decoded("dtstamp") == first_time
    assert int(events[uc.uid_for(changed)]["SEQUENCE"]) == 1
    assert int(events[uc.uid_for(other)]["SEQUENCE"]) == 0


def test_removed_event_is_omitted():
    first_time = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    other = screening(
        ticket_url=(
            "https://cinemateketbergen.ticketco.events/"
            "no/nb/e/annafilm__222_kl_1800"
        )
    )
    first = uc.build_calendar([screening(), other], now=first_time)
    assert len(find_events(first)) == 2

    content = uc.build_calendar(
        [screening()], now=second_time, previous_content=first
    )
    events = find_events(content)

    assert len(events) == 1
    assert uc.uid_for(screening()) in events


def test_invalid_previous_content_falls_back_to_current_dtstamp():
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    content = uc.build_calendar(
        [screening()], now=now, previous_content="ikkje ein gyldig kalender"
    )
    event = find_event(content)

    assert event.decoded("dtstamp") == now
    assert int(event["SEQUENCE"]) == 0
