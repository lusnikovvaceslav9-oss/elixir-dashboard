# FB Ads Scraper → Google Sheets → Elixir Dashboard

Автомат сбора Facebook Ads через **AdsPower** с разнесением **по проектам дашборда**.

## Схема

```
Elixir dashboard (JSONBin)  ← редактируете в админке
    ↓
Mac: fb_ads_scraper.py подтягивает конфиг перед каждым запуском
    ↓
AdsPower profile → FB Ads Manager (BM + act из конфига)
    ↓
Google Sheet проекта
  • Лист 1        — формат для Elixir (дашборд читает этот лист)
  • FB Кампании   — детальная выгрузка всех метрик
    ↓
elixir.html → Обновить
```

## Быстрый старт

```bash
cd fb-ads-scraper
bash setup_mac.sh
# credentials.json уже должен лежать в папке
# В config.json добавьте jsonbin_master_key (тот же, что в дашборде)
python3 show_projects.py   # проверить маппинг с JSONBin
bash run.sh                # первый запуск
```

## Конфиг на бэке (рекомендуется)

Настройки хранятся в **JSONBin** вместе с проектами дашборда. Редактируются в Elixir:

1. Войти в админку (пароль в дашборде)
2. Открыть проект → **Редактировать**
3. Секция **FB Ads Scraper (Mac)**:
   - ✅ Парсить автоматически
   - **AdsPower profile ID** — профиль с залогиненным FB
   - **Business Manager ID** — номер BM, где лежат РК
   - **Ad account (act)** — рекламный кабинет, откуда парсить
   - **Лист для дашборда** / **Лист детализации**
4. **Сохранить** — Mac-скрипт подхватит при следующем запуске

ID таблицы берётся автоматически из ссылки Google Sheets проекта.

### `config.json` на Mac

```json
{
  "projects_source": "auto",
  "jsonbin_bin_id": "6a2d1063f5f4af5e29eaccbd",
  "jsonbin_master_key": "ВАШ_КЛЮЧ"
}
```

| `projects_source` | Поведение |
|-------------------|-----------|
| `auto` | Сначала JSONBin, если недоступен — `projects.json` |
| `jsonbin` | Только JSONBin (ошибка, если пусто) |
| `local` | Только локальный `projects.json` |

Ключ можно задать через переменные `JSONBIN_BIN_ID` / `JSONBIN_MASTER_KEY`.

## Локальный fallback: `projects.json`

Если JSONBin недоступен, используется локальный файл (как раньше).

| Поле | Что это |
|------|---------|
| `dashboard_id` | ID проекта в Elixir |
| `profile_id` | ID профиля AdsPower |
| `bm_id` | Business Manager ID |
| `ad_account_id` | Ad account (act) |
| `sheet_id` | Google Таблица проекта |
| `dashboard_sheet` | Лист для дашборда |
| `detail_sheet` | Лист детализации |
| `export_mode` | `campaign` или `daily` |

```bash
python3 show_projects.py   # что видит скрапер сейчас
```

## Формат листа для дашборда (`export_mode: campaign`)

```
Дата,Кампания,Спенд,Результаты,Показы,Клики
09.07.2026,Кампания 1,454,2,12000,58
09.07.2026,Кампания 2,389,7,8000,52
```

Elixir автоматически распознаёт тип **campaign** (есть Показы/Клики).

## Автозапуск каждые 2 часа

```bash
cp com.user.fbscraper.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.fbscraper.plist
```

## Файлы

| Файл | Назначение |
|------|------------|
| `config.json` | AdsPower API, JSONBin, таймауты |
| `remote_config.py` | Загрузка конфига с JSONBin |
| `projects.json` | Локальный fallback |
| `credentials.json` | OAuth Google (не в git) |
| `status.json` | Результат последнего прогона |
| `fb_ads_scraper.log` | Лог |

## Требования

- Mac с запущенным **AdsPower** (Local API :50325)
- FB залогинен в каждом профиле AdsPower
- Google OAuth (`credentials.json`) с доступом к таблицам

## Связка с дашбордом

URL проекта в дашборде указывает на ту же таблицу, что и `sheet_id` в конфиге.
После прогона скрапера нажмите **Обновить** в Elixir — данные подтянутся.
