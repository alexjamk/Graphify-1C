# Граф проекта 1С для Claude Code

Работай из корня проекта. Основная конфигурация находится в `src/cf`, расширения — в `src/cfe/*`. Для вопросов об объектах, вызовах и расширениях сначала обращайся к `graphify-out/graph.sqlite`:

```text
python -m graphify.onec.index search graphify-out/graph.sqlite '<имя>'
python -m graphify.onec.index neighbors graphify-out/graph.sqlite '<точный id>' --direction both --relation calls
```

Используй точный ID, тип связи и пути к исходникам из ответа. Для важных выводов открой указанные XML/BSL и проверь строки. Учитывай `INFERRED` и `AMBIGUOUS`. Не читай целиком `graph.json` и не делай вывод о том, чего граф не разрешил.

Если графа нет или исходники изменились, создай его заново: `python -m graphify analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. Если после анализа SQLite отсутствует, выполни `python -m graphify.onec.index build graphify-out/graph.json graphify-out/graph.sqlite`. Команда общего Graphify `graphify update .` не обновляет граф 1С.
