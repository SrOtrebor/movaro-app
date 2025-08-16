import os
import sqlite3

# Configuración "inteligente" de la base de datos
if os.environ.get('RENDER'):
    # Estamos en producción (Render), usamos el disco persistente
    DATABASE = '/data/agenda.db'
else:
    # Estamos en desarrollo (local), usamos un archivo en la misma carpeta
    DATABASE = 'agenda.db'

db_path = DATABASE
print(f"--- Usando Base de Datos en: {db_path} ---")
try:
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # --- PASO 1: LA TABLA 'usuarios' DEBE SER LA PRIMERA EN CREARSE ---
    print("OK: Creando/Verificando tabla 'usuarios'...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, nombre_salon TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente', fecha_vencimiento DATE,
            meta_fidelizacion INTEGER DEFAULT 5,
            url_salon TEXT UNIQUE,
            nombre_publico TEXT
        )
    ''')

    # --- AHORA LAS DEMÁS TABLAS QUE DEPENDEN DE 'usuarios' ---
    print("OK: Creando/Verificando tabla 'clientes'...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL,
            nombre TEXT NOT NULL, apellido TEXT NOT NULL,
            telefono TEXT, cumpleanos TEXT, avatar_path TEXT,
            premios_recibidos INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )''')

    print("OK: Creando/Verificando tabla 'servicios'...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            duracion INTEGER NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )''')

    print("OK: Creando/Verificando tabla 'turnos'...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS turnos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL,
            cliente_id INTEGER NOT NULL, servicio_id INTEGER NOT NULL,
            servicio_nombre TEXT NOT NULL, fecha TEXT NOT NULL,
            hora TEXT NOT NULL, foto_path TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
            FOREIGN KEY (cliente_id) REFERENCES clientes (id),
            FOREIGN KEY (servicio_id) REFERENCES servicios (id)
        )''')
        
    print("OK: Creando/Verificando tabla 'horarios'...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS horarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            dia_semana INTEGER NOT NULL, -- 0=Lunes, 1=Martes, etc.
            hora_inicio TEXT,
            hora_fin TEXT,
            trabaja BOOLEAN NOT NULL DEFAULT 1, -- 1=Sí trabaja, 0=No trabaja
            UNIQUE(usuario_id, dia_semana),
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )
    ''')

    print("OK: Creando/Verificando tabla 'plantillas_mensajes'...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plantillas_mensajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            tipo_mensaje TEXT NOT NULL, -- ej: 'recordatorio', 'cumpleanos'
            texto_mensaje TEXT NOT NULL,
            UNIQUE(usuario_id, tipo_mensaje),
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )
    ''')

    conn.commit()
    conn.close()
    print("\n¡ÉXITO! La base de datos se creó/verificó correctamente.")

except Exception as e:
    # --- PASO 2: CORREGIMOS EL PARÉNTESIS EXTRA AL FINAL ---
    print(f"\n¡ERROR! Ocurrió un problema: {e}")