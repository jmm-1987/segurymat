"""Aplicación Flask principal con webhook de Telegram y web app"""
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file
from functools import wraps
import logging
import json
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import config
import database
import telegram_bot
import os
import shutil
from datetime import datetime
from pathlib import Path

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Filtro Jinja2 para parsear JSON
@app.template_filter('fromjson')
def fromjson_filter(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except:
            return []
    return value if isinstance(value, list) else []

@app.template_filter('tojson')
def tojson_filter(value):
    """Convierte valor a JSON string seguro para JavaScript"""
    return json.dumps(value) if value is not None else 'null'

@app.template_filter('format_date')
def format_date_filter(value):
    """Formatea fecha a dd/mm/yyyy"""
    if not value:
        return ''
    try:
        from datetime import datetime
        # Intentar parsear diferentes formatos
        if isinstance(value, str):
            # Si tiene formato ISO con hora
            if 'T' in value or ' ' in value:
                try:
                    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    return dt.strftime('%d/%m/%Y')
                except:
                    try:
                        dt = datetime.strptime(value[:10], '%Y-%m-%d')
                        return dt.strftime('%d/%m/%Y')
                    except:
                        return value[:10] if len(value) >= 10 else value
            else:
                # Solo fecha
                try:
                    dt = datetime.strptime(value[:10], '%Y-%m-%d')
                    return dt.strftime('%d/%m/%Y')
                except:
                    return value
        elif isinstance(value, datetime):
            return value.strftime('%d/%m/%Y')
        return str(value)
    except Exception:
        return str(value) if value else ''

@app.template_filter('date_weekday')
def date_weekday_filter(value):
    """Obtiene el día de la semana de una fecha"""
    if not value:
        return ''
    try:
        from datetime import datetime
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return dt.strftime('%A')
            except:
                try:
                    dt = datetime.strptime(value[:10], '%Y-%m-%d')
                    return dt.strftime('%A')
                except:
                    return ''
        elif isinstance(value, datetime):
            return value.strftime('%A')
        return ''
    except Exception:
        return ''

# Inicializar bot de Telegram
bot_handler = telegram_bot.TelegramBotHandler()
telegram_app = None
telegram_loop = None  # Event loop compartido del Application
telegram_loop_thread = None  # Thread que mantiene el loop vivo
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telegram_bot")
telegram_initialized = False

if config.TELEGRAM_BOT_TOKEN:
    telegram_app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    
    # Handlers
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.handle_text_message))
    telegram_app.add_handler(MessageHandler(filters.VOICE, bot_handler.handle_voice_message))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, bot_handler.handle_photo_message))
    telegram_app.add_handler(CallbackQueryHandler(bot_handler.handle_callback_query))
    
    # Comando /start
    from telegram.ext import CommandHandler
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await bot_handler.handle_text_message(update, context)
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", start_command))
    
    # Inicializar el Application
    # En producción usa webhook, en local usa polling
    if config.TELEGRAM_WEBHOOK_URL:
        logger.info("Bot de Telegram configurado (modo webhook - inicialización lazy)")
    else:
        logger.info("Bot de Telegram configurado (modo polling para desarrollo local)")
else:
    logger.warning("TELEGRAM_BOT_TOKEN no configurado. Bot deshabilitado.")


# ========== AUTHENTICATION ==========

from werkzeug.security import generate_password_hash, check_password_hash

# Variable global para rastrear si ya se inicializó el usuario maestro
_master_user_initialized = False

def init_master_user():
    """Inicializa el usuario maestro si no existe"""
    global _master_user_initialized
    
    try:
        db = database.db
        
        # Asegurar que la base de datos esté inicializada
        db.init_db()
        
        master_user = db.get_web_user_by_username('master')
        if not master_user:
            # Crear usuario maestro con la contraseña del admin
            if not config.ADMIN_PASSWORD:
                logger.error("ADMIN_PASSWORD no está configurado. No se puede crear usuario maestro.")
                return False
            
            password_hash = generate_password_hash(config.ADMIN_PASSWORD)
            user_id = db.create_web_user('master', password_hash, 'Usuario Maestro', is_master=True)
            logger.info(f"✅ Usuario maestro creado automáticamente (ID: {user_id}, usuario: master)")
            _master_user_initialized = True
            return True
        else:
            # Verificar que el usuario esté activo
            if not master_user.get('is_active'):
                logger.warning("Usuario maestro existe pero está inactivo. Activando...")
                db.update_web_user(master_user['id'], is_active=True)
            
            _master_user_initialized = True
            logger.debug("Usuario maestro ya existe y está activo")
            return True
    except Exception as e:
        logger.error(f"❌ Error inicializando usuario maestro: {e}", exc_info=True)
        return False

# Hook de Flask que se ejecuta antes de cada request (solo la primera vez)
@app.before_request
def ensure_master_user():
    """Asegura que el usuario maestro existe antes de cada request"""
    global _master_user_initialized
    if not _master_user_initialized:
        init_master_user()

# Inicializar usuario maestro al importar el módulo (para desarrollo local)
try:
    logger.info("🔧 Inicializando usuario maestro al arrancar aplicación...")
    init_master_user()
except Exception as e:
    logger.error(f"❌ Error crítico al inicializar usuario maestro al arrancar: {e}", exc_info=True)

def login_required(f):
    """Decorador para requerir login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def master_required(f):
    """Decorador para requerir que el usuario sea maestro"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        db = database.db
        user = db.get_web_user_by_id(session['user_id'])
        if not user or not user.get('is_master'):
            return redirect(url_for('tasks'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    """Obtiene el usuario actual desde la sesión"""
    if 'user_id' not in session:
        return None
    db = database.db
    return db.get_web_user_by_id(session['user_id'])

@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    """Login de usuarios"""
    # Asegurar que el usuario maestro existe
    try:
        init_master_user()
    except Exception as e:
        logger.error(f"Error al verificar usuario maestro en login: {e}", exc_info=True)
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            return render_template('login.html', error='Usuario y contraseña requeridos')
        
        db = database.db
        user = db.get_web_user_by_username(username)
        
        if not user:
            logger.warning(f"Intento de login con usuario inexistente: {username}")
            return render_template('login.html', error='Usuario o contraseña incorrectos')
        
        if not user.get('is_active'):
            logger.warning(f"Intento de login con usuario inactivo: {username}")
            return render_template('login.html', error='Usuario inactivo')
        
        # Verificar contraseña
        if check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_master'] = user.get('is_master', False)
            session['full_name'] = user.get('full_name', username)
            logger.info(f"Login exitoso para usuario: {username}")
            return redirect(url_for('tasks'))
        else:
            logger.warning(f"Contraseña incorrecta para usuario: {username}")
            return render_template('login.html', error='Usuario o contraseña incorrectos')
    
    return render_template('login.html')


@app.route('/admin/logout')
def logout():
    """Logout"""
    session.clear()
    return redirect(url_for('login'))


# ========== WEBHOOK TELEGRAM ==========

def _ensure_telegram_loop():
    """Asegura que existe un loop compartido para el Application"""
    global telegram_loop, telegram_loop_thread, telegram_initialized
    
    if telegram_loop is not None and not telegram_loop.is_closed():
        return telegram_loop
    
    def run_loop():
        """Ejecuta el loop en un thread separado"""
        global telegram_loop, telegram_initialized
        import asyncio
        
        # Crear nuevo loop para este thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        telegram_loop = loop
        
        # Inicializar Application en este loop
        try:
            logger.info("[LOOP] Inicializando Application en loop compartido...")
            loop.run_until_complete(telegram_app.initialize())
            telegram_initialized = True
            logger.info("[LOOP] ✅ Application inicializado en loop compartido")
        except Exception as e:
            logger.error(f"[LOOP] Error inicializando Application: {e}", exc_info=True)
            telegram_initialized = False
            return
        
        # Mantener el loop corriendo
        try:
            loop.run_forever()
        except Exception as e:
            logger.error(f"[LOOP] Error en loop: {e}", exc_info=True)
        finally:
            loop.close()
    
    # Iniciar thread con el loop
    telegram_loop_thread = threading.Thread(target=run_loop, daemon=True, name="telegram_loop")
    telegram_loop_thread.start()
    
    # Esperar a que el loop esté listo
    import time
    max_wait = 5
    waited = 0
    while (telegram_loop is None or telegram_loop.is_closed() or not telegram_initialized) and waited < max_wait:
        time.sleep(0.1)
        waited += 0.1
    
    if telegram_loop is None or telegram_loop.is_closed():
        raise RuntimeError("No se pudo crear el loop compartido")
    
    return telegram_loop


@app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook para recibir actualizaciones de Telegram"""
    global telegram_initialized, telegram_loop
    
    if not telegram_app:
        logger.error("Webhook recibido pero bot no configurado")
        return jsonify({'error': 'Bot no configurado'}), 503
    
    # Asegurar que existe el loop compartido
    try:
        _ensure_telegram_loop()
    except Exception as e:
        logger.error(f"[WEBHOOK] Error asegurando loop: {e}", exc_info=True)
        return jsonify({'error': 'Error inicializando bot'}), 500
    
    # Verificar secreto si está configurado
    if config.TELEGRAM_WEBHOOK_SECRET:
        secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
        if secret != config.TELEGRAM_WEBHOOK_SECRET:
            logger.warning("Intento de webhook con secreto incorrecto")
            return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        update_data = request.get_json()
        if not update_data:
            logger.warning("Webhook recibido sin datos")
            return jsonify({'error': 'No data'}), 400
        
        # Crear el Update
        update = Update.de_json(update_data, telegram_app.bot if telegram_app._initialized else None)
        update_type = 'message' if update.message else 'callback_query' if update.callback_query else 'other'
        logger.info(f"[WEBHOOK] Recibida actualización {update.update_id}, tipo: {update_type}")
        
        # Procesar actualización en el loop compartido
        def process_update_async():
            """Ejecuta process_update en el loop compartido"""
            import asyncio
            try:
                # Usar run_coroutine_threadsafe para ejecutar en el loop compartido
                future = asyncio.run_coroutine_threadsafe(
                    telegram_app.process_update(update),
                    telegram_loop
                )
                # Esperar a que termine (con timeout)
                future.result(timeout=30)
                logger.info(f"[WEBHOOK] Actualización {update.update_id} procesada")
            except Exception as e:
                logger.error(f"[WEBHOOK] Error procesando actualización {update.update_id}: {e}", exc_info=True)
        
        executor.submit(process_update_async)
        
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"[WEBHOOK] Error procesando webhook: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/webhook/set', methods=['POST'])
def set_webhook():
    """Configura webhook de Telegram (requiere autenticación)"""
    if not config.TELEGRAM_BOT_TOKEN:
        return jsonify({'error': 'Bot no configurado'}), 503
    
    webhook_url = config.TELEGRAM_WEBHOOK_URL or request.json.get('url')
    if not webhook_url:
        return jsonify({'error': 'URL de webhook requerida'}), 400
    
    secret_token = config.TELEGRAM_WEBHOOK_SECRET
    
    try:
        bot = telegram_app.bot if telegram_app else None
        if not bot:
            return jsonify({'error': 'Bot no inicializado'}), 503
        
        result = bot.set_webhook(
            url=webhook_url,
            secret_token=secret_token
        )
        
        return jsonify({
            'success': result,
            'webhook_url': webhook_url
        })
    except Exception as e:
        logger.error(f"Error configurando webhook: {e}")
        return jsonify({'error': str(e)}), 500


# ========== WEB APP ==========

@app.route('/')
@login_required
def index():
    """Redirigir a tareas"""
    return redirect(url_for('tasks'))


@app.route('/admin/tasks')
@login_required
def tasks():
    """Vista de tareas"""
    from datetime import datetime
    
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('login'))
    
    status = request.args.get('status', 'open')  # Por defecto mostrar tareas abiertas
    priority = request.args.get('priority', 'all')
    category = request.args.get('category', 'all')
    user_id = request.args.get('user_id', type=int)
    task_date = request.args.get('task_date', '')
    view_mode = request.args.get('view_mode', 'list')
    search_query_raw = request.args.get('search', '').strip()
    search_query = search_query_raw.lower()
    week_offset = request.args.get('week_offset', type=int, default=0)  # Offset de semanas (0 = semana actual)
    
    db = database.db
    tasks_list = db.get_tasks()
    
    # Obtener categorías según permisos del usuario
    if current_user.get('is_master'):
        categories_list = db.get_all_categories()  # Master ve todas las categorías
    else:
        # Usuarios normales solo ven sus categorías asignadas
        allowed_category_names = db.get_user_categories(current_user['id'])
        all_categories = db.get_all_categories()
        categories_list = [cat for cat in all_categories if cat.get('name') in allowed_category_names]
    
    # Filtrar tareas por categorías permitidas si no es maestro
    if not current_user.get('is_master'):
        allowed_categories = db.get_user_categories(current_user['id'])
        tasks_list = [t for t in tasks_list if not t.get('category') or t.get('category') in allowed_categories]
    
    # Filtrar
    if status != 'all':
        tasks_list = [t for t in tasks_list if t['status'] == status]
    if priority != 'all':
        tasks_list = [t for t in tasks_list if t['priority'] == priority]
    if category != 'all':
        tasks_list = [t for t in tasks_list if t.get('category') == category]
    if user_id:
        tasks_list = [t for t in tasks_list if t['user_id'] == user_id]
    
    # Búsqueda en todos los campos
    if search_query:
        search_results = []
        for task in tasks_list:
            # Buscar en todos los campos relevantes
            searchable_fields = [
                task.get('title', '') or '',
                task.get('description', '') or '',
                task.get('client_name_raw', '') or '',
                task.get('solution', '') or '',
                task.get('ampliacion', '') or '',
                task.get('category', '') or '',
                task.get('user_name', '') or '',
            ]
            # Concatenar todos los campos y buscar
            searchable_text = ' '.join(str(field) for field in searchable_fields).lower()
            if search_query in searchable_text:
                search_results.append(task)
        tasks_list = search_results
    
    if task_date:
        # Filtrar por fecha de tarea (comparar solo la fecha, sin hora)
        try:
            filter_date = datetime.strptime(task_date, '%Y-%m-%d').date()
            filtered_tasks = []
            for t in tasks_list:
                if t.get('task_date'):
                    try:
                        task_dt = datetime.fromisoformat(t['task_date'].replace('Z', '+00:00'))
                        if task_dt.date() == filter_date:
                            filtered_tasks.append(t)
                    except (ValueError, AttributeError):
                        # Si hay error al parsear la fecha, intentar formato alternativo
                        try:
                            task_dt = datetime.strptime(t['task_date'][:10], '%Y-%m-%d')
                            if task_dt.date() == filter_date:
                                filtered_tasks.append(t)
                        except (ValueError, AttributeError):
                            continue
            tasks_list = filtered_tasks
        except ValueError:
            # Si la fecha no es válida, ignorar el filtro
            pass
    
    # Obtener clientes para filtro
    clients = db.get_all_clients()
    
    # Obtener usuarios únicos
    users = {}
    for task in tasks_list:
        user_id = task['user_id']
        if user_id not in users:
            users[user_id] = task.get('user_name', f'Usuario {user_id}')
    
    # Asegurar que current_status tenga un valor válido
    if not status or status == '':
        status = 'open'
    
    # Separar tareas con fecha y sin fecha
    tasks_with_date = []
    tasks_without_date = []
    
    for task in tasks_list:
        if task.get('task_date'):
            tasks_with_date.append(task)
        else:
            tasks_without_date.append(task)
    
    # Ordenar tareas con fecha por fecha más reciente primero (descendente)
    tasks_with_date.sort(key=lambda x: x.get('task_date', '') or '', reverse=True)
    
    # Obtener imágenes y última ampliación para cada tarea
    for task in tasks_with_date:
        task['images'] = db.get_task_images(task['id'])
        # Obtener última ampliación del historial
        last_ampliacion = db.get_last_ampliacion(task['id'])
        if last_ampliacion:
            task['last_ampliacion'] = {
                'text': last_ampliacion.get('ampliacion_text', ''),
                'user_name': last_ampliacion.get('user_name', ''),
                'created_at': last_ampliacion.get('created_at', '')
            }
        else:
            task['last_ampliacion'] = None
    for task in tasks_without_date:
        task['images'] = db.get_task_images(task['id'])
        # Obtener última ampliación del historial
        last_ampliacion = db.get_last_ampliacion(task['id'])
        if last_ampliacion:
            task['last_ampliacion'] = {
                'text': last_ampliacion.get('ampliacion_text', ''),
                'user_name': last_ampliacion.get('user_name', ''),
                'created_at': last_ampliacion.get('created_at', '')
            }
        else:
            task['last_ampliacion'] = None
    
    # Para vista de calendario, calcular la semana y organizar tareas por día
    tasks_by_weekday = {}
    week_dates = {}  # Fechas exactas de cada día de la semana
    if view_mode == 'calendar':
        from datetime import timedelta
        
        # Calcular el lunes de la semana seleccionada
        today = datetime.now().date()
        days_since_monday = today.weekday()  # 0 = lunes, 6 = domingo
        monday_of_week = today - timedelta(days=days_since_monday)
        monday_of_selected_week = monday_of_week + timedelta(weeks=week_offset)
        
        # Calcular las fechas de cada día de la semana (lunes a domingo)
        week_days_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        for i, day_name in enumerate(week_days_order):
            day_date = monday_of_selected_week + timedelta(days=i)
            week_dates[day_name] = day_date
        
        # Filtrar tareas que pertenecen a la semana seleccionada
        week_start = monday_of_selected_week
        week_end = week_start + timedelta(days=6)
        
        for task in tasks_with_date:
            try:
                task_dt = datetime.fromisoformat(task['task_date'].replace('Z', '+00:00'))
                task_date_only = task_dt.date()
                
                # Verificar si la tarea está en la semana seleccionada
                if week_start <= task_date_only <= week_end:
                    weekday = task_dt.strftime('%A')  # Monday, Tuesday, etc.
                    if weekday not in tasks_by_weekday:
                        tasks_by_weekday[weekday] = []
                    tasks_by_weekday[weekday].append(task)
            except (ValueError, AttributeError):
                try:
                    task_dt = datetime.strptime(task['task_date'][:10], '%Y-%m-%d')
                    task_date_only = task_dt.date()
                    
                    # Verificar si la tarea está en la semana seleccionada
                    if week_start <= task_date_only <= week_end:
                        weekday = task_dt.strftime('%A')
                        if weekday not in tasks_by_weekday:
                            tasks_by_weekday[weekday] = []
                        tasks_by_weekday[weekday].append(task)
                except (ValueError, AttributeError):
                    pass
        
        # Ordenar tareas dentro de cada día por fecha/hora
        for weekday in tasks_by_weekday:
            tasks_by_weekday[weekday].sort(key=lambda t: t.get('task_date', '') or '')
    
    return render_template(
        'tasks.html',
        tasks_with_date=tasks_with_date,
        tasks_without_date=tasks_without_date,
        tasks_by_weekday=tasks_by_weekday,
        week_dates=week_dates,
        week_offset=week_offset,
        clients=clients,
        users=users,
        current_status=status,
        current_priority=priority,
        current_category=category,
        current_user_id=user_id,
        current_task_date=task_date,
        current_search=search_query_raw,
        view_mode=view_mode,
        categories=categories_list,
        current_user=current_user
    )


@app.route('/admin/clients')
@login_required
def clients():
    """Vista de clientes"""
    db = database.db
    clients_list = db.get_all_clients()
    return render_template('clients.html', clients=clients_list)


@app.route('/admin/clients/create', methods=['POST'])
@login_required
def create_client():
    """Crear cliente"""
    name = request.form.get('name', '').strip()
    aliases_str = request.form.get('aliases', '').strip()
    
    if not name:
        return redirect(url_for('clients'))
    
    aliases = [a.strip() for a in aliases_str.split(',') if a.strip()]
    
    db = database.db
    try:
        db.create_client(name, aliases)
        return redirect(url_for('clients'))
    except ValueError as e:
        return render_template('clients.html', error=str(e), clients=db.get_all_clients())


@app.route('/admin/clients/<int:client_id>/edit', methods=['POST'])
@login_required
def edit_client(client_id):
    """Editar cliente"""
    name = request.form.get('name', '').strip()
    aliases_str = request.form.get('aliases', '').strip()
    
    aliases = [a.strip() for a in aliases_str.split(',') if a.strip()] if aliases_str else []
    
    db = database.db
    db.update_client(client_id, name=name if name else None, aliases=aliases if aliases else None)
    return redirect(url_for('clients'))


@app.route('/admin/clients/<int:client_id>/delete', methods=['POST'])
@login_required
def delete_client(client_id):
    """Eliminar cliente"""
    db = database.db
    db.delete_client(client_id)
    return redirect(url_for('clients'))


@app.route('/admin/categories')
@master_required
def categories():
    """Vista de edición de categorías y usuarios (solo para maestros)"""
    db = database.db
    categories_list = db.get_all_categories()
    
    # Si es maestro, obtener usuarios para gestión (excluyendo el master)
    users_with_categories = []
    if get_current_user() and get_current_user().get('is_master'):
        users_list = db.get_all_web_users()
        for user in users_list:
            # Excluir usuarios master de la lista
            if not user.get('is_master'):
                user_categories = db.get_user_categories(user['id'])
                users_with_categories.append({
                    **user,
                    'categories': user_categories
                })
    
    return render_template('categories.html', 
                          categories=categories_list,
                          users=users_with_categories if users_with_categories else None)


@app.route('/admin/categories/<int:category_id>/update', methods=['POST'])
@login_required
def update_category(category_id):
    """Actualiza una categoría - BLOQUEADO: Las categorías no se pueden editar"""
    return jsonify({'error': 'Las categorías están bloqueadas y no se pueden editar'}), 403


@app.route('/admin/database')
@login_required
def database_management():
    """Vista de gestión de base de datos"""
    return render_template('database.html')


# ========== GESTIÓN DE USUARIOS (dentro de categorías) ==========

@app.route('/admin/categories/users/create', methods=['POST'])
@master_required
def create_user():
    """Crear nuevo usuario"""
    try:
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        category_names = request.form.getlist('categories')
        
        if not username or not password or not full_name:
            return jsonify({'error': 'Usuario, contraseña y nombre completo requeridos'}), 400
        
        db = database.db
        
        # Verificar que el usuario no existe
        if db.get_web_user_by_username(username):
            return jsonify({'error': 'El usuario ya existe'}), 400
        
        # Crear usuario (siempre activo por defecto)
        password_hash = generate_password_hash(password)
        user_id = db.create_web_user(username, password_hash, full_name, is_master=False, is_active=True)
        
        # Asignar categorías
        if category_names:
            db.set_user_categories(user_id, category_names)
        
        return jsonify({'success': True, 'user_id': user_id})
    except Exception as e:
        logger.error(f"Error al crear usuario: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/admin/categories/users/<int:user_id>')
@master_required
def get_user(user_id):
    """Obtiene los datos de un usuario para editar"""
    try:
        db = database.db
        user = db.get_web_user_by_id(user_id)
        
        if not user:
            return jsonify({'error': 'Usuario no encontrado'}), 404
        
        # Obtener categorías del usuario
        categories = db.get_user_categories(user_id)
        user['categories'] = categories
        
        return jsonify(user)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/categories/users/<int:user_id>/update', methods=['POST'])
@master_required
def update_user(user_id):
    """Actualizar usuario"""
    try:
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        is_active = request.form.get('is_active') == 'on'
        category_names = request.form.getlist('categories')
        
        db = database.db
        
        # Verificar que el usuario existe
        user = db.get_web_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'Usuario no encontrado'}), 404
        
        # No permitir modificar al usuario maestro
        if user.get('is_master'):
            return jsonify({'error': 'No se puede modificar el usuario maestro'}), 400
        
        # Actualizar datos
        update_data = {}
        if username and username != user['username']:
            # Verificar que el nuevo username no existe
            if db.get_web_user_by_username(username):
                return jsonify({'error': 'El nombre de usuario ya existe'}), 400
            update_data['username'] = username
        
        if password:
            update_data['password_hash'] = generate_password_hash(password)
        
        if full_name:
            update_data['full_name'] = full_name
        
        update_data['is_active'] = is_active
        
        db.update_web_user(user_id, **update_data)
        
        # Actualizar categorías
        if category_names is not None:
            db.set_user_categories(user_id, category_names)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/categories/users/<int:user_id>/delete', methods=['POST'])
@master_required
def delete_user(user_id):
    """Eliminar usuario"""
    try:
        db = database.db
        user = db.get_web_user_by_id(user_id)
        
        if not user:
            return jsonify({'error': 'Usuario no encontrado'}), 404
        
        # No permitir eliminar al usuario maestro
        if user.get('is_master'):
            return jsonify({'error': 'No se puede eliminar el usuario maestro'}), 400
        
        db.delete_web_user(user_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error al eliminar usuario: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/admin/tasks/pending_approval')
@master_required
def pending_approval_tasks():
    """Vista de tareas pendientes de aprobación"""
    db = database.db
    tasks_list = db.get_tasks(status='pending_approval')
    
    return render_template('tasks.html', 
                          tasks=tasks_list, 
                          status='pending_approval',
                          view_mode='list',
                          categories=db.get_all_categories(),
                          clients=db.get_all_clients())


@app.route('/admin/tasks/<int:task_id>/approve', methods=['POST'])
@master_required
def approve_task(task_id):
    """Aprobar tarea completada"""
    db = database.db
    db.complete_task(task_id)
    return redirect(url_for('tasks'))


@app.route('/admin/tasks/create', methods=['POST'])
@login_required
def create_task():
    """Crea una nueva tarea desde el panel web"""
    try:
        data = request.get_json()
        current_user = get_current_user()
        
        if not current_user:
            return jsonify({'error': 'Usuario no autenticado'}), 401
        
        db = database.db
        
        # Validar datos requeridos
        title = data.get('title', '').strip()
        if not title:
            return jsonify({'error': 'El título es requerido'}), 400
        
        # Parsear fecha si existe
        task_date = None
        if data.get('task_date'):
            from datetime import datetime
            try:
                task_date = datetime.strptime(data.get('task_date'), '%Y-%m-%d')
            except ValueError:
                return jsonify({'error': 'Formato de fecha inválido'}), 400
        
        # Crear tarea
        task_id = db.create_task(
            user_id=current_user['id'],
            user_name=current_user.get('full_name') or current_user.get('username', 'Usuario Web'),
            title=title,
            description=data.get('description'),
            priority=data.get('priority', 'normal'),
            task_date=task_date,
            client_id=None,
            client_name_raw=None,
            category=data.get('category')
        )
        
        return jsonify({'success': True, 'task_id': task_id})
    except Exception as e:
        logger.error(f"Error al crear tarea: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/admin/tasks/<int:task_id>/edit')
@login_required
def get_task_edit(task_id):
    """Obtiene los datos de una tarea para editar"""
    try:
        db = database.db
        task = db.get_task_by_id(task_id)
        
        if not task:
            return jsonify({'error': 'Tarea no encontrada'}), 404
        
        return jsonify(task)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/tasks/<int:task_id>/update', methods=['POST'])
@login_required
def update_task(task_id):
    """Actualiza una tarea"""
    try:
        data = request.get_json()
        db = database.db
        
        # Validar que la tarea existe
        task = db.get_task_by_id(task_id)
        if not task:
            return jsonify({'error': 'Tarea no encontrada'}), 404
        
        # Convertir cadenas vacías de task_date a None para quitar la fecha
        if 'task_date' in data and (data['task_date'] == '' or data['task_date'] is None):
            data['task_date'] = None
        
        # Actualizar tarea
        success = db.update_task(task_id, **data)
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'No se pudo actualizar la tarea'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/tasks/<int:task_id>/complete', methods=['POST'])
@login_required
def complete_task(task_id):
    """Completar tarea (requiere aprobación del maestro si no es maestro)"""
    current_user = get_current_user()
    db = database.db
    
    # Verificar que la tarea existe
    task = db.get_task_by_id(task_id)
    if not task:
        return redirect(url_for('tasks'))
    
    # Verificar acceso a la categoría si no es master
    if not current_user.get('is_master'):
        task_category = task.get('category')
        if task_category and not db.user_has_category_access(current_user['id'], task_category):
            return redirect(url_for('tasks'))
    
    if current_user.get('is_master'):
        # El maestro puede completar directamente
        db.complete_task(task_id)
    else:
        # Usuarios normales marcan como pendiente de aprobación
        db.update_task(task_id, status='pending_approval')
    
    return redirect(url_for('tasks'))


@app.route('/admin/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    """Eliminar tarea"""
    db = database.db
    db.delete_task(task_id)
    return redirect(url_for('tasks'))


@app.route('/admin/tasks/<int:task_id>/ampliar', methods=['POST'])
@login_required
def ampliar_task(task_id):
    """Añade una ampliación a una tarea (guarda en historial)"""
    try:
        data = request.get_json()
        ampliacion_text = data.get('ampliacion', '').strip()
        
        if not ampliacion_text:
            return jsonify({'error': 'La ampliación no puede estar vacía'}), 400
        
        db = database.db
        current_user = get_current_user()
        
        # Obtener la tarea actual
        task = db.get_task_by_id(task_id)
        if not task:
            return jsonify({'error': 'Tarea no encontrada'}), 404
        
        # Verificar acceso a la categoría si no es master
        if not current_user.get('is_master'):
            task_category = task.get('category')
            if task_category and not db.user_has_category_access(current_user['id'], task_category):
                return jsonify({'error': 'No tienes acceso a esta categoría'}), 403
        
        # Guardar en historial con fecha y hora
        user_name = current_user.get('full_name') or current_user.get('username', 'Usuario Web')
        from datetime import datetime
        timestamp = datetime.now().strftime('%d/%m/%Y %H:%M')
        ampliacion_con_timestamp = f"[{timestamp}] {user_name}:\n{ampliacion_text}"
        
        db.add_ampliacion_history(task_id, ampliacion_text, user_name, current_user['id'])
        
        # Obtener todas las ampliaciones del historial para actualizar el campo ampliacion
        history = db.get_task_ampliaciones_history(task_id)
        ampliacion_completa = "\n\n---\n\n".join([
            f"[{datetime.fromisoformat(h['created_at'].replace('Z', '+00:00')).strftime('%d/%m/%Y %H:%M')}] {h['user_name']}:\n{h['ampliacion_text']}"
            for h in history
        ])
        
        # Actualizar campo ampliacion con todo el historial formateado
        db.update_task(task_id, ampliacion=ampliacion_completa, ampliacion_user=user_name)
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error al ampliar tarea: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/admin/tasks/<int:task_id>/ampliaciones-history')
@login_required
def get_task_ampliaciones_history(task_id):
    """Obtiene el historial de ampliaciones de una tarea"""
    try:
        db = database.db
        current_user = get_current_user()
        
        # Verificar que la tarea existe
        task = db.get_task_by_id(task_id)
        if not task:
            return jsonify({'error': 'Tarea no encontrada'}), 404
        
        # Verificar acceso a la categoría si no es master
        if not current_user.get('is_master'):
            task_category = task.get('category')
            if task_category and not db.user_has_category_access(current_user['id'], task_category):
                return jsonify({'error': 'No tienes acceso a esta categoría'}), 403
        
        history = db.get_task_ampliaciones_history(task_id)
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        logger.error(f"Error al obtener historial de ampliaciones: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/admin/tasks/<int:task_id>/solution', methods=['POST'])
@login_required
def update_task_solution(task_id):
    """Actualizar solución/resolución de tarea"""
    current_user = get_current_user()
    solution = request.form.get('solution', '').strip()
    db = database.db
    db.update_task(
        task_id, 
        solution=solution if solution else None,
        solution_user=current_user.get('full_name', current_user.get('username', '')) if solution else None
    )
    return redirect(url_for('tasks'))


@app.route('/admin/tasks/<int:task_id>/set_date', methods=['POST'])
@login_required
def set_task_date(task_id):
    """Asignar fecha a una tarea (usado para drag and drop)"""
    try:
        data = request.get_json()
        task_date = data.get('task_date')
        
        if not task_date:
            return jsonify({'error': 'Fecha no proporcionada'}), 400
        
        db = database.db
        
        # Validar que la tarea existe
        task = db.get_task_by_id(task_id)
        if not task:
            return jsonify({'error': 'Tarea no encontrada'}), 404
        
        # Convertir la fecha al formato correcto (añadir hora si no la tiene)
        from datetime import datetime
        try:
            # Si la fecha viene como 'YYYY-MM-DD', añadir hora por defecto (09:00)
            if len(task_date) == 10:
                task_date_dt = datetime.strptime(task_date, '%Y-%m-%d')
                task_date_dt = task_date_dt.replace(hour=9, minute=0, second=0)
            else:
                task_date_dt = datetime.fromisoformat(task_date.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'error': 'Formato de fecha inválido'}), 400
        
        # Actualizar la tarea
        success = db.update_task(task_id, task_date=task_date_dt)
        
        if success:
            return jsonify({'success': True, 'message': 'Fecha asignada correctamente'})
        else:
            return jsonify({'error': 'No se pudo actualizar la tarea'}), 400
    except Exception as e:
        logger.error(f"Error asignando fecha a tarea {task_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/admin/tasks/<int:task_id>/images/<int:image_id>')
@login_required
def get_task_image(task_id, image_id):
    """Sirve una imagen de una tarea"""
    from sftp_storage import sftp_storage
    import tempfile
    
    db = database.db
    images = db.get_task_images(task_id)
    
    # Buscar la imagen específica
    image = next((img for img in images if img['id'] == image_id), None)
    
    if not image or not image.get('file_path'):
        return jsonify({'error': 'Imagen no encontrada'}), 404
    
    file_path = image['file_path']
    
    # Verificar si es una ruta remota de SFTP o local
    is_remote_path = False
    if file_path.startswith('/') and not os.path.exists(file_path):
        # Podría ser una ruta remota de SFTP
        # Verificar si tiene el formato de ruta remota (/images/tasks/...)
        if file_path.startswith('/images/tasks/') or (sftp_storage.enabled and not os.path.exists(file_path)):
            is_remote_path = True
    
    if is_remote_path and sftp_storage.enabled:
        # Descargar desde SFTP temporalmente
        temp_path = None
        try:
            # Crear archivo temporal en el directorio de imágenes temporales
            images_dir = os.path.join(config.TEMP_DIR, 'task_images')
            os.makedirs(images_dir, exist_ok=True)
            temp_path = os.path.join(images_dir, f"temp_{task_id}_{image_id}_{os.path.basename(file_path)}")
            
            # Descargar desde SFTP
            sftp, transport = sftp_storage._get_connection()
            try:
                # Usar la ruta remota directamente (ya viene completa desde upload_image)
                # Si la ruta empieza con /images/tasks/, usarla directamente
                # Si no, construirla usando remote_path + nombre de archivo
                if file_path.startswith('/'):
                    remote_file_path = file_path
                else:
                    # Si no empieza con /, podría ser relativa
                    remote_filename = os.path.basename(file_path)
                    remote_file_path = f"{sftp_storage.remote_path}/{remote_filename}"
                
                logger.info(f"Descargando imagen desde SFTP: {remote_file_path}")
                
                # Descargar archivo
                sftp.get(remote_file_path, temp_path)
                logger.info(f"Imagen descargada temporalmente a: {temp_path}")
                
                # Verificar que el archivo se descargó correctamente
                if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                    raise FileNotFoundError(f"Archivo descargado está vacío o no existe: {temp_path}")
                
                # Leer el archivo y limpiarlo después
                def generate_and_cleanup():
                    """Genera la respuesta y limpia el archivo después"""
                    try:
                        with open(temp_path, 'rb') as f:
                            while True:
                                chunk = f.read(8192)  # Leer en chunks de 8KB
                                if not chunk:
                                    break
                                yield chunk
                    finally:
                        # Limpiar el archivo después de enviarlo
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                                logger.info(f"Archivo temporal borrado: {temp_path}")
                        except Exception as e:
                            logger.warning(f"No se pudo borrar archivo temporal: {e}")
                
                from flask import Response
                return Response(
                    generate_and_cleanup(),
                    mimetype='image/jpeg',
                    headers={'Content-Disposition': f'inline; filename={os.path.basename(file_path)}'}
                )
            finally:
                sftp.close()
                transport.close()
        except Exception as e:
            logger.error(f"Error descargando imagen desde SFTP: {e}", exc_info=True)
            # Intentar borrar archivo temporal si existe
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
            return jsonify({'error': f'Error descargando imagen: {str(e)}'}), 500
    elif os.path.exists(file_path):
        # Archivo local, servir directamente
        return send_file(file_path, mimetype='image/jpeg')
    else:
        # Archivo no encontrado ni local ni remoto
        logger.error(f"Imagen no encontrada: {file_path}")
        return jsonify({'error': 'Archivo no encontrado'}), 404


# ========== IMPORTAR/EXPORTAR BASE DE DATOS ==========

@app.route('/descargar_db')
@login_required
def descargar_db():
    """Descarga una copia de la base de datos"""
    try:
        db_path = config.SQLITE_PATH
        
        # Verificar que el archivo existe
        if not os.path.exists(db_path):
            return jsonify({'error': 'Base de datos no encontrada'}), 404
        
        # Generar nombre de archivo con timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'app_db_{timestamp}.db'
        
        # Enviar el archivo
        return send_file(
            db_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/x-sqlite3'
        )
    except Exception as e:
        logger.error(f"Error descargando base de datos: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/importar_db', methods=['POST'])
@login_required
def importar_db():
    """Importa una base de datos desde un archivo"""
    try:
        # Verificar que se haya enviado un archivo
        if 'db_file' not in request.files:
            return jsonify({'error': 'No se proporcionó archivo'}), 400
        
        file = request.files['db_file']
        
        # Verificar que el archivo no esté vacío
        if file.filename == '':
            return jsonify({'error': 'Archivo vacío'}), 400
        
        # Verificar que sea un archivo .db
        if not file.filename.endswith('.db'):
            return jsonify({'error': 'El archivo debe ser una base de datos SQLite (.db)'}), 400
        
        db_path = config.SQLITE_PATH
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        
        # Crear respaldo de la base de datos actual si existe
        backup_created = False
        if os.path.exists(db_path):
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = db_dir / f'app_db_backup_{timestamp}.db'
            shutil.copy2(db_path, backup_path)
            backup_created = True
            logger.info(f"Respaldo creado: {backup_path}")
        
        # Guardar el archivo importado
        file.save(db_path)
        
        # Reinicializar la conexión de la base de datos
        database.db.init_db()
        
        logger.info(f"Base de datos importada exitosamente desde {file.filename}")
        
        return jsonify({
            'success': True,
            'message': 'Base de datos importada exitosamente',
            'backup_created': backup_created
        })
        
    except Exception as e:
        logger.error(f"Error importando base de datos: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ========== API JSON ==========

@app.route('/api/tasks', methods=['GET'])
def api_tasks():
    """API JSON para obtener tareas"""
    status = request.args.get('status')
    client_id = request.args.get('client_id', type=int)
    user_id = request.args.get('user_id', type=int)
    
    db = database.db
    tasks_list = db.get_tasks(status=status, client_id=client_id)
    
    if user_id:
        tasks_list = [t for t in tasks_list if t['user_id'] == user_id]
    
    return jsonify({'tasks': tasks_list})


@app.route('/api/clients', methods=['GET'])
def api_clients():
    """API JSON para obtener clientes"""
    db = database.db
    clients_list = db.get_all_clients()
    return jsonify({'clients': clients_list})


# ========== ERROR HANDLERS ==========

@app.errorhandler(500)
def internal_error(error):
    """Manejo de errores 500"""
    logger.error(f"Error 500: {error}", exc_info=True)
    return jsonify({'error': 'Error interno del servidor'}), 500

@app.errorhandler(404)
def not_found(error):
    """Manejo de errores 404"""
    return jsonify({'error': 'Recurso no encontrado'}), 404

# ========== HEALTH CHECK ==========

@app.route('/health')
def health():
    """Health check"""
    # Asegurar que el usuario maestro existe
    try:
        init_master_user()
    except Exception as e:
        logger.error(f"Error verificando usuario maestro en health check: {e}")
    
    try:
        # Verificar que la base de datos esté accesible
        db = database.db
        db.get_connection().close()
        db_status = 'ok'
        
        # Verificar usuario maestro
        master_user = db.get_web_user_by_username('master')
        master_status = 'exists' if master_user else 'missing'
    except Exception as e:
        logger.error(f"Error verificando base de datos: {e}")
        db_status = f'error: {str(e)}'
        master_status = 'unknown'
    
    return jsonify({
        'status': 'ok',
        'telegram_configured': bool(config.TELEGRAM_BOT_TOKEN),
        'telegram_initialized': telegram_initialized,
        'calendar_configured': config.GOOGLE_CALENDAR_ENABLED,
        'database_path': config.SQLITE_PATH,
        'database_status': db_status,
        'master_user_status': master_status,
        'admin_password_configured': bool(config.ADMIN_PASSWORD)
    })

@app.route('/admin/reset-master', methods=['POST'])
def reset_master_user():
    """Endpoint para resetear/crear el usuario maestro (solo en desarrollo o con secreto)"""
    # Verificar secreto si está en producción
    secret = request.form.get('secret') or request.json.get('secret') if request.is_json else None
    expected_secret = os.getenv('RESET_MASTER_SECRET', 'reset123')
    
    if secret != expected_secret:
        return jsonify({'error': 'Secreto incorrecto'}), 403
    
    try:
        db = database.db
        
        # Buscar usuario maestro existente
        master_user = db.get_web_user_by_username('master')
        
        # Crear o actualizar contraseña
        password_hash = generate_password_hash(config.ADMIN_PASSWORD)
        
        if master_user:
            # Actualizar contraseña del usuario existente
            db.update_web_user(
                master_user['id'],
                password_hash=password_hash,
                is_active=True
            )
            logger.info("Usuario maestro actualizado")
            return jsonify({
                'success': True,
                'message': 'Usuario maestro actualizado',
                'username': 'master',
                'password': config.ADMIN_PASSWORD
            })
        else:
            # Crear nuevo usuario maestro
            user_id = db.create_web_user('master', password_hash, 'Usuario Maestro', is_master=True)
            logger.info(f"Usuario maestro creado con ID: {user_id}")
            return jsonify({
                'success': True,
                'message': 'Usuario maestro creado',
                'username': 'master',
                'password': config.ADMIN_PASSWORD,
                'user_id': user_id
            })
    except Exception as e:
        logger.error(f"Error reseteando usuario maestro: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/admin/check-users')
def check_users():
    """Endpoint de diagnóstico para verificar usuarios (sin autenticación)"""
    try:
        db = database.db
        users = db.get_all_web_users()
        
        # Ocultar contraseñas
        users_info = []
        for user in users:
            users_info.append({
                'id': user.get('id'),
                'username': user.get('username'),
                'full_name': user.get('full_name'),
                'is_master': bool(user.get('is_master')),
                'is_active': bool(user.get('is_active')),
                'has_password': bool(user.get('password_hash'))
            })
        
        return jsonify({
            'success': True,
            'total_users': len(users_info),
            'users': users_info,
            'admin_password_set': bool(config.ADMIN_PASSWORD),
            'admin_password_length': len(config.ADMIN_PASSWORD) if config.ADMIN_PASSWORD else 0
        })
    except Exception as e:
        logger.error(f"Error verificando usuarios: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/webhook/status')
def webhook_status():
    """Endpoint para verificar el estado del webhook y del bot"""
    if not telegram_app:
        return jsonify({
            'bot_configured': False,
            'error': 'Bot no configurado'
        }), 503
    
    try:
        # Obtener información del webhook desde Telegram
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            webhook_info = loop.run_until_complete(telegram_app.bot.get_webhook_info())
            return jsonify({
                'bot_configured': True,
                'bot_initialized': telegram_initialized,
                'webhook_info': {
                    'url': webhook_info.url or 'No configurado',
                    'has_custom_certificate': webhook_info.has_custom_certificate,
                    'pending_update_count': webhook_info.pending_update_count,
                    'last_error_date': str(webhook_info.last_error_date) if webhook_info.last_error_date else None,
                    'last_error_message': webhook_info.last_error_message,
                    'max_connections': webhook_info.max_connections
                },
                'expected_webhook_url': config.TELEGRAM_WEBHOOK_URL,
                'webhook_secret_configured': bool(config.TELEGRAM_WEBHOOK_SECRET)
            })
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"Error obteniendo estado del webhook: {e}", exc_info=True)
        return jsonify({
            'bot_configured': True,
            'bot_initialized': telegram_initialized,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # Inicializar base de datos
    database.db.init_db()
    
    # En producción (con webhook): NO usar polling, solo webhook
    # En desarrollo local (sin webhook): usar polling
    if telegram_app and config.TELEGRAM_WEBHOOK_URL:
        logger.info("🌐 Bot configurado para usar webhook (producción)")
        logger.info(f"   Webhook URL: {config.TELEGRAM_WEBHOOK_URL}")
        logger.info("   ⚠️  IMPORTANTE: NO se usará polling, solo webhook")
        # Asegurar que no haya webhooks locales configurados
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            webhook_info = loop.run_until_complete(telegram_app.bot.get_webhook_info())
            if webhook_info.url and 'localhost' in webhook_info.url or '127.0.0.1' in webhook_info.url or 'ngrok' in webhook_info.url:
                logger.warning(f"⚠️  Webhook local detectado: {webhook_info.url}")
                logger.warning("   Esto puede causar conflictos. Configura el webhook para Render.")
            loop.close()
        except Exception as e:
            logger.debug(f"No se pudo verificar webhook: {e}")
    elif telegram_app and not config.TELEGRAM_WEBHOOK_URL:
        # Solo usar polling si NO hay webhook configurado (desarrollo local)
        def start_polling():
            """Inicia el bot en modo polling en un thread separado"""
            try:
                logger.info("🤖 Iniciando bot de Telegram en modo polling (desarrollo local)...")
                telegram_app.run_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                    stop_signals=None  # No manejar señales en thread
                )
            except Exception as e:
                logger.error(f"Error en polling: {e}", exc_info=True)
        
        # Iniciar polling en un thread separado para no bloquear Flask
        polling_thread = threading.Thread(target=start_polling, daemon=True, name="telegram_polling")
        polling_thread.start()
        logger.info("✅ Bot de Telegram iniciado en modo polling (desarrollo local)")
    
    # Iniciar aplicación Flask
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG
    )

