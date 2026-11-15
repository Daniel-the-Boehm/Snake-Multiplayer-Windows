# Snake für Windows – Installation

Anleitung, um aus `snake.py` eine fertige `Snake.exe` zu bauen und das Spiel zu starten.
Der Build läuft einmal auf einem Windows-PC. Die fertige `Snake.exe` lässt sich danach auf jeden anderen Windows-PC kopieren, ohne dass dort Python installiert sein muss.

---

## Voraussetzungen

| Was | Details |
|---|---|
| Betriebssystem | Windows 10 oder 11 (64 Bit) |
| Internet | Nur beim Build (lädt pygame und PyInstaller) |
| Speicherplatz | ca. 300 MB während des Builds, die fertige exe hat ca. 30 MB |
| Dateien | `snake.py`, `build_exe.bat`, `snake.ico` im selben Ordner |

---

## Schritt 1: Dateien auf den Windows-PC kopieren

1. Einen Ordner anlegen, z. B. `C:\Users\<Name>\Documents\Snake`.
2. Die drei Dateien `snake.py`, `build_exe.bat` und `snake.ico` hineinkopieren (USB-Stick, OneDrive, Mail …).
3. Prüfen, dass alle drei direkt im Ordner liegen und nicht in einem Unterordner.

> Nicht in `C:\Program Files` ablegen, dort fehlen die Schreibrechte für den Build.

---

## Schritt 2: Python installieren

Falls Python 3.13 oder 3.12 schon installiert ist, weiter mit Schritt 3.

1. <https://www.python.org/downloads/windows/> öffnen.
2. Den aktuellen **Python 3.13** Installer herunterladen („Windows installer (64-bit)“).
3. Installer starten.
4. **Unten beide Häkchen setzen:**
   - `Use admin privileges when installing py.exe`
   - `Add python.exe to PATH`
5. Auf **Install Now** klicken und warten, bis „Setup was successful“ erscheint.
6. Test: Startmenü → `cmd` eingeben → Eingabeaufforderung öffnen → eingeben:

   ```
   py -3.13 --version
   ```

   Die Ausgabe sollte so aussehen: `Python 3.13.x`. Kommt stattdessen ein Fehler, Python neu installieren und die Häkchen aus Punkt 4 kontrollieren.

---

## Schritt 3: Snake.exe bauen

1. Im Snake-Ordner doppelt auf **`build_exe.bat`** klicken.
2. Falls Windows „Der Computer wurde durch Windows geschützt“ anzeigt: **Weitere Informationen → Trotzdem ausführen**.
3. Das Skript erledigt automatisch:
   - Es sucht Python 3.13 (sonst 3.12).
   - Es installiert bzw. aktualisiert `pygame` und `pyinstaller`.
   - Es prüft, ob pygame geladen werden kann.
   - Es baut `Snake.exe`.
4. Der Build dauert 1–3 Minuten. Am Ende steht:

   ```
   Done! Your game is here: dist\Snake.exe
   ```

5. Eine beliebige Taste drücken, das Fenster schließt sich.

Im Ordner liegen danach zusätzlich `build\`, `dist\` und `Snake.spec`. Gebraucht wird nur **`dist\Snake.exe`**, der Rest darf gelöscht werden.

---

## Schritt 4: Spiel starten

1. `dist\Snake.exe` an einen beliebigen Ort verschieben, z. B. auf den Desktop.
2. Doppelklick auf `Snake.exe`.
3. **Beim ersten Start** kann SmartScreen warnen, weil die exe nicht signiert ist: **Weitere Informationen → Trotzdem ausführen**.
4. Der erste Start dauert ein paar Sekunden länger, weil die exe sich entpackt. Das ist normal.

Optional für eine Verknüpfung: Rechtsklick auf `Snake.exe` → **Weitere Optionen anzeigen → Senden an → Desktop (Verknüpfung erstellen)**.

---

## Schritt 5: Multiplayer freigeben

Beim ersten Hosten über WLAN fragt die **Windows-Firewall**, ob Snake ins Netzwerk darf.

1. Häkchen bei **Private Netzwerke** setzen.
2. **Zugriff zulassen** klicken.

Damit die anderen das Spiel finden, müssen außerdem:
- alle im **selben WLAN** sein (kein Gäste-WLAN, das blockiert Geräte untereinander),
- das WLAN in Windows auf **Privat** stehen: Einstellungen → Netzwerk und Internet → WLAN → Netzwerk anklicken → **Privates Netzwerk**.

Verwendete Ports: **TCP 55055** (Spiel) und **UDP 55056** (automatische Suche).
Windows und Mac können gegenseitig hosten und beitreten.

---

## Steuerung

| Taste | Funktion |
|---|---|
| Pfeiltasten / WASD | Starten und steuern |
| P | Pause |
| R | Neustart (Apple-Modus: Teleport) |
| Esc / Zahnrad | Einstellungen |
| M / 2P-Button | Multiplayer |
| Leertaste / Enter | Nochmal spielen |
| F11 | Vollbild |
| F3 | FPS anzeigen |
| Bild ins Fenster ziehen | Als Hintergrund, Kopf oder Frucht verwenden |

---

## Wo liegen die Daten?

Alles liegt in `%APPDATA%\Snake` (in die Adresszeile des Explorers eintippen):

| Datei | Inhalt |
|---|---|
| `snake_save.json` | Einstellungen und Highscores |
| `snake_photos\` | Eigene Fotos (Hintergrund, Kopf, Frucht) |
| `snake_error.log` | Fehlerbericht, falls das Spiel abstürzt |

Spielstände einer alten Version, die neben der alten exe liegen, werden beim ersten Start automatisch übernommen.

---

## Ohne exe direkt starten (optional)

Zum Testen ohne Build, im Snake-Ordner in der Eingabeaufforderung:

```
py -3.13 -m pip install pygame
py -3.13 snake.py
```

---

## Probleme lösen

| Problem | Lösung |
|---|---|
| `Python 3.13 or 3.12 not found` | Schritt 2 wiederholen und bei der Installation das `py.exe`-Häkchen setzen |
| `pip failed` | Internetverbindung prüfen. Im Schul- oder Firmennetz blockiert oft ein Proxy, dann zuhause bauen |
| `Build failed` | Den Ordner `build\` löschen und `build_exe.bat` erneut starten. Liegt der Ordner in OneDrive, ihn nach `Documents` außerhalb von OneDrive verschieben |
| Antivirus löscht `Snake.exe` | Fehlalarm, der bei PyInstaller-Programmen häufig vorkommt. `dist\` im Virenscanner als Ausnahme hinzufügen und neu bauen |
| Spiel startet nicht / Fenster blitzt kurz auf | `%APPDATA%\Snake\snake_error.log` öffnen, dort steht die Ursache |
| Freunde finden das Spiel nicht | Schritt 5 prüfen. Alternativ die IP aus dem Host-Fenster bei „Join“ manuell eintippen |
| „Couldn't connect“ beim Beitreten | Firewall auf dem **Host**-PC prüfen: Windows-Sicherheit → Firewall- & Netzwerkschutz → App durch Firewall zulassen → Snake bei „Privat“ anhaken |
| Bild unscharf | Sollte nicht vorkommen. Falls doch: Rechtsklick `Snake.exe` → Eigenschaften → Kompatibilität → Hohe DPI-Einstellungen ändern → „Skalierung durch: Anwendung“ |
