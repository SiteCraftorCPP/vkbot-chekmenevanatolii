# VK Sports AI Bot

VK-бот принимает подтверждение регистрации, отправляет его нескольким
администраторам на модерацию, а после одобрения распознаёт скриншоты спортивных
событий через локальный PaddleOCR и передаёт текст в DeepSeek.

## Возможности

- роли пользователя и нескольких администраторов;
- редактирование текста, фото и ссылки стартового оффера через админ-панель;
- раздел заявок с пролистыванием и модерацией;
- конкурентно-безопасное подтверждение заявки кнопками;
- хранение всех пользователей, заявок и прогнозов в базе;
- рассылка текста и вложений всем пользователям;
- локальное OCR без передачи скриншота стороннему vision API;
- прогноз с конкретной ставкой и процентной вероятностью захода.

## Локальный запуск (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
# заполнить .env
.venv\Scripts\python.exe -m app.main
```

## Запуск через Docker

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose logs -f bot
```

## Запуск на VPS через systemd

Каталог проекта:

```bash
cd /var/www/vkbot-chekmenevanatolii
```

Установка:

```bash
apt-get update
apt-get install -y python3.11 python3.11-venv git libgomp1 libgl1 libglib2.0-0

mkdir -p /var/www/vkbot-chekmenevanatolii/data
cd /var/www/vkbot-chekmenevanatolii

git clone https://github.com/SiteCraftorCPP/vkbot-chekmenevanatolii.git .
python3.11 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
# заполнить .env

cp vkbot.service /etc/systemd/system/vkbot.service
systemctl daemon-reload
systemctl enable vkbot
systemctl start vkbot
systemctl status vkbot
```

Логи:

```bash
journalctl -u vkbot -f
```

## Использование

- `/start` — стартовый экран или главное меню;
- кнопка «Админ-панель ⚙️» — настройки оффера, заявки и рассылка (только для админов);
- незарегистрированный пользователь отправляет фото подтверждения;
- одобренный пользователь отправляет фото события.

Бот автоматически создаёт таблицы при старте.
