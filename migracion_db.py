import sqlite3
import os

# Configuración "inteligente" de la base de datos
if os.environ.get('RENDER'):
    # En producción (Render), usamos el disco persistente
    DATABASE = '/data/agenda.db'
else:
    # En desarrollo (local), usamos un archivo en la misma carpeta
    DATABASE = 'agenda.db'

def migrar_base_de_datos():
    """
    Aplica los cambios necesarios a la base de datos para las nuevas funcionalidades.
    - Agrega la columna 'email' a la tabla 'clientes'.
    - Agrega la columna 'notas' a la tabla 'turnos'.
    - Crea la tabla 'configuracion_email' para las credenciales SMTP.
    """
    print(f"--- Iniciando migración de la base de datos '{DATABASE}' ---")
    conn = None
    try:
        # Conectar a la base de datos
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()

        # 1. Agregar columna 'email' a la tabla 'clientes'
        try:
            cursor.execute("ALTER TABLE clientes ADD COLUMN email TEXT")
            print("  - Columna 'email' agregada a la tabla 'clientes'.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("  - La columna 'email' ya existía en 'clientes'.")
            else:
                raise

        # 2. Agregar columna 'notas' a la tabla 'turnos'
        try:
            cursor.execute("ALTER TABLE turnos ADD COLUMN notas TEXT")
            print("  - Columna 'notas' agregada a la tabla 'turnos'.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("  - La columna 'notas' ya existía en 'turnos'.")
            else:
                raise

        # 3. Crear tabla 'configuracion_email'
        try:
            cursor.execute("""
                CREATE TABLE configuracion_email (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL UNIQUE,
                    smtp_server TEXT NOT NULL,
                    smtp_port INTEGER NOT NULL,
                    smtp_usuario TEXT NOT NULL,
                    smtp_password_hash TEXT NOT NULL,
                    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
                )
            """)
            print("  - Tabla 'configuracion_email' creada con éxito.")
        except sqlite3.OperationalError as e:
            if "table configuracion_email already exists" in str(e):
                print("  - La tabla 'configuracion_email' ya existía.")
            else:
                raise
        
        # Guardar los cambios
        conn.commit()
        print("--- Migración completada con éxito. ---")

    except sqlite3.Error as e:
        print(f"!!! Error durante la migración: {e}")
    finally:
        if conn:
            conn.close()
            print("--- Conexión a la base de datos cerrada. ---")

if __name__ == '__main__':
    migrar_base_de_datos()