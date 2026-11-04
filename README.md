# Cinemateket Bergen kalender

Dette repoet lagar ein abonnementskalender (ICS) med filmprogrammet til
[Cinemateket Bergen](https://www.cinemateketbergen.no/program). Kalenderen blir
oppdatert automatisk kvar måndag.

## Abonner

Legg til denne adressa som ekstern kalender i kalenderappen din:

```text
https://esnalabu.github.io/cinemateketbergen-calendar/cinemateketbergen.ics
```

Kalenderen er offentleg og krev ingen innlogging.

## Oppdatering

Programmet blir henta frå Cinemateket sine nettsider. Kalenderen blir oppdatert
kvar måndag. Dersom ei oppdatering feilar, blir den sist publiserte kalenderen
liggjande.

Kalenderappar hentar endringar på eiga tid. Google Calendar kan bruke timar eller
dagar før nye eller endra visingar blir synlege.

## Tid og varigheit

For visingar med billettar er Ticketco sin ICS-fil hovudkjelda for start- og
sluttid. Dersom sluttida manglar eller er openbert feil, brukar kalenderen
spilletida som er oppgitt på arrangementssida, eller 120 minutt som standard.

For arrangement utan billettar blir eit tydeleg tidsintervall i skildringa brukt
når det finst. Viss berre datoen er truverdig, blir arrangementet lagt inn som
heildagsevent.

Prioritet:

1. Ticketco-ICS for billettvisingar
2. tidsintervall i skildringa
3. oppgitt starttid i programmet
4. heildagsevent når berre dato er truverdig
5. visinga blir hoppa over dersom ho ikkje kan plasserast

## Køyr lokalt

Prosjektet brukar [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python scripts/update_calendar.py
uv run pytest -q
```

Skriptet skriv til `cinemateketbergen.ics` som standard. Bruk `--output` for å
velje ei anna fil, `--no-detail` for å hoppe over detaljsidene, og `--min-events`
for å krevje eit minste tal visingar.

## Avgrensingar

- Programmet på nettsida dekkjer i praksis inneverande og neste månad. Kalenderen
  blir derfor ikkje fullstendig for månader lenger fram.
- Dersom nettsida blir endra, kan oppdateringane feile eller bli utdaterte.
- Enkelte arrangement kan få feil tid, særleg når tida berre står i fritekst.
- Planlagde oppdateringar kan bli forseinka.
- Kalenderappar oppdaterer abonnement på eiga tid; Google Calendar kan bruke
  timar eller dagar.
