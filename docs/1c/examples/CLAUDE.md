# Граф проекта 1С для Claude Code

Работай из корня проекта. Основная конфигурация находится в `src/cf`, расширения — в `src/cfe/*`. Для вопросов об объектах, вызовах и расширениях сначала обращайся к `graphify-out/graph.sqlite`:

```text
graphify-1c index search graphify-out/graph.sqlite '<имя>'
graphify-1c index neighbors graphify-out/graph.sqlite '<точный id>' --direction in --relation calls --limit 100 --offset 0
```

Граф включает метаданные, BSL, основную конфигурацию и расширения: проверяй `source_type`, `extension_name`, входящую и исходящую `contains`, `EXTENDS` и перехваты методов. Для «всех мест» проходи `--offset 100`, `200` и далее до пустого ответа; проверь подходящие отношения (`calls`, `references`, `type_reference`, `query_reads`, `writes`). Используй точный ID, тип связи и пути к исходникам. Для важных выводов открой указанные XML/BSL и проверь строки. Учитывай `INFERRED` и `AMBIGUOUS`. Не читай целиком `graph.json` и не делай вывод о том, чего граф не разрешил.

Если графа нет, создай его: `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. После изменения исходников выполни `graphify-1c update .` — это полный повторный анализ 1С. Если после анализа SQLite отсутствует, выполни `graphify-1c index build graphify-out/graph.json graphify-out/graph.sqlite`.
