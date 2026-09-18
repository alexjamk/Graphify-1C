# Граф проекта 1С

Исходники находятся в `src/`: основная конфигурация — `src/cf`, расширения — `src/cfe/*`. Сохранённый граф находится в `graphify-out/graph.sqlite`; он создаётся командой `python -m graphify analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`.

Перед ответом на вопрос о зависимостях или влиянии изменения:

1. Найди объект: `python -m graphify.onec.index search graphify-out/graph.sqlite '<имя>'`.
2. Возьми точный `id` из результата. Запроси нужную группу связей, например `python -m graphify.onec.index neighbors graphify-out/graph.sqlite '<id>' --direction both --relation calls`. Вместо `calls` подставь требуемое отношение (`EXTENDS`, `AFTER`, `writes` и т. д.).
3. Проверь важные связи по `source_file` и `start_line` в исходниках XML/BSL. Укажи уверенность связи, если она выведена, а не прочитана напрямую.
4. Если граф отсутствует или создан до последних изменений исходников, сообщи об этом и перестрой его перед выводом о текущем коде. Если после анализа SQLite отсутствует, создай его командой `python -m graphify.onec.index build graphify-out/graph.json graphify-out/graph.sqlite`.

Не загружай весь `graphify-out/graph.json` в контекст. Для узла с большим числом связей сужай запрос по отношению и направлению; локальный HTML-просмотрщик показывает связи страницами. `graphify update .` не обновляет граф 1С.
