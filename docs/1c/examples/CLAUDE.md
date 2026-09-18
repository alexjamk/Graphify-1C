# Граф проекта 1С для Claude Code

Установите Graphify-1C один раз для пользователя, затем в корне каждого проекта
выполните `graphify-1c init-project . --agents claude`. Этот файл и SQLite-граф
относятся к текущему проекту.

Работай из корня проекта. Основная конфигурация находится в `src/cf`; в `src/cfe/*` могут быть расширения, исключённые из текущего графа. Сначала прочитай маленький `graphify-out/.graphify_onec.json`, если он существует: `extensions` перечисляет выбранные расширения, индекс находится рядом с `html` с суффиксом `.sqlite`. Обозначь этот путь `<индекс>`; без манифеста это `graphify-out/graph.sqlite`:

```text
graphify-1c index search '<индекс>' '<имя>'
graphify-1c index neighbors '<индекс>' '<точный id>' --direction in --relation calls --limit 10 --offset 0
```

Граф включает метаданные, BSL, основную конфигурацию и расширения: проверяй `source_type`, `extension_name`, входящую и исходящую `contains`, `EXTENDS` и перехваты методов. Для «всех мест» проходи `--offset 10`, `20` и далее до пустого ответа; проверь подходящие отношения (`calls`, `references`, `type_reference`, `query_reads`, `writes`). Используй точный ID, тип связи и пути к исходникам. Для важных выводов открой указанные XML/BSL и проверь строки. Учитывай `INFERRED` и `AMBIGUOUS`. Не читай целиком `graph.json` и не делай вывод о том, чего граф не разрешил.

Если индекс отсутствует при существующем манифесте, выполни `graphify-1c update .` и сохрани выбор расширений. Без манифеста создай граф: `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`. После изменения исходников выполни `graphify-1c update .`; при изменениях это полный повторный анализ 1С. Если после анализа SQLite отсутствует, выполни `graphify-1c index build graphify-out/graph.json graphify-out/graph.sqlite`.
