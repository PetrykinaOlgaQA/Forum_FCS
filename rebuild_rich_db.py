"""
Полная очистка контента и пересборка осмысленной демо-БД: темы-пары, споры,
вложенные комментарии, товары и услуги — без служебных префиксов в тексте.

Удаляет: moderation_log, comments, post_reactions, posts, topics,
         всех пользователей кроме admin@fkn.vsu.ru (пароль существующего админа не меняется).

Запуск:  python rebuild_rich_db.py --yes
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import defaultdict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from auth_passwords import hash_password

ADMIN_EMAIL = "admin@fkn.vsu.ru"
ADMIN_USER = "admin_fkn"
ADMIN_PLAIN = "AdminFkn2026!"
DEMO_PASSWORD = "DemoHub2026!"


def load_dotenv(path: str = ".env"):
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def engine_from_env():
    load_dotenv()
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "home1213")
    h = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "5432"))
    db = os.getenv("DB_NAME", "forum_bd")
    url = os.getenv("DATABASE_URL")
    if not url:
        url = URL.create("postgresql+pg8000", username=u, password=pw, host=h, port=port, database=db)
    return create_engine(url, pool_pre_ping=True)


def wipe_content(conn):
    conn.execute(text("TRUNCATE moderation_log RESTART IDENTITY CASCADE"))
    conn.execute(text("TRUNCATE comments RESTART IDENTITY CASCADE"))
    conn.execute(text("TRUNCATE post_reactions RESTART IDENTITY CASCADE"))
    conn.execute(text("TRUNCATE posts RESTART IDENTITY CASCADE"))
    conn.execute(text("TRUNCATE topics RESTART IDENTITY CASCADE"))
    conn.execute(text("DELETE FROM users WHERE email <> :e"), {"e": ADMIN_EMAIL})


def ensure_admin(conn) -> int:
    row = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": ADMIN_EMAIL}).fetchone()
    if row:
        return int(row[0])
    hp = hash_password(ADMIN_PLAIN)
    conn.execute(
        text(
            "INSERT INTO users (username, email, password, role) VALUES (:u, :e, :p, 'admin')"
        ),
        {"u": ADMIN_USER, "e": ADMIN_EMAIL, "p": hp},
    )
    return int(conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": ADMIN_EMAIL}).scalar())


def insert_personas(conn, admin_id: int) -> dict[str, int]:
    hp = hash_password(DEMO_PASSWORD)
    personas = [
        ("maria_morf", "maria.morf@student.fkn.local"),
        ("ilya_proofs", "ilya.proofs@student.fkn.local"),
        ("egor_gym", "egor.gym@student.fkn.local"),
        ("lena_ethics", "lena.ethics@student.fkn.local"),
        ("dima_startup", "dima.startup@student.fkn.local"),
    ]
    ids: dict[str, int] = {"admin": admin_id}
    for uname, email in personas:
        conn.execute(
            text(
                "INSERT INTO users (username, email, password, role) VALUES (:u, :e, :p, 'user')"
            ),
            {"u": uname, "e": email, "p": hp},
        )
        ids[uname] = int(conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar())
    return ids


def insert_topics(conn, u: dict[str, int]) -> dict[str, int]:
    t: dict[str, int] = {}

    def ins(title: str, desc: str, author_key: str) -> int:
        return int(
            conn.execute(
                text(
                    "INSERT INTO topics (title, description, user_id) VALUES (:t, :d, :uid) RETURNING id"
                ),
                {"t": title[:200], "d": desc, "uid": u[author_key]},
            ).scalar()
        )

    # Пары «тема — контртема»
    a1 = ins(
        "Тихий кабинет: одна мониторная армия",
        "Один монитор — меньше отвлечений и честнее для шеи. Здесь защищаем минимализм.",
        "maria_morf",
    )
    b1 = ins(
        "Две мониторки — религия продуктивности",
        "Два экрана: код слева, документация справа. Контекст не теряется.",
        "ilya_proofs",
    )
    conn.execute(text("UPDATE topics SET paired_topic_id = :p WHERE id = :id"), {"p": b1, "id": a1})
    conn.execute(text("UPDATE topics SET paired_topic_id = :p WHERE id = :id"), {"p": a1, "id": b1})
    t["mono_a"], t["mono_b"] = a1, b1

    a2 = ins(
        "Питон для всего",
        "Почему на старте хочется писать всё на Python — и почему это педагогически оправдано.",
        "maria_morf",
    )
    b2 = ins(
        "Системный язык первым: спор поколений",
        "C++ или Rust: дисциплина памяти и скорость мышления vs мягкий вход.",
        "ilya_proofs",
    )
    conn.execute(text("UPDATE topics SET paired_topic_id = :p WHERE id = :id"), {"p": b2, "id": a2})
    conn.execute(text("UPDATE topics SET paired_topic_id = :p WHERE id = :id"), {"p": a2, "id": b2})
    t["py_a"], t["py_b"] = a2, b2

    t["session"] = ins(
        "Сессия выживания: честные стратегии",
        "Сон, приоритеты, дедлайны и когда просить отсрочку — без магии.",
        "lena_ethics",
    )
    t["intern"] = ins(
        "Стажировка до диплома: когда уже поздно?",
        "Портфолио, стек, страхи первого курса vs четвёртого.",
        "dima_startup",
    )
    t["ethics"] = ins(
        "Этика ИИ в курсовых: где грань?",
        "Генераторы текста, цитирование и что скажет комиссия.",
        "lena_ethics",
    )
    t["olymp"] = ins(
        "Олимпиадный угол: не только задачи",
        "Выгорание, команда, когда остановиться — и куда деться после.",
        "egor_gym",
    )
    t["rumors"] = ins(
        "Коридорные слухи ФКН",
        "Короткие заметки: что обсуждают между парами (без персоналий).",
        "dima_startup",
    )
    return t


def insert_post(conn, topic_id: int, user_id: int, content: str, ptype: str, meta: dict | None) -> int:
    return int(
        conn.execute(
            text(
                """
                INSERT INTO posts (topic_id, user_id, content, post_type, meta)
                VALUES (:tid, :uid, :c, :pt, CAST(:m AS jsonb))
                RETURNING id
                """
            ),
            {
                "tid": topic_id,
                "uid": user_id,
                "c": content,
                "pt": ptype,
                "m": json.dumps(meta or {}, ensure_ascii=False),
            },
        ).scalar()
    )


def insert_posts(conn, u: dict[str, int], t: dict[str, int]) -> dict[str, list[int]]:
    """thread_key -> список id постов (для комментариев)."""
    out: dict[str, list[int]] = defaultdict(list)

    def P(thread, topic_key, author, ptype, text_body, meta=None):
        pid = insert_post(conn, t[topic_key], u[author], text_body, ptype, meta)
        out[thread].append(pid)

    # --- Спор мониторы ---
    P(
        "mono",
        "mono_a",
        "maria_morf",
        "post",
        "Один монитор заставляет держать один контекст. Меньше «скачков» глаз — быстрее закрываю лабы по теории.",
    )
    P(
        "mono",
        "mono_b",
        "ilya_proofs",
        "post",
        "С двумя мониторами я не теряю место в коде, когда сравниваю два PDF методички. Это не лень — это экономия переключений.",
    )
    P(
        "mono",
        "mono_a",
        "egor_gym",
        "post",
        "На контесте один экран — норма. В жизни два. Спор бессмысленный, если не сказать задачу.",
    )

    # --- Спор языки ---
    P(
        "lang",
        "py_a",
        "maria_morf",
        "post",
        "Python учит думать алгоритмами, а не бороться с компилятором в первый месяц.",
    )
    P(
        "lang",
        "py_b",
        "ilya_proofs",
        "post",
        "Если никогда не видел сегфолт, в проде потом больно. Системный язык рано — как зарядка для мозга.",
    )
    P(
        "lang",
        "py_a",
        "egor_gym",
        "post",
        "Я выучил и то, и другое. Спорите о порядке, а не о том, что «язык злой».",
    )

    # --- Сессия ---
    P(
        "session",
        "session",
        "lena_ethics",
        "post",
        "Честная стратегия: без сна качество кода падает быстрее, чем вы успеваете коммитить. Лучше сократить объём, чем сдать нечитаемое.",
    )
    P(
        "session",
        "session",
        "maria_morf",
        "post",
        "Расписание по часам, один «якорный» предмет в день. Попросить помощи — не слабость.",
    )
    P(
        "session",
        "session",
        "lena_ethics",
        "post",
        """Длинная заметка про «интригу» расписания.

Когда два экзамена в один день, начинается дипломатия: очередь к преподавателю, перенос консультации, нервы в чате курса. Здесь не злодеи — люди в дефиците времени. Честный совет: не разжигать конфликт в мессенджерах, а синхронизироваться со старостой и официальными объявлениями.

Интерес начинается там, где мы признаём ограничения и всё равно пытаемся сыграть честно.""",
    )

    # --- Стажировка ---
    P(
        "intern",
        "intern",
        "dima_startup",
        "post",
        "Поздно — когда не можешь рассказать, что делал три месяца подряд, кроме «учился». Портфолио из маленьких законченных штук лучше слайдов с обещаниями.",
    )
    P(
        "intern",
        "intern",
        "ilya_proofs",
        "post",
        "Стажировка после второго курса — ок, если вытянули алгоритмы и базы. Иначе горите на простых задачах и думаете, что индустрия токсична.",
    )

    # --- Этика ИИ ---
    P(
        "ethics",
        "ethics",
        "lena_ethics",
        "post",
        "Если нейросеть перефразировала чужую идею без ссылки — это всё ещё академическая нечестность. Обсудим критерии, а не «все так делают».",
    )
    P(
        "ethics",
        "ethics",
        "maria_morf",
        "post",
        "В пояснительной явно пишу: где руками, где инструмент. Комиссия ценит прозрачность больше «идеального» текста.",
    )

    # --- Олимпиады + товар ---
    P(
        "oly",
        "olymp",
        "egor_gym",
        "post",
        "Интрига кружка: кто-то в ICPC, кто-то в продукт. Оба пути уважаемы, если не подменять успех чужими метриками.",
    )
    P(
        "oly",
        "olymp",
        "egor_gym",
        "good",
        "Разобранные конспекты по динамике на массивах + подборка задач. Состояние аккуратное.",
        {"price_rub": 350, "contact": "@egor_gym", "contact_link": "https://vk.com"},
    )

    # --- Услуга ---
    P(
        "svc",
        "intern",
        "dima_startup",
        "service",
        "Помогаю собрать «историю проекта» для резюме: три созвона, чеклист, без воды.",
        {"price_rub": 1500, "contact": "@dima_startup"},
    )

    # --- Слухи ---
    P(
        "rumor",
        "rumors",
        "dima_startup",
        "post",
        "Слух: на третьем этаже обсуждают новый формат зачёта. Проверяйте официальные каналы — споры интереснее, когда факты есть.",
    )

    return dict(out)


def insert_comments(conn, u: dict[str, int], posts: dict[str, list[int]]):
    def c(post_id: int, user_key: str, body: str, parent_id: int | None = None) -> int:
        return int(
            conn.execute(
                text(
                    """
                    INSERT INTO comments (post_id, user_id, content, parent_id)
                    VALUES (:pid, :uid, :txt, :par)
                    RETURNING id
                    """
                ),
                {"pid": post_id, "uid": u[user_key], "txt": body, "par": parent_id},
            ).scalar()
        )

    mono = posts["mono"][0]
    cid = c(mono, "maria_morf", "Согласна: на теории один экран ок. На практике с отладчиком — уже тесно.", None)
    cid = c(mono, "ilya_proofs", "Отладчик можно на втором виртуальном столе — не обязательно второй монитор.", cid)
    c(mono, "egor_gym", "На контесте ноут + один экран. Всё.", None)
    c(mono, "lena_ethics", "Я за эргономику: если шея болит, спор про «продуктивность» бессмысленен.", None)
    c(mono, "dima_startup", "В стартапе у всех разные сетапы — главное, чтобы ревью проходило без «у меня не влезло».", None)

    mono1 = posts["mono"][1]
    cid = c(mono1, "ilya_proofs", "Два PDF рядом — это не лень, это экономия переключений.", None)
    c(mono1, "maria_morf", "Согласна частично: но один PDF + заметки на бумаге тоже работает.", cid)
    c(mono1, "egor_gym", "Главное — не спорить в чате, а замерить своё время. Цифры решают.", None)

    mono2 = posts["mono"][2]
    c(mono2, "lena_ethics", "Формулировка «спор бессмысленный» звучит резко — лучше «зависит от задачи».", None)
    c(mono2, "ilya_proofs", "Согласен: без задачи мы сравниваем религии.", None)

    lang = posts["lang"][0]
    cid = c(lang, "ilya_proofs", "Спор превращается в холивар, если не договориться о цели: прод или научка?", None)
    c(lang, "lena_ethics", "В научке важнее воспроизводимость и ссылки, чем скорость набора кода.", cid)
    c(lang, "dima_startup", "В проде важны метрики и инциденты — там C++/Go живут дольше.", None)
    c(lang, "maria_morf", "Python как первый язык не отменяет потом взять Rust для души.", None)

    lang1 = posts["lang"][1]
    cid = c(lang1, "ilya_proofs", "Сегфолт учит уважать память — но не на первой неделе обучения.", None)
    c(lang1, "maria_morf", "Пусть студент сначала полюбит алгоритмы, потом страдать от borrow checker.", cid)
    c(lang1, "egor_gym", "Я за оба языка в портфолио — пусть рекрутер видит ширину.", None)

    lang2 = posts["lang"][2]
    c(lang2, "lena_ethics", "Токсичность в споре обычно из страха «меня заставят переписать всё».", None)
    c(lang2, "dima_startup", "Компромисс: один курс на Python, пара лаб на C++ — реально.", None)

    sess = posts["session"][0]
    cid = c(sess, "dima_startup", "Я один раз сдал «на нервах» — больше не повторяю. Сон — часть плана.", None)
    c(sess, "egor_gym", "+1. После ночи решал задачи в два раза медленнее — замерял.", cid)
    c(sess, "maria_morf", "Расписание по часам спасло меня на третьем курсе — делюсь шаблоном в комменте ниже.", None)
    c(sess, "ilya_proofs", "Просить отсрочку нормально, если есть аргументы, а не «я забыл».", None)

    sess1 = posts["session"][1]
    c(sess1, "lena_ethics", "«Якорный предмет» — хорошая метафора. Я бы добавила буферный день.", None)
    c(sess1, "egor_gym", "Буферный день на контестах не всегда возможен — но согласен в целом.", None)

    sess2 = posts["session"][2]
    cid = c(sess2, "maria_morf", "Дипломатия в чате курса реальна — главное не разгонять панику.", None)
    c(sess2, "lena_ethics", "Паника обычно от неопределённости. Официальное письмо от старосты снимает половину.", cid)

    intern0 = posts["intern"][0]
    cid = c(intern0, "dima_startup", "Три месяца подряд без истории — красный флаг для интервьюера.", None)
    c(intern0, "ilya_proofs", "Маленькие законченные штуки > полуфабрикаты. Согласен на все сто.", cid)
    c(intern0, "maria_morf", "Я бы добавила: open source PR тоже считается историей.", None)

    intern1 = posts["intern"][1]
    c(intern1, "egor_gym", "Алгоритмы и базы — минимум, иначе стажировка превращается в стресс.", None)
    c(intern1, "lena_ethics", "«Индустрия токсична» часто про выгорание, не про язык.", None)

    eth0 = posts["ethics"][0]
    cid = c(eth0, "ilya_proofs", "Фрагмент >3 предложений от нейросети — в приложении с промптом. Поддерживаю.", None)
    c(eth0, "maria_morf", "Важно не превратить это в охоту — цель прозрачность, а не наказание.", cid)
    c(eth0, "dima_startup", "В проде у нас чеклист на AI-assist — работает для джунов.", None)

    eth1 = posts["ethics"][1]
    c(eth1, "lena_ethics", "Комиссия правда ценит честность больше «идеального» текста.", None)
    c(eth1, "egor_gym", "Я в пояснительной к задачам тоже пишу, где гуглил идею — нормально.", None)

    oly = posts["oly"][0]
    cid = c(oly, "maria_morf", "Конспекты интересуют. Торг уместен в личке?", None)
    c(oly, "ilya_proofs", "Лучше фиксировать цену в посте — меньше недопониманий.", cid)
    c(oly, "egor_gym", "ICPC и продукт — разные игры, оба валидны.", None)

    good = posts["oly"][1]
    c(good, "dima_startup", "350 ₽ за пакет задач — честно, если там действительно разборы.", None)
    c(good, "lena_ethics", "Проверьте, что автор не нарушает права источников в конспектах.", None)

    svc = posts["svc"][0]
    cid = c(svc, "maria_morf", "Три созвона за 1500 — норм, если есть отзывы.", None)
    c(svc, "ilya_proofs", "Чеклист без воды — ключевое слово. Ненавижу общие советы «будь собой».", cid)

    rumor = posts["rumor"][0]
    cid = c(rumor, "lena_ethics", "Слухи без источника — развлечение, но проверяйте деканат.", None)
    c(rumor, "egor_gym", "Я уже видел фейк про перенос — лучше ждать официальный канал.", cid)
    c(rumor, "maria_morf", "Иногда «слух» — это просто опечатка в старом объявлении.", None)
    c(rumor, "dima_startup", "Споры интереснее с фактами — согласен с автором поста.", None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Подтвердить DESTRUCTIVE операции")
    args = parser.parse_args()
    if not args.yes:
        print("Это удалит контент и пересоздаст демо-данные. Запустите: python rebuild_rich_db.py --yes")
        sys.exit(0)

    eng = engine_from_env()
    with eng.begin() as conn:
        wipe_content(conn)
        admin_id = ensure_admin(conn)
        users = insert_personas(conn, admin_id)
        topics = insert_topics(conn, users)
        posts_map = insert_posts(conn, users, topics)
        insert_comments(conn, users, posts_map)

    print("Готово. Пользователи (пароль для всех кроме админа):", DEMO_PASSWORD)
    print("Админ:", ADMIN_EMAIL, "/", ADMIN_PLAIN)
    print("Персонажи:", ", ".join(k for k in users if k != "admin"))


if __name__ == "__main__":
    main()
