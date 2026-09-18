# Готовый запрос для ChatGPT

Скопируйте текст ниже в ChatGPT, если ему доступен локальный проект и терминал:

> Ты работаешь в корне проекта 1С. Исходники основной конфигурации находятся в `src/cf`, расширения — в `src/cfe/*`. Граф уже построен в `graphify-out/graph.sqlite`. Для моего вопроса сначала найди нужный узел командой `graphify-1c index search graphify-out/graph.sqlite '<имя>'`, затем запроси нужные связи через `graphify-1c index neighbors graphify-out/graph.sqlite '<точный id>' --direction both --relation calls` (замени `calls` нужным типом связи). Проверь важные выводы по `source_file` и `start_line` в XML/BSL. Укажи тип связи и уверенность. Не загружай весь JSON-граф. Если граф отсутствует или устарел, сообщи об этом и предложи команду пересборки `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`.

Если ChatGPT не имеет доступа к терминалу, выполните `search` и `neighbors` сами и приложите их небольшой вывод с вопросом. Обычный веб-чат не видит локальный `src` автоматически.
