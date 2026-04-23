from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.engine import URL
import os

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


def run_legacy_migration_if_needed():
    migrate_path = Path(__file__).resolve().parent / "db" / "migrate_v2.sql"
    if not migrate_path.exists():
        return

    raw_sql = migrate_path.read_text(encoding="utf-8")
    statements = [s.strip() for s in raw_sql.split(";") if s.strip() and not s.strip().startswith("--")]
    if not statements:
        return

    try:
        with engine.begin() as conn:
            users_exists = conn.execute(text("SELECT to_regclass('public.users')")).scalar()
            if not users_exists:
                return
            for statement in statements:
                conn.execute(text(statement))
    except Exception:
        # Не блокируем запуск: миграция совместимости "best effort".
        pass


run_legacy_migration_if_needed()

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


# Обработчик ошибок подключения к базе данных
@app.errorhandler(ConnectionError)
@app.errorhandler(OperationalError)
@app.errorhandler(ProgrammingError)
def handle_db_error(e):
    return render_template('error.html', 
                         error_title="Ошибка подключения к базе данных",
                         error_message=str(e)), 500

# Переключение дизайна
@app.route('/toggle_theme')
def toggle_theme():
    current_theme = session.get('theme', 'old')
    new_theme = 'old' if current_theme == 'new' else 'old'
    session['theme'] = new_theme
    return redirect(request.referrer or url_for('index'))


@app.route('/new')
def open_new_version():
    session['theme'] = 'new'
    return redirect(url_for('index'))

# Переключение темной темы (только для нового дизайна)
@app.route('/toggle_dark_mode')
def toggle_dark_mode():
    current_dark = session.get('dark_mode', False)
    session['dark_mode'] = not current_dark
    return redirect(request.referrer or url_for('index'))

# Контекстный процессор для автоматической передачи темы во все шаблоны
@app.context_processor
def inject_theme():
    return dict(
        theme=session.get('theme', 'old'),
        dark_mode=session.get('dark_mode', False),
        is_moderator=is_moderator,
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
            
            user_repo.create(username, email, password)
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
            where_conditions.append("(p.content ILIKE :search OR t.title ILIKE :search OR u.username ILIKE :search)")
            params['search'] = f"%{search_query}%"

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

        return render_template(
            'index.html',
            posts=posts,
            user=session.get('user'),
            current_sort=sort,
            search_query=search_query,
            topic_filter=topic_filter,
            author_filter=author_filter,
            date_filter=date_filter,
            type_filter=type_filter,
            topics_list=topics_list,
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
                
                if user_password and user_password == password:
                    try:
                        user_role = user.role if hasattr(user, "role") else user[4]
                    except (AttributeError, IndexError):
                        user_role = "user"
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
            
            content = request.form.get('content', '').strip()
            if content:
                comment_repo.create(post_id, session['user']['id'], content)
                trans.commit()
                flash("Комментарий добавлен!", "success")
                return redirect(url_for('post', post_id=post_id))
            else:
                flash("Комментарий не может быть пустым", "danger")
        
        post_data = post_repo.get_post(post_id)
        if not post_data:
            abort(404)
        
        comments = comment_repo.get_by_post_id(post_id)
        user_liked = False
        if session.get("user"):
            user_liked = reaction_repo.user_has_like(session["user"]["id"], post_id)
        return render_template(
            'post.html',
            post=post_data,
            comments=comments,
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
    try:
        if request.method == 'POST':
            topic_title = request.form.get('topic_title', '').strip()
            content = request.form.get('content', '').strip()
            post_type = request.form.get('post_type', 'post').strip()
            if post_type not in ('post', 'good', 'service'):
                post_type = 'post'
            meta = {}
            if post_type in ('good', 'service'):
                price_raw = request.form.get('price_rub', '').strip()
                if price_raw.isdigit():
                    meta['price_rub'] = int(price_raw)
                contact = request.form.get('contact', '').strip()
                if contact:
                    meta['contact'] = contact
            
            if not topic_title or not content:
                flash("Все поля обязательны для заполнения", "danger")
                return render_template('create_post.html', user=session.get('user'))
            
            topic = topic_repo.get_by_title(topic_title)
            if not topic:
                topic_id = topic_repo.create(topic_title, "", session['user']['id'])
            else:
                try:
                    topic_id = topic.id if hasattr(topic, 'id') else topic[0]
                except (AttributeError, IndexError):
                    topic_id = topic[0] if len(topic) > 0 else None
            
            post_repo.create(
                topic_id,
                session['user']['id'],
                content,
                post_type=post_type,
                meta=meta,
            )
            trans.commit()
            flash("Пост создан!", "success")
            return redirect(url_for('index'))
        
        return render_template('create_post.html', user=session.get('user'))
    except Exception as e:
        trans.rollback()
        flash(f"Ошибка при создании поста: {str(e)}", "danger")
        return render_template('create_post.html', user=session.get('user'))
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
            if content:
                post_repo.update(post_id, content)
                trans.commit()
                flash("Пост обновлен!", "success")
                return redirect(url_for('post', post_id=post_id))
            else:
                flash("Содержание поста не может быть пустым", "danger")
        
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
        if post_user_id != session['user']['id'] and not is_moderator():
            flash("Вы можете удалять только свои посты", "danger")
            return redirect(url_for('post', post_id=post_id))
        
        post_repo.delete(post_id)
        trans.commit()
        flash("Пост удалён" + (" модератором" if is_moderator() and post_user_id != session['user']['id'] else "") + "!", "success")
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
            if content:
                comment_repo.update(comment_id, content)
                trans.commit()
                flash("Комментарий обновлен!", "success")
                return redirect(url_for('post', post_id=comment_post_id))
            else:
                flash("Комментарий не может быть пустым", "danger")
        
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
        
        if is_moderator() and comment_user_id != session['user']['id']:
            comment_repo.mark_deleted_by_moderator(comment_id)
            trans.commit()
            flash("Комментарий скрыт модератором", "success")
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

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("Вы вышли из системы", "info")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
