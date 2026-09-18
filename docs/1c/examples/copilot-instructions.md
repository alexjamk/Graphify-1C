# Инструкции GitHub Copilot для проекта 1С

Открой корень проекта в VS Code. Основная конфигурация: `src/cf`; расширения: `src/cfe/*`. Для вопросов о структуре и зависимостях используй локальный SQLite-граф `graphify-out/graph.sqlite` через терминал:

```text
graphify-1c index search graphify-out/graph.sqlite '<имя>'
graphify-1c index neighbors graphify-out/graph.sqlite '<точный id>' --direction in --relation calls --limit 10 --offset 0
```

Индекс содержит объекты метаданных и BSL-код, включая принадлежность основной конфигурации и расширениям. Для всех использований проходи страницы `--offset 10`, `20` и далее до пустого ответа; проверяй нужные отношения: `calls`, `references`, `type_reference`, `query_reads`, `writes`, `EXTENDS` и перехваты. Перед изменением кода проверь найденные `source_file` и `start_line` в XML/BSL. Указывай тип связи и степень уверенности. Не открывай весь `graph.json` и не трактуй неоднозначную связь как точный вызов.

Если граф отсутствует, создай его командой `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. После изменения исходников выполни `graphify-1c update .` — полный повторный анализ 1С. Если SQLite не появился, выполни `graphify-1c index build graphify-out/graph.json graphify-out/graph.sqlite`.
