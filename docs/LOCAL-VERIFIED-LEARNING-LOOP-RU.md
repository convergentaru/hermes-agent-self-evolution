# Local Verified Learning Loop для Hermes Agent

**Статус:** первая локальная реализация/пилот. Это не fine-tuning и пока не автоматический перехват всех Hermes-сессий.  
**Цель:** перестать путать «записал урок» с «стал работать лучше». Каждый урок должен быть связан с наблюдением, кандидатным правилом, повторяемой проверкой и измеримым решением о принятии.

## Что показали исходные материалы

- [Reflexio](https://github.com/ReflexioAI/reflexio) показывает полезный паттерн: сохранять пользовательские коррекции и успешные процедуры в виде извлекаемых playbooks, отделять персональные сведения от общих правил и измерять добавочную пользу на фоне уже обучающегося агента.
- Их [GDPVal results](https://github.com/ReflexioAI/reflexio/blob/main/benchmark/gdpval/RESULTS.md) сравнивают Cold → Warm → Warm + Reflexio. В детальном `RESULTS.md` указано: четыре из пяти выбранных задач улучшились; медианное снижение на семи квалифицируемых host-измерениях — 50% шагов и 57% токенов против warm baseline. При этом headline в README Reflexio показывает −81% шагов / −72% токенов. Это несоответствие между кратким README и подробно расписанной текущей таблицей нельзя молча игнорировать; для наших выводов используем детальную таблицу и методологические ограничения. Это собственный эксперимент авторов проекта, а не независимое подтверждение универсального улучшения.
- В обсуждении [benchmarks for self-improving agents](https://www.reddit.com/r/AI_Agents/comments/1slqhzu/are_there_any_benchmarks_for_selfimproving_agents/) участники отмечают, что benchmark памяти не равен benchmark улучшения поведения, и что самооценка LLM не заменяет проверку фактического результата.
- [Вики r/AI_Agents](https://www.reddit.com/r/AI_Agents/wiki) — полезный список сообщества, но не нормативная спецификация и не доказательство качества конкретного подхода.

Ссылки выше отражают материалы, изученные при создании проекта; детали внешних репозиториев меняются со временем. Мы не копируем код Reflexio. Лицензия проекта и исходные файлы этого репозитория остаются самостоятельными.

## Дизайн MVP

```text
Наблюдаемая задача / исправление владельца
                  |
                  v
         Локальный журнал событий
                  |
                  v
      Кандидат урока (не активен)
                  |
                  v
   Парные baseline/candidate прогоны
   на одном наборе фиксированных задач
                  |
                  v
   Детерминированные safety/regression gates
                  |
          PASS --+-- REJECT/NO IMPROVEMENT
                  |
          Явное одобрение владельца
                  |
                  v
      Локальный approved playbook store
                  |
                  v
   Ограниченный релевантный контекст для задачи
```

Данные хранятся по умолчанию в `~/.local/share/hermes-learning-loop`, а не в Git. Можно задать другое место через `--store` или `HERMES_LEARNING_HOME`. Права каталогов стараются ограничить владельцем (0700), файлов — 0600.

### Команды

Из корня репозитория, используя тот Python 3, в котором доступны зависимости проекта:

```bash
# Инициализация локального хранилища
python -m evolution.learning.loop init

# Зафиксировать короткий результат/исправление, не копируя транскрипт
python -m evolution.learning.loop record \
  --task-class "project-work" \
  --namespace "project:acer-rescue-usb" \
  --outcome corrected \
  --summary "Агент заявил, что проверка прошла, но не выполнил саму команду" \
  --correction "PASS возможен только с фактической командой, выводом и exit code" \
  --lesson "Не считать собственный текст агента доказательством выполнения" \
  --tag verification --tag task-execution

# Команда выведет event_id; на его основе создаётся кандидат:
python -m evolution.learning.loop propose \
  --title "Require execution evidence" \
  --trigger "При отчёте о тестировании" \
  --instruction "Приложи реальную команду, фактический вывод и exit code; иначе пометь NOT RUN" \
  --why "В журнале есть подтверждённый случай ложного утверждения о проверке" \
  --source-event EVENT_ID \
  --test-case honest-test-report --test-case missing-exit-code \
  --tag verification

# Сравнение результатов. JSON-схема описана ниже.
python -m evolution.learning.loop gate \
  --candidate-id CANDIDATE_ID \
  --baseline baseline.json \
  --candidate-run candidate.json

# Только после PASS и явного одобрения человеком:
python -m evolution.learning.loop accept \
  --candidate-id CANDIDATE_ID \
  --gate-report /path/to/reports/gate-report.json \
  --approved-by "owner-review"

# Получить до 3000 символов проверенных правил для текущей задачи:
python -m evolution.learning.loop context \
  --namespace "project:acer-rescue-usb" \
  --task "проверка тестов и отчёт с доказательствами" --max-chars 3000
```

Глобальный аргумент `--store /path` ставится перед командой, например `python -m evolution.learning.loop --store /tmp/hermes-learnings init`. Для тестов следует использовать только временный каталог.

## Контракт evidence

`gate` принимает два JSON receipt одинаковой структуры. Это **чек результатов фактического запуска**, не команда запуска Hermes и не подтверждение подлинности этих результатов.

```json
{
  "schema_version": 1,
  "suite_id": "hermes-regression-v1",
  "condition": "warm",
  "model_id": "local:stable-model-alias",
  "environment_id": "hermes-version-and-runtime-fingerprint",
  "artifact_path": "/local/private/path/to/instruction.md",
  "artifact_sha256": "actual-sha256-of-tested-file",
  "candidate_id": "baseline",
  "cases": [
    {
      "case_id": "honest-test-report",
      "repeat": 1,
      "passed": true,
      "safety_passed": true,
      "evidence_path": "/local/private/path/to/case-01-repeat-01.json",
      "evidence_sha256": "actual-sha256-of-evidence-file",
      "steps": 4,
      "tokens": 850,
      "latency_ms": 2400
    }
  ]
}
```

Для первого локального gate нужно не меньше пяти уникальных case ID и трёх повторов на каждый case для baseline и candidate. Baseline обязан иметь `condition: "warm"`, а candidate — `condition: "warm_plus_learning"`: измеряем добавочную пользу поверх обычного повторного запуска, а не только преимущество над cold-run. В обоих receipts должны совпадать suite, модель, runtime/environment и набор пар (case_id, repeat). Baseline использует `candidate_id: "baseline"`; candidate — ID соответствующего предложения. Пути к тестируемому файлу и evidence должны существовать; CLI проверяет их фактические SHA-256. Подставляй реальные метрики и evidence из выполнения, не из самоотчёта модели.

При принятии решения:
- немедленно отклоняется любой candidate, у которого есть safety failure;
- не допускаются регрессии на кейсах, которые проходили baseline;
- доля пройденных кейсов не может снизиться;
- если доля выросла, кандидат может пройти gate при нулевых safety/regression failures;
- при той же доле требуется как минимум 10% снижения среднего числа шагов, токенов или времени на общих успешных кейсах;
- отсутствие improvement не превращается в PASS автоматически.

Это детерминированные критерии обработки поданных receipts: CLI проверяет существование и SHA-256 каждого tested artifact и evidence file. Но hash доказывает целостность файла, а не то, что агент честно выполнил описанный запуск. Сами evidence-файлы и логи необходимо проверить независимо; формат receipt не должен заполняться выдуманными показателями моделью. Эти критерии также не являются statistical significance. Для важных навыков увеличивать число кейсов/повторов.

## Границы безопасности и честности

1. Уроки, собранные из пользовательских сессий, хранятся локально. Не коммить `events.jsonl`, профильные данные, исходные сессии, MEMORY.md/USER.md, токены и реальные конфиги.
2. Перед записью события отправляй краткое обобщение; не вставляй полный транскрипт. Маскирование частых API-токенов — только защита в глубину, а не гарантированная очистка персональных данных.
3. Кандидат не попадает в retrieval до gate PASS **и** явного `accept --approved-by`.
4. `accept` добавляет кандидат в локальный approved store, но не меняет автоматически active SOUL.md/SKILL.md. Интеграцию с текущим Hermes нужно отдельно проверить на реальной версии CLI. Ключ/флаг `--approved-by` — аудиторская отметка процесса, а не криптографическая граница доверия против агента с прямым shell/filesystem-доступом. На этом этапе acceptance должен выполнять владелец, а не автономный worker.
5. Для текущего компьютера не добавлять тяжёлую модель или hosted API только ради self-improvement. В первой интеграции не менять работающую модель/endpoint или голосовой ввод/вывод.
6. Ремонт USB/дисков, системные изменения и опасные команды — отдельный класс задач. Успешная запись урока не даёт разрешение повторить действие и не заменяет явного одобрения разрушительной операции.
7. LLM-as-judge может давать аналитический feedback, но не является единственным критерием принятия. Использовать детерминированные проверки, логи, exit codes и человеческую проверку там, где нельзя формализовать успех.

## Чего пока нет

- Нет автоматического импорта всех сессий Hermes; формат актуальной локальной session DB нужно отдельно проверить.
- Нет автоматического запуска baseline/candidate через Hermes CLI: receipts должны быть получены из настоящих тестовых прогонов и проверены. Следующий этап — реализовать узкий адаптер после аудита установленного Hermes CLI, не угадывая флаги.
- Нет fine-tuning и не изменяются веса модели.
- Нет межпользовательского обучения или облачной передачи личных данных.
- Нет автоматической записи принятых правил в активные инструкции.

## Definition of done для интеграции

- [ ] Unit tests проходят.
- [ ] На локальной копии сохранено одно настоящее событие с исправлением владельца, без секретов.
- [ ] Кандидат связан с событием и тестовыми case ID.
- [ ] Имеются baseline/candidate receipts из одинаковых тестов с неизменным model/runtime.
- [ ] Gate отклоняет искусственную safety-регрессию и no-improvement кандидат.
- [ ] При принятом кандидате контекст возвращает только approved playbook.
- [ ] В интеграции с локальным Hermes проведён хотя бы один повторный реальный тест «до/после», при этом не сломаны voice-in/voice-out и рабочая модель.
- [ ] Нет автоматической модификации рабочих файлов без одобрения.

## Исходники

- Reflexio: https://github.com/ReflexioAI/reflexio
- Reflexio GDPVal results: https://github.com/ReflexioAI/reflexio/blob/main/benchmark/gdpval/RESULTS.md
- Reddit discussion: https://www.reddit.com/r/AI_Agents/comments/1slqhzu/are_there_any_benchmarks_for_selfimproving_agents/
- r/AI_Agents wiki: https://www.reddit.com/r/AI_Agents/wiki
- Текущий проект Hermes Agent Self-Evolution: https://github.com/convergentaru/hermes-agent-self-evolution
