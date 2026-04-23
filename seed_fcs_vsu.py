"""
Наполнение БД демонстрационными данными по мотивам жизни ФКН ВГУ.
Запуск: после create_database.py и применения db/schema.sql (или migrate_v2.sql).

  python seed_fcs_vsu.py

Переменные окружения: DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME (как в app.py).
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "HOME1213")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "forum_bd")

DATABASE_URL = f"postgresql+pg8000://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

POST_SNIPPETS = [
    "На кафедре МО ЭВМ завтра консультация по курсовым — не опаздывайте, аудитория 331.",
    "Кто пойдёт на Codeforces вместе? Нужен напарник по динамике.",
    "Сдаю конспекты по дискретной математике (2 курс), печать + PDF.",
    "Ищу команду на ICPC: сильный алгоритмист + кто-то по реализации на C++.",
    "Лекция по машинному обучению перенесена на среду, ссылка в LMS.",
    "В общаге №4 отключат горячую воду с 10 до 14, планируйте душ заранее.",
    "Репетиторство по Python и SQL для первокурсников, опыт 2 года на ФКН.",
    "Продаю механическую клавиатуру после перехода на ноут, торг уместен.",
    "Доклад на семинаре по NLP: разбор transformers на примере классификации текстов.",
    "Кто-нибудь разобрался с настройкой WSL2 + Docker для лаб по ОС?",
]

GOOD_META = [
    {"price_rub": 400, "contact": "@fkn_books", "tags": ["учеба", "конспекты"]},
    {"price_rub": 2500, "contact": "@vsu_kb", "tags": ["периферия"]},
    {"price_rub": 150, "contact": "stud@student.vsu.ru", "tags": ["разное"]},
]

SERVICE_META = [
    {"price_rub": 800, "contact": "@tutor_cpp", "tags": ["репетитор", "C++"]},
    {"price_rub": 500, "contact": "@help_latex", "tags": ["LaTeX", "курсовая"]},
    {"price_rub": 1200, "contact": "@ml_mentor", "tags": ["ML", "проект"]},
]


def main() -> None:
    engine = create_engine(DATABASE_URL)
    rnd = random.Random(42)

    users_spec = [
        ("moderator_fkn", "moderator@cs.vsu.ru", "mod12345", "moderator"),
    ]
    for i in range(18):
        uname = f"fkn_student_{i + 1:02d}"
        email = f"{uname}@student.vsu.ru"
        users_spec.append((uname, email, "student123", "user"))

    topic_defs = [
        {
            "title": "Олимпиадное программирование · ВГУ",
            "description": "Тренировки, разборы задач, отборы на ICPC.",
            "pair": "Алгоритмы и структуры данных",
        },
        {
            "title": "Алгоритмы и структуры данных",
            "description": "Графы, деревья, жадность, ДП — в связке с олимпиадами.",
            "pair": "Олимпиадное программирование · ВГУ",
        },
        {
            "title": "Машинное обучение и анализ данных",
            "description": "Курсы, датасеты соревнований, обсуждение моделей.",
            "pair": "Статистика и теорвер на ФКН",
        },
        {
            "title": "Статистика и теорвер на ФКН",
            "description": "Конспекты, экзамены, связь с ML и аналитикой.",
            "pair": "Машинное обучение и анализ данных",
        },
        {
            "title": "Системное программирование и ОС",
            "description": "Linux, процессы, потоки, лабораторные.",
            "pair": "Кафедра МО ЭВМ — новости",
        },
        {
            "title": "Кафедра МО ЭВМ — новости",
            "description": "Объявления преподавателей, расписание консультаций.",
            "pair": "Системное программирование и ОС",
        },
        {
            "title": "Общежитие и быт (ФКН)",
            "description": "Бытовые вопросы, соседи, документы в деканат.",
            "pair": "Деканат ФКН — справки",
        },
        {
            "title": "Деканат ФКН — справки",
            "description": "Справки, военкомат, пересдачи — делимся опытом.",
            "pair": "Общежитие и быт (ФКН)",
        },
        {
            "title": "Проектная практика и стажировки",
            "description": "Вакансии, хакатоны, партнёрские компании региона.",
            "pair": None,
        },
    ]

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                TRUNCATE TABLE post_reactions, comments, posts, topics, users
                RESTART IDENTITY CASCADE
                """
            )
        )

        uid_by_username: dict[str, int] = {}
        for username, email, password, role in users_spec:
            row = conn.execute(
                text(
                    """
                    INSERT INTO users (username, email, password, role)
                    VALUES (:u, :e, :p, :r)
                    RETURNING id
                    """
                ),
                {"u": username, "e": email, "p": password, "r": role},
            ).fetchone()
            uid_by_username[username] = int(row[0])

        mod_id = uid_by_username["moderator_fkn"]
        student_ids = [v for k, v in uid_by_username.items() if k != "moderator_fkn"]

        tid_by_title: dict[str, int] = {}
        for td in topic_defs:
            row = conn.execute(
                text(
                    """
                    INSERT INTO topics (title, description, user_id, paired_topic_id)
                    VALUES (:t, :d, :uid, NULL)
                    RETURNING id
                    """
                ),
                {
                    "t": td["title"],
                    "d": td["description"],
                    "uid": mod_id,
                },
            ).fetchone()
            tid_by_title[td["title"]] = int(row[0])

        for td in topic_defs:
            pair_title = td.get("pair")
            if not pair_title:
                continue
            a = tid_by_title[td["title"]]
            b = tid_by_title.get(pair_title)
            if b is not None:
                conn.execute(
                    text(
                        "UPDATE topics SET paired_topic_id = :p WHERE id = :id"
                    ),
                    {"p": b, "id": a},
                )

        titles = list(tid_by_title.keys())
        post_ids: list[int] = []
        base_time = datetime.utcnow() - timedelta(days=45)

        for n in range(55):
            ttitle = rnd.choice(titles)
            tid = tid_by_title[ttitle]
            author = rnd.choice(student_ids + [mod_id])
            kind = rnd.choices(
                ["post", "good", "service"],
                weights=[0.55, 0.22, 0.23],
                k=1,
            )[0]
            body = rnd.choice(POST_SNIPPETS)
            if rnd.random() < 0.35:
                body = body + " " + rnd.choice(POST_SNIPPETS)
            meta: dict = {}
            if kind == "good":
                meta = dict(rnd.choice(GOOD_META))
            elif kind == "service":
                meta = dict(rnd.choice(SERVICE_META))

            created = base_time + timedelta(hours=n * 18 + rnd.randint(0, 12))
            row = conn.execute(
                text(
                    """
                    INSERT INTO posts (topic_id, user_id, content, post_type, meta, created_date)
                    VALUES (:tid, :uid, :c, :pt, CAST(:meta AS jsonb), :cd)
                    RETURNING id
                    """
                ),
                {
                    "tid": tid,
                    "uid": author,
                    "c": body,
                    "pt": kind,
                    "meta": json.dumps(meta, ensure_ascii=False),
                    "cd": created,
                },
            ).fetchone()
            post_ids.append(int(row[0]))

        for _ in range(95):
            pid = rnd.choice(post_ids)
            author = rnd.choice(student_ids)
            phrases = (
                "Согласен, на лекции это тоже обсуждали.",
                "Кинь ссылку на материалы, пожалуйста.",
                "На ФКН в прошлом году делали похожий проект.",
                "Пиши в личку, помогу с разбором.",
                "Это в курсе лектор упоминал в конце семестра.",
                "+1, полезная тема для первого курса.",
            )
            conn.execute(
                text(
                    """
                    INSERT INTO comments (post_id, user_id, content)
                    VALUES (:pid, :uid, :c)
                    """
                ),
                {"pid": pid, "uid": author, "c": rnd.choice(phrases)},
            )

        pairs_likes = set()
        for _ in range(220):
            uid = rnd.choice(student_ids)
            pid = rnd.choice(post_ids)
            key = (uid, pid)
            if key in pairs_likes:
                continue
            pairs_likes.add(key)
            conn.execute(
                text(
                    """
                    INSERT INTO post_reactions (user_id, post_id, reaction)
                    VALUES (:uid, :pid, 1)
                    ON CONFLICT (user_id, post_id) DO NOTHING
                    """
                ),
                {"uid": uid, "pid": pid},
            )

    print("Готово: пользователи, темы-пары, посты (post/good/service), комментарии, лайки.")
    print("Модератор: moderator@cs.vsu.ru / mod12345")
    print("Студент (пример): fkn_student_01@student.vsu.ru / student123")


if __name__ == "__main__":
    main()
