"""Modelos de base de datos SQLite"""
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path
import json
import logging
import config

logger = logging.getLogger(__name__)


class Database:
    """Gestor de base de datos SQLite"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.SQLITE_PATH
        self.init_db()
    
    def get_connection(self):
        """Obtiene conexión a la base de datos"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """Inicializa las tablas de la base de datos"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Tabla de clientes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                normalized_name TEXT NOT NULL,
                aliases TEXT,  -- JSON array de aliases
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabla de categorías
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                icon TEXT DEFAULT '📂',
                color TEXT DEFAULT '#4A90E2',
                display_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabla de imágenes de tareas
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS task_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        ''')
        
        # Tabla de usuarios web
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS web_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                is_master BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabla de relación usuario-categoría (muchos a muchos)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES web_users(id) ON DELETE CASCADE,
                FOREIGN KEY (category_name) REFERENCES categories(name) ON DELETE CASCADE,
                UNIQUE(user_id, category_name)
            )
        ''')
        
        # Tabla de historial de ampliaciones
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS task_ampliaciones_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                ampliacion_text TEXT NOT NULL,
                user_name TEXT NOT NULL,
                user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        ''')
        
        # Tabla de tareas
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'open' CHECK(status IN ('open', 'completed', 'cancelled', 'pending_approval')),
                priority TEXT DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
                task_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                client_id INTEGER,
                client_name_raw TEXT,
                category TEXT,
                google_event_id TEXT,
                google_event_link TEXT,
                ampliacion TEXT,
                ampliacion_user TEXT,
                solution TEXT,
                solution_user TEXT,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL
            )
        ''')
        
        # Migración: agregar campos ampliacion, ampliacion_user, solution, solution_user si no existen
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN ampliacion TEXT')
        except sqlite3.OperationalError:
            pass  # Columna ya existe
        
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN ampliacion_user TEXT')
        except sqlite3.OperationalError:
            pass  # Columna ya existe
        
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN solution TEXT')
        except sqlite3.OperationalError:
            pass  # Columna ya existe
        
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN solution_user TEXT')
        except sqlite3.OperationalError:
            pass  # Columna ya existe
        
        # Migración: Actualizar CHECK constraint para incluir 'pending_approval'
        # SQLite no permite modificar CHECK constraints directamente, así que necesitamos recrear la tabla
        try:
            # Verificar si la tabla existe
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
            if cursor.fetchone():
                # Intentar insertar un registro con 'pending_approval' para verificar si el constraint lo permite
                # Si falla, necesitamos recrear la tabla
                try:
                    cursor.execute("INSERT INTO tasks (user_id, title, status) VALUES (999999, 'test_migration', 'pending_approval')")
                    cursor.execute("DELETE FROM tasks WHERE user_id = 999999 AND title = 'test_migration'")
                    # Si llegamos aquí, el constraint ya permite 'pending_approval'
                except sqlite3.IntegrityError:
                    # El constraint no permite 'pending_approval', necesitamos recrear la tabla
                    logger.info("Migrando tabla tasks para incluir 'pending_approval' en CHECK constraint...")
                    
                    # Crear tabla temporal con el constraint correcto
                    cursor.execute('DROP TABLE IF EXISTS tasks_new')
                    cursor.execute('''
                        CREATE TABLE tasks_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            user_name TEXT,
                            title TEXT NOT NULL,
                            description TEXT,
                            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'completed', 'cancelled', 'pending_approval')),
                            priority TEXT DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
                            task_date TIMESTAMP,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            client_id INTEGER,
                            client_name_raw TEXT,
                            category TEXT,
                            google_event_id TEXT,
                            google_event_link TEXT,
                            ampliacion TEXT,
                            ampliacion_user TEXT,
                            solution TEXT,
                            solution_user TEXT,
                            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL
                        )
                    ''')
                    
                    # Copiar datos
                    cursor.execute('''
                        INSERT INTO tasks_new 
                        SELECT * FROM tasks
                    ''')
                    
                    # Eliminar tabla antigua
                    cursor.execute('DROP TABLE tasks')
                    
                    # Renombrar tabla nueva
                    cursor.execute('ALTER TABLE tasks_new RENAME TO tasks')
                    
                    # Recrear índices
                    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)')
                    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)')
                    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_client_id ON tasks(client_id)')
                    
                    conn.commit()
                    logger.info("Tabla tasks migrada exitosamente con constraint actualizado para incluir 'pending_approval'")
        except Exception as e:
            logger.error(f"Error en migración de tabla tasks: {e}", exc_info=True)
            conn.rollback()
        
        # Índices
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_client_id ON tasks(client_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_clients_normalized_name ON clients(normalized_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_task_images_task_id ON task_images(task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_categories_user_id ON user_categories(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_categories_category ON user_categories(category_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_web_users_username ON web_users(username)')
        
        # Inicializar categorías por defecto si no existen
        self._init_default_categories(cursor)
        
        conn.commit()
        conn.close()
    
    # ========== CLIENTES ==========
    
    def create_client(self, name: str, aliases: List[str] = None) -> int:
        """Crea un nuevo cliente"""
        from utils import normalize_text
        normalized = normalize_text(name)
        aliases_json = json.dumps(aliases or [])
        
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO clients (name, normalized_name, aliases)
                VALUES (?, ?, ?)
            ''', (name, normalized, aliases_json))
            client_id = cursor.lastrowid
            conn.commit()
            return client_id
        except sqlite3.IntegrityError:
            raise ValueError(f"Cliente '{name}' ya existe")
        finally:
            conn.close()
    
    def get_client_by_id(self, client_id: int) -> Optional[Dict]:
        """Obtiene cliente por ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clients WHERE id = ?', (client_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def get_client_by_name(self, name: str) -> Optional[Dict]:
        """Obtiene cliente por nombre exacto (normalizado)"""
        from utils import normalize_text
        normalized = normalize_text(name)
        
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clients WHERE normalized_name = ?', (normalized,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def get_all_clients(self) -> List[Dict]:
        """Obtiene todos los clientes"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clients ORDER BY name')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_client(self, client_id: int, name: str = None, aliases: List[str] = None):
        """Actualiza cliente"""
        from utils import normalize_text
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if name:
            normalized = normalize_text(name)
            updates.append('name = ?')
            updates.append('normalized_name = ?')
            params.extend([name, normalized])
        
        if aliases is not None:
            aliases_json = json.dumps(aliases)
            updates.append('aliases = ?')
            params.append(aliases_json)
        
        if updates:
            params.append(client_id)
            cursor.execute(f'''
                UPDATE clients SET {', '.join(updates)}
                WHERE id = ?
            ''', params)
            conn.commit()
        
        conn.close()
    
    def delete_client(self, client_id: int):
        """Elimina cliente (las tareas mantienen client_name_raw)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM clients WHERE id = ?', (client_id,))
        conn.commit()
        conn.close()
    
    # ========== TAREAS ==========
    
    def create_task(self, user_id: int, user_name: str, title: str,
                    description: str = None, priority: str = 'normal',
                    task_date: datetime = None, client_id: int = None,
                    client_name_raw: str = None, category: str = None) -> int:
        """Crea una nueva tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        task_date_str = task_date.isoformat() if task_date else None
        
        cursor.execute('''
            INSERT INTO tasks (
                user_id, user_name, title, description, priority,
                task_date, client_id, client_name_raw, category
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, user_name, title, description, priority,
              task_date_str, client_id, client_name_raw, category))
        
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return task_id
    
    def get_task_by_id(self, task_id: int) -> Optional[Dict]:
        """Obtiene tarea por ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def get_tasks(self, user_id: int = None, status: str = None,
                  client_id: int = None, limit: int = None) -> List[Dict]:
        """Obtiene tareas con filtros"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = 'SELECT * FROM tasks WHERE 1=1'
        params = []
        
        if user_id:
            query += ' AND user_id = ?'
            params.append(user_id)
        
        if status:
            query += ' AND status = ?'
            params.append(status)
        
        if client_id:
            query += ' AND client_id = ?'
            params.append(client_id)
        
        query += ' ORDER BY created_at DESC'
        
        if limit:
            query += ' LIMIT ?'
            params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_task(self, task_id: int, **kwargs) -> bool:
        """Actualiza tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        allowed_fields = ['title', 'description', 'status', 'priority',
                         'task_date', 'client_id', 'client_name_raw',
                         'category', 'google_event_id', 'google_event_link',
                         'ampliacion', 'ampliacion_user', 'solution', 'solution_user']
        
        updates = []
        params = []
        
        for key, value in kwargs.items():
            if key in allowed_fields:
                # Manejar valores None/null para establecer NULL en la base de datos
                if value is None:
                    updates.append(f'{key} = NULL')
                elif isinstance(value, datetime):
                    value = value.isoformat()
                    updates.append(f'{key} = ?')
                    params.append(value)
                else:
                    updates.append(f'{key} = ?')
                    params.append(value)
        
        if updates:
            updates.append('updated_at = CURRENT_TIMESTAMP')
            params.append(task_id)
            cursor.execute(f'''
                UPDATE tasks SET {', '.join(updates)}
                WHERE id = ?
            ''', params)
            conn.commit()
            success = cursor.rowcount > 0
        else:
            success = False
        
        conn.close()
        return success
    
    def delete_task(self, task_id: int) -> bool:
        """Elimina tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    
    def complete_task(self, task_id: int) -> bool:
        """Marca tarea como completada"""
        return self.update_task(task_id, status='completed')
    
    def get_open_tasks_by_client(self, user_id: int, client_id: int,
                                 limit: int = 5) -> List[Dict]:
        """Obtiene tareas abiertas de un cliente"""
        return self.get_tasks(
            user_id=user_id,
            status='open',
            client_id=client_id,
            limit=limit
        )
    
    # ========== CATEGORÍAS ==========
    
    def _init_default_categories(self, cursor):
        """Inicializa categorías por defecto si no existen"""
        default_categories = [
            ('personal', '👤', '#1ABC9C', 'Personal'),
            ('delegado', '👥', '#16A085', 'Delegado'),
            ('en_espera', '⏳', '#95A5A6', 'En Espera'),
            ('ideas', '💡', '#FFD700', 'Ideas'),
            ('llamar', '📞', '#E67E22', 'Llamar'),
            ('presupuestos', '💰', '#2ECC71', 'Presupuestos'),
            ('visitas', '🏠', '#3498DB', 'Visitas'),
            ('administracion', '📋', '#9B59B6', 'Administración'),
            ('reclamaciones', '📢', '#FF4757', 'Reclamaciones'),
            ('calidad', '⭐', '#8E44AD', 'Calidad'),
            ('comercial', '💼', '#34495E', 'Comercial'),
            ('incidencias', '⚠️', '#FF6B6B', 'Incidencias'),
        ]
        
        for name, icon, color, display_name in default_categories:
            cursor.execute('''
                INSERT OR IGNORE INTO categories (name, icon, color, display_name)
                VALUES (?, ?, ?, ?)
            ''', (name, icon, color, display_name))
    
    def get_all_categories(self) -> List[Dict]:
        """Obtiene todas las categorías"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM categories ORDER BY name')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_category(self, category_id: int, icon: str = None, 
                       color: str = None, display_name: str = None) -> bool:
        """Actualiza una categoría"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if icon is not None:
            updates.append('icon = ?')
            params.append(icon)
        
        if color is not None:
            updates.append('color = ?')
            params.append(color)
        
        if display_name is not None:
            updates.append('display_name = ?')
            params.append(display_name)
        
        if updates:
            params.append(category_id)
            cursor.execute(f'''
                UPDATE categories SET {', '.join(updates)}
                WHERE id = ?
            ''', params)
            conn.commit()
            success = cursor.rowcount > 0
        else:
            success = False
        
        conn.close()
        return success
    
    # ========== IMÁGENES DE TAREAS ==========
    
    def add_image_to_task(self, task_id: int, file_id: str, file_path: str) -> int:
        """Añade una imagen a una tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO task_images (task_id, file_id, file_path)
            VALUES (?, ?, ?)
        ''', (task_id, file_id, file_path))
        
        image_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return image_id
    
    def get_task_images(self, task_id: int) -> List[Dict]:
        """Obtiene todas las imágenes de una tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM task_images WHERE task_id = ? ORDER BY created_at', (task_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def delete_task_image(self, image_id: int) -> bool:
        """Elimina una imagen de una tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM task_images WHERE id = ?', (image_id,))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    
    # ========== USUARIOS WEB ==========
    
    def create_web_user(self, username: str, password_hash: str, full_name: str, is_master: bool = False, is_active: bool = True) -> int:
        """Crea un nuevo usuario web (siempre activo por defecto)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO web_users (username, password_hash, full_name, is_master, is_active)
            VALUES (?, ?, ?, ?, ?)
        ''', (username, password_hash, full_name, 1 if is_master else 0, 1 if is_active else 0))
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return user_id
    
    def get_web_user_by_username(self, username: str) -> Optional[Dict]:
        """Obtiene un usuario web por nombre de usuario"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM web_users WHERE username = ?', (username,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_web_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Obtiene un usuario web por ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM web_users WHERE id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_all_web_users(self) -> List[Dict]:
        """Obtiene todos los usuarios web"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM web_users ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_web_user(self, user_id: int, username: str = None, password_hash: str = None,
                       full_name: str = None, is_active: bool = None):
        """Actualiza un usuario web"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if username is not None:
            updates.append('username = ?')
            params.append(username)
        if password_hash is not None:
            updates.append('password_hash = ?')
            params.append(password_hash)
        if full_name is not None:
            updates.append('full_name = ?')
            params.append(full_name)
        if is_active is not None:
            updates.append('is_active = ?')
            params.append(1 if is_active else 0)
        
        if updates:
            updates.append('updated_at = CURRENT_TIMESTAMP')
            params.append(user_id)
            cursor.execute(f'''
                UPDATE web_users SET {', '.join(updates)}
                WHERE id = ?
            ''', params)
            conn.commit()
        
        conn.close()
    
    def delete_web_user(self, user_id: int) -> bool:
        """Elimina un usuario web"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM web_users WHERE id = ?', (user_id,))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    
    def get_user_categories(self, user_id: int) -> List[str]:
        """Obtiene las categorías permitidas para un usuario"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT category_name FROM user_categories
            WHERE user_id = ?
        ''', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]
    
    def set_user_categories(self, user_id: int, category_names: List[str]):
        """Establece las categorías permitidas para un usuario"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Eliminar categorías existentes
        cursor.execute('DELETE FROM user_categories WHERE user_id = ?', (user_id,))
        
        # Agregar nuevas categorías
        for category_name in category_names:
            cursor.execute('''
                INSERT INTO user_categories (user_id, category_name)
                VALUES (?, ?)
            ''', (user_id, category_name))
        
        conn.commit()
        conn.close()
    
    def user_has_category_access(self, user_id: int, category_name: str) -> bool:
        """Verifica si un usuario tiene acceso a una categoría"""
        user = self.get_web_user_by_id(user_id)
        if not user:
            return False
        
        # El maestro tiene acceso a todas las categorías
        if user.get('is_master'):
            return True
        
        # Verificar si el usuario tiene acceso a esta categoría
        categories = self.get_user_categories(user_id)
        return category_name in categories
    
    # ========== HISTORIAL DE AMPLIACIONES ==========
    
    def add_ampliacion_history(self, task_id: int, ampliacion_text: str, user_name: str, user_id: int = None) -> int:
        """Añade una entrada al historial de ampliaciones"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO task_ampliaciones_history (task_id, ampliacion_text, user_name, user_id)
            VALUES (?, ?, ?, ?)
        ''', (task_id, ampliacion_text, user_name, user_id))
        history_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return history_id
    
    def get_task_ampliaciones_history(self, task_id: int) -> List[Dict]:
        """Obtiene el historial de ampliaciones de una tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, ampliacion_text, user_name, user_id, created_at
            FROM task_ampliaciones_history
            WHERE task_id = ?
            ORDER BY created_at ASC
        ''', (task_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_last_ampliacion(self, task_id: int) -> Optional[Dict]:
        """Obtiene la última ampliación de una tarea"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, ampliacion_text, user_name, user_id, created_at
            FROM task_ampliaciones_history
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        ''', (task_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None


# Instancia global
db = Database()
