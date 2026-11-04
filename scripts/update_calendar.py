from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


PROGRAM_URL = "https://www.cinemateketbergen.no/program"
SCREENING_EVENT_TYPE = "https://schema.org/ScreeningEvent"
TICKET_URL_PREFIX = "https://cinemateketbergen.ticketco.events/"
DEFAULT_DURATION_MINUTES = 120
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 8 * 60
TICKET_TIME_TOLERANCE_SECONDS = 60
OSLO_TZ = ZoneInfo("Europe/Oslo")
LOCATION = "Cinemateket Bergen, USF Verftet, Georgernes verft 12, 5011 Bergen"
UID_DOMAIN = "cinemateketkalender.no"
USER_AGENT = (
    "CinemateketKalenderFeed/1.0 "
    "(+https://github.com/esnalabu/cinemateketbergen-calendar)"
)
NETWORK_TIMEOUT_SECONDS = 30
OUTPUT_FILENAME = "cinemateketbergen.ics"

DURATION_PATTERN = re.compile(
    r"Spilletid\s*:\s*(?:(\d+)\s*(?:t|timer|timar))?\s*"
    r"(?:(\d+)\s*(?:min|minutt|minutter))?",
    re.IGNORECASE,
)

TIME_RANGE_PATTERN = re.compile(
    r"kl\.?\s*(\d{1,2})(?:[:.](\d{2}))?\s*"
    r"(?:-|–|—|til|og)\s*"
    r"(?:kl\.?\s*)?(\d{1,2})(?:[:.](\d{2}))?",
    re.IGNORECASE,
)


@dataclass
class ScreeningInfo:
    ticket_url: str | None
    start: datetime


@dataclass
class EventInfo:
    title: str
    description: str
    detail_url: str | None
    screenings: list[ScreeningInfo] = field(default_factory=list)


@dataclass
class Screening:
    title: str
    description: str
    detail_url: str | None
    ticket_url: str | None
    start: datetime
    duration_minutes: int
    all_day_date: date | None = None
    source_start: datetime | None = None


@dataclass(frozen=True)
class PreviousEvent:
    dtstamp: datetime
    sequence: int
    signature: bytes


def parse_instant(value: str) -> datetime | None:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_duration_minutes(text: str) -> int | None:
    match = DURATION_PATTERN.search(text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    total = hours * 60 + minutes
    return total if total > 0 else None


def format_duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}t {remainder}min"
    if hours:
        return f"{hours}t"
    return f"{remainder}min"


def is_plausible_duration(minutes: int | None) -> bool:
    return (
        minutes is not None
        and MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES
    )


def parse_time_range(text: str) -> tuple[dt_time, dt_time] | None:
    match = TIME_RANGE_PATTERN.search(text)
    if not match:
        return None
    start_hour, start_minute, end_hour, end_minute = match.groups()
    try:
        start = dt_time(int(start_hour), int(start_minute or 0))
        end = dt_time(int(end_hour), int(end_minute or 0))
    except ValueError:
        return None
    if start == end:
        return None
    return start, end


def local_range_to_utc(
    source_start: datetime, start_time: dt_time, end_time: dt_time
) -> tuple[datetime, datetime]:
    local_date = source_start.astimezone(OSLO_TZ).date()
    local_start = datetime.combine(local_date, start_time, tzinfo=OSLO_TZ)
    local_end = datetime.combine(local_date, end_time, tzinfo=OSLO_TZ)
    if local_end <= local_start:
        local_end += timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def ticket_ics_url(ticket_url: str) -> str:
    return ticket_url if ticket_url.endswith(".ics") else f"{ticket_url}.ics"


def minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


class ProgramScraper:
    def __init__(
        self,
        session: requests.Session | None = None,
        fetch_detail_durations: bool = True,
        detail_delay_seconds: float = 0.0,
        on_warning: Callable[[str], None] | None = None,
    ) -> None:
        self.session = session or build_session()
        self.fetch_detail_durations = fetch_detail_durations
        self.detail_delay_seconds = detail_delay_seconds
        self.on_warning = on_warning or self._print_warning
        self.warnings: list[str] = []
        self.ticket_times: dict[str, tuple[datetime, datetime] | None] = {}
        self.detail_texts: dict[str, str | None] = {}
        self.detail_durations: dict[str, int] = {}

    @staticmethod
    def _print_warning(message: str) -> None:
        print(f"Åtvaring: {message}", file=sys.stderr)

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        self.on_warning(message)

    def scrape(
        self, on_progress: Callable[[str], None] | None = None
    ) -> list[Screening]:
        self.warnings.clear()
        self.ticket_times.clear()
        self.detail_texts.clear()
        self.detail_durations.clear()

        progress = on_progress or (lambda _: None)
        progress("Hentar program ...")
        html = self.fetch_text(PROGRAM_URL)
        events = self.parse_program(html)
        if not events:
            raise RuntimeError("Fann ingen framsyningar i programmet.")

        ticket_urls = list(
            dict.fromkeys(
                screening.ticket_url
                for event in events
                for screening in event.screenings
                if screening.ticket_url
            )
        )
        for index, ticket_url in enumerate(ticket_urls, start=1):
            progress(f"Les billettinfo {index}/{len(ticket_urls)} ...")
            self.fetch_ticket_times(ticket_url)
            if self.detail_delay_seconds > 0:
                time.sleep(self.detail_delay_seconds)

        result = [
            self.resolve_screening(event, screening)
            for event in events
            for screening in event.screenings
        ]
        result.sort(key=lambda item: (item.start, item.title, item.ticket_url or ""))
        if not result:
            raise RuntimeError("Fann ingen framsyningar i programmet.")
        return result

    def resolve_screening(
        self, event: EventInfo, screening: ScreeningInfo
    ) -> Screening:
        start = screening.start
        duration: int | None = None
        all_day_date: date | None = None

        if screening.ticket_url:
            ticket_times = self.fetch_ticket_times(screening.ticket_url)
            if ticket_times:
                ticket_start, ticket_end = ticket_times
                if (
                    abs(
                        (ticket_start - screening.start).total_seconds()
                    )
                    > TICKET_TIME_TOLERANCE_SECONDS
                ):
                    self._warn(
                        f"{event.title}: Ticketco-ICS start "
                        f"{ticket_start.isoformat()} avvik frå strukturert start "
                        f"{screening.start.isoformat()}; brukar Ticketco-ICS."
                    )
                start = ticket_start
                duration = minutes_between(ticket_start, ticket_end)
                if not is_plausible_duration(duration):
                    self._warn(
                        f"{event.title}: Ticketco-ICS gav ugyldig varigheit "
                        f"({duration} minutt); brukar spilletid frå "
                        "arrangementssida."
                    )
                    duration = self.detail_duration_or_default(event)
            else:
                self._warn(
                    f"{event.title}: Fann ikkje Ticketco-ICS for "
                    f"{screening.ticket_url}; brukar strukturert starttid."
                )
                duration = self.detail_duration_or_default(event)
        else:
            time_range = self.event_time_range(event)
            if time_range:
                range_start, range_end = local_range_to_utc(
                    screening.start, *time_range
                )
                range_duration = minutes_between(range_start, range_end)
                if is_plausible_duration(range_duration):
                    start = range_start
                    duration = range_duration
                    if (
                        abs(
                            (start - screening.start).total_seconds()
                        )
                        > TICKET_TIME_TOLERANCE_SECONDS
                    ):
                        self._warn(
                            f"{event.title}: Korrigerer starttid frå "
                            f"{screening.start.isoformat()} til "
                            f"{start.isoformat()} basert på tidsrange i "
                            "skildringa."
                        )
                else:
                    self._warn(
                        f"{event.title}: Tidsrange i skildringa gav ugyldig "
                        f"varigheit ({range_duration} minutt); ignorerer range."
                    )

            if duration is None:
                local_start = screening.start.astimezone(OSLO_TZ)
                if local_start.time() == dt_time(0, 0):
                    all_day_date = local_start.date()
                    duration = 0
                    self._warn(
                        f"{event.title}: Starttid er midnatt og sluttid "
                        f"manglar; lagar heildagsevent "
                        f"{all_day_date.isoformat()}."
                    )
                else:
                    duration = self.detail_duration_or_default(event)
                    if local_start.hour < 6:
                        self._warn(
                            f"{event.title}: Mistenkeleg starttid "
                            f"{local_start.isoformat()} utan Ticketco-ICS; "
                            "behald strukturert tid."
                        )

        if duration is None:
            duration = DEFAULT_DURATION_MINUTES

        return Screening(
            title=event.title,
            description=event.description,
            detail_url=event.detail_url,
            ticket_url=screening.ticket_url,
            start=start,
            duration_minutes=duration,
            all_day_date=all_day_date,
            source_start=screening.start,
        )

    def event_time_range(self, event: EventInfo) -> tuple[dt_time, dt_time] | None:
        time_range = parse_time_range(event.description)
        if time_range or not self.fetch_detail_durations or not event.detail_url:
            return time_range
        detail_text = self.fetch_detail_text(event.detail_url)
        if detail_text:
            return parse_time_range(detail_text)
        return None

    def detail_duration_or_default(self, event: EventInfo) -> int:
        if not self.fetch_detail_durations or not event.detail_url:
            return DEFAULT_DURATION_MINUTES
        return self.fetch_duration_minutes(event.detail_url)

    def parse_program(self, html: str | BeautifulSoup) -> list[EventInfo]:
        document = (
            BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
        )
        events: dict[str, EventInfo] = {}
        seen_screenings: set[tuple[str, str, str | None]] = set()

        for article in document.find_all("article"):
            itemtype = article.get("itemtype") or article.get("itemType")
            if itemtype != SCREENING_EVENT_TYPE:
                continue

            title_element = article.select_one("h1[itemprop=name]")
            if title_element is None:
                title_element = article.select_one("[itemprop=name]")
            title = (
                title_element.get_text(" ", strip=True) if title_element else ""
            )
            if not title:
                continue

            detail_url = None
            for link in article.select("a[href]"):
                href = link.get("href", "").strip()
                if not href:
                    continue
                absolute = urljoin(PROGRAM_URL, href)
                if "/arrangementer/" in absolute:
                    detail_url = absolute
                    break

            description_element = article.select_one("[itemprop=description]")
            description = (
                description_element.get_text(" ", strip=True)
                if description_element
                else ""
            )

            event_key = detail_url or f"title:{title}"
            event = events.get(event_key)
            if event is None:
                event = EventInfo(
                    title=title,
                    description=description,
                    detail_url=detail_url,
                )
                events[event_key] = event
            elif not event.description and description:
                event.description = description

            for start_element in article.select("[itemprop=startDate]"):
                raw_start = start_element.get("content") or ""
                start = parse_instant(raw_start)
                if start is None:
                    self._warn(
                        f"{title}: Hopper over vising med ugyldig eller "
                        f"manglande starttid {raw_start!r}."
                    )
                    continue

                parent_link = start_element.find_parent("a")
                href = (parent_link.get("href") if parent_link else "") or ""
                href = href.strip()
                ticket_url = urljoin(PROGRAM_URL, href) if href else None
                if ticket_url and not ticket_url.startswith(TICKET_URL_PREFIX):
                    ticket_url = None

                screening_key = (
                    event_key,
                    start.isoformat(),
                    ticket_url,
                )
                if screening_key in seen_screenings:
                    continue
                seen_screenings.add(screening_key)
                event.screenings.append(
                    ScreeningInfo(ticket_url=ticket_url, start=start)
                )

        return [event for event in events.values() if event.screenings]

    def fetch_ticket_times(
        self, ticket_url: str
    ) -> tuple[datetime, datetime] | None:
        if ticket_url in self.ticket_times:
            return self.ticket_times[ticket_url]

        url = ticket_ics_url(ticket_url)
        try:
            content = self.fetch_text(url)
            calendar = Calendar.from_ical(content)
            event = next(
                (
                    component
                    for component in calendar.walk()
                    if component.name == "VEVENT"
                ),
                None,
            )
            if event is None:
                raise ValueError("manglar VEVENT")
            start = event.decoded("dtstart")
            end = event.decoded("dtend")
            if not isinstance(start, datetime) or not isinstance(end, datetime):
                raise ValueError("manglar DTSTART/DTEND")
            if start.tzinfo is None:
                start = start.replace(tzinfo=OSLO_TZ)
            if end.tzinfo is None:
                end = end.replace(tzinfo=OSLO_TZ)
            times = (
                start.astimezone(timezone.utc),
                end.astimezone(timezone.utc),
            )
        except Exception as exc:
            self.ticket_times[ticket_url] = None
            self._warn(
                f"Klarte ikkje å lese Ticketco-ICS for {ticket_url}: {exc}"
            )
            return None

        self.ticket_times[ticket_url] = times
        return times

    def fetch_detail_text(self, url: str) -> str | None:
        if url in self.detail_texts:
            return self.detail_texts[url]
        try:
            text = self.fetch_text(url)
        except Exception as exc:
            self.detail_texts[url] = None
            self._warn(f"Klarte ikkje å hente arrangementssida {url}: {exc}")
            return None
        self.detail_texts[url] = text
        return text

    def fetch_duration_minutes(self, url: str) -> int:
        if url in self.detail_durations:
            return self.detail_durations[url]

        text = self.fetch_detail_text(url)
        if text is None:
            duration = DEFAULT_DURATION_MINUTES
        else:
            document = BeautifulSoup(text, "html.parser")
            parsed_duration = parse_duration_minutes(
                document.get_text(" ", strip=True)
            )
            if parsed_duration is None:
                self._warn(
                    f"Fann ikkje spilletid på {url}; brukar "
                    f"{DEFAULT_DURATION_MINUTES} minutt."
                )
                duration = DEFAULT_DURATION_MINUTES
            elif not is_plausible_duration(parsed_duration):
                self._warn(
                    f"Spilletid på {url} er ugyldig "
                    f"({parsed_duration} minutt); brukar "
                    f"{DEFAULT_DURATION_MINUTES} minutt."
                )
                duration = DEFAULT_DURATION_MINUTES
            else:
                duration = parsed_duration

        self.detail_durations[url] = duration
        return duration

    def fetch_text(self, url: str) -> str:
        response = self.session.get(url, timeout=NETWORK_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,text/calendar",
        }
    )
    return session


def ticket_slug(ticket_url: str | None) -> str | None:
    if not ticket_url:
        return None
    raw = urlparse(ticket_url).path.rsplit("/", 1)[-1]
    if not raw:
        return None
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_")
    return safe or None


def uid_for(screening: Screening) -> str:
    slug = ticket_slug(screening.ticket_url)
    if slug:
        year = screening.start.astimezone(OSLO_TZ).year
        return f"cinemateket-{slug}-{year}@{UID_DOMAIN}"
    source_start = screening.source_start or screening.start
    identity = screening.detail_url or screening.title
    digest = hashlib.sha256(
        f"{identity}|{source_start.isoformat()}".encode("utf-8")
    ).hexdigest()[:32]
    return f"cinemateket-{digest}@{UID_DOMAIN}"


def description_for(screening: Screening) -> str:
    if screening.all_day_date:
        details = ["Heildag: kjelda oppgir berre dato, ikkje klokkeslett."]
    else:
        details = [f"Spilletid: {format_duration(screening.duration_minutes)}"]
    if screening.ticket_url:
        details.append(f"Billetter: {screening.ticket_url}")
    if screening.detail_url:
        details.append(f"Mer info: {screening.detail_url}")
    description = screening.description.strip()
    if description:
        return f"{description}\n\n" + "\n".join(details)
    return "\n".join(details)


def event_url_for(screening: Screening) -> str:
    return screening.ticket_url or screening.detail_url or PROGRAM_URL


def create_event(
    screening: Screening, dtstamp: datetime, sequence: int
) -> Event:
    event = Event()
    event.add("uid", uid_for(screening))
    event.add("dtstamp", dtstamp)
    if screening.all_day_date:
        event.add("dtstart", screening.all_day_date)
        event.add("dtend", screening.all_day_date + timedelta(days=1))
    else:
        event.add("dtstart", screening.start)
        event.add(
            "dtend",
            screening.start + timedelta(minutes=screening.duration_minutes),
        )
    event.add("summary", screening.title)
    event.add("description", description_for(screening))
    event.add("location", LOCATION)
    event.add("url", event_url_for(screening))
    event.add("categories", ["FILM"])
    event.add("status", "CONFIRMED")
    event.add("sequence", sequence)
    event.add("transp", "OPAQUE")
    return event


def event_signature(event: Event) -> bytes:
    return b"\r\n".join(
        line
        for line in event.to_ical().split(b"\r\n")
        if not line.startswith((b"DTSTAMP", b"SEQUENCE"))
    )


def previous_events(content: str | None) -> dict[str, PreviousEvent]:
    if not content:
        return {}

    try:
        calendar = Calendar.from_ical(content)
    except Exception:
        return {}

    events: dict[str, PreviousEvent] = {}
    for component in calendar.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", "")).strip()
        if not uid:
            continue

        try:
            dtstamp = component.decoded("DTSTAMP")
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(dtstamp, datetime):
            continue
        if dtstamp.tzinfo is None:
            dtstamp = dtstamp.replace(tzinfo=timezone.utc)

        try:
            sequence = int(component.decoded("SEQUENCE"))
        except (KeyError, TypeError, ValueError):
            sequence = 0

        events[uid] = PreviousEvent(
            dtstamp=dtstamp.astimezone(timezone.utc),
            sequence=max(sequence, 0),
            signature=event_signature(component),
        )
    return events


def build_calendar(
    screenings: Iterable[Screening],
    now: datetime | None = None,
    previous_content: str | None = None,
) -> str:
    timestamp = now or datetime.now(timezone.utc)
    previous = previous_events(previous_content)
    calendar = Calendar()
    calendar.add("prodid", "-//Cinemateket Kalender//NO")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "Cinemateket Bergen")
    calendar.add("x-wr-caldesc", "Program for Cinemateket Bergen")
    calendar.add("x-wr-timezone", "Europe/Oslo")
    calendar.add("refresh-interval", "P1W", parameters={"VALUE": "DURATION"})
    calendar.add("x-published-ttl", "P1W")

    for screening in sorted(screenings, key=lambda item: item.start):
        preview = create_event(screening, timestamp, 0)
        existing = previous.get(uid_for(screening))
        if existing and existing.signature == event_signature(preview):
            dtstamp = existing.dtstamp
            sequence = existing.sequence
        else:
            dtstamp = timestamp
            sequence = existing.sequence + 1 if existing else 0
        calendar.add_component(create_event(screening, dtstamp, sequence))

    return calendar.to_ical().decode("utf-8")


def validate_calendar(content: str) -> int:
    calendar = Calendar.from_ical(content)
    events = [component for component in calendar.walk() if component.name == "VEVENT"]
    if not events:
        raise ValueError("Kalenderen inneheld ingen VEVENT.")
    return len(events)


def write_atomic(path: Path, content: str) -> None:
    validate_calendar(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(content.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def default_output_path() -> Path:
    return Path(__file__).resolve().parents[1] / OUTPUT_FILENAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hentar filmprogrammet til Cinemateket Bergen og lagar ei ICS-fil."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help=f"Stad for ICS-fila (standard: {OUTPUT_FILENAME}).",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Hopp over henting av spilletid og detaljtekst frå arrangementssidene.",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=1,
        help="Krev minst dette talet på visingar før fila vert skriven.",
    )
    args = parser.parse_args(argv)

    scraper = ProgramScraper(
        fetch_detail_durations=not args.no_detail,
        detail_delay_seconds=0.1,
    )
    screenings = scraper.scrape(on_progress=print)
    if scraper.warnings:
        print(
            f"Fekk {len(scraper.warnings)} åtvaring(ar).",
            file=sys.stderr,
        )
    if len(screenings) < args.min_events:
        print(
            f"Fann berre {len(screenings)} visingar, krevde {args.min_events}.",
            file=sys.stderr,
        )
        return 1

    previous_content = (
        args.output.read_text(encoding="utf-8")
        if args.output.exists()
        else None
    )
    content = build_calendar(screenings, previous_content=previous_content)
    write_atomic(args.output, content)
    print(f"Skreiv {len(screenings)} visingar til {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
