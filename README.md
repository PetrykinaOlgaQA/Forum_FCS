# Forum_FCS

Веб-платформа для студентов ФКН ВГУ: обсуждения, публикация постов, товаров и услуг, комментарии, лайки и базовая модерация.

## О проекте

- Регистрация и вход пользователей
- Лента постов с фильтрацией, поиском и сортировкой
- Типы постов: `post`, `good`, `service`
- Комментарии и реакции к постам
- Роли: обычный пользователь и модератор
- Поддержка парных тем (`topics.paired_topic_id`)

## Технологический стек

- **Backend:** Python 3.11, Flask
- **База данных:** PostgreSQL
- **ORM/SQL:** SQLAlchemy 2.x (Core + text queries)
- **Миграции:** Alembic
- **Шаблоны:** Jinja2 + HTML/CSS (Bootstrap 5)
- **Драйвер БД:** `pg8000` (также в зависимостях есть `psycopg2-binary`)

## Структура проекта

- `app.py` — основной Flask-приложение и маршруты
- `repositories/` — слой доступа к данным
- `models/` — модели (структура сущностей)
- `db/schema.sql` — схема БД "с нуля"
- `db/migrate_v2.sql` — миграция старой схемы
- `create_database.py` — создание БД и применение схемы
- `seed_fcs_vsu.py` — заполнение тестовыми данными
- `templates/` — шаблоны интерфейса
- `alembic/` — конфигурация миграций

## Требования

- Python 3.10+ (рекомендуется 3.11)
- PostgreSQL 13+
- Доступ к пользователю PostgreSQL с правами на создание/изменение БД

## Установка и запуск (Windows / PowerShell)

### 1) Клонировать репозиторий

```powershell
git clone https://github.com/PetrykinaOlgaQA/Forum_FCS.git
cd Forum_FCS
```

### 2) Создать и активировать виртуальное окружение

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3) Установить зависимости

```powershell
pip install -r requirements.txt
```

### 4) Настроить переменные окружения

Создайте файл `.env` рядом с `app.py`:

```env
DB_USER=postgres
DB_PASSWORD=home1213
DB_HOST=localhost
DB_PORT=5432
DB_NAME=forum_bd
FLASK_SECRET_KEY=change_me_to_long_random_value
```

> Если у вас другой пароль PostgreSQL — укажите свой в `DB_PASSWORD`.

### 5) Создать БД и схему

```powershell
python create_database.py
```

Скрипт:
- создаст БД `forum_bd` (если ее нет),
- применит `db/schema.sql`,
- подскажет по фиксации миграции Alembic.

### 6) (Опционально) заполнить тестовыми данными

```powershell
python seed_fcs_vsu.py
```

### 7) Запустить приложение

```powershell
python app.py
```

Открыть в браузере: `http://127.0.0.1:5000`

## Обновление старой БД

Если есть старая версия схемы, выполните:

```sql
\i db/migrate_v2.sql
```

или запустите приложение: в `app.py` есть мягкая авто-попытка применить совместимую миграцию.

## Полезные команды

Проверка синтаксиса:

```powershell
python -m py_compile app.py create_database.py
```

## Примечания

- Новая версия сайта в папке `templates/new/` не включена в текущую выгрузку репозитория.
- Для production рекомендуется запуск через WSGI-сервер и отдельная конфигурация секретов/переменных окружения.
