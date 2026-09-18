# Инструкции GitHub Copilot для проекта 1С

Открой корень проекта в VS Code. Основная конфигурация: `src/cf`; расширения: `src/cfe/*`. Для вопросов о структуре и зависимостях используй локальный SQLite-граф `graphify-out/graph.sqlite` через терминал:

```text
python -m graphify.onec.index search graphify-out/graph.sqlite '<имя>'
python -m graphify.onec.index neighbors graphify-out/graph.sqlite '<точный id>' --direction both --relation calls
```

Перед изменением кода проверь найденные `source_file` и `start_line` в XML/BSL. Указывай тип связи и степень уверенности. Не открывай весь `graph.json` и не трактуй неоднозначную связь как точный вызов.

Если граф отсутствует или устарел, перестрой его командой `python -m graphify analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. Если SQLite не появился, выполни `python -m graphify.onec.index build graphify-out/graph.json graphify-out/graph.sqlite`. `graphify update .` относится к универсальному графу кода, а не к анализу 1С.
