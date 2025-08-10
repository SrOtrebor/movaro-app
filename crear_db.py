import sqlite3
import os

DATABASE = 'agenda.db'
db_path = os.path.abspath(DATABASE)
print(f"--- Creando/Reseteando Base de Datos en: {db_path} ---")

if os.path.exists(db_path):
    os.remove(db_path)
    print("OK: Base de datos anterior borrada.")

try:
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    print("OK: Creando tabla 'usuarios'...")
    cursor.execute('''
        CREATE TABLE usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, nombre_salon TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente', fecha_vencimiento DATE,
            meta_fidelizacion INTEGER DEFAULT 5,
            -- NUEVA COLUMNA PARA EL LINK PÚBLICO --
            url_salon TEXT UNIQUE
        )
    ''')
 

    print("OK: Creando tabla 'clientes'...")
    cursor.execute('''
        CREATE TABLE clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL,
            nombre TEXT NOT NULL, apellido TEXT NOT NULL,
            telefono TEXT, cumpleanos TEXT, avatar_path TEXT,
            premios_recibidos INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )''')

    print("OK: Creando tabla 'servicios'...")
    cursor.execute('''
        CREATE TABLE servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            duracion INTEGER NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )''')

    print("OK: Creando tabla 'turnos'...")
    cursor.execute('''
        CREATE TABLE turnos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL,
            cliente_id INTEGER NOT NULL, servicio_id INTEGER NOT NULL,
            servicio_nombre TEXT NOT NULL, fecha TEXT NOT NULL,
            hora TEXT NOT NULL, foto_path TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
            FOREIGN KEY (cliente_id) REFERENCES clientes (id),
            FOREIGN KEY (servicio_id) REFERENCES servicios (id)
        )''')
        
    print("OK: Creando tabla 'horarios'...")
    cursor.execute('''
        CREATE TABLE horarios (
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

# ... (aquí termina el bloque que crea la tabla 'horarios')

    print("OK: Creando tabla 'plantillas_mensajes'...")
    cursor.execute('''
        CREATE TABLE plantillas_mensajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            tipo_mensaje TEXT NOT NULL, -- ej: 'recordatorio', 'cumpleanos'
            texto_mensaje TEXT NOT NULL,
            UNIQUE(usuario_id, tipo_mensaje),
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )
    ''')

# ... (aquí empieza la línea conn.commit())

    conn.commit()
    conn.close()
    print("\n¡ÉXITO! La base de datos se creó correctamente.")

except Exception as e:
    print(f"\n¡ERROR! Ocurrió un problema: {e}")