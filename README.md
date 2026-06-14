# ig-reels — автогенерация и автопостинг Reels в [@ai_findir](https://instagram.com/ai_findir)

Полностью бесплатный конвейер (без карты, без сервера). **GitHub Actions** рендерит
вертикальные Reels из текстовых сценариев и публикует их в Instagram через Graph API.
Твой торговый VPS не используется — всё считается на раннерах GitHub.

## Как это работает

```
queue/*.json  →  GitHub Actions
                 ├─ edge-tts: озвучка + тайминги по словам
                 ├─ ffmpeg:  градиентный фон + субтитры → MP4 1080×1920
                 ├─ commit MP4 → jsDelivr отдаёт публичный URL
                 ├─ Graph API: создать контейнер → дождаться → опубликовать
                 ├─ Telegram: «опубликовано ✅»
                 └─ сценарий → published/
```

Расписание: **Пн/Ср/Пт 10:00 МСК**. Вручную: вкладка **Actions → Publish Reel → Run workflow**.

## Секреты репозитория
`Settings → Secrets and variables → Actions → New repository secret`

| Имя | Значение |
|-----|----------|
| `META_TOKEN` | долгоживущий токен Graph API (~60 дней, обновлять) |
| `IG_USER_ID` | `17841428244696286` |
| `TG_BOT_TOKEN` | *(опц.)* токен Telegram-бота для уведомлений |
| `TG_CHAT_ID` | *(опц.)* id чата, куда слать уведомления |

## Добавить сценарии
Положи в `queue/` файл `NNN-slug.json` (берутся по порядку имени):

```json
{
  "hook": "Цепляющий заголовок (вверху экрана)",
  "voiceover": "Полный текст озвучки. Первые 3 секунды решают всё.",
  "caption": "Подпись с эмодзи, CTA и #хэштегами"
}
```

Сгенерировать новую пачку — попроси Claude.

## Поменять голос
Секрет/переменная `TTS_VOICE`, напр. `ru-RU-SvetlanaNeural` (женский).
Список: `edge-tts --list-voices` (фильтр `ru-RU`).

## Локальный тест рендера
```bash
pip install -r requirements.txt   # + ffmpeg, шрифт с кириллицей
python scripts/render.py queue/001-cashflow-trap.json out.mp4
```
