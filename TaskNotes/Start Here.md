# Einstieg in TaskNotes

Diese Notiz führt Sie kurz durch TaskNotes in Ihrem eigenen Vault. Arbeiten Sie die Schritte hier durch und behalten Sie die Notiz anschließend als Referenz oder löschen Sie sie, wenn Sie sie nicht mehr benötigen.

TaskNotes funktioniert am besten, wenn drei Dinge zusammenwirken:

1. Erfassen Sie eine Aufgabe schnell.
2. Speichern Sie die Aufgabe als normale Markdown-Notiz.
3. Sehen Sie Aufgaben in Bases-Ansichten durch.

## Bevor Sie beginnen

- [ ] Aktivieren Sie das Obsidian-Kern-Plugin **Bases**.
- [ ] Prüfen Sie, ob TaskNotes die standardmäßigen `.base`-Dateien in `TaskNotes/Views/` erstellt hat.
- [ ] Öffnen Sie die Befehlspalette und führen Sie **TaskNotes: Aufgabenansicht öffnen** aus.

TaskNotes erstellt fehlende standardmäßige `.base`-Dateien beim Start automatisch, wenn **Standarddateien automatisch erstellen** aktiviert ist. Wenn die Dateien fehlen, verwenden Sie **Einstellungen -> TaskNotes -> Allgemein -> Ansichten und Base-Dateien -> Dateien erstellen**.

## 1. Aufgabe über die Befehlspalette erstellen

Führen Sie **TaskNotes: Neue Aufgabe erstellen** aus und erstellen Sie eine echte Aufgabe, die Sie bald erledigen möchten.

Probieren Sie beispielsweise diesen Titel:

```text
Review my TaskNotes setup tomorrow #admin
```

Nach dem Speichern erstellt TaskNotes eine Markdown-Notiz für diese Aufgabe. Öffnen Sie die Aufgabennotiz und betrachten Sie die YAML-Eigenschaften am Anfang. Anhand dieser Eigenschaften kann Bases Aufgaben filtern, sortieren, gruppieren und im Kalender platzieren.

## 2. Inline-Aufgaben in TaskNotes umwandeln

Die Kontrollkästchen unten sind gewöhnliche Markdown-Aufgaben. Wandeln Sie jede davon über die Inline-Aktion der Zeile oder die Befehlspalette in eine Aufgabennotiz um.

- [ ] TaskNotes-Aufgabe im Eingang erstellen, die morgen fällig ist #admin
- [ ] Eine 20-minütige Planungsaufgabe für nächsten Montag planen #planning
- [ ] Eine Aufgabe mit einer aktiven Projektnotiz verknüpfen

Beachten Sie nach der Umwandlung Folgendes:

- Die ursprüngliche Zeile kann als Kontext in dieser Notiz bleiben.
- Die eigentliche Aufgabe liegt nun als eigene Markdown-Notiz vor und kann daher in TaskNotes-Ansichten erscheinen.
- Die umgewandelte Zeile verlinkt auf die Aufgabennotiz. Klicken Sie mit der rechten Maustaste auf diesen Link, um das Aufgabenmenü zu öffnen, und ändern Sie versuchsweise eine Eigenschaft wie Status, Priorität, Fälligkeitsdatum, Planungsdatum oder Projekt.

## 3. Aufgaben durchsehen

Die eingebetteten Ansichten unten verwenden die standardmäßigen TaskNotes-Base-Dateien.

### Aufgabenliste

![[TaskNotes/Views/tasks-default.base]]

Verwenden Sie diese Ansicht für eine übersichtliche Liste aller offenen Aufgaben.

### Agenda

![[TaskNotes/Views/agenda-default.base]]

Verwenden Sie diese Ansicht, wenn Fälligkeits- und Planungsdaten sowie bald anstehende Aufgaben wichtig sind.

### Minikalender

![[TaskNotes/Views/mini-calendar-default.base]]

Verwenden Sie diese Ansicht für einen kompakten Kalender neben Ihren Notizen.

### Kalender

![[TaskNotes/Views/calendar-default.base]]

Verwenden Sie diese Ansicht für einen größeren Kalender mit geplanter Arbeit, Fälligkeiten, Zeitblöcken und Kalenderereignissen.

### Kanban

![[TaskNotes/Views/kanban-default.base]]

Verwenden Sie diese Ansicht, um Aufgaben durch Status wie offen, in Bearbeitung und erledigt zu bewegen.

## 4. Nur störende Einstellungen ändern

Die Standardeinstellungen reichen für den Anfang. Öffnen Sie die Einstellungen später, wenn Sie wissen, was Sie ändern möchten:

- Falls Aufgabendateien am falschen Ort landen, prüfen Sie **Einstellungen -> TaskNotes -> Allgemein**.
- Falls umgewandelte Inline-Aufgaben an einem anderen Ort erstellt werden sollen, prüfen Sie **Einstellungen -> TaskNotes -> Einstellungen für Inline-Aufgaben**.
- Falls der Aufgabendialog zu viel oder zu wenig anzeigt, prüfen Sie **Einstellungen -> TaskNotes -> Dialogfelder**.
- Falls Ihr Vault andere Eigenschaftsnamen, Status, Prioritäten oder benutzerdefinierte Felder verwendet, prüfen Sie **Einstellungen -> TaskNotes -> Aufgabeneigenschaften**.

## 5. An Ihre Bedürfnisse anpassen

Fügen Sie einer Aufgabe eine benutzerdefinierte Eigenschaft hinzu, etwa `energy`, `client` oder `area`. Da Aufgaben Markdown-Notizen sind, können Sie benutzerdefinierte Eigenschaften in Bases nutzen, ohne an eine separate Aufgabendatenbank gebunden zu sein.

## 6. Kontextbezogene Ansichten

Sie können Bases-Ansichten in tägliche Notizen, Projektnotizen, Besprechungsnotizen oder Aufgabennotizen einbetten. Erweiterte Bases-Filter können die aktuelle Notiz als Kontext verwenden, sodass eine Ansicht Aufgaben zur jeweils einbettenden Notiz zeigt.

TaskNotes nutzt dieses Muster für das Beziehungs-Widget in Aufgabennotizen. Wenn Sie Ihre Ansichten anpassen möchten, finden Sie Beispiele in der TaskNotes-Dokumentation.

Weitere nützliche Experimente:

- Duplizieren Sie `TaskNotes/Views/tasks-default.base` und erstellen Sie eine Ansicht für ein Projekt.
- Gruppieren Sie eine Base-Ansicht beispielsweise nach Status oder Priorität.
- Filtern Sie nach Aufgaben, die diese Woche fällig sind.
- Verknüpfen Sie eine Aufgabe mit einer Projektnotiz und nutzen Sie Backlinks, um ihre Erwähnungen zu finden.

## Weitere Hilfe und Erweiterungen

- Dokumentation: [tasknotes.dev](https://tasknotes.dev/)
- Probleme oder Fehler: [Issue auf GitHub eröffnen](https://github.com/callumalpass/tasknotes/issues)
- Ergänzende Plugins:
  - [Canvas Bases](https://tasknotes.dev/companion-plugins/canvas-bases/) ergänzt Bases-Ansichten um Canvas-Boards und JSON-Canvas-Snapshots sowie optional TaskNotes-Badges, Aktionen und Beziehungskanten.
  - [TaskNotes Workflows](https://tasknotes.dev/companion-plugins/tasknotes-workflows/) ergänzt Markdown-definierte Automatisierung für TaskNotes-Ereignisse, Zeitpläne, manuelle Befehle und Aufgabenaktionen.

## Fertig

Sie kennen jetzt die Grundidee: Aufgaben sind Notizen, Eigenschaften machen sie abfragbar und Bases verwandelt sie in Ansichten.
