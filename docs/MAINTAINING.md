# Гайд мейнтейнера

Как устроена автоматизация, что один раз настроить на GitHub и как с этим жить дальше.

## Как всё работает

```
Форма «Добавить VPN» ──► submission.yml ──► проверка автора ──► проверка формы и сайта ──► PR от бота
                                              │ не прошёл          │ ошибки
                                              ▼                    ▼
                                       комментарий + закрыть   комментарий + needs-changes
                                                               (автор правит заявку → перепроверка)

PR напрямую ──► pr-guard.yml: новый сервис от новичка → закрыть и отправить в форму;
                смена домена → domain-change; правки автоматизации → sensitive-change
Любой PR ──► ci.yml: тесты, структура README, якоря оглавления, новые ссылки открываются
Каждый понедельник ──► link-check.yml: битые ссылки и редиректы на чужие домены → issue link-report
```

Бот дописывает новый сервис в README в трёх местах: строку в конец сравнительной таблицы (перед `<!-- services:end -->`), обзор (перед `<!-- reviews:end … -->`) и пункт оглавления (в конец списка обзоров). Эти комментарии нельзя удалять — CI проверяет, что они на месте.

Проверка автора (`scripts/trust.py`, пороги в `.github/submission-policy.toml`):
- аккаунту не меньше 30 дней для новых сервисов и 7 дней для правок (кроме тех, у кого уже есть смёрженный PR здесь);
- не больше 1 открытой заявки и 3 заявок за 30 дней;
- не в `blocked_users`;
- мейнтейнеры (владелец и коллабораторы с правом записи) проходят без проверок.

Что проверяется в заявке, описано в [CONTRIBUTING.md](../CONTRIBUTING.md#что-проверяет-бот).

| Файл | Зачем |
| :--- | :--- |
| `.github/ISSUE_TEMPLATE/add-vpn.yml` | Форма заявки. Бот читает поля прямо из неё; варианты протоколов, платформ и оплаты сверяет тест |
| `.github/submission-policy.toml` | Пороги, лимиты, блок-листы |
| `.github/workflows/submission.yml` | Запуск бота на событиях issues |
| `.github/workflows/pr-guard.yml` | Проверка PR, открытых напрямую (`pull_request_target`, код PR не запускается) |
| `.github/workflows/ci.yml` | Тесты и проверка README на каждом PR и push в `main` |
| `.github/workflows/link-check.yml` | Еженедельная проверка ссылок |
| `.github/workflows/workflow-lint.yml` | actionlint и zizmor для изменений workflow |
| `scripts/submission.py` | Заявка → проверки → PR |
| `scripts/pr_guard.py` | Правила для PR напрямую |
| `scripts/readme.py` | Разбор README и вставка нового сервиса |
| `scripts/validate.py` | Проверка README для CI |
| `scripts/policy.py`, `scripts/netcheck.py`, `scripts/trust.py` | Правила ссылок и текстов, сетевые проверки, проверка автора |

Все скрипты — на стандартной библиотеке Python, без зависимостей.

---

## Разовая настройка

Всё ниже делается в репозитории `kozlovartem20201/awesome-vpn-list-russia` на github.com. Сначала запушьте изменения в `main`: бот на событиях issues всегда берёт workflow из ветки по умолчанию.

### Шаг 1. Метки — обязательно

GitHub **молча выбрасывает** метки формы, которых нет в репозитории, а бот реагирует только на issue с меткой `submission`. Без этого шага бот не запустится ни разу.

```bash
R=kozlovartem20201/awesome-vpn-list-russia
gh label create submission          -R $R -c 0E8A16 -d "Заявка на добавление VPN (обрабатывает бот)"
gh label create maintainer-approved -R $R -c 5319E7 -d "Мейнтейнер снял проверки автора — бот перепроверит заявку"
gh label create needs-changes       -R $R -c FBCA04 -d "Бот нашёл проблемы, автор должен поправить"
gh label create pr-opened           -R $R -c 1D76DB -d "Бот открыл pull request"
gh label create trust-check-failed  -R $R -c B60205 -d "Автор не прошёл проверки"
gh label create needs-manual-review -R $R -c D93F0B -d "Нужна внимательная ручная проверка"
gh label create new-service         -R $R -c 0E8A16 -d "PR бота с новым сервисом"
gh label create vendor-submitted    -R $R -c C5DEF5 -d "Автор связан с сервисом"
gh label create domain-change       -R $R -c D93F0B -d "У существующего сервиса меняется домен"
gh label create sensitive-change    -R $R -c B60205 -d "Правки автоматизации или правил"
gh label create use-submission-form -R $R -c FBCA04 -d "Новый сервис нужно добавить через форму"
gh label create update-request      -R $R -c 1D76DB -d "Сервис изменился или перестал работать"
gh label create feedback            -R $R -c C2E0C6 -d "Идея или вопрос"
gh label create link-report         -R $R -c D73A4A -d "Еженедельный отчёт о ссылках"
```

### Шаг 2. Настройки Actions

**Settings → Actions → General:**

1. **Fork pull request workflows from outside collaborators:** «Require approval for all external contributors». Чужие PR не запустят CI без вашего одобрения.
2. **Workflow permissions:** «Read repository contents and packages permissions». Нужные права workflow запрашивают сами.
3. **«Allow GitHub Actions to create and approve pull requests»:**
   - **включите**, если пропускаете шаг 3 (без App бот открывает PR от имени `github-actions`);
   - **выключите**, если настроили App.
4. **Actions permissions → «Require actions to be pinned to a full-length commit SHA»** — включите: все actions здесь уже закреплены по SHA, а Dependabot обновляет их так же.

### Шаг 3. GitHub App для бота — рекомендуется

Без App бот тоже работает, но PR, открытые через `GITHUB_TOKEN`, **не запускают CI** — проверку придётся запускать, закрыв и открыв PR. С App PR бота сразу проверяются.

У вас уже есть App, которое работает в `amnezia-vpn-russia`. Проще всего установить его и сюда:

1. Под `kozlovartem20201`: **Settings (профиля) → Developer settings → GitHub Apps → ваше приложение**.
2. **Install App** → шестерёнка у аккаунта → **Only select repositories** → добавьте `awesome-vpn-list-russia` → **Save**. Права (Contents, Issues, Pull requests — Read and write) уже подходят.
3. На странице приложения: **Private keys → Generate a private key**. Новый ключ не отменяет старый, amnezia продолжит работать.
4. Здесь, в **Settings → Secrets and variables → Actions**:
   - **Secrets → New repository secret:** `BOT_PRIVATE_KEY` — всё содержимое `.pem`, включая строки `-----BEGIN…` и `-----END…`;
   - **Variables → New repository variable:** `BOT_CLIENT_ID` — тот же Client ID, что в amnezia-vpn-russia.
5. Удалите `.pem` с компьютера.

Workflow увидят `BOT_CLIENT_ID` и переключатся на App. Секреты не передаются в workflow из чужих форков.

### Шаг 4. Защита ветки main — рекомендуется

**Settings → Rules → Rulesets → New ruleset → New branch ruleset:**

- **Ruleset name:** `main`, **Enforcement status:** Active;
- **Target branches → Add target → Include default branch**;
- **Restrict deletions** и **Block force pushes**;
- **Require status checks to pass** → **Add checks** → `Проверка README` (источник GitHub Actions);
- **Bypass list → Add bypass → Repository admin**, режим **Always** — чтобы вы по-прежнему могли править README прямо в веб-интерфейсе.

Так `main` нельзя удалить или переписать, а у чужих PR и PR бота кнопка мержа ждёт зелёный CI. «Require a pull request» с обязательным approval для одного мейнтейнера неудобен: свои PR одобрить нельзя, а правки в веб-интерфейсе пришлось бы оформлять через PR. Включайте его, только если появятся коллабораторы.

### Шаг 5. Безопасность и прочее

- **Settings → Advanced Security:** включите **Private vulnerability reporting** — без него не работает приватная форма из [SECURITY.md](../SECURITY.md) и из меню «New issue». Там же включите **Dependabot alerts**, **Secret scanning** и **Push protection**.
- **Settings → General → Pull Requests:** включите **Automatically delete head branches** — ветки `submission/issue-N` будут удаляться после мержа.
- **Settings → General → Social preview:** загрузите обложку 1280×640 — она показывается в превью ссылки в Telegram, X и мессенджерах.

### Шаг 6. Проверка, что всё работает

1. После push в `main` откройте **Actions → CI** — проверка должна пройти зелёным.
2. **Issues → New issue → «Добавить VPN в список»** с тестовыми данными реального сервиса, которого ещё нет в списке. Через 1–2 минуты бот оставит комментарий с превью и откроет PR `Добавить VPN: …`. Если не нужен — закройте PR и issue.
3. Ещё одна заявка с описанием «Лучший VPN!» — бот должен ответить «Нужно исправить» и поставить `needs-changes`.
4. **Actions → Проверка ссылок → Run workflow** — если найдутся битые ссылки, появится issue с меткой `link-report`.

---

## Повседневная работа

**PR от бота.** Проверьте превью, раздел «Требует внимания» и метки:
- `needs-manual-review` — молодой аккаунт, сайт закрыт защитой от ботов, домен .ru/.рф без даты в RDAP, название или домен похожи на уже добавленный сервис;
- `vendor-submitted` — заявку прислал сам сервис.

Мелочи можно поправить прямо в ветке PR (например, дописать «Для кого подходит»). После мержа заявка закроется сама.

**Конфликт после мержа другого PR.** Если два PR бота добавляют сервисы одновременно, второй получит конфликт в README. Закройте и снова откройте заявку (issue) — бот пересоберёт PR поверх свежего `main`.

**Одобрить заявку вручную.** Если честный автор не прошёл проверку (свежий аккаунт) или домен моложе 90 дней, поставьте на issue метку `maintainer-approved`. Бот переоткроет заявку и проверит её без проверок автора; молодой домен станет предупреждением. Метка учитывается, только если её поставил человек с правом записи.

**Заявки «Сообщить об изменении»** (`update-request`) бот не обрабатывает — правьте README сами или через PR.

**Блокировка спама.** Добавьте логин в `blocked_users` или домен в `blocked_domains` в `.github/submission-policy.toml`. Там же меняются пороги.

**Еженедельный отчёт.** Issue с меткой `link-report`: сайты, которые стали перенаправлять на другой домен (возможен угон — сначала уберите ссылку, потом разбирайтесь), и битые ссылки (сервис мог закрыться).

**Правки руками.** README по-прежнему можно править как угодно — главное, не трогайте служебные комментарии, давайте каждому сервису из таблицы обзор `### Название` и проверяйте, что ссылки оглавления ведут на заголовки. Всё это проверяет CI.

## Запуск локально

```bash
python -m unittest discover -s tests                        # тесты, без сети
python scripts/validate.py                                  # проверить README
python scripts/validate.py --base origin/main --online      # плюс новые ссылки относительно main
python scripts/submission.py --event event.json --offline   # прогнать бота на сохранённом событии issues
python scripts/linkcheck.py                                 # проверить все ссылки
```

## Если что-то не так

| Симптом | Причина и решение |
| :--- | :--- |
| Бот не реагирует на заявку | Нет метки `submission` в репозитории (шаг 1) — создайте её и поставьте на issue руками. Либо изменения ещё не в `main`. Либо Actions выключены |
| `Resource not accessible by integration` при создании PR | Без App: не включено «Allow GitHub Actions to create and approve pull requests» (шаг 2.3) |
| На PR бота нет проверки CI | PR открыт через `GITHUB_TOKEN`. Закройте и откройте PR или настройте App (шаг 3) |
| Ошибка создания токена App | Неверный `BOT_CLIENT_ID`, ключ вставлен не целиком или App не установлено в этот репозиторий |
| «Разметка README сломана» в логах бота | Кто-то удалил служебный комментарий или заголовок раздела обзоров. Верните их — CI подскажет, чего не хватает |
| Приватная форма из SECURITY.md выдаёт 404 | Не включён Private vulnerability reporting (шаг 5) |
