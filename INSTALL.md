# Elpris AI - Installation via GitHub

## Trin 1: Opret GitHub repository

1. Gå til https://github.com/new
2. Opret et nyt repository kaldt `elpris-ai-ha`
3. Vælg "Public" (eller Private hvis du foretrækker det)
4. Tilføj en README.md

## Trin 2: Upload filerne

Kopier hele mappen `/addons/elpris-ai/` til dit repository. Du kan bruge:

```bash
# Lokalt på din computer
git clone https://github.com/DIT_BRUGERNavn/elpris-ai-ha.git
cp -r /addons/elpris-ai/* elpris-ai-ha/
cd elpris-ai-ha
git add .
git commit -m "Initial commit"
git push
```

## Trin 3: Installer i Home Assistant

### Option A: Via Supervisor (anbefalet)

1. Gå til **Settings > Add-ons**
2. Klik på **+** (Tilføj add-on) i højre hjørne
3. Klik på **Tilføj repository**
4. Indtast: `DIT_BRUGERNavn/elpris-ai-ha`
5. Klik **Tilføj**
6. Find **Elpris AI** i listen
7. Klik **Installer**
8. Klik **Start**

### Option B: Manuel installation

Hvis Supervisor ikke kan finde dit repository:

1. SSH ind i din Home Assistant maskine
2. Kør:
```bash
cd /addons
git clone https://github.com/DIT_BRUGERNavn/elpris-ai-ha.git elpris-ai
```
3. Genstart Home Assistant
4. Gå til **Settings > Add-ons**
5. Find **Elpris AI** og installer den

## Trin 4: Konfigurer

1. Gå til **Settings > Add-ons > Elpris AI**
2. Klik på **Configuration**
3. Indstil:
   - **Region**: DK1 eller DK2 (afhænger af hvor du bor)
   - **Momsprocent**: 25
   - **Fast tillæg**: 0.1293 (eller dit eget tillæg)
   - **Update interval**: 60 minutter
4. Klik **Gem**

## Trin 5: Installer Custom Component (valgfrit)

Hvis du vil have de avancerede sensorer:

```bash
# SSH ind i Home Assistant
cp -r /addons/elpris-ai/custom_components/elpris_ai /config/custom_components/
# Genstart Home Assistant
```

## API Documentation

Når add-on'et kører, kan du tilgå:

- `http://192.168.x.x:8001/` - Service status
- `http://192.168.x.x:8001/health` - Sundhedstjek
- `http://192.168.x.x:8001/forecast` - Komplet 7-dages forecast
- `http://192.168.x.x:8001/forecast/today` - Dagens priser
- `http://192.168.x.x:8001/forecast/tomorrow` - Morgenpriser

## Fejlsøgning

### Add-on vises ikke i Supervisor

1. Tjek at repository'et er public
2. Tjek at filerne ligger i roden (ikke i en undermappe)
3. Genstart Home Assistant

### Add-on starter ikke

1. Gå til **Settings > Add-ons > Elpris AI > Logs**
2. Tjek for fejlmeddelelser
3. Sørg for at port 8001 ikke er optaget

### Sensorer viser "unavailable"

1. Tjek at add-on'et kører
2. Tjek at port 8001 er tilgængelig
3. Brug `curl http://localhost:8001/health` til at teste

## Brug i Automations

Når add-on'et kører, kan du bruge sensorerne:

```yaml
# Eksempel: Tænd lys når det er billigt
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

## Support

Hvis du har problemer, tjek:
1. Home Assistant logs
2. Add-on logs
3. GitHub issues (hvis du har oprettet et repository)
