import os
import uuid
from datetime import timezone
from functools import wraps
from pathlib import Path

import re

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
from markupsafe import Markup, escape
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from werkzeug.utils import secure_filename

from app_support import (
    ALLOWED_UPLOAD_EXT,
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    DEMO_ADMIN_USERNAME,
    MAX_UPLOAD_BYTES,
    build_comment_tree,
    connection_error_message,
    content_ban_active,
    create_db_engine,
    ensure_demo_admin,
    load_dotenv,
    log_moderation,
    mute_until_for_user,
    row_get,
    run_sql_migrations,
    session_user_from_row,
    uploads_goods_dir,
)
from auth_passwords import hash_password, is_hashed, verify_password
from repositories.comment_repository import CommentRepository
from repositories.post_repository import PostRepository
from repositories.reaction_repository import ReactionRepository
from repositories.topic_repository import TopicRepository
from repositories.user_repository import UserRepository
from services.post_fields import parse_price_rub, validate_http_url, validate_telegram_nick
from services.profanity import contains_profanity
from services.toxicity import is_toxic

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "forum_bd")

engine = create_db_engine()
run_sql_migrations(engine)
ensure_demo_admin(engine)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fkn_hub_dev_secret_key")


def toxicity_block(text: str) -> bool:
    """Проверяет, нужно ли отклонить текст по ML-фильтру токсичности."""
    return is_toxic(text)


@app.template_filter("read_minutes")
def read_minutes_filter(text) -> int:
    """Оценивает время чтения текста в минутах для бейджа в ленте."""
    if not text:
        return 1
    return max(1, (len(str(text).strip()) + 849) // 850)


@app.template_filter("highlight_search")
def highlight_search_filter(text, query: str):
    """Подсвечивает вхождения поискового запроса в безопасном HTML."""
    raw = str(text or "")
    q = (query or "").strip()
    if not q:
        return escape(raw)
    safe = escape(raw)
    pattern = re.compile(re.escape(q), re.IGNORECASE)
    return Markup(
        pattern.sub(
            lambda m: f'<mark class="search-hit">{m.group(0)}</mark>',
            safe,
        )
    )


@app.before_request
def ensure_session_user_exists():
    """Сбрасывает сессию, если id пользователя из cookie отсутствует в БД."""
    ep = request.endpoint
    if not ep or ep == "static":
        return
    if ep in ("login", "register", "api_login", "api_register"):
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


def admin_delete_reason_required() -> str | None:
    """Возвращает причину удаления из формы админа или None, если короче 8 символов."""
    reason = (request.form.get("delete_reason") or "").strip()
    if len(reason) < 8:
        return None
    return reason


def get_repos():
    """Открывает соединение с БД, транзакцию и набор репозиториев для одного запроса."""
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
        raise ConnectionError(
            connection_error_message(e, DB_NAME, DB_HOST, DB_PORT, DB_USER)
        ) from e


def current_user():
    """Возвращает словарь текущего пользователя из сессии или None."""
    return session.get("user")


def is_moderator() -> bool:
    """True, если в сессии пользователь с ролью moderator."""
    user = current_user()
    return bool(user and user.get("role") == "moderator")


def is_admin() -> bool:
    """True, если в сессии пользователь с ролью admin."""
    user = current_user()
    return bool(user and user.get("role") == "admin")


def is_staff() -> bool:
    """True для модератора или администратора."""
    user = current_user()
    return bool(user and user.get("role") in ("moderator", "admin"))


def staff_required(view_func):
    """Декоратор: доступ только для moderator и admin."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_staff():
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    """Декоратор: доступ только для admin."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_admin():
            flash("Доступно только администратору", "danger")
            return redirect(url_for("index"))
        return view_func(*args, **kwargs)

    return wrapped


def login_user_from_row(user_repo, trans, row, plain_password: str) -> bool:
    """Проверяет пароль, при необходимости хэширует его и записывает пользователя в session. Возвращает успех входа."""
    data = session_user_from_row(row)
    if not data:
        return False
    stored = data.pop("_password", None)
    if not stored or not verify_password(stored, plain_password):
        return False
    if not is_hashed(stored):
        user_repo.update_password(data["id"], hash_password(plain_password))
        trans.commit()
    session["user"] = {"id": data["id"], "name": data["name"], "role": data["role"]}
    return True


@app.errorhandler(ConnectionError)
@app.errorhandler(OperationalError)
@app.errorhandler(ProgrammingError)
def handle_db_error(e):
    """Показывает страницу ошибки при недоступности PostgreSQL."""
    return render_template(
        "error.html",
        error_title="Ошибка подключения к базе данных",
        error_message=str(e),
    ), 500


@app.route("/toggle_dark_mode")
def toggle_dark_mode():
    """Переключает флаг тёмной темы в сессии и возвращает на предыдущую страницу."""
    session["dark_mode"] = not session.get("dark_mode", False)
    return redirect(request.referrer or url_for("index"))


@app.context_processor
def inject_globals():
    """Передаёт в шаблоны роли, тему, мут и данные демо-админа."""
    show_profanity_modal = session.pop("show_profanity_modal", False)
    mute_until = None
    mute_display = None
    u = session.get("user")
    if u and u.get("id"):
        mute_until = mute_until_for_user(engine, int(u["id"]))
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

@app.route("/register", methods=["GET", "POST"])
def register():
    """Регистрация нового пользователя (форма, без автовхода)."""
    conn, trans, user_repo, _, _, _, _ = get_repos()
    try:
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "").strip()
            confirm = request.form.get("confirm_password", "").strip()

            if not username or not email or not password or not confirm:
                flash("Все поля обязательны для заполнения", "danger")
                return render_template("register.html")

            if password != confirm:
                flash("Пароли не совпадают", "danger")
                return render_template("register.html")

            if len(username) < 3 or len(username) > 50:
                flash("Имя пользователя должно содержать от 3 до 50 символов", "danger")
                return render_template("register.html")

            if len(password) < 6:
                flash("Пароль должен содержать минимум 6 символов", "danger")
                return render_template("register.html")
            
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

@app.route("/create_topic", methods=["GET", "POST"])
def create_topic():
    """Создание темы раздела форума (только для авторизованных)."""
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

@app.route("/")
def index():
    """Главная лента постов с поиском, фильтрами и пагинацией."""
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
            safe_topic = topic_filter.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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

        if sort == "old":
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
                pid = row_get(row, "id", 0)
                if reaction_repo.user_has_like(uid, pid):
                    liked_post_ids.add(pid)

        topics_list = [row_get(t, "title", 1) for t in topic_repo.get_all()]

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

@app.route("/login", methods=["GET", "POST"])
def login():
    """Вход по email и паролю (отдельная страница)."""
    conn, trans, user_repo, _, _, _, _ = get_repos()
    try:
        if request.method == "POST":
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "").strip()
            row = user_repo.get_by_email(email)
            if row and login_user_from_row(user_repo, trans, row, password):
                return redirect(url_for("index"))
            flash("Неверный email или пароль", "danger")
        return render_template("login.html")
    except Exception as e:
        flash(f"Ошибка при входе: {str(e)}", "danger")
        return render_template('login.html')
    finally:
        conn.close()

@app.route("/post/<int:post_id>", methods=["GET", "POST"])
def post(post_id):
    """Страница поста: просмотр, комментарии и ответы в ветке."""
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        if request.method == 'POST':
            if 'user' not in session:
                flash("Войдите, чтобы оставить комментарий", "danger")
                return redirect(url_for('login'))

            uid = session["user"]["id"]
            ban_until = content_ban_active(user_repo, uid)
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

            if toxicity_block(content):
                flash("Комментарий не прошёл фильтр токсичности.", "danger")
                return redirect(url_for("post", post_id=post_id))

            if parent_id:
                parent_row = comment_repo.get_by_id(parent_id)
                if not parent_row:
                    flash("Ответ: родительский комментарий не найден.", "danger")
                    return redirect(url_for("post", post_id=post_id))
                if int(row_get(parent_row, "post_id", 4)) != int(post_id):
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


@app.route("/post/<int:post_id>/like", methods=["POST"])
def toggle_post_like(post_id):
    """Переключает лайк поста (форма, редирект назад)."""
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


@app.route("/pairs")
def topic_pairs():
    """Список пар связанных тем."""
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


@app.route("/create_post", methods=["GET", "POST"])
def create_post():
    """Создание публикации: обсуждение, товар или услуга."""
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
            ban_until = content_ban_active(user_repo, uid)
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

            if toxicity_block(content):
                flash("Публикация отклонена: высокая вероятность токсичного содержимого.", "danger")
                return _render_create()

            if post_type == 'good':
                f = request.files.get('good_photo')
                if f and getattr(f, "filename", None):
                    raw_name = secure_filename(f.filename)
                    ext = Path(raw_name).suffix.lower()
                    if ext not in ALLOWED_UPLOAD_EXT:
                        flash("Фото товара: допустимы форматы JPG, PNG, GIF, WEBP.", "danger")
                        return _render_create()
                    blob = f.read()
                    if len(blob) > MAX_UPLOAD_BYTES:
                        flash("Размер фото не более 5 МБ.", "danger")
                        return _render_create()
                    fn = f"{uuid.uuid4().hex}{ext}"
                    dest = uploads_goods_dir() / fn
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

@app.route("/edit_post/<int:post_id>", methods=["GET", "POST"])
def edit_post(post_id):
    """Редактирование текста своего поста."""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        post_data = post_repo.get_post(post_id)
        if not post_data:
            abort(404)
        
        post_user_id = row_get(post_data, "user_id", 7)
        if post_user_id != session["user"]["id"]:
            flash("Вы можете редактировать только свои посты", "danger")
            return redirect(url_for("post", post_id=post_id))

        if request.method == "POST":
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
            elif toxicity_block(content):
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

@app.route("/delete_post/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    """Удаление поста автором или staff; для админа — с причиной в журнал."""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        post_data = post_repo.get_post(post_id)
        if not post_data:
            abort(404)
        
        post_user_id = row_get(post_data, "user_id", 7)
        if post_user_id != session["user"]["id"] and not is_staff():
            flash("Вы можете удалять только свои посты", "danger")
            return redirect(url_for('post', post_id=post_id))

        reason_log = None
        if is_admin() and post_user_id != session["user"]["id"]:
            reason_log = admin_delete_reason_required()
            if not reason_log:
                flash(
                    "Администратор должен указать причину удаления чужого поста (не менее 8 символов).",
                    "danger",
                )
                return redirect(request.referrer or url_for("post", post_id=post_id))

        post_repo.delete(post_id)
        if reason_log:
            log_moderation(
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

@app.route("/edit_comment/<int:comment_id>", methods=["GET", "POST"])
def edit_comment(comment_id):
    """Редактирование своего комментария (не удалённого модератором)."""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        comment = comment_repo.get_by_id(comment_id)
        if not comment:
            abort(404)
        
        comment_user_id = row_get(comment, "user_id", 3)
        comment_post_id = row_get(comment, "post_id", 4)
        if row_get(comment, "deleted_by_moderator", 5):
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
            elif toxicity_block(content):
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
        if comment and row_get(comment, "post_id", 4):
            return redirect(url_for("post", post_id=row_get(comment, "post_id", 4)))
        return redirect(url_for("index"))
    finally:
        conn.close()


@app.route("/delete_comment/<int:comment_id>", methods=["POST"])
def delete_comment(comment_id):
    """Удаление своего комментария или чужого — staff/admin с журналом."""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn, trans, user_repo, topic_repo, post_repo, comment_repo, reaction_repo = get_repos()
    try:
        comment = comment_repo.get_by_id(comment_id)
        if not comment:
            abort(404)
        
        comment_user_id = row_get(comment, "user_id", 3)
        comment_post_id = row_get(comment, "post_id", 4)

        if is_staff() and comment_user_id != session["user"]["id"]:
            reason_log = None
            if is_admin():
                reason_log = admin_delete_reason_required()
                if not reason_log:
                    flash(
                        "Администратор должен указать причину удаления чужого комментария (не менее 8 символов).",
                        "danger",
                    )
                    return redirect(url_for("post", post_id=comment_post_id))
            comment_repo.mark_deleted_by_moderator(comment_id)
            if reason_log:
                log_moderation(
                    conn,
                    int(session["user"]["id"]),
                    "hide_comment",
                    "comment",
                    comment_id,
                    reason_log,
                )
            trans.commit()
            flash("Комментарий скрыт модератором (заглушка вместо текста).", "success")
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
        if comment and comment_post_id:
            return redirect(url_for("post", post_id=comment_post_id))
        return redirect(url_for("index"))
    finally:
        conn.close()


@app.route("/profile")
def profile():
    """Личный кабинет: список постов и комментариев пользователя."""
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
    """JSON-вход для модального окна в шапке."""
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip()
    password = (payload.get("password") or "").strip()
    if not email or not password:
        return jsonify(ok=False, error="Укажите email и пароль"), 400
    conn, trans, user_repo, *_ = get_repos()
    try:
        row = user_repo.get_by_email(email)
        if not row or not login_user_from_row(user_repo, trans, row, password):
            return jsonify(ok=False, error="Неверный email или пароль"), 401
        u = session["user"]
        return jsonify(ok=True, user={"name": u["name"], "role": u["role"]})
    finally:
        conn.close()


@app.route("/api/register", methods=["POST"])
def api_register():
    """JSON-регистрация с автоматическим входом в сессию."""
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip()
    password = (payload.get("password") or "").strip()
    confirm = (payload.get("confirm_password") or payload.get("confirmPassword") or "").strip()

    if not username or not email or not password or not confirm:
        return jsonify(ok=False, error="Укажите username, email, пароль и подтверждение"), 400
    if password != confirm:
        return jsonify(ok=False, error="Пароли не совпадают"), 400
    if len(username) < 3 or len(username) > 50:
        return jsonify(ok=False, error="username должен быть от 3 до 50 символов"), 400
    if len(password) < 6:
        return jsonify(ok=False, error="Пароль должен содержать минимум 6 символов"), 400

    conn, trans, user_repo, *_ = get_repos()
    try:
        if user_repo.exists_by_email_or_username(email, username):
            return jsonify(ok=False, error="Пользователь с таким email или именем уже существует"), 409

        user_repo.create(username, email, hash_password(password))
        trans.commit()

        user = user_repo.get_by_email(email)
        if not user:
            return jsonify(ok=False, error="Не удалось создать пользователя"), 500

        data = session_user_from_row(user)
        if not data:
            return jsonify(ok=False, error="Не удалось создать пользователя"), 500
        data.pop("_password", None)
        session["user"] = data
        return jsonify(ok=True, user={"name": data["name"], "role": data["role"]})
    except Exception as e:
        trans.rollback()
        return jsonify(ok=False, error=str(e)), 500
    finally:
        conn.close()


@app.route("/api/post/<int:post_id>/like", methods=["POST"])
def api_post_like(post_id):
    """JSON: переключить лайк поста и вернуть актуальный счётчик."""
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
    """Панель администратора: сводная статистика."""
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
    """Список пользователей для staff."""
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
    """Список постов для модерации."""
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
    """Активные ограничения на публикации (content_ban)."""
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
    """Снимает временный бан на посты и комментарии с пользователя."""
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
    """Управление темами и удаление с указанием причины."""
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
    """Каскадное удаление темы и записи в moderation_log."""
    conn, trans, _, topic_repo, _, _, _ = get_repos()
    try:
        t = topic_repo.get_by_id(topic_id)
        if not t:
            abort(404)
        reason = admin_delete_reason_required()
        if not reason:
            flash(
                "Укажите причину удаления темы в форме (не менее 8 символов).",
                "danger",
            )
            return redirect(url_for("admin_topics"))
        topic_repo.delete(topic_id)
        log_moderation(
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
    """Удаление поста из админ-раздела."""
    conn, trans, _, _, post_repo, _, _ = get_repos()
    try:
        if is_admin():
            reason = admin_delete_reason_required()
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
            log_moderation(
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


@app.route("/logout")
def logout():
    """Завершение сессии пользователя."""
    session.pop('user', None)
    flash("Вы вышли из системы", "info")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
