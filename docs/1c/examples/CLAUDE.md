# Граф проекта 1С для Claude Code

Работай из корня проекта. Основная конфигурация находится в `src/cf`, расширения — в `src/cfe/*`. Для вопросов об объектах, вызовах и расширениях сначала обращайся к `graphify-out/graph.sqlite`:

```text
graphify-1c index search graphify-out/graph.sqlite '<имя>'
graphify-1c index neighbors graphify-out/graph.sqlite '<точный id>' --direction both --relation calls
```

Используй точный ID, тип связи и пути к исходникам из ответа. Для важных выводов открой указанные XML/BSL и проверь строки. Учитывай `INFERRED` и `AMBIGUOUS`. Не читай целиком `graph.json` и не делай вывод о том, чего граф не разрешил.

Если графа нет или исходники изменились, создай его заново: `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. Если после анализа SQLite отсутствует, выполни `graphify-1c index build graphify-out/graph.json graphify-out/graph.sqlite`. Команда общего Graphify `graphify update .` не обновляет граф 1С.
