from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps
import uuid

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from pathlib import Path
from werkzeug.utils import secure_filename

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.engine import URL
import os

from auth_passwords import hash_password, is_hashed, verify_password
from services.post_fields import parse_price_rub, validate_http_url, validate_telegram_nick
from services.profanity import contains_profanity

# --- Модель токсичности: ml/models/toxicity_model.joblib (обучить: python ml/train_toxicity.py) ---
_TOX_MODEL = None


def _toxicity_score(text: str) -> float:
    global _TOX_MODEL
    if not text or not str(text).strip():
        return 0.0
    if _TOX_MODEL is False:
        return 0.0
    try:
        import joblib
        from pathlib import Path
        if _TOX_MODEL is None:
            fp = Path(__file__).resolve().parent / "ml" / "models" / "toxicity_model.joblib"
            if not fp.exists():
                _TOX_MODEL = False
                return 0.0
            _TOX_MODEL = joblib.load(fp)
        return float(_TOX_MODEL.predict_proba([text])[0][1])
    except Exception:
        _TOX_MODEL = False
        return 0.0


def _toxicity_block(text: str) -> bool:
    return _toxicity_score(text) >= float(os.getenv("TOXIC_BLOCK_THRESHOLD", "0.72"))


# Демо-администратор (создаётся при старте, если нет пользователя с таким email).
DEMO_ADMIN_EMAIL = "admin@fkn.vsu.ru"
DEMO_ADMIN_USERNAME = "admin_fkn"
DEMO_ADMIN_PASSWORD = "AdminFkn2026!"

_ALLOWED_UPLOAD_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _uploads_goods_dir() -> Path:
    d = Path(__file__).resolve().parent / "static" / "uploads" / "goods"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _content_ban_active(user_repo, user_id: int) -> datetime | None:
    until = user_repo.get_content_ban_until(user_id)
    if until is None:
        return None
    if getattr(until, "tzinfo", None) is None:
        until = until.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if until > now:
        return until
    return None


def _row_pid(row):
    if hasattr(row, "_mapping") and "parent_id" in row._mapping:
        return row._mapping.get("parent_id")
    if hasattr(row, "parent_id"):
        return row.parent_id
    return row[6] if len(row) > 6 else None


def build_comment_tree(rows):
    """Плоский список из БД -> список (row, depth) в порядке дерева."""
    children = defaultdict(list)
    for r in rows:
        children[_row_pid(r) or 0].append(r)

    def sort_key(r):
        return r.created_date if hasattr(r, "created_date") else r[3]

    out = []

    def walk(parent_key: int, depth: int):
        for n in sorted(children.get(parent_key, []), key=sort_key):
            out.append((n, depth))
            nid = n.id if hasattr(n, "id") else n[0]
            walk(nid, depth + 1)

    walk(0, 0)
    return out


def ensure_demo_admin():
    """Создаёт демо-админа или поднимает роль до admin по email."""
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT id, role FROM users WHERE email = :e"),
                {"e": DEMO_ADMIN_EMAIL},
            ).fetchone()
            if row:
                uid = row.id if hasattr(row, "id") else row[0]
                role = row.role if hasattr(row, "role") else row[1]
                if role != "admin":
                    conn.execute(
                        text("UPDATE users SET role = 'admin' WHERE id = :id"),
                        {"id": uid},
                    )
                return
            hp = hash_password(DEMO_ADMIN_PASSWORD)
            conn.execute(
                text(
                    """
                    INSERT INTO users (username, email, password, role)
                    VALUES (:u, :e, :p, 'admin')
                    """
                ),
                {"u": DEMO_ADMIN_USERNAME, "e": DEMO_ADMIN_EMAIL, "p": hp},
            )
    except Exception:
        pass


# Импорты репозиториев
from repositories.user_repository import UserRepository
from repositories.topic_repository import TopicRepository
from repositories.post_repository import PostRepository
from repositories.comment_repository import CommentRepository
from repositories.reaction_repository import ReactionRepository

app = Flask(__name__)
# В debug режиме приложение может перезапускаться; ключ должен быть стабильным,
# иначе сессия (авторизация) будет сбрасываться после reload.
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fkn_hub_dev_secret_key")


@app.template_filter("read_minutes")
def read_minutes_filter(text) -> int:
    if not text:
        return 1
    return max(1, (len(str(text).strip()) + 849) // 850)


def load_dotenv(path: str = ".env"):
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()

# Получаем параметры подключения из переменных окружения или используем значения по умолчанию
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'home1213')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'forum_bd')

# Приоритет у полного URL из окружения, иначе собираем его безопасно через URL.create.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = URL.create(
        "postgresql+pg8000",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
    )

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True  # Проверка соединения перед использованием
)


def _migration_sql_statements(raw_sql: str) -> list[str]:
    """Делит SQL по ';' и убирает ведущие построчные комментарии -- (иначе блок «-- … \\n ALTER» отбрасывался целиком)."""
    out: list[str] = []
    for raw_chunk in raw_sql.split(";"):
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        while lines and lines[0].strip().startswith("--"):
            lines.pop(0)
        stmt = "\n".join(lines).strip()
        if stmt:
            out.append(stmt)
    return out


def run_sql_migrations_best_effort():
    """Применяет db/migrate_v2.sql, migrate_v3.sql и т.д. к существующей БД."""
    root = Path(__file__).resolve().parent / "db"
    for fname in ("migrate_v2.sql", "migrate_v3.sql", "migrate_v4.sql", "migrate_v5.sql", "migrate_v6.sql"):
        migrate_path = root / fname
        if not migrate_path.exists():
            continue
        raw_sql = migrate_path.read_text(encoding="utf-8")
        statements = _migration_sql_statements(raw_sql)
        if not statements:
            continue
        try:
            with engine.begin() as conn:
                users_exists = conn.execute(text("SELECT to_regclass('public.users')")).scalar()
                if not users_exists:
                    return
                for statement in statements:
                    try:
                        conn.execute(text(statement))
                    except Exception:
                        continue
        except Exception:
            pass


run_sql_migrations_best_effort()
ensure_demo_admin()


@app.before_request
def ensure_session_user_exists():
    """Если в сессии user_id, которого нет в БД (пересборка БД, удаление), сбросить сессию до INSERT."""
    ep = request.endpoint
    if not ep or ep == "static":
        return
    if ep in ("login", "register"):
        return
    u = session.get("user")
    if not u or u.get("id") is None:
        return
    try:
        uid = int(u["id"])
    except (TypeError, ValueError):
        session.pop("user", None)
        flash("Некорректная сессия. Войдите снова.", "warning")
        return
    try:
        with engine.connect() as conn:
            ok = conn.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": uid}).scalar()
        if not ok:
            session.pop("user", None)
            flash(
                "Аккаунт не найден в базе (например после пересборки данных). Войдите снова.",
                "warning",
            )
    except Exception:
        pass


def _log_moderation(conn, actor_id: int, action: str, target_type: str, target_id: int, reason: str):
    try:
        conn.execute(
            text(
                """
                INSERT INTO moderation_log (actor_id, action, target_type, target_id, reason)
                VALUES (:a, :ac, :tt, :ti, :r)
                """
            ),
            {
                "a": actor_id,
                "ac": action[:64],
                "tt": target_type[:32],
                "ti": target_id,
                "r": (reason or "")[:4000],
            },
        )
    except Exception:
        pass


def _admin_delete_reason_required() -> str | None:
    """Для админа возвращает причину из формы или None если невалидно."""
    r = (request.form.get("delete_reason") or "").strip()
    if len(r) < 8:
        return None
    return r

# === Вспомогательная функция для подключения ===
def get_repos():
    try:
        conn = engine.connect()
        trans = conn.begin()
        return (
            conn,
            trans,
            UserRepository(conn),
            TopicRepository(conn),
            PostRepository(conn),
            CommentRepository(conn),
            ReactionRepository(conn),
        )
    except (OperationalError, ProgrammingError) as e:
        error_str = str(e)
        # Проверяем, является ли ошибка связанной с отсутствием базы данных
        if '3D000' in error_str or 'database' in error_str.lower() or 'не существует' in error_str:
            error_msg = (
                f"База данных '{DB_NAME}' не существует.\n\n"
                f"Для создания базы данных выполните:\n"
                f"python create_database.py\n\n"
                f"Или подключитесь к PostgreSQL и выполните:\n"
                f"CREATE DATABASE {DB_NAME};"
            )
        else:
            error_msg = (
                f"Не удалось подключиться к базе данных PostgreSQL.\n"
                f"Проверьте:\n"
                f"1. Запущен ли сервер PostgreSQL на {DB_HOST}:{DB_PORT}\n"
                f"2. Существует ли база данных '{DB_NAME}'\n"
                f"3. Правильны ли учетные данные (пользователь: {DB_USER})\n"
                f"4. Доступен ли сервер из сети\n"
                f"5. Заполнен ли файл .env (DB_USER/DB_PASSWORD/DB_NAME)\n\n"
                f"Ошибка: {str(e)}"
            )
        raise ConnectionError(error_msg) from e


def current_user():
    return session.get("user")


def is_moderator() -> bool:
    u = current_user()
    return bool(u and u.get("role") == "moderator")


def is_admin() -> bool:
    u = current_user()
    return bool(u and u.get("role") == "admin")


def is_staff() -> bool:
    u = current_user()
    return bool(u and u.get("role") in ("moderator", "admin"))


def staff_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_staff():
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_admin():
            flash("Доступно только администратору", "danger")
            return redirect(url_for("index"))
        return view_func(*args, **kwargs)

    return wrapped


def _user_active_mute_until(user_id: int):
    """Время окончания активного мута на посты/комментарии или None."""
    try:
        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT content_ban_until FROM users WHERE id = :id"),
                {"id": user_id},
            ).scalar()
        if raw is None:
            return None
        mt = raw if getattr(raw, "tzinfo", None) else raw.replace(tzinfo=timezone.utc)
        if mt > datetime.now(timezone.utc):
            return mt
        return None
    except Exception:
        return None


# Обработчик ошибок подключения к базе данных
@app.errorhandler(ConnectionError)
@app.errorhandler(OperationalError)
@app.errorhandler(ProgrammingError)
def handle_db_error(e):
    return render_template('error.html', 
                         error_title="Ошибка подключения к базе данных",
                         error_message=str(e)), 500

# Переключение тёмной темы
@app.route('/toggle_dark_mode')
def toggle_dark_mode():
    current_dark = session.get('dark_mode', False)
    session['dark_mode'] = not current_dark
    return redirect(request.referrer or url_for('index'))

@app.context_processor
def inject_globals():
    show_profanity_modal = session.pop("show_profanity_modal", False)
    mute_until = None
    mute_display = None
    u = session.get("user")
    if u and u.get("id"):
        mute_until = _user_active_mute_until(int(u["id"]))
        if mute_until:
            mute_display = mute_until.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return dict(
        dark_mode=session.get('dark_mode', False),
        is_moderator=is_moderator,
        is_admin=is_admin,
        is_staff=is_staff,
        show_profanity_modal=show_profanity_modal,
        content_mute_until=mute_until,
        content_mute_until_display=mute_display,
        is_content_muted=bool(mute_until),
        admin_demo_email=DEMO_ADMIN_EMAIL,
        admin_demo_username=DEMO_ADMIN_USERNAME,
        admin_demo_password=DEMO_ADMIN_PASSWORD,
    )

@app.route('/register', methods=['GET', 'POST'])
def register():
    conn, trans, user_repo, _, _, _, _ = get_repos()
    try:
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '').strip()
            
            if not username or not email or not password:
                flash("Все поля обязательны для заполнения", "danger")
                return render_template('register.html')
            
            if len(username) < 3 or len(username) > 50:
                flash("Имя пользователя должно содержать от 3 до 50 символов", "danger")
                return render_template('register.html')
            
            if len(password) < 6:
                flash("Пароль должен содержать минимум 6 символов", "danger")
                return render_template('register.html')
            
            if user_repo.exists_by_email_or_username(email, username):
                flash("Пользователь с таким email или именем уже существует", "danger")
                return render_template('register.html')
            
            user_repo.create(username, email, hash_password(password))
            trans.commit()
            flash("Регистрация успешна! Войдите в систему", "success")
            return redirect(url_for('login'))
        return render_template('register.html')
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при регистрации: {str(e)}", "danger")
        return render_template('register.html')
    finally:
        conn.close()

@app.route('/create_topic', methods=['GET', 'POST'])
def create_topic():
    if 'user' not in session:
        return redirect(url_for('login'))
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            if title:
                existing_topic = topic_repo.get_by_title(title)
                if existing_topic:
                    flash("Тема с таким названием уже существует", "warning")
                    return render_template('create_topic.html', user=session.get('user'))
                topic_repo.create(title, description, session['user']['id'])
                trans.commit()
                flash("Тема создана!", "success")
                return redirect(url_for('index'))
            else:
                flash("Название темы обязательно", "danger")
        return render_template('create_topic.html', user=session.get('user'))
    except IntegrityError as e:
        trans.rollback()
        if "23505" in str(e) and "topics_title_key" in str(e):
            flash("Тема с таким названием уже существует", "warning")
            return render_template('create_topic.html', user=session.get('user'))
        flash(f"Ошибка при создании темы: {str(e)}", "danger")
        return render_template('create_topic.html', user=session.get('user'))
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при создании темы: {str(e)}", "danger")
        return render_template('create_topic.html', user=session.get('user'))
    finally:
        conn.close()

@app.route('/')
def index():
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        sort = request.args.get('sort', 'new')
        search_query = request.args.get('q', '').strip()
        topic_filter = request.args.get('topic', '').strip()
        author_filter = request.args.get('author', '').strip()
        date_filter = request.args.get('date', '').strip()
        type_filter = request.args.get('type', '').strip()
        page = request.args.get('page', 1, type=int)
        per_page = 10

        # Построение WHERE clause с фильтрами
        where_conditions = []
        params = {}

        if type_filter in ('post', 'good', 'service'):
            where_conditions.append("p.post_type = :ptype")
            params['ptype'] = type_filter
        
        if search_query:
            q = search_query.strip()
            where_conditions.append(
                "("
                "(to_tsvector('simple', coalesce(p.content, '')) @@ plainto_tsquery('simple', :fts_q)) "
                "OR (to_tsvector('simple', coalesce(t.title, '')) @@ plainto_tsquery('simple', :fts_q)) "
                "OR (to_tsvector('simple', coalesce(u.username, '')) @@ plainto_tsquery('simple', :fts_q)) "
                "OR (p.content ILIKE :search OR t.title ILIKE :search OR u.username ILIKE :search)"
                ")"
            )
            params["fts_q"] = q
            params["search"] = f"%{q}%"

        if topic_filter:
            # Экранируем спецсимволы для ILIKE
            safe_topic = topic_filter.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where_conditions.append("t.title ILIKE :topic ESCAPE '\\'")
            params['topic'] = f"%{safe_topic}%"
        
        if author_filter:
            where_conditions.append("u.username ILIKE :author")
            params['author'] = f"%{author_filter}%"
        
        if date_filter:
            if date_filter == 'today':
                where_conditions.append("DATE(p.created_date) = CURRENT_DATE")
            elif date_filter == 'week':
                where_conditions.append("p.created_date >= CURRENT_DATE - INTERVAL '7 days'")
            elif date_filter == 'month':
                where_conditions.append("p.created_date >= CURRENT_DATE - INTERVAL '30 days'")
        
        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

        # Сортировка
        if sort == 'old':
            order_by = "p.created_date ASC"
        elif sort == 'popular':
            order_by = (
                "(SELECT COUNT(*) FROM post_reactions pr WHERE pr.post_id = p.id AND pr.reaction = 1) "
                "DESC, p.created_date DESC"
            )
        elif sort == 'comments':
            order_by = (
                "(SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) ASC, p.created_date DESC"
            )
        else:
            order_by = "p.created_date DESC"

        total = post_repo.count(where_clause, params)
        total_pages = (total + per_page - 1) // per_page
        posts = post_repo.get_all(where_clause, params, order_by, per_page, (page - 1) * per_page)

        liked_post_ids: set[int] = set()
        u = session.get("user")
        if u and posts:
            uid = u["id"]
            for row in posts:
                pid = row.id if hasattr(row, "id") else row[0]
                if reaction_repo.user_has_like(uid, pid):
                    liked_post_ids.add(pid)

        # Получаем список всех тем для фильтра (опционально, для автодополнения)
        all_topics = topic_repo.get_all()
        topics_list = []
        for t in all_topics:
            try:
                if hasattr(t, 'title'):
                    topics_list.append(t.title)
                elif isinstance(t, (tuple, list)) and len(t) > 1:
                    topics_list.append(t[1])
                else:
                    topics_list.append(str(t))
            except:
                pass

        recent_posts = conn.execute(
            text(
                """
                SELECT p.id, left(p.content, 140) AS snippet, u.username AS author,
                       p.created_date, t.title AS topic_title
                FROM posts p
                JOIN users u ON u.id = p.user_id
                JOIN topics t ON t.id = p.topic_id
                ORDER BY p.created_date DESC
                LIMIT 6
                """
            )
        ).fetchall()

        return render_template(
            'index.html',
            posts=posts,
            user=session.get('user'),
            liked_post_ids=liked_post_ids,
            current_sort=sort,
            search_query=search_query,
            topic_filter=topic_filter,
            author_filter=author_filter,
            date_filter=date_filter,
            type_filter=type_filter,
            topics_list=topics_list,
            recent_posts=recent_posts,
            page=page,
            total_pages=total_pages,
            total=total
        )
    except Exception as e:
        flash(f"Ошибка при загрузке постов: {str(e)}", "danger")
        return render_template(
            'index.html',
            posts=[],
            user=session.get('user'),
            liked_post_ids=set(),
            total_pages=1,
            page=1,
            total=0,
            current_sort='new',
            search_query='',
            topic_filter='',
            author_filter='',
            date_filter='',
            type_filter='',
            topics_list=[],
            recent_posts=[],
        )
    finally:
        conn.close()

# === Пример login (остальные маршруты аналогично) ===
@app.route('/login', methods=['GET', 'POST'])
def login():
    conn, trans, user_repo, _, _, _, _ = get_repos()
    try:
        if request.method == 'POST':
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '').strip()
            user = user_repo.get_by_email(email)
            if user:
                # Доступ к данным Row объекта через атрибуты (SQLAlchemy 2.0 поддерживает)
                try:
                    user_password = user.password if hasattr(user, 'password') else user[3]
                    user_id = user.id if hasattr(user, 'id') else user[0]
                    user_username = user.username if hasattr(user, 'username') else user[1]
                except (AttributeError, IndexError):
                    # Fallback на индексы
                    user_password = user[3] if len(user) > 3 else None
                    user_id = user[0] if len(user) > 0 else None
                    user_username = user[1] if len(user) > 1 else None
                
                if user_password and verify_password(user_password, password):
                    try:
                        user_role = user.role if hasattr(user, "role") else user[4]
                    except (AttributeError, IndexError):
                        user_role = "user"
                    # Миграция: при успешном входе по старому открытому паролю — сохраняем хэш
                    if not is_hashed(user_password):
                        user_repo.update_password(user_id, hash_password(password))
                    trans.commit()
                    session["user"] = {
                        "id": user_id,
                        "name": user_username,
                        "role": user_role,
                    }
                    return redirect(url_for('index'))
            flash("Неверный email или пароль", "danger")
        return render_template('login.html')
    except Exception as e:
        flash(f"Ошибка при входе: {str(e)}", "danger")
        return render_template('login.html')
    finally:
        conn.close()

@app.route('/post/<int:post_id>', methods=['GET', 'POST'])
def post(post_id):
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        if request.method == 'POST':
            if 'user' not in session:
                flash("Войдите, чтобы оставить комментарий", "danger")
                return redirect(url_for('login'))

            uid = session["user"]["id"]
            ban_until = _content_ban_active(user_repo, uid)
            if ban_until:
                flash(
                    "Создание комментариев временно ограничено до "
                    f"{ban_until.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M')} (UTC).",
                    "danger",
                )
                return redirect(url_for("post", post_id=post_id))

            content = request.form.get('content', '').strip()
            parent_raw = (request.form.get("parent_id") or "").strip()
            parent_id = int(parent_raw) if parent_raw.isdigit() else None
            if not content:
                flash("Комментарий не может быть пустым", "danger")
                return redirect(url_for('post', post_id=post_id))

            if contains_profanity(content):
                if not is_staff():
                    user_repo.set_content_ban_hours(uid, 1.0)
                    session["show_profanity_modal"] = True
                trans.commit()
                flash(
                    "Обнаружена нецензурная лексика. Комментарий не сохранён."
                    + ("" if is_staff() else " На 1 час ограничены публикации и комментарии."),
                    "danger",
                )
                return redirect(url_for("post", post_id=post_id))

            if _toxicity_block(content):
                flash("Комментарий не прошёл фильтр токсичности.", "danger")
                return redirect(url_for("post", post_id=post_id))

            if parent_id:
                parent_row = comment_repo.get_by_id(parent_id)
                if not parent_row:
                    flash("Ответ: родительский комментарий не найден.", "danger")
                    return redirect(url_for("post", post_id=post_id))
                p_pid = (
                    parent_row.post_id
                    if hasattr(parent_row, "post_id")
                    else parent_row[4]
                )
                if int(p_pid) != int(post_id):
                    flash("Ответ привязан к другому посту.", "danger")
                    return redirect(url_for("post", post_id=post_id))

            comment_repo.create(post_id, uid, content, parent_id=parent_id)
            trans.commit()
            flash("Комментарий добавлен!", "success")
            return redirect(url_for('post', post_id=post_id))

        post_data = post_repo.get_post(post_id)
        if not post_data:
            abort(404)

        comments = comment_repo.get_by_post_id(post_id)
        comment_tree = build_comment_tree(comments)
        user_liked = False
        if session.get("user"):
            user_liked = reaction_repo.user_has_like(session["user"]["id"], post_id)
        return render_template(
            'post.html',
            post=post_data,
            comments=comments,
            comment_tree=comment_tree,
            user=session.get('user'),
            user_liked=user_liked,
        )
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка: {str(e)}", "danger")
        return redirect(url_for('index'))
    finally:
        conn.close()


@app.route('/post/<int:post_id>/like', methods=['POST'])
def toggle_post_like(post_id):
    if 'user' not in session:
        flash("Войдите, чтобы оценивать посты", "danger")
        return redirect(url_for('login', next=request.referrer or url_for('post', post_id=post_id)))
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        post_row = post_repo.get_post(post_id)
        if not post_row:
            abort(404)
        reaction_repo.toggle_like(session['user']['id'], post_id)
        trans.commit()
    except Exception as e:
        trans.rollback()
        flash(str(e), "danger")
    return redirect(request.referrer or url_for('post', post_id=post_id))


@app.route('/pairs')
def topic_pairs():
    conn, trans, _, topic_repo, _, _, _ = get_repos()
    try:
        pairs = topic_repo.list_pairs()
        trans.commit()
        return render_template(
            'pairs.html',
            pairs=pairs,
            user=session.get('user'),
        )
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка: {e}", "danger")
        return render_template('pairs.html', pairs=[], user=session.get('user'))
    finally:
        conn.close()


@app.route('/create_post', methods=['GET', 'POST'])
def create_post():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    topics: list = []
    try:
        topics = topic_repo.get_all()
        uid = session["user"]["id"]

        def _render_create(extra=None):
            return render_template(
                "create_post.html",
                user=session.get("user"),
                topics=topics,
                **(extra or {}),
            )

        if request.method == 'POST':
            ban_until = _content_ban_active(user_repo, uid)
            if ban_until:
                flash(
                    "Создание постов временно ограничено до "
                    f"{ban_until.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M')} (UTC).",
                    "danger",
                )
                return _render_create()

            topic_raw = (request.form.get("topic_id") or "").strip()
            if not topic_raw.isdigit():
                flash("Выберите тему строго из списка существующих тем.", "danger")
                return _render_create()
            topic_id = int(topic_raw)
            topic_row = topic_repo.get_by_id(topic_id)
            if not topic_row:
                flash("Такой темы нет. Сначала создайте тему или выберите из списка.", "danger")
                return _render_create()

            content = request.form.get('content', '').strip()
            post_type = request.form.get('post_type', 'post').strip()
            if post_type not in ('post', 'good', 'service'):
                post_type = 'post'
            meta: dict = {}
            if post_type in ('good', 'service'):
                price_raw = request.form.get('price_rub', '').strip()
                if post_type == 'good' and not price_raw:
                    flash("Для товара укажите цену в рублях.", "danger")
                    return _render_create()
                if price_raw:
                    pr, err = parse_price_rub(price_raw)
                    if err:
                        flash(err, "danger")
                        return _render_create()
                    meta['price_rub'] = pr
                tg_raw = request.form.get('contact_tg', '').strip()
                if not tg_raw:
                    flash("Укажите ник в Telegram для связи.", "danger")
                    return _render_create()
                tg, err = validate_telegram_nick(tg_raw)
                if err:
                    flash(err, "danger")
                    return _render_create()
                meta['contact'] = tg
                link_raw = request.form.get('contact_link', '').strip()
                if link_raw:
                    url, err = validate_http_url(link_raw)
                    if err:
                        flash(err, "danger")
                        return _render_create()
                    meta['contact_link'] = url

            if not content:
                flash("Текст публикации обязателен.", "danger")
                return _render_create()

            if contains_profanity(content):
                if not is_staff():
                    user_repo.set_content_ban_hours(uid, 1.0)
                    session["show_profanity_modal"] = True
                trans.commit()
                flash(
                    "Обнаружена нецензурная лексика. Пост не сохранён."
                    + ("" if is_staff() else " На 1 час ограничены публикации и комментарии."),
                    "danger",
                )
                return redirect(url_for("index"))

            if _toxicity_block(content):
                flash("Публикация отклонена: высокая вероятность токсичного содержимого.", "danger")
                return _render_create()

            if post_type == 'good':
                f = request.files.get('good_photo')
                if f and getattr(f, "filename", None):
                    raw_name = secure_filename(f.filename)
                    ext = Path(raw_name).suffix.lower()
                    if ext not in _ALLOWED_UPLOAD_EXT:
                        flash("Фото товара: допустимы форматы JPG, PNG, GIF, WEBP.", "danger")
                        return _render_create()
                    blob = f.read()
                    if len(blob) > _MAX_UPLOAD_BYTES:
                        flash("Размер фото не более 5 МБ.", "danger")
                        return _render_create()
                    fn = f"{uuid.uuid4().hex}{ext}"
                    dest = _uploads_goods_dir() / fn
                    dest.write_bytes(blob)
                    meta['good_photo'] = f"uploads/goods/{fn}"

            post_repo.create(
                topic_id,
                uid,
                content,
                post_type=post_type,
                meta=meta,
            )
            trans.commit()
            flash("Пост создан!", "success")
            return redirect(url_for('index'))

        return _render_create()
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при создании поста: {str(e)}", "danger")
        return render_template('create_post.html', user=session.get('user'), topics=topics)
    finally:
        conn.close()

@app.route('/edit_post/<int:post_id>', methods=['GET', 'POST'])
def edit_post(post_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        post_data = post_repo.get_post(post_id)
        if not post_data:
            abort(404)
        
        try:
            post_user_id = post_data.user_id if hasattr(post_data, 'user_id') else post_data[7]
        except (AttributeError, IndexError):
            post_user_id = post_data[7] if len(post_data) > 7 else None
        if post_user_id != session['user']['id']:
            flash("Вы можете редактировать только свои посты", "danger")
            return redirect(url_for('post', post_id=post_id))
        
        if request.method == 'POST':
            content = request.form.get('content', '').strip()
            if not content:
                flash("Содержание поста не может быть пустым", "danger")
            elif contains_profanity(content):
                if not is_staff():
                    user_repo.set_content_ban_hours(session["user"]["id"], 1.0)
                    session["show_profanity_modal"] = True
                trans.commit()
                flash(
                    "Обнаружена нецензурная лексика. Изменения не сохранены."
                    + ("" if is_staff() else " На 1 час ограничены публикации и комментарии."),
                    "danger",
                )
            elif _toxicity_block(content):
                flash("Текст не прошёл фильтр токсичности.", "danger")
            else:
                post_repo.update(post_id, content)
                trans.commit()
                flash("Пост обновлен!", "success")
                return redirect(url_for('post', post_id=post_id))
        
        return render_template('edit_post.html', post=post_data, user=session.get('user'))
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при редактировании поста: {str(e)}", "danger")
        return redirect(url_for('post', post_id=post_id))
    finally:
        conn.close()

@app.route('/delete_post/<int:post_id>', methods=['POST'])
def delete_post(post_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        post_data = post_repo.get_post(post_id)
        if not post_data:
            abort(404)
        
        try:
            post_user_id = post_data.user_id if hasattr(post_data, 'user_id') else post_data[7]
        except (AttributeError, IndexError):
            post_user_id = post_data[7] if len(post_data) > 7 else None
        if post_user_id != session['user']['id'] and not is_staff():
            flash("Вы можете удалять только свои посты", "danger")
            return redirect(url_for('post', post_id=post_id))

        reason_log = None
        if is_admin() and post_user_id != session["user"]["id"]:
            reason_log = _admin_delete_reason_required()
            if not reason_log:
                flash(
                    "Администратор должен указать причину удаления чужого поста (не менее 8 символов).",
                    "danger",
                )
                return redirect(request.referrer or url_for("post", post_id=post_id))

        post_repo.delete(post_id)
        if reason_log:
            _log_moderation(
                conn,
                int(session["user"]["id"]),
                "delete_post",
                "post",
                post_id,
                reason_log,
            )
        trans.commit()
        staff_other = is_staff() and post_user_id != session['user']['id']
        flash(
            "Пост удалён"
            + (" службой модерации / администратором" if staff_other else "")
            + "!",
            "success",
        )
        return redirect(url_for('index'))
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при удалении поста: {str(e)}", "danger")
        return redirect(url_for('post', post_id=post_id))
    finally:
        conn.close()

@app.route('/edit_comment/<int:comment_id>', methods=['GET', 'POST'])
def edit_comment(comment_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        comment = comment_repo.get_by_id(comment_id)
        if not comment:
            abort(404)
        
        try:
            comment_user_id = comment.user_id if hasattr(comment, 'user_id') else comment[3]
            comment_post_id = comment.post_id if hasattr(comment, 'post_id') else comment[4]
        except (AttributeError, IndexError):
            comment_user_id = comment[3] if len(comment) > 3 else None
            comment_post_id = comment[4] if len(comment) > 4 else None
        
        try:
            mod_deleted = (
                comment.deleted_by_moderator
                if hasattr(comment, "deleted_by_moderator")
                else comment[5]
            )
        except (AttributeError, IndexError):
            mod_deleted = False
        if mod_deleted:
            flash("Комментарий удалён модератором и не подлежит редактированию", "danger")
            return redirect(url_for('post', post_id=comment_post_id))

        if comment_user_id != session['user']['id']:
            flash("Вы можете редактировать только свои комментарии", "danger")
            return redirect(url_for('post', post_id=comment_post_id))
        
        if request.method == 'POST':
            content = request.form.get('content', '').strip()
            if not content:
                flash("Комментарий не может быть пустым", "danger")
            elif contains_profanity(content):
                if not is_staff():
                    user_repo.set_content_ban_hours(session["user"]["id"], 1.0)
                    session["show_profanity_modal"] = True
                trans.commit()
                flash(
                    "Обнаружена нецензурная лексика. Изменения не сохранены."
                    + ("" if is_staff() else " На 1 час ограничены публикации и комментарии."),
                    "danger",
                )
            elif _toxicity_block(content):
                flash("Текст не прошёл фильтр токсичности.", "danger")
            else:
                comment_repo.update(comment_id, content)
                trans.commit()
                flash("Комментарий обновлен!", "success")
                return redirect(url_for('post', post_id=comment_post_id))
        
        return render_template('edit_comment.html', comment=comment, user=session.get('user'))
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при редактировании комментария: {str(e)}", "danger")
        if comment:
            try:
                comment_post_id = comment.post_id if hasattr(comment, 'post_id') else comment[4]
            except (AttributeError, IndexError):
                comment_post_id = comment[4] if len(comment) > 4 else None
            if comment_post_id:
                return redirect(url_for('post', post_id=comment_post_id))
        return redirect(url_for('index'))
    finally:
        conn.close()

@app.route('/delete_comment/<int:comment_id>', methods=['POST'])
def delete_comment(comment_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        comment = comment_repo.get_by_id(comment_id)
        if not comment:
            abort(404)
        
        try:
            comment_user_id = comment.user_id if hasattr(comment, 'user_id') else comment[3]
            comment_post_id = comment.post_id if hasattr(comment, 'post_id') else comment[4]
        except (AttributeError, IndexError):
            comment_user_id = comment[3] if len(comment) > 3 else None
            comment_post_id = comment[4] if len(comment) > 4 else None
        
        if is_staff() and comment_user_id != session['user']['id']:
            reason_log = None
            if is_admin():
                reason_log = _admin_delete_reason_required()
                if not reason_log:
                    flash(
                        "Администратор должен указать причину удаления чужого комментария (не менее 8 символов).",
                        "danger",
                    )
                    return redirect(url_for("post", post_id=comment_post_id))
            comment_repo.delete(comment_id)
            if reason_log:
                _log_moderation(
                    conn,
                    int(session["user"]["id"]),
                    "delete_comment",
                    "comment",
                    comment_id,
                    reason_log,
                )
            trans.commit()
            flash("Комментарий удалён службой модерации или администратором.", "success")
            return redirect(url_for('post', post_id=comment_post_id))

        if comment_user_id != session['user']['id']:
            flash("Вы можете удалять только свои комментарии", "danger")
            return redirect(url_for('post', post_id=comment_post_id))
        
        comment_repo.delete(comment_id)
        trans.commit()
        flash("Комментарий удалён!", "success")
        return redirect(url_for('post', post_id=comment_post_id))
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при удалении комментария: {str(e)}", "danger")
        if comment:
            try:
                comment_post_id = comment.post_id if hasattr(comment, 'post_id') else comment[4]
            except (AttributeError, IndexError):
                comment_post_id = comment[4] if len(comment) > 4 else None
            if comment_post_id:
                return redirect(url_for('post', post_id=comment_post_id))
        return redirect(url_for('index'))
    finally:
        conn.close()

@app.route('/profile')
def profile():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        posts = post_repo.get_by_user_id(session['user']['id'])
        comments = comment_repo.get_by_user_id(session['user']['id'])
        return render_template('profile.html', user=session.get('user'), posts=posts, comments=comments)
    except Exception as e:
        flash(f"Ошибка при загрузке профиля: {str(e)}", "danger")
        return render_template('profile.html', user=session.get('user'), posts=[], comments=[])
    finally:
        conn.close()

@app.route("/api/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip()
    password = (payload.get("password") or "").strip()
    if not email or not password:
        return jsonify(ok=False, error="Укажите email и пароль"), 400
    conn, trans, user_repo, *_ = get_repos()
    try:
        user = user_repo.get_by_email(email)
        if not user:
            return jsonify(ok=False, error="Неверный email или пароль"), 401
        user_password = user.password if hasattr(user, "password") else user[3]
        user_id = user.id if hasattr(user, "id") else user[0]
        user_username = user.username if hasattr(user, "username") else user[1]
        try:
            user_role = user.role if hasattr(user, "role") else user[4]
        except (AttributeError, IndexError):
            user_role = "user"
        if not verify_password(user_password, password):
            return jsonify(ok=False, error="Неверный email или пароль"), 401
        if not is_hashed(user_password):
            user_repo.update_password(user_id, hash_password(password))
            trans.commit()
        session["user"] = {"id": user_id, "name": user_username, "role": user_role}
        return jsonify(ok=True, user={"name": user_username, "role": user_role})
    finally:
        conn.close()


@app.route("/api/post/<int:post_id>/like", methods=["POST"])
def api_post_like(post_id):
    if "user" not in session:
        return jsonify(ok=False, error="auth"), 401
    conn, trans, _, _, post_repo, _, reaction_repo = get_repos()
    try:
        post_row = post_repo.get_post(post_id)
        if not post_row:
            return jsonify(ok=False, error="not_found"), 404
        liked = reaction_repo.toggle_like(session["user"]["id"], post_id)
        trans.commit()
        n = reaction_repo.count_likes(post_id)
        return jsonify(ok=True, liked=liked, like_count=n)
    except Exception as e:
        trans.rollback()
        return jsonify(ok=False, error=str(e)), 500
    finally:
        conn.close()


@app.route("/admin")
@staff_required
def admin_dashboard():
    conn, trans, *_ = get_repos()
    try:
        nu = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        np = conn.execute(text("SELECT COUNT(*) FROM posts")).scalar()
        nc = conn.execute(text("SELECT COUNT(*) FROM comments")).scalar()
        trans.commit()
        return render_template("admin/dashboard.html", nu=nu, np=np, nc=nc, user=session.get("user"))
    finally:
        conn.close()


@app.route("/admin/users")
@staff_required
def admin_users():
    conn, trans, *_ = get_repos()
    try:
        rows = conn.execute(
            text(
                "SELECT id, username, email, role, created_at FROM users ORDER BY id LIMIT 500"
            )
        ).fetchall()
        trans.commit()
        return render_template("admin/users.html", users=rows, user=session.get("user"))
    finally:
        conn.close()


@app.route("/admin/posts")
@staff_required
def admin_posts():
    conn, trans, *_ = get_repos()
    try:
        rows = conn.execute(
            text(
                """
                SELECT p.id AS id, left(p.content, 100) AS snippet, u.username AS author, p.created_date AS created_date
                FROM posts p JOIN users u ON u.id = p.user_id
                ORDER BY p.created_date DESC LIMIT 200
                """
            )
        ).fetchall()
        trans.commit()
        return render_template("admin/posts.html", posts=rows, user=session.get("user"))
    finally:
        conn.close()


@app.route("/admin/mutes")
@admin_required
def admin_mutes():
    conn, trans, user_repo, *_ = get_repos()
    try:
        rows = user_repo.list_active_content_bans()
        trans.commit()
        return render_template("admin/mutes.html", rows=rows, user=session.get("user"))
    finally:
        conn.close()


@app.route("/admin/user/<int:user_id>/clear_mute", methods=["POST"])
@admin_required
def admin_clear_mute(user_id):
    conn, trans, user_repo, *_ = get_repos()
    try:
        user_repo.clear_content_ban(user_id)
        trans.commit()
        flash("Ограничение на посты и комментарии снято.", "success")
    except Exception as e:
        trans.rollback()
        flash(str(e), "danger")
    return redirect(url_for("admin_mutes"))


@app.route("/admin/topics")
@admin_required
def admin_topics():
    conn, trans, _, topic_repo, _, _, _ = get_repos()
    try:
        rows = topic_repo.list_with_stats()
        trans.commit()
        return render_template("admin/topics.html", topics=rows, user=session.get("user"))
    finally:
        conn.close()


@app.route("/admin/topic/<int:topic_id>/delete", methods=["POST"])
@admin_required
def admin_delete_topic(topic_id):
    conn, trans, _, topic_repo, _, _, _ = get_repos()
    try:
        t = topic_repo.get_by_id(topic_id)
        if not t:
            abort(404)
        reason = _admin_delete_reason_required()
        if not reason:
            flash(
                "Укажите причину удаления темы в форме (не менее 8 символов).",
                "danger",
            )
            return redirect(url_for("admin_topics"))
        topic_repo.delete(topic_id)
        _log_moderation(
            conn,
            int(session["user"]["id"]),
            "delete_topic",
            "topic",
            topic_id,
            reason,
        )
        trans.commit()
        flash("Тема удалена вместе со всеми постами и комментариями в них.", "success")
    except Exception as e:
        trans.rollback()
        flash(str(e), "danger")
    return redirect(url_for("admin_topics"))


@app.route("/admin/post/<int:post_id>/delete", methods=["POST"])
@staff_required
def admin_delete_post_route(post_id):
    conn, trans, _, _, post_repo, _, _ = get_repos()
    try:
        if is_admin():
            reason = _admin_delete_reason_required()
            if not reason:
                flash(
                    "Укажите причину удаления поста (не менее 8 символов).",
                    "danger",
                )
                return redirect(url_for("admin_posts"))
        else:
            reason = ""
        post_repo.delete(post_id)
        if is_admin() and reason:
            _log_moderation(
                conn,
                int(session["user"]["id"]),
                "admin_delete_post",
                "post",
                post_id,
                reason,
            )
        trans.commit()
        flash("Пост удалён", "success")
    except Exception as e:
        trans.rollback()
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("admin_posts"))


@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("Вы вышли из системы", "info")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
