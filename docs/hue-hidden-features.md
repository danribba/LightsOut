# Hue API - Dolda funktioner

Dessa funktioner finns i Philips Hue REST API men exponeras **inte** i iPhone Hem-appen eller Hue-appen.

## 1. Transition Time (Fade)

Gradvis ändring av ljusstyrka/färg över tid.

```json
PUT /api/<user>/lights/<id>/state
{
  "on": true,
  "bri": 254,
  "transitiontime": 6000
}
```

| Parameter | Beskrivning |
|-----------|-------------|
| `transitiontime` | Tid i 1/10 sekunder. Default: 4 (0.4s) |

**Exempel:**
- `transitiontime: 10` = 1 sekund
- `transitiontime: 600` = 1 minut
- `transitiontime: 6000` = 10 minuter (perfekt för wake-up light)

**Användningsfall:**
- Wake-up light: Gradvis öka ljusstyrka från 0 till 100% över 15-30 min
- Kvällsdimning: Sänk ljuset mjukt inför läggdags
- Smooth on/off: Undvik abrupt på/av

---

## 2. Alert (Blink)

Få lampan att blinka för att fånga uppmärksamhet.

```json
PUT /api/<user>/lights/<id>/state
{
  "alert": "lselect"
}
```

| Värde | Beskrivning |
|-------|-------------|
| `none` | Ingen alert (standard) |
| `select` | Blinkar en gång |
| `lselect` | Blinkar i 15 sekunder |

**Användningsfall:**
- Dörrklocka: Blinka hallampan när någon ringer
- Timer: Signalera att maten är klar
- Hitta lampa: Identifiera vilken fysisk lampa som är vilken

---

## 3. Effect (Color Loop)

Automatisk cykling genom alla färger.

```json
PUT /api/<user>/lights/<id>/state
{
  "effect": "colorloop"
}
```

| Värde | Beskrivning |
|-------|-------------|
| `none` | Ingen effekt (standard) |
| `colorloop` | Cyklar genom hela färgspektrat |

**Notera:** Endast för färglampor (Color/Ambiance). Ignoreras på vita lampor.

**Användningsfall:**
- Festläge
- Disco-effekt
- RGB-stämningsbelysning

---

## 4. XY Color (CIE 1931)

Exakt färgstyrning via CIE-koordinater. Mer precist än hue/saturation.

```json
PUT /api/<user>/lights/<id>/state
{
  "xy": [0.675, 0.322]
}
```

| Färg | X | Y |
|------|---|---|
| Röd | 0.675 | 0.322 |
| Grön | 0.409 | 0.518 |
| Blå | 0.167 | 0.040 |
| Vit | 0.323 | 0.329 |
| Varm vit | 0.459 | 0.410 |

**Konvertering RGB → XY:**
```python
def rgb_to_xy(r, g, b):
    # Gamma-korrektion
    r = pow((r + 0.055) / 1.055, 2.4) if r > 0.04045 else r / 12.92
    g = pow((g + 0.055) / 1.055, 2.4) if g > 0.04045 else g / 12.92
    b = pow((b + 0.055) / 1.055, 2.4) if b > 0.04045 else b / 12.92

    # Konvertera till XYZ
    X = r * 0.4124 + g * 0.3576 + b * 0.1805
    Y = r * 0.2126 + g * 0.7152 + b * 0.0722
    Z = r * 0.0193 + g * 0.1192 + b * 0.9505

    # Konvertera till xy
    x = X / (X + Y + Z)
    y = Y / (X + Y + Z)
    return [x, y]
```

---

## 5. Color Temperature (Mirek)

Färgtemperatur för vita/ambiance-lampor.

```json
PUT /api/<user>/lights/<id>/state
{
  "ct": 250
}
```

| Mirek | Kelvin | Beskrivning |
|-------|--------|-------------|
| 153 | 6500K | Dagsljus (kallvit) |
| 250 | 4000K | Neutral |
| 366 | 2732K | Varm |
| 454 | 2200K | Extra varm (kvällsljus) |
| 500 | 2000K | Stearinljus |

**Formel:** `mirek = 1000000 / kelvin`

---

## 6. Brightness (Bri)

Ljusstyrka 1-254 (0 stänger av lampan).

```json
PUT /api/<user>/lights/<id>/state
{
  "on": true,
  "bri": 254
}
```

| Värde | Procent |
|-------|---------|
| 1 | 0.4% |
| 127 | 50% |
| 254 | 100% |

**Tips:** `bri_inc` för relativ justering:
```json
{ "bri_inc": 50 }   // Öka med 50
{ "bri_inc": -50 }  // Minska med 50
```

---

## 7. Schedules (Schemaläggning)

Skapa schemalagda kommandon direkt i bryggan.

```json
POST /api/<user>/schedules
{
  "name": "Wake up Hugo",
  "description": "Tänd Hugos lampor gradvis",
  "command": {
    "address": "/api/<user>/lights/5/state",
    "method": "PUT",
    "body": {
      "on": true,
      "bri": 254,
      "transitiontime": 9000
    }
  },
  "localtime": "W124/T06:45:00",
  "status": "enabled"
}
```

**Tidsformat:**
| Format | Beskrivning |
|--------|-------------|
| `2026-01-20T07:00:00` | Specifikt datum/tid |
| `W124/T07:00:00` | Återkommande (bitmask för veckodagar) |
| `PT00:15:00` | Timer (om 15 min) |

**Veckodags-bitmask:**
- Måndag = 64, Tisdag = 32, Onsdag = 16, Torsdag = 8, Fredag = 4, Lördag = 2, Söndag = 1
- Vardagar (mån-fre) = 64+32+16+8+4 = **124**
- Helger (lör-sön) = 2+1 = **3**
- Alla dagar = **127**

---

## 8. Scenes

Fördefinierade ljusscener som kan aktiveras.

```json
PUT /api/<user>/groups/<group_id>/action
{
  "scene": "ABC123def"
}
```

**Hämta alla scener:**
```
GET /api/<user>/scenes
```

**Skapa egen scen:**
```json
POST /api/<user>/scenes
{
  "name": "Filmkväll",
  "lights": ["1", "2", "3"],
  "recycle": false
}
```

---

## 9. Groups/Rooms

Styr flera lampor samtidigt.

```json
PUT /api/<user>/groups/<group_id>/action
{
  "on": true,
  "bri": 200,
  "transitiontime": 100
}
```

**Fördelar:**
- Ett API-anrop istället för flera
- Synkron ändring (alla lampor ändras samtidigt)
- Stödjer alla samma parametrar som enskilda lampor

---

## 10. Sensor-baserade triggers

Hue-sensorer kan trigga regler automatiskt.

```json
POST /api/<user>/rules
{
  "name": "Motion aktiverar hall",
  "conditions": [
    {
      "address": "/sensors/2/state/presence",
      "operator": "eq",
      "value": "true"
    }
  ],
  "actions": [
    {
      "address": "/lights/1/state",
      "method": "PUT",
      "body": { "on": true }
    }
  ]
}
```

---

## Kombinerade exempel

### Wake-up Light (15 min fade)
```json
// Steg 1: Sätt ljusstyrka till 0, sedan fade till max
PUT /api/<user>/lights/5/state
{ "on": true, "bri": 1 }

// Steg 2: Starta fade (efter kort delay)
PUT /api/<user>/lights/5/state
{ "bri": 254, "transitiontime": 9000 }
```

### Kvällsscen med fade
```json
PUT /api/<user>/groups/1/action
{
  "on": true,
  "bri": 150,
  "ct": 454,
  "transitiontime": 100
}
```

### Sekvens: Hall → Kök → Vardagsrum
```python
# Pseudo-kod
set_light(hall, on=True)
sleep(120)  # 2 minuter
set_light(kok, on=True)
sleep(60)   # 1 minut
set_light(vardagsrum, on=True)
```

---

## API-åtkomst

**Hitta din bridge:**
```
GET https://discovery.meethue.com
```

**Skapa användare (tryck länkknappen först):**
```
POST http://<bridge-ip>/api
{ "devicetype": "lightsout#raspi" }
```

**Testa:**
```
GET http://<bridge-ip>/api/<username>/lights
```

---

## Begränsningar

| Resurs | Max |
|--------|-----|
| Schedules | 100 |
| Rules | 250 |
| Scenes | 200 |
| Sensors (virtuella) | 250 |
| ResourceLinks | 64 |

**Rate limits:**
- 10 kommandon/sekund till grupper
- 1 kommando/sekund per lampa (rekommenderat)
