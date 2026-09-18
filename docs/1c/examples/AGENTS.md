# Граф проекта 1С

Рекомендуемый способ получить актуальную версию этих правил в каждом проекте:
`graphify-1c init-project . --agents codex`. Graphify-1C устанавливается один раз
для пользователя; этот файл и `graphify-out/` относятся только к текущему проекту.

Исходники находятся в `src/`: основная конфигурация — `src/cf`, расширения — `src/cfe/*`. Сохранённый граф находится в `graphify-out/graph.sqlite`; он создаётся командой `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`.

Перед ответом на вопрос о зависимостях или влиянии изменения:

1. Найди объект: `graphify-1c index search graphify-out/graph.sqlite '<имя>'`.
2. Возьми точный `id` из результата, проверь `kind`, `source_type` и `extension_name`. Запроси нужную группу связей, например `graphify-1c index neighbors graphify-out/graph.sqlite '<id>' --direction in --relation calls --limit 10 --offset 0`. Вместо `calls` подставь требуемое отношение (`contains`, `references`, `type_reference`, `query_reads`, `writes`, `EXTENDS`, `AFTER` и т. д.). Входящие связи показывают использования, исходящие — зависимости и состав объекта.
3. Проверь важные связи по `source_file` и `start_line` в исходниках XML/BSL. Укажи уверенность связи, если она выведена, а не прочитана напрямую.
4. Если граф отсутствует, создай его командой `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. Если исходники изменились, выполни `graphify-1c update .` до вывода о текущем коде. Если после анализа SQLite отсутствует, создай его командой `graphify-1c index build graphify-out/graph.json graphify-out/graph.sqlite`.

Конфигурация и расширения — узлы графа; `contains` связывает их с объектами метаданных, модулями и методами. Перед длинным обходом используй `index groups`; для тысяч совпадений используй `index export`, не выводя JSONL в диалог. Для вопроса «все места использования» проходи страницы `--offset 0`, `10`, `20` до пустого ответа и проверяй все относящиеся к вопросу типы связей. Не загружай весь `graphify-out/graph.json` в контекст. `graphify-1c update .` быстро завершится, если исходники не менялись, и полностью перестроит граф после изменений.
