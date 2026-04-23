# Миграции БД (Alembic) и запуск проекта

Подключение к PostgreSQL задаётся переменными окружения (как в `app.py`):

| Переменная   | По умолчанию |
|-------------|--------------|
| `DB_USER`   | `postgres`   |
| `DB_PASSWORD` | `HOME1213` |
| `DB_HOST`   | `localhost`  |
| `DB_PORT`   | `5432`       |
| `DB_NAME`   | `forum_bd`   |

## 1. Установка зависимостей

Из корня проекта (папка `BD-master`):

```powershell
cd "c:\Users\petry\OneDrive\Desktop\6 семестр\Технология программирования\BD-master"
python -m pip install -r requirements.txt
```

## 2. Создать пустую базу (если ещё нет)

```powershell
python create_database.py
```

Скрипт создаёт БД `forum_bd` (если не существует) и при **пустой** схеме применяет `db/schema.sql`. Сразу после этого обязательно:

```powershell
alembic stamp fkn_001_initial
```

Иначе следующий `alembic upgrade head` попытается создать таблицы заново и выдаст ошибку.

Если таблицы в БД **уже есть** (скрипт только сообщил, что БД существует), схему накатывайте только через шаг 3.

## 3. Применить миграции Alembic

Из корня проекта (где лежат `alembic.ini` и папка `alembic`):

```powershell
alembic upgrade head
```

Будет создана таблица `alembic_version` и выполнена ревизия `fkn_001_initial` (содержимое `db/schema.sql`).

**Уже развёрнута схема** (`create_database.py` без `stamp`, или ручной импорт SQL), но Alembic ещё не использовался:

```powershell
alembic stamp fkn_001_initial
```

Помечает текущую БД как соответствующую последней миграции **без** повторного DDL.

## 4. Старая БД без новых полей (до ФКН-схемы)

Если у вас остались таблицы старого форума без `role`, `post_type`, `post_reactions` и т.д., выполните в клиенте PostgreSQL файл:

`db/migrate_v2.sql`

После этого при необходимости выполните:

```powershell
alembic stamp fkn_001_initial
```

## 5. Тестовые данные (опционально)

```powershell
python seed_fcs_vsu.py
```

## 6. Запуск приложения

```powershell
python app.py
```

Откройте в браузере адрес, который выведет Flask (обычно `http://127.0.0.1:5000`).

## Полезные команды Alembic

- Текущая версия: `alembic current`
- История: `alembic history`
- Откат на шаг назад: `alembic downgrade -1` (удалит таблицы проекта — осторожно)
