# Инструкции GitHub Copilot для проекта 1С

После однократной установки Graphify-1C выполните в корне каждого проекта
`graphify-1c init-project . --agents vscode`. Эта инструкция и индекс относятся
только к открытому проекту.

Открой корень проекта в VS Code. Основная конфигурация: `src/cf`; в `src/cfe/*` могут быть расширения, не включённые в текущий граф. Прочитай маленький `graphify-out/.graphify_onec.json`, если он есть: `extensions` перечисляет выбранные расширения, SQLite-индекс находится рядом с `html` под суффиксом `.sqlite`. Обозначь его путь `<индекс>`; без манифеста это `graphify-out/graph.sqlite`. Для вопросов о структуре и зависимостях используй индекс через терминал:

```text
graphify-1c index search '<индекс>' '<имя>'
graphify-1c index neighbors '<индекс>' '<точный id>' --direction in --relation calls --limit 10 --offset 0
```

Индекс содержит объекты метаданных и BSL-код, включая принадлежность основной конфигурации и расширениям. Для всех использований проходи страницы `--offset 10`, `20` и далее до пустого ответа; проверяй нужные отношения: `calls`, `references`, `type_reference`, `query_reads`, `writes`, `EXTENDS` и перехваты. Перед изменением кода проверь найденные `source_file` и `start_line` в XML/BSL. Указывай тип связи и степень уверенности. Не открывай весь `graph.json` и не трактуй неоднозначную связь как точный вызов.

Если индекс отсутствует при существующем манифесте, выполни `graphify-1c update .`, сохранив выбор расширений. Без манифеста создай граф командой `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. После изменения исходников выполни `graphify-1c update .`; при изменениях граф 1С перестраивается полностью. Если SQLite не появился, выполни `graphify-1c index build graphify-out/graph.json graphify-out/graph.sqlite`.
