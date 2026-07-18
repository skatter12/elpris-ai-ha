# Elpris AI - Home Assistant Add-on

AI-drevet elprisberegner for Danmark der forudser priser 7 dage frem.

## Funktioner

- **Historisk data**: Henter elpriser fra Energi Data Service (30 dage tilbage)
- **Vejrprognoser**: Bruger DMI vejrdata (temperatur, vind, skydække)
- **Råvarepriser**: Inkluderer CO2-udledning og energiproduktion
- **ML-model**: Prophet-tidsrækkefølgeforudsigelse med ekstra regressorer
- **REST API**: Eksponerer data til Home Assistant
- **Automatisk genoptræning**: Modellen genoptrænes automatisk

## Installation

### 1. Installer Add-on'et

1. Kopier mappen `elpris-ai` til `/addons/` på din Home Assistant
2. Genstart Home Assistant
3. Gå til **Settings > Add-ons** og find **Elpris AI**
4. Klik **Install** og derefter **Start**

### 2. Installer Custom Component

1. Kopier mappen `custom_components/elpris_ai` til dit Home Assistant config directory
2. Genstart Home Assistant
3. Gå til **Settings > Devices & services**
4. Klik **Add Integration** og søg efter **Elpris AI**
5. Indtast host og port (standard: `192.168.1.68:8001`)

### 3. Konfigurer

Gå til **Settings > Add-ons > Elpris AI > Configuration** og tilpas:

- **Region**: Vælg DK1 eller DK2
- **Momsprocent**: Standard 25%
- **Fast tillæg**: Tillæg i kr/kWh (nettarif, etc.)
- **Opdateringsinterval**: Hvor ofte data opdateres
- **Forecast dage**: Hvor mange dage der forudsiges

## Sensorer

Efter installation får du disse sensorer:

| Sensor | Beskrivelse |
|--------|-------------|
| `sensor.elpris_ai_current_price` | Nuværende timepris (inkl. moms og tillæg) |
| `sensor.elpris_ai_cheapest_today` | Billigste time i dag |
| `sensor.elpris_ai_cheapest_tomorrow` | Billigste time i morgen |
| `sensor.elpris_ai_most_expensive_today` | Dyreste time i dag |
| `sensor.elpris_ai_average_today` | Gennemsnitspris i dag |
| `sensor.elpris_ai_forecast` | 7-dags forecast data |
| `sensor.elpris_ai_confidence` | Modellens tillid (0-100%) |

## API Endpoints

Add-on'et eksponerer disse endpoints:

- `GET /` - Service status
- `GET /health` - Sundhedstjek
- `GET /forecast` - Komplet forecast
- `GET /forecast/today` - Dagens priser
- `GET /forecast/tomorrow` - Morgenpriser
- `GET /forecast/week` - Ugentlig oversigt
- `POST /update` - Tving opdatering
- `GET /settings` - Vis indstillinger
- `POST /settings` - Opdater indstillinger

## Brug i Automations

### Eksempel: Tænd lys når det er billigt

```yaml
action: light.turn_on
target:
  entity_id: light.stue
data:
  brightness_pct: >
    {% if states('sensor.elpris_ai_current_price') | float < 0.5 %}
      100
    {% else %}
      50
    {% endif %}
```

### Eksempel: Send notifikation ved billigste time

```yaml
action: notify.notify
data:
  title: "Billigste time nu!"
  message: >
    Elprisen er {{ states('sensor.elpris_ai_current_price') }} kr/kWh
    i {{ states('sensor.elpris_ai_cheapest_today') }} time.
```

## Datakilder

- **Energi Data Service**: Historiske elpriser og CO2-data
- **DMI**: Vejrprognoser for Danmark
- **Nordpool**: Reelle elpriser (valgfrit)

## ML-Modellen

Modellen bruger Facebook Prophet med:

- **Daglig sæsonvariabilitet**: Mønstre i løbet af dagen
- **Ugentlig sæsonvariabilitet**: Forskelle på hverdage og weekender
- **Årlig sæsonvariabilitet**: Sæsonmæssige mønstre
- **Ekstra regressorer**:
  - Temperatur
  - Vindhastighed
  - Skydække
  - CO2-udledning
  - Energiproduktion og -forbrug

## Fejlsøgning

### Service kører ikke

1. Tjek at add-on'et kører: **Settings > Add-ons > Elpris AI**
2. Tjek logs: **Settings > Add-ons > Elpris AI > Logs**

### Sensorer viser "unavailable"

1. Tjek at custom componenten er installeret korrekt
2. Genstart Home Assistant
3. Tjek logs for fejl

### Forkerte priser

1. Tjek at region er sat korrekt (DK1/DK2)
2. Tjek at momssats og tillæg er korrekte
3. Tving en opdatering: `POST /update`

## Teknisk

### Add-on Structure

```
addons/elpris-ai/
├── config.json          # Add-on konfiguration
├── Dockerfile          # Docker image
├── requirements.txt    # Python dependencies
├── main.py            # FastAPI server
├── data_collector.py  # Dataindsamling
├── ml_model.py        # ML-model
├── models.py          # Data modeller
└── translations/      # Oversættelser
    ├── da.json
    └── en.json
```

### Custom Component Structure

```
custom_components/elpris_ai/
├── __init__.py        # Integration setup
├── config_flow.py     # Setup flow
├── const.py          # Konstanter
├── sensor.py         # Sensor entities
├── manifest.json     # Integration metadata
├── strings.json      # Oversættelser
└── dashboard.yaml    # Dashboard template
```

## Licens

MIT
