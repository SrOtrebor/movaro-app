import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import sqlite3
import calendar
import os
import time
import urllib.parse
import base64, io
from PIL import Image, ImageOps
from flask import Flask, render_template, request, redirect, flash, session, jsonify, get_flashed_messages, send_from_directory, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
from collections import Counter
from werkzeug.utils import secure_filename
from cryptography.fernet import Fernet, InvalidToken

MENSAJES_FIJOS = {
    'recordatorio': "¡Hola {{nombre_cliente}}! Te recuerdo tu turno para {{servicio_nombre}} mañana a las {{hora_turno}}. ¡Te espero!",
    'cumpleanos': "¡Feliz cumpleaños, {{nombre_cliente}}! 🎂 Que tengas un día genial.",
    'inactivo': "¡Hola, {{nombre_cliente}}! ¿Cómo estás? Hace tiempo no nos vemos.",
    'fidelizacion': "¡Hola, {{nombre_cliente}}! ✨ ¡Alcanzaste los {{total_turnos}} servicios y te ganaste un premio!"
}
app = Flask(__name__)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.secret_key = 'mi_clave_secreta_super_dificil_v2' # TODO: Consider moving this to an environment variable as well.

# --- ENCRYPTION SETUP ---
# Load the secret key from environment variables
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("No SECRET_KEY set for Flask application. Did you set the environment variable?")
fernet = Fernet(SECRET_KEY.encode())

def encrypt_password(password: str) -> str:
    """Encrypts a password using the app's secret key."""
    if not password:
        return ""
    return fernet.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password: str) -> str | None:
    """Decrypts a password using the app's secret key."""
    if not encrypted_password:
        return None
    try:
        return fernet.decrypt(encrypted_password.encode()).decode()
    except InvalidToken:
        # This can happen if the key is wrong or the data is corrupt
        app.logger.error("Failed to decrypt password. InvalidToken.")
        return None
# --- END ENCRYPTION SETUP ---

@app.context_processor
def inject_now():
    return {'now_timestamp': int(time.time())}

# Configuración "inteligente" de la base de datos
if os.environ.get('RENDER'):
    # Estamos en producción (Render), usamos el disco persistente
    DATABASE = '/data/agenda.db'
else:
    # Estamos en desarrollo (local), usamos un archivo en la misma carpeta
    DATABASE = 'agenda.db'

db_path = DATABASE
print(f"--- Usando Base de Datos en: {db_path} ---")

app.config['UPLOAD_FOLDER'] = '/data/uploads' if os.environ.get('RENDER') else 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
TOLERANCIA_MINUTOS = 10 
DIAS_INACTIVIDAD = 30

# --- CONFIGURACIÓN DE ADMIN ---
ADMIN_EMAIL = "admin@movaroapp.com"

# --- DECORADOR PARA PROTEGER RUTAS DE ADMIN ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_email' not in session or session.get('usuario_email') != ADMIN_EMAIL:
            flash("No tenés permiso para acceder a esta página.", "error")
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function

@app.route('/configuracion_publica', methods=['GET', 'POST'])
def configuracion_publica():
    if 'usuario_id' not in session: 
        return redirect('/login')
    
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        url_salon = request.form['url_salon'].lower().strip()
        nombre_publico = request.form['nombre_publico'].strip()

        try:
            cursor.execute("UPDATE usuarios SET url_salon = ?, nombre_publico = ? WHERE id = ?",
                           (url_salon, nombre_publico, usuario_id_actual))
            conn.commit()
            flash("¡Tu página pública fue guardada con éxito!", "success")
        except sqlite3.IntegrityError:
            flash("Error: Esa dirección URL ya está en uso por otro salón.", "error")
        
        conn.close()
        return redirect('/configuracion_publica')

    # --- LÓGICA GET (AQUÍ ESTÁ LA CORRECCIÓN) ---
    cursor.execute("SELECT url_salon, nombre_publico, nombre_salon FROM usuarios WHERE id = ?", (usuario_id_actual,))
    usuario = cursor.fetchone()
    conn.close()

    if usuario:
        url_actual = usuario['url_salon'] if usuario['url_salon'] else ""
        nombre_publico_actual = usuario['nombre_publico'] if usuario['nombre_publico'] else usuario['nombre_salon']
    else:
        flash("Hubo un error al cargar tus datos, por favor iniciá sesión de nuevo.", "error")
        return redirect('/logout')

    return render_template('configuracion_publica.html', url_actual=url_actual, nombre_publico_actual=nombre_publico_actual)

@app.route('/configuracion/email', methods=['GET', 'POST'])
def configuracion_email():
    if 'usuario_id' not in session:
        return redirect('/login')

    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        smtp_server = request.form.get('smtp_server')
        smtp_port = request.form.get('smtp_port', type=int)
        smtp_usuario = request.form.get('smtp_usuario')
        smtp_password = request.form.get('smtp_password')

        # Fetch existing config to check if we need to keep the old password
        cursor.execute("SELECT smtp_password_hash FROM configuracion_email WHERE usuario_id = ?", (usuario_id_actual,))
        existing_config = cursor.fetchone()

        if not all([smtp_server, smtp_port, smtp_usuario]):
            flash("Los campos de servidor, puerto y usuario son obligatorios.", "error")
            return redirect(url_for('configuracion_email'))

        encrypted_password = None
        if smtp_password:
            # User entered a new password, encrypt it
            encrypted_password = encrypt_password(smtp_password)
        elif existing_config:
            # User did not enter a new password, keep the old one
            encrypted_password = existing_config['smtp_password_hash']
        else:
            # This is the first time setup and the password is required
            flash("La contraseña es obligatoria para la configuración inicial.", "error")
            conn.close()
            return redirect(url_for('configuracion_email'))

        try:
            cursor.execute("""
                REPLACE INTO configuracion_email (usuario_id, smtp_server, smtp_port, smtp_usuario, smtp_password_hash)
                VALUES (?, ?, ?, ?, ?)
            """, (usuario_id_actual, smtp_server, smtp_port, smtp_usuario, encrypted_password))
            conn.commit()
            flash("¡Configuración de email guardada con éxito!", "success")
        except Exception as e:
            flash(f"Error al guardar la configuración: {e}", "error")
        finally:
            conn.close()
        return redirect(url_for('configuracion_email'))

    # GET request: Fetch existing configuration
    cursor.execute("SELECT * FROM configuracion_email WHERE usuario_id = ?", (usuario_id_actual,))
    config = cursor.fetchone()
    conn.close()
    # IMPORTANT: The template should never display the password. It should only be a field to enter a new one.
    return render_template('configuracion_email.html', config=config)

@app.route('/probar-email', methods=['POST'])
def probar_email():
    if 'usuario_id' not in session:
        return jsonify({'success': False, 'message': 'No autorizado'}), 401

    data = request.get_json()
    smtp_server = data.get('smtp_server')
    smtp_port = data.get('smtp_port')
    smtp_usuario = data.get('smtp_usuario')
    smtp_password = data.get('smtp_password')

    if not all([smtp_server, smtp_port, smtp_usuario, smtp_password]):
        return jsonify({'success': False, 'message': 'Todos los campos son requeridos para la prueba.'})

    try:
        server = smtplib.SMTP(smtp_server, int(smtp_port), timeout=10)
        server.starttls()
        server.login(smtp_usuario, smtp_password)
        server.quit()
        return jsonify({'success': True, 'message': '¡Conexión exitosa!'})
    except smtplib.SMTPAuthenticationError:
        return jsonify({'success': False, 'message': 'Error de autenticación. Revisa tu email y contraseña.'})
    except smtplib.SMTPServerDisconnected:
        return jsonify({'success': False, 'message': 'El servidor se desconectó. ¿El puerto es correcto?'})
    except ConnectionRefusedError:
        return jsonify({'success': False, 'message': 'Conexión rechazada. ¿El puerto es correcto?'})
    except smtplib.SMTPConnectError:
        return jsonify({'success': False, 'message': 'No se pudo conectar. ¿El nombre del servidor es correcto?'})
    except Exception as e:
        # Captura cualquier otro error (DNS, timeout, etc.)
        app.logger.error(f"Error en prueba de conexión SMTP: {e}")
        return jsonify({'success': False, 'message': f'Ocurrió un error: {e}'})

# --- RUTAS PRINCIPALES Y DE AUTENTICACIÓN ---
@app.route('/')
def landing():
    if 'usuario_id' in session:
        return redirect('/panel')
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
        usuario = cursor.fetchone()
        conn.close()
        if usuario and check_password_hash(usuario['password_hash'], password):
            if usuario['estado'] != 'activo' and usuario['email'] != ADMIN_EMAIL:
                flash(f"Tu cuenta se encuentra en estado '{usuario['estado']}'. Contactá al administrador.", "error")
                return redirect('/login')
            if usuario['fecha_vencimiento'] and usuario['email'] != ADMIN_EMAIL:
                fecha_vencimiento = datetime.strptime(usuario['fecha_vencimiento'], '%Y-%m-%d').date()
                if fecha_vencimiento < datetime.now().date():
                    flash("Tu suscripción ha vencido. Por favor, contactá al administrador para renovarla.", "error")
                    return redirect('/login')
            session['usuario_id'] = usuario['id']
            session['nombre_salon'] = usuario['nombre_salon']
            session['usuario_email'] = usuario['email']
            if usuario['email'] == ADMIN_EMAIL:
                return redirect('/superadmin')
            return redirect('/panel')
        else:
            flash("Email o contraseña incorrectos.", "error")
            return redirect('/login')
    return render_template('login.html')

def enviar_notificacion_registro(nombre_salon, email_nuevo_usuario):
    app.logger.info(f"Iniciando envío de notificación para el nuevo registro: {nombre_salon}")
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM usuarios WHERE email = ?", (ADMIN_EMAIL,))
        admin_user = cursor.fetchone()
        if not admin_user:
            app.logger.error(f"No se encontró al usuario administrador ({ADMIN_EMAIL}) para enviar la notificación.")
            return

        cursor.execute("SELECT * FROM configuracion_email WHERE usuario_id = ?", (admin_user['id'],))
        config = cursor.fetchone()
        if not config or not config['smtp_password_hash']:
            app.logger.error("El administrador no tiene un email configurado o falta la contraseña para enviar notificaciones.")
            return

        password = decrypt_password(config['smtp_password_hash'])
        if not password:
            app.logger.error("No se pudo desencriptar la contraseña del email del administrador.")
            return

        asunto = f"[Movaro] Nuevo Registro Pendiente: {nombre_salon}"
        cuerpo_html = f"""            <h1>Nuevo Registro en Movaro</h1>
            <p>Se ha registrado un nuevo profesional y su cuenta está pendiente de activación.</p>
            <ul>
                <li><strong>Nombre del Salón:</strong> {nombre_salon}</li>
                <li><strong>Email de Contacto:</strong> {email_nuevo_usuario}</li>
            </ul>
            <p>Puedes activar su cuenta desde el panel de Superadmin:</p>
            <a href="https://movaroapp.com/superadmin">Activar Cuenta</a>
            <p>Para contactar al profesional, simplemente responde a este correo.</p>
        """

        msg = MIMEMultipart('alternative')
        msg['Subject'] = asunto
        msg['From'] = config['smtp_usuario']
        msg['To'] = "activaciones@movaroapp.com"
        msg['Reply-To'] = email_nuevo_usuario
        msg.attach(MIMEText(cuerpo_html, 'html'))

        server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
        server.starttls()
        server.login(config['smtp_usuario'], password)
        server.send_message(msg)
        server.quit()
        app.logger.info(f"Notificación de nuevo registro enviada para '{nombre_salon}' a activaciones@movaroapp.com")

    except Exception as e:
        app.logger.error(f"Error crítico al enviar notificación de registro: {e}")
    finally:
        conn.close()


@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre_salon = request.form['nombre_salon']
        email = request.form['email']
        password = request.form['password']

        if len(password) < 8 or not any(c.isupper() for c in password) or not any(c.isdigit() for c in password):
            flash("La contraseña debe tener al menos 8 caracteres, una mayúscula y un número.", "error")
            return redirect('/registro')

        password_hash = generate_password_hash(password)
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()

        try:
            cursor.execute("INSERT INTO usuarios (nombre_salon, email, password_hash) VALUES (?, ?, ?)", (nombre_salon, email, password_hash))
            conn.commit()
            # --- ¡NUEVO! Enviar notificación por email ---
            enviar_notificacion_registro(nombre_salon, email)
            # --- Fin de la nueva lógica ---
            flash("¡Registro exitoso! Ahora contactá al administrador por WhatsApp para activar tu cuenta y empezar.", "success")
            return redirect('/login')
        except sqlite3.IntegrityError:
            flash("El email ya está en uso.", "error")
            return redirect('/registro')
        finally:
            conn.close()
            
    return render_template('registro.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Has cerrado la sesión.", "success")
    return redirect('/')

# --- PANEL DE CONTROL DE USUARIO (COMPLETO Y CORRECTO) ---
@app.route('/panel', methods=['GET', 'POST'])
def panel_control():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        # El único POST que llega aquí es el de la meta de fidelización
        nueva_meta = request.form.get('meta_fidelizacion')
        if nueva_meta:
            cursor.execute("UPDATE usuarios SET meta_fidelizacion = ? WHERE id = ?", (nueva_meta, usuario_id_actual))
            conn.commit()
        conn.close()
        flash("¡Configuración de fidelización guardada!", "success")
        return redirect('/panel')

    # --- LÓGICA GET MEJORADA ---
    
    # 1. Buscamos las plantillas de mensajes del usuario para las alertas
    cursor.execute("SELECT tipo_mensaje, texto_mensaje FROM plantillas_mensajes WHERE usuario_id = ?", (usuario_id_actual,))
    plantillas_db = {row['tipo_mensaje']: row['texto_mensaje'] for row in cursor.fetchall()}
    
    # 2. Obtenemos datos del usuario, clientes, etc.
    cursor.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id_actual,))
    usuario = cursor.fetchone()
    meta_fidelizacion = int(usuario['meta_fidelizacion']) if usuario and usuario['meta_fidelizacion'] else 5

    hoy = datetime.now()
    ano, mes = hoy.year, hoy.month
    cal = calendar.Calendar()
    dias_del_mes = cal.monthdayscalendar(ano, mes)
    nombres_meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    nombre_mes_actual = nombres_meses[mes - 1]
    
    mes_actual_str = f"{ano}-{mes:02d}"
    cursor.execute("SELECT fecha FROM turnos WHERE usuario_id = ? AND strftime('%Y-%m', fecha) = ?", (usuario_id_actual, mes_actual_str))
    turnos_del_mes = cursor.fetchall()
    dias_con_turnos = {datetime.strptime(turno['fecha'], '%Y-%m-%d').day for turno in turnos_del_mes}
    
    todos_mis_clientes = cursor.execute("SELECT * FROM clientes WHERE usuario_id = ?", (usuario_id_actual,)).fetchall()
    
    # --- 3. GENERAMOS TODAS LAS ALERTAS USANDO LAS PLANTILLAS ---
    
    # Alerta de Cumpleaños
    cumpleaneras = []
    plantilla_cumple = plantillas_db.get('cumpleanos', "¡Feliz cumpleaños, {{nombre_cliente}}! 🎂")
    for clienta in todos_mis_clientes:
        if clienta['cumpleanos'] and clienta['cumpleanos'][5:] == hoy.strftime('%m-%d'):
            clienta_dict = dict(clienta)
            telefono_local = (clienta['telefono'] or '').strip()
            clienta_dict['telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
            mensaje_personalizado = plantilla_cumple.replace("{{nombre_cliente}}", clienta['nombre'])
            clienta_dict['mensaje_wa'] = urllib.parse.quote_plus(mensaje_personalizado)
            cumpleaneras.append(clienta_dict)

    # Alerta de Turnos para Mañana
    turnos_manana = []
    plantilla_recordatorio = plantillas_db.get('recordatorio', "Hola {{nombre_cliente}}, te recuerdo tu turno mañana a las {{hora_turno}}.")
    manana_str = (hoy + timedelta(days=1)).strftime('%Y-%m-%d')
    turnos_manana_db = cursor.execute("SELECT t.*, c.nombre, c.apellido, c.telefono FROM turnos t JOIN clientes c ON t.cliente_id = c.id WHERE t.usuario_id = ? AND t.fecha = ?", (usuario_id_actual, manana_str)).fetchall()
    for turno in turnos_manana_db:
        turno_dict = dict(turno)
        turno_dict['cliente_nombre'] = f"{turno['nombre']} {turno['apellido']}"
        telefono_local = (turno['telefono'] or '').strip()
        turno_dict['cliente_telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
        
        mensaje_personalizado = plantilla_recordatorio.replace("{{nombre_cliente}}", turno['nombre'])
        mensaje_personalizado = mensaje_personalizado.replace("{{hora_turno}}", turno['hora'])
        mensaje_personalizado = mensaje_personalizado.replace("{{servicio_nombre}}", turno['servicio_nombre'])
        turno_dict['mensaje_wa'] = urllib.parse.quote_plus(mensaje_personalizado)
        turnos_manana.append(turno_dict)
    
    # Alerta de Clientes Inactivos
    clientes_inactivos = []
    plantilla_inactivo = plantillas_db.get('inactivo', "¡Hola, {{nombre_cliente}}! Hace tiempo no nos vemos.")
    fecha_limite = hoy - timedelta(days=30)
    ultimo_turno_por_cliente = {row['cliente_id']: row['ultima_fecha'] for row in cursor.execute("SELECT cliente_id, MAX(fecha) as ultima_fecha FROM turnos WHERE usuario_id = ? GROUP BY cliente_id", (usuario_id_actual,)).fetchall()}
    for clienta in todos_mis_clientes:
        ultima_fecha_str = ultimo_turno_por_cliente.get(clienta['id'])
        if ultima_fecha_str and (datetime.strptime(ultima_fecha_str, '%Y-%m-%d').date() < fecha_limite.date()):
            clienta_dict = dict(clienta)
            telefono_local = (clienta['telefono'] or '').strip()
            clienta_dict['telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
            mensaje_personalizado = plantilla_inactivo.replace("{{nombre_cliente}}", clienta['nombre'])
            clienta_dict['mensaje_wa'] = urllib.parse.quote_plus(mensaje_personalizado)
            clientes_inactivos.append(clienta_dict)

    # Alerta de Fidelización
    clientes_a_premiar = []
    plantilla_fidelizacion = plantillas_db.get('fidelizacion', "¡Hola, {{nombre_cliente}}! ¡Te ganaste un premio!")
    conteo_turnos_por_cliente = {row['cliente_id']: row['total_turnos'] for row in cursor.execute("SELECT cliente_id, COUNT(*) as total_turnos FROM turnos WHERE usuario_id = ? GROUP BY cliente_id", (usuario_id_actual,)).fetchall()}
    for clienta in todos_mis_clientes:
        total_turnos = conteo_turnos_por_cliente.get(clienta['id'], 0)
        premios_recibidos = clienta['premios_recibidos']
        if total_turnos > 0 and meta_fidelizacion > 0 and (total_turnos // meta_fidelizacion) > premios_recibidos:
            clienta_dict = dict(clienta)
            clienta_dict['total_turnos'] = total_turnos
            telefono_local = (clienta['telefono'] or '').strip()
            clienta_dict['telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
            mensaje_personalizado = plantilla_fidelizacion.replace("{{nombre_cliente}}", clienta['nombre'])
            mensaje_personalizado = mensaje_personalizado.replace("{{total_turnos}}", str(total_turnos))
            clienta_dict['mensaje_wa'] = urllib.parse.quote_plus(mensaje_personalizado)
            clientes_a_premiar.append(clienta_dict)

    # Alerta de Vencimiento de Suscripción (esta no cambia)
    alerta_vencimiento = None
    if usuario and usuario['fecha_vencimiento']:
        fecha_vencimiento = datetime.strptime(usuario['fecha_vencimiento'], '%Y-%m-%d').date()
        dias_restantes = (fecha_vencimiento - hoy.date()).days
        if 0 <= dias_restantes <= 7:
            mensaje = "¡Tu suscripción vence hoy!" if dias_restantes == 0 else f"¡Atención! Tu suscripción vence en {dias_restantes} día(s)."
            alerta_vencimiento = mensaje

    # --- NUEVO: BUSCAR PLANTILLAS DE CAMPAÑA ---
    tipos_fijos = list(MENSAJES_FIJOS.keys())
    placeholders = ','.join('?' for _ in tipos_fijos)
    sql_query = f"SELECT id, tipo_mensaje FROM plantillas_mensajes WHERE usuario_id = ? AND tipo_mensaje NOT IN ({placeholders})"
    parametros = [usuario_id_actual] + tipos_fijos
    cursor.execute(sql_query, parametros)
    plantillas_campana = cursor.fetchall()

    # Datos para el pop-up de agendar turno
    clientes_para_modal = todos_mis_clientes
    servicios_para_modal = cursor.execute("SELECT * FROM servicios WHERE usuario_id = ?", (usuario_id_actual,)).fetchall()
    
    conn.close()
            
    return render_template('panel.html', 
                           meta_actual=meta_fidelizacion,
                           dias_del_mes=dias_del_mes, nombre_mes=nombre_mes_actual, ano=ano, mes=mes,
                           dias_con_turnos=dias_con_turnos, cumpleaneras=cumpleaneras,
                           turnos_manana=turnos_manana, clientes_inactivos=clientes_inactivos,
                           clientes_a_premiar=clientes_a_premiar, alerta_vencimiento=alerta_vencimiento,
                           clientes=clientes_para_modal, servicios=servicios_para_modal,
                           fecha_hoy=hoy,
                           plantillas_campana=plantillas_campana)
@app.route('/premiar_cliente', methods=['POST'])
def premiar_cliente():
    if 'usuario_id' not in session: return redirect('/login')
    cliente_id = request.form['cliente_id']
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE clientes SET premios_recibidos = premios_recibidos + 1 WHERE id = ? AND usuario_id = ?", (cliente_id, usuario_id_actual))
    conn.commit()
    conn.close()
    flash("¡Cliente marcado como premiado!", "success")
    return redirect('/panel')

# --- RUTAS DE SUPER ADMIN ---
@app.route('/superadmin')
@admin_required
def panel_superadmin():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, nombre_salon, email, estado, fecha_vencimiento FROM usuarios")
    todos_los_usuarios = cursor.fetchall()
    conn.close()
    return render_template('superadmin.html', usuarios=todos_los_usuarios)

@app.route('/superadmin/cambiar_estado', methods=['POST'])
@admin_required
def cambiar_estado_usuario():
    usuario_id = request.form['usuario_id']
    nuevo_estado = request.form['nuevo_estado']
    if nuevo_estado not in ['activo', 'suspendido']:
        flash("Estado no válido.", "error")
        return redirect('/superadmin')

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Verificamos si el usuario ya tiene una fecha de vencimiento
    cursor.execute("SELECT fecha_vencimiento FROM usuarios WHERE id = ?", (usuario_id,))
    usuario = cursor.fetchone()

    if nuevo_estado == 'activo' and not usuario['fecha_vencimiento']:
        # Si se está activando y no tiene fecha, se le asignan 30 días
        fecha_vencimiento = datetime.now().date() + timedelta(days=30)
        cursor.execute("UPDATE usuarios SET estado = ?, fecha_vencimiento = ? WHERE id = ?", 
                       (nuevo_estado, fecha_vencimiento.strftime('%Y-%m-%d'), usuario_id))
        flash(f"Usuario activado con éxito. Su suscripción vence en 30 días.", "success")
    else:
        # Si no, solo se actualiza el estado
        cursor.execute("UPDATE usuarios SET estado = ? WHERE id = ?", (nuevo_estado, usuario_id))
        flash(f"Usuario actualizado al estado '{nuevo_estado}' con éxito.", "success")

    conn.commit()
    conn.close()
    return redirect('/superadmin')

@app.route('/superadmin/borrar_usuario', methods=['POST'])
@admin_required
def borrar_usuario():
    usuario_id_a_borrar = request.form['usuario_id']
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT foto_path FROM turnos WHERE usuario_id = ? AND foto_path IS NOT NULL", (usuario_id_a_borrar,))
    fotos_turnos = cursor.fetchall()
    for foto in fotos_turnos:
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], foto[0]))
        except (OSError, TypeError): pass
    cursor.execute("SELECT avatar_path FROM clientes WHERE usuario_id = ? AND avatar_path IS NOT NULL", (usuario_id_a_borrar,))
    fotos_avatares = cursor.fetchall()
    for avatar in fotos_avatares:
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], avatar[0]))
        except (OSError, TypeError): pass
    cursor.execute("DELETE FROM turnos WHERE usuario_id = ?", (usuario_id_a_borrar,))
    cursor.execute("DELETE FROM servicios WHERE usuario_id = ?", (usuario_id_a_borrar,))
    cursor.execute("DELETE FROM clientes WHERE usuario_id = ?", (usuario_id_a_borrar,))
    cursor.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id_a_borrar,))
    conn.commit()
    conn.close()
    flash("Usuario y todos sus datos han sido eliminados permanentemente.", "success")
    return redirect('/superadmin')

@app.route('/superadmin/set_vencimiento', methods=['POST'])
@admin_required
def set_vencimiento():
    usuario_id = request.form['usuario_id']
    fecha_vencimiento = request.form['fecha_vencimiento']
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET fecha_vencimiento = ? WHERE id = ?", (fecha_vencimiento, usuario_id))
    conn.commit()
    conn.close()
    flash("Fecha de vencimiento actualizada.", "success")
    return redirect('/superadmin')

@app.route('/superadmin/resetear/<int:usuario_id>')
@admin_required
def mostrar_formulario_reset(usuario_id):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
    usuario = cursor.fetchone()
    conn.close()
    if usuario: return render_template('resetear_password.html', usuario=usuario)
    return redirect('/superadmin')

@app.route('/superadmin/resetear', methods=['POST'])
@admin_required
def procesar_reseteo():
    usuario_id = request.form['usuario_id']
    nueva_password = request.form['nueva_password']
    if len(nueva_password) < 8 or not any(c.isupper() for c in nueva_password) or not any(c.isdigit() for c in nueva_password):
        flash("La nueva contraseña debe tener al menos 8 caracteres, una mayúscula y un número.", "error")
        return redirect(f'/superadmin/resetear/{usuario_id}')
    nuevo_password_hash = generate_password_hash(nueva_password)
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (nuevo_password_hash, usuario_id))
    conn.commit()
    conn.close()
    flash("¡Contraseña actualizada con éxito!", "success")
    return redirect('/superadmin')

# --- RUTAS DE GESTIÓN (Clientes, Servicios, Agenda) ---
@app.route('/clientes')
def ver_clientes():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    
    query = request.args.get('q', '')
    
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if query:
        search_term = f"%{query}%"
        cursor.execute("SELECT * FROM clientes WHERE usuario_id = ? AND (nombre LIKE ? OR apellido LIKE ?) ORDER BY apellido, nombre", 
                       (usuario_id_actual, search_term, search_term))
    else:
        cursor.execute("SELECT * FROM clientes WHERE usuario_id = ? ORDER BY apellido, nombre", (usuario_id_actual,))
    
    clientes_del_usuario = cursor.fetchall()
    conn.close()
    
    return render_template('clientes.html', clientes=clientes_del_usuario, query=query)

@app.route('/agregar_cliente', methods=['POST'])
def agregar_cliente():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    email = request.form.get('email')
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO clientes (usuario_id, nombre, apellido, telefono, cumpleanos, email) VALUES (?, ?, ?, ?, ?, ?)", 
                   (usuario_id_actual, request.form['nombre'], request.form['apellido'], request.form['telefono'], request.form['cumpleanos'], email))
    conn.commit()
    conn.close()
    return redirect('/clientes')

@app.route('/cliente/<int:cliente_id>')
def ver_cliente_detalle(cliente_id):
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clientes WHERE id = ? AND usuario_id = ?", (cliente_id, usuario_id_actual))
    cliente = cursor.fetchone()
    if cliente:
        cursor.execute("SELECT * FROM turnos WHERE cliente_id = ? AND usuario_id = ? ORDER BY fecha DESC, hora DESC", (cliente_id, usuario_id_actual))
        turnos_del_cliente = cursor.fetchall()
        conn.close()
        return render_template('detalle_cliente.html', clienta=cliente, turnos=turnos_del_cliente)
    conn.close()
    return redirect('/clientes')

@app.route('/cliente/<int:cliente_id>/editar', methods=['GET', 'POST'])
def editar_cliente(cliente_id):
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        apellido = request.form.get('apellido')
        telefono = request.form.get('telefono')
        cumpleanos = request.form.get('cumpleanos')
        email = request.form.get('email')

        cursor.execute("""UPDATE clientes 
                          SET nombre = ?, apellido = ?, telefono = ?, cumpleanos = ?, email = ?
                          WHERE id = ? AND usuario_id = ?""",
                       (nombre, apellido, telefono, cumpleanos, email, cliente_id, usuario_id_actual))
        conn.commit()
        conn.close()
        flash('¡Cliente actualizado con éxito!', 'success')
        return redirect(url_for('ver_cliente_detalle', cliente_id=cliente_id))

    # Lógica GET
    cursor.execute("SELECT * FROM clientes WHERE id = ? AND usuario_id = ?", (cliente_id, usuario_id_actual))
    cliente = cursor.fetchone()
    conn.close()
    if cliente:
        return render_template('editar_cliente.html', clienta=cliente)
    return redirect('/clientes')

@app.route('/cliente/<int:cliente_id>/eliminar', methods=['POST'])
def eliminar_cliente(cliente_id):
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT avatar_path FROM clientes WHERE id = ? AND usuario_id = ?", (cliente_id, usuario_id_actual))
    cliente = cursor.fetchone()
    if cliente and cliente['avatar_path']:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], cliente['avatar_path']))
        except (OSError, TypeError):
            pass

    cursor.execute("DELETE FROM turnos WHERE cliente_id = ? AND usuario_id = ?", (cliente_id, usuario_id_actual))
    cursor.execute("DELETE FROM clientes WHERE id = ? AND usuario_id = ?", (cliente_id, usuario_id_actual))
    conn.commit()
    conn.close()
    flash('Cliente y todos sus turnos han sido eliminados.', 'success')
    return redirect(url_for('ver_clientes'))

@app.route('/subir_foto', methods=['POST'])
def subir_foto():
    if 'usuario_id' not in session: return redirect('/login')
    
    turno_id = request.form.get('turno_id')
    cliente_id = request.form.get('cliente_id')
    if not all([turno_id, cliente_id]):
        flash("Error: No se pudo identificar el turno o el cliente. Inténtalo de nuevo.", "error")
        return redirect('/clientes')

    foto = request.files.get('foto_turno')

    if foto and foto.filename != '':
        nombre_original = secure_filename(foto.filename)
        nombre_unico = f"{turno_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nombre_original}"
        ruta_guardado = os.path.join(app.config['UPLOAD_FOLDER'], nombre_unico)

        try:
            imagen = Image.open(foto.stream)
            imagen = ImageOps.exif_transpose(imagen)
            imagen.save(ruta_guardado, optimize=True, quality=85)
        except Exception as e:
            flash(f"Hubo un error al procesar la imagen: {e}", "error")
            return redirect(f'/cliente/{cliente_id}')

        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("UPDATE turnos SET foto_path = ? WHERE id = ? AND usuario_id = ?", (nombre_unico, turno_id, session['usuario_id']))
        conn.commit()
        conn.close()
        flash("¡Foto subida con éxito!", "success")
    else:
        flash("No se seleccionó ningún archivo.", "error")

    return redirect(f'/cliente/{cliente_id}')

@app.route('/subir_avatar', methods=['POST'])
def subir_avatar():
    if 'usuario_id' not in session: return redirect('/login')
    
    cliente_id = request.form.get('cliente_id')
    if not cliente_id:
        flash("Error: No se pudo identificar el cliente. Inténtalo de nuevo.", "error")
        return redirect('/clientes')

    avatar = request.files.get('avatar')

    if avatar and avatar.filename != '':
        nombre_archivo = secure_filename(avatar.filename)
        nombre_unico = f"avatar_{cliente_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nombre_archivo}"
        ruta_guardado = os.path.join(app.config['UPLOAD_FOLDER'], nombre_unico)

        try:
            imagen = Image.open(avatar.stream)
            imagen = ImageOps.exif_transpose(imagen)
            imagen.thumbnail((400, 400))
            imagen.save(ruta_guardado, optimize=True, quality=85)
        except Exception as e:
            flash(f"Hubo un error al procesar la imagen: {e}", "error")
            return redirect(f'/cliente/{cliente_id}')

        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("UPDATE clientes SET avatar_path = ? WHERE id = ? AND usuario_id = ?", (nombre_unico, cliente_id, session['usuario_id']))
        conn.commit()
        conn.close()
        flash("¡Foto de perfil actualizada!")
    else:
        flash("No se seleccionó ningún archivo.")

    return redirect(f'/cliente/{cliente_id}')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/servicios', methods=['GET', 'POST'])
def gestionar_servicios():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if request.method == 'POST':
        nombre = request.form['nombre']
        duracion = request.form['duracion']
        cursor.execute("INSERT INTO servicios (usuario_id, nombre, duracion) VALUES (?, ?, ?)", (usuario_id_actual, nombre, duracion))
        conn.commit()
        flash("¡Servicio agregado con éxito!")
        conn.close()
        return redirect('/servicios')
    cursor.execute("SELECT * FROM servicios WHERE usuario_id = ?", (usuario_id_actual,))
    servicios_propios = cursor.fetchall()
    conn.close()
    return render_template('servicios.html', servicios=servicios_propios)

@app.route('/servicios/borrar/<int:servicio_id>', methods=['POST'])
def borrar_servicio(servicio_id):
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM servicios WHERE id = ? AND usuario_id = ?", (servicio_id, usuario_id_actual))
    conn.commit()
    conn.close()
    flash("Servicio eliminado.")
    return redirect('/servicios')

@app.route('/horarios', methods=['GET', 'POST'])
def gestionar_horarios():
    if 'usuario_id' not in session: return redirect('/login')
    
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        for i in range(7): # 0 a 6 para Lunes a Domingo
            trabaja = 'trabaja_'+str(i) in request.form
            hora_inicio = request.form.get('inicio_'+str(i))
            hora_fin = request.form.get('fin_'+str(i))
            
            cursor.execute("""
                REPLACE INTO horarios (usuario_id, dia_semana, trabaja, hora_inicio, hora_fin)
                VALUES (?, ?, ?, ?, ?)
            """, (usuario_id_actual, i, trabaja, hora_inicio, hora_fin))
        
        conn.commit()
        flash("¡Horarios guardados con éxito!", "success")
        conn.close()
        return redirect('/horarios')

    cursor.execute("SELECT * FROM horarios WHERE usuario_id = ?", (usuario_id_actual,))
    horarios_guardados = {h['dia_semana']: h for h in cursor.fetchall()}
    conn.close()
    
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    
    return render_template('horarios.html', dias_semana=dias_semana, horarios=horarios_guardados)


@app.route('/enviar_email', methods=['POST'])
def enviar_email():
    if 'usuario_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    usuario_id_actual = session['usuario_id']
    cliente_id = request.json.get('cliente_id')
    mensaje_html = request.json.get('mensaje')
    asunto = request.json.get('asunto', 'Notificación de tu Salón')

    if not all([cliente_id, mensaje_html]):
        return jsonify({'status': 'error', 'message': 'Faltan datos (cliente o mensaje).'}), 400

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT email FROM clientes WHERE id = ? AND usuario_id = ?", (cliente_id, usuario_id_actual))
    cliente = cursor.fetchone()
    if not cliente or not cliente['email']:
        conn.close()
        return jsonify({'status': 'error', 'message': 'El cliente no tiene un email registrado.'}), 404

    cursor.execute("SELECT smtp_server, smtp_port, smtp_usuario, smtp_password_hash FROM configuracion_email WHERE usuario_id = ?", (usuario_id_actual,))
    config = cursor.fetchone()
    conn.close()

    if not config or not config['smtp_password_hash']:
        return jsonify({'status': 'error', 'message': 'No has configurado tus credenciales de email o falta la contraseña.'}), 400

    # --- SECURE DECRYPTION ---
    password = decrypt_password(config['smtp_password_hash'])
    if not password:
        return jsonify({'status': 'error', 'message': 'Error de seguridad: no se pudo desencriptar la contraseña. Verifica que tu SECRET_KEY sea correcta.'}), 500
    # --- END SECURE DECRYPTION ---

    msg = MIMEMultipart('alternative')
    msg['Subject'] = asunto
    msg['From'] = config['smtp_usuario']
    msg['To'] = cliente['email']
    msg.attach(MIMEText(mensaje_html, 'html'))

    try:
        server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
        server.starttls()
        server.login(config['smtp_usuario'], password)
        server.sendmail(config['smtp_usuario'], cliente['email'], msg.as_string())
        server.quit()
        return jsonify({'status': 'success', 'message': f'Email enviado a {cliente["email"]}'})
    except Exception as e:
        app.logger.error(f"Error al enviar email: {e}")
        return jsonify({'status': 'error', 'message': f'Error al conectar con el servidor de email. Revisa la configuración y la contraseña.'}), 500


@app.route('/calendario/<int:ano>/<int:mes>')
def ver_calendario(ano, mes):
    if 'usuario_id' not in session: return redirect('/login')
    
    datos_viejos = session.pop('form_data', None)
    
    usuario_id_actual = session['usuario_id']
    hoy = datetime.now()
    cal = calendar.Calendar()
    dias_del_mes = cal.monthdayscalendar(ano, mes)
    nombres_meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    nombre_mes_actual = nombres_meses[mes - 1]

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    mes_actual_str = f"{ano}-{mes:02d}"
    cursor.execute("SELECT fecha, hora FROM turnos WHERE usuario_id = ? AND strftime('%Y-%m', fecha) = ? ORDER BY hora", (usuario_id_actual, mes_actual_str))
    turnos_del_mes = cursor.fetchall()
    
    turnos_por_dia = {}
    for turno in turnos_del_mes:
        dia = datetime.strptime(turno['fecha'], '%Y-%m-%d').day
        if dia not in turnos_por_dia:
            turnos_por_dia[dia] = []
        turnos_por_dia[dia].append(turno['hora'])

    fecha_actual = datetime(ano, mes, 1)
    mes_anterior_dt = fecha_actual - timedelta(days=1)
    mes_siguiente_dt = (fecha_actual + timedelta(days=31)).replace(day=1)

    cursor.execute("SELECT * FROM clientes WHERE usuario_id = ?", (usuario_id_actual,))
    clientes = cursor.fetchall()
    cursor.execute("SELECT * FROM servicios WHERE usuario_id = ?", (usuario_id_actual,))
    servicios_propios = cursor.fetchall()
    conn.close()
    
    return render_template('calendario.html', 
                           dias_del_mes=dias_del_mes,
                           nombre_mes=nombre_mes_actual,
                           ano=ano,
                           mes=mes,
                           turnos_por_dia=turnos_por_dia,
                           clientes=clientes,
                           servicios=servicios_propios,
                           mes_anterior=mes_anterior_dt,
                           mes_siguiente=mes_siguiente_dt,
                           fecha_hoy=hoy,
                           datos_viejos=datos_viejos)

@app.route('/calendario')
def calendario_redirect():
    hoy = datetime.now()
    return redirect(f'/calendario/{hoy.year}/{hoy.month}')

@app.route('/dia/<int:ano>/<int:mes>/<int:dia>')
def ver_detalle_dia(ano, mes, dia):
    if 'usuario_id' not in session: return redirect('/login')
    
    usuario_id_actual = session['usuario_id']
    fecha_seleccionada = f"{ano}-{mes:02d}-{dia:02d}"

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT t.id, t.hora, t.servicio_nombre, c.nombre, c.apellido 
        FROM turnos t
        JOIN clientes c ON t.cliente_id = c.id
        WHERE t.usuario_id = ? AND t.fecha = ?
        ORDER BY t.hora
    """, (usuario_id_actual, fecha_seleccionada))
    turnos_del_dia = cursor.fetchall()
    conn.close()

    return render_template('detalle_dia.html', 
                           turnos=turnos_del_dia,
                           fecha=fecha_seleccionada)
@app.route('/guardar_nota_turno', methods=['POST'])
def guardar_nota_turno():
    if 'usuario_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    usuario_id_actual = session['usuario_id']
    data = request.get_json()
    turno_id = data.get('turno_id')
    nota = data.get('nota')

    if turno_id is None:
        return jsonify({'status': 'error', 'message': 'Falta el ID del turno.'}), 400

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE turnos SET notas = ? WHERE id = ? AND usuario_id = ?", (nota, turno_id, usuario_id_actual))
    conn.commit()
    
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Turno no encontrado o no tienes permiso para editarlo.'}), 404
    
    conn.close()
    return jsonify({'status': 'success', 'message': '¡Nota guardada!'})
@app.route('/agregar_turno', methods=['POST'])
def agregar_turno():
    if 'usuario_id' not in session:
        return jsonify({'status': 'error', 'message': 'No has iniciado sesión.'})

    usuario_id_actual = session['usuario_id']
    fecha_str = request.form.get('fecha')
    hora_str = request.form.get('hora')
    try:
        cliente_id = int(request.form.get('cliente_id', 0))
        servicio_id = int(request.form.get('servicio_id', 0))
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'El cliente o servicio no es válido.'})

    if not all([fecha_str, hora_str, cliente_id, servicio_id]):
        return jsonify({'status': 'error', 'message': 'Todos los campos son obligatorios.'})

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
        dia_semana = fecha_dt.weekday()
        cursor.execute("SELECT trabaja, hora_inicio, hora_fin FROM horarios WHERE usuario_id = ? AND dia_semana = ?", (usuario_id_actual, dia_semana))
        horario_laboral = cursor.fetchone()

        if not horario_laboral or not horario_laboral['trabaja']:
            conn.close()
            return jsonify({'status': 'error', 'message': 'El día seleccionado está configurado como no laborable.'})

        if hora_str < horario_laboral['hora_inicio'] or hora_str >= horario_laboral['hora_fin']:
            conn.close()
            return jsonify({'status': 'error', 'message': f"El horario está fuera del rango laboral ({horario_laboral['hora_inicio']} - {horario_laboral['hora_fin']})."})
    except (ValueError, TypeError):
        conn.close()
        return jsonify({'status': 'error', 'message': 'La fecha o la hora tienen un formato inválido.'})

    servicio_seleccionado = cursor.execute("SELECT nombre, duracion FROM servicios WHERE id = ? AND usuario_id = ?", (servicio_id, usuario_id_actual)).fetchone()
    if not servicio_seleccionado:
        conn.close()
        return jsonify({'status': 'error', 'message': 'El servicio seleccionado no es válido.'})

    duracion_servicio = servicio_seleccionado['duracion']
    TOLERANCIA_MINUTOS = 10
    inicio_nuevo_turno = datetime.strptime(f"{fecha_str} {hora_str}", '%Y-%m-%d %H:%M')
    fin_nuevo_turno = inicio_nuevo_turno + timedelta(minutes=duracion_servicio + TOLERANCIA_MINUTOS)

    turnos_existentes = cursor.execute("SELECT t.hora, s.duracion FROM turnos t JOIN servicios s ON t.servicio_id = s.id WHERE t.usuario_id = ? AND t.fecha = ?", (usuario_id_actual, fecha_str)).fetchall()
    for turno_existente in turnos_existentes:
        inicio_existente = datetime.strptime(f"{fecha_str} {turno_existente['hora']}", '%Y-%m-%d %H:%M')
        fin_existente = inicio_existente + timedelta(minutes=turno_existente['duracion'] + TOLERANCIA_MINUTOS)
        if max(inicio_nuevo_turno, inicio_existente) < min(fin_nuevo_turno, fin_existente):
            conn.close()
            return jsonify({'status': 'error', 'message': f"Conflicto: el horario se superpone con el turno de las {turno_existente['hora']}."})

    cursor.execute(
        "INSERT INTO turnos (usuario_id, cliente_id, servicio_id, servicio_nombre, fecha, hora) VALUES (?, ?, ?, ?, ?, ?)",
        (usuario_id_actual, cliente_id, servicio_id, servicio_seleccionado['nombre'], fecha_str, hora_str)
    )
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': '¡Turno agendado con éxito!'})
@app.route('/estadisticas')
def ver_estadisticas():
    if 'usuario_id' not in session: return redirect('/login')
    return render_template('estadisticas.html')

@app.route('/api/estadisticas')
def api_estadisticas():
    if 'usuario_id' not in session: return jsonify({'error': 'No autorizado'}), 401
    
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT fecha, servicio_nombre FROM turnos WHERE usuario_id = ?", (usuario_id_actual,))
    todos_los_turnos = cursor.fetchall()
    conn.close()

    contador_servicios = Counter(turno['servicio_nombre'] for turno in todos_los_turnos)
    servicios_populares = contador_servicios.most_common(10)
    labels_servicios = [item[0] for item in servicios_populares]
    data_servicios = [item[1] for item in servicios_populares]

    turnos_por_mes = Counter(turno['fecha'][:7] for turno in todos_los_turnos)
    labels_meses = []
    hoy = datetime.now()
    for i in range(12, 0, -1):
        fecha = hoy - timedelta(days=(i-1)*30)
        labels_meses.append(fecha.strftime("%Y-%m"))
    
    labels_meses = sorted(list(set(labels_meses)))

    data_meses = [turnos_por_mes.get(mes, 0) for mes in labels_meses]

    return jsonify({
        'popularidad_servicios': {
            'labels': labels_servicios,
            'data': data_servicios
        },
        'turnos_por_mes': {
            'labels': labels_meses,
            'data': data_meses
        }
    })


@app.route('/mensajes', methods=['GET', 'POST'])
def gestionar_mensajes():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        for tipo in MENSAJES_FIJOS:
            nuevo_texto = request.form.get(f'fijo_{tipo}')
            if nuevo_texto is not None:
                cursor.execute(
                    "REPLACE INTO plantillas_mensajes (usuario_id, tipo_mensaje, texto_mensaje) VALUES (?, ?, ?)",
                    (usuario_id_actual, tipo, nuevo_texto)
                )
        
        nombre_plantilla = request.form.get('nombre_plantilla')
        texto_plantilla = request.form.get('texto_plantilla')
        if nombre_plantilla and texto_plantilla:
            try:
                cursor.execute(
                    "INSERT INTO plantillas_mensajes (usuario_id, tipo_mensaje, texto_mensaje) VALUES (?, ?, ?)",
                    (usuario_id_actual, nombre_plantilla, texto_plantilla)
                )
            except sqlite3.IntegrityError:
                flash("Error: Ya existe una plantilla personalizada con ese nombre.", "error")
        
        conn.commit()
        conn.close()
        flash("¡Mensajes guardados con éxito!", "success")
        return redirect('/mensajes')

    cursor.execute("SELECT id, tipo_mensaje, texto_mensaje FROM plantillas_mensajes WHERE usuario_id = ?", (usuario_id_actual,))
    plantillas_db = cursor.fetchall()
    conn.close()
    
    mensajes_fijos_para_template = {}
    plantillas_personalizadas = []

    for p in plantillas_db:
        if p['tipo_mensaje'] in MENSAJES_FIJOS:
            mensajes_fijos_para_template[p['tipo_mensaje']] = p['texto_mensaje']
        else:
            plantillas_personalizadas.append(p)
    
    for tipo, texto_default in MENSAJES_FIJOS.items():
        if tipo not in mensajes_fijos_para_template:
            mensajes_fijos_para_template[tipo] = texto_default

    return render_template('mensajes.html', 
                           mensajes_fijos=mensajes_fijos_para_template, 
                           plantillas_personalizadas=plantillas_personalizadas)

@app.route('/mensajes/crear', methods=['POST'])
def crear_plantilla():
    if 'usuario_id' not in session:
        return redirect('/login')

    usuario_id_actual = session['usuario_id']
    nombre_plantilla = request.form.get('nombre_plantilla')
    texto_plantilla = request.form.get('texto_plantilla')

    if not nombre_plantilla or not texto_plantilla:
        flash("El nombre y el texto de la plantilla no pueden estar vacíos.", "error")
        return redirect('/mensajes')
    
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO plantillas_mensajes (usuario_id, tipo_mensaje, texto_mensaje) VALUES (?, ?, ?)",
            (usuario_id_actual, nombre_plantilla, texto_plantilla)
        )
        conn.commit()
        flash("¡Plantilla creada con éxito!", "success")
    except sqlite3.IntegrityError:
        flash("Error: Ya existe una plantilla con ese nombre.", "error")
    finally:
        conn.close()

    return redirect('/mensajes')

@app.route('/mensajes/borrar', methods=['POST'])
def borrar_plantilla():
    if 'usuario_id' not in session:
        return redirect('/login')
        
    usuario_id_actual = session['usuario_id']
    plantilla_id = request.form.get('plantilla_id')

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM plantillas_mensajes WHERE id = ? AND usuario_id = ?", (plantilla_id, usuario_id_actual))
    conn.commit()
    conn.close()

    flash("Plantilla eliminada.", "success")
    return redirect('/mensajes')

@app.route('/campanas', methods=['GET', 'POST'])
def campanas():
    if 'usuario_id' not in session:
        return redirect('/login')

    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, tipo_mensaje FROM plantillas_mensajes WHERE usuario_id = ? ORDER BY tipo_mensaje", (usuario_id_actual,))
    plantillas = cursor.fetchall()

    if request.method == 'POST':
        plantilla_id = request.form.get('plantilla_id')
        canal = request.form.get('canal', 'whatsapp')

        if plantilla_id:
            cursor.execute("SELECT texto_mensaje, tipo_mensaje FROM plantillas_mensajes WHERE id = ? AND usuario_id = ?", (plantilla_id, usuario_id_actual))
            plantilla_seleccionada = cursor.fetchone()
            
            if plantilla_seleccionada:
                session['ultima_plantilla_texto'] = plantilla_seleccionada['texto_mensaje']
                session['ultima_campana_canal'] = canal
                session['ultima_campana_asunto'] = plantilla_seleccionada['tipo_mensaje']

                lista_clientes = []
                if canal == 'whatsapp':
                    cursor.execute("SELECT id, nombre, apellido, telefono FROM clientes WHERE usuario_id = ? AND telefono IS NOT NULL AND telefono != ''", (usuario_id_actual,))
                    clientes = cursor.fetchall()
                    for cliente in clientes:
                        cliente_dict = dict(cliente)
                        mensaje_personalizado = session['ultima_plantilla_texto'].replace("{{nombre_cliente}}", cliente['nombre'])
                        mensaje_codificado = urllib.parse.quote_plus(mensaje_personalizado)
                        telefono_limpio = ''.join(filter(str.isdigit, cliente['telefono']))
                        if len(telefono_limpio) >= 10:
                            telefono_limpio = "549" + telefono_limpio
                        cliente_dict['link_wa'] = f"https://wa.me/{telefono_limpio}?text={mensaje_codificado}"
                        lista_clientes.append(cliente_dict)
                
                elif canal == 'email':
                    cursor.execute("SELECT id, nombre, apellido, email FROM clientes WHERE usuario_id = ? AND email IS NOT NULL AND email != ''", (usuario_id_actual,))
                    clientes = cursor.fetchall()
                    for cliente in clientes:
                        cliente_dict = dict(cliente)
                        mensaje_personalizado = session['ultima_plantilla_texto'].replace("{{nombre_cliente}}", cliente['nombre'])
                        cliente_dict['mensaje'] = mensaje_personalizado
                        lista_clientes.append(cliente_dict)

                session['ultima_campana'] = lista_clientes
                conn.close()
                return redirect('/campanas')

    lista_clientes = session.get('ultima_campana', [])
    plantilla_seleccionada_texto = session.get('ultima_plantilla_texto', "")
    canal_seleccionado = session.get('ultima_campana_canal', 'whatsapp')
    asunto_seleccionado = session.get('ultima_campana_asunto', 'Notificación de tu Salón')
    conn.close()

    return render_template('campanas.html', 
                           plantillas=plantillas, 
                           lista_clientes=lista_clientes,
                           plantilla_seleccionada_texto=plantilla_seleccionada_texto,
                           canal_seleccionado=canal_seleccionado,
                           asunto_seleccionado=asunto_seleccionado)


@app.route('/campanas/limpiar')
def limpiar_campana():
    session.pop('ultima_campana', None)
    session.pop('ultima_plantilla_texto', None)
    session.pop('ultima_campana_canal', None)
    session.pop('ultima_campana_asunto', None)
    flash("limpiar_storage", "info") 
    return redirect('/campanas')

@app.route('/publico/<string:url_salon>')
@app.route('/publico/<string:url_salon>/<int:ano>/<int:mes>')
def calendario_publico(url_salon, ano=None, mes=None):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, nombre_salon, nombre_publico FROM usuarios WHERE url_salon = ?", (url_salon,))
    usuario = cursor.fetchone()

    if not usuario:
        conn.close(); return "Página no encontrada", 404

    usuario_id = usuario['id']
    titulo_pagina = usuario['nombre_publico'] if usuario['nombre_publico'] else usuario['nombre_salon']

    hoy = datetime.now()
    if ano is None or mes is None: ano, mes = hoy.year, hoy.month
    
    cal = calendar.Calendar()
    dias_del_mes = cal.monthdayscalendar(ano, mes)
    nombres_meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    nombre_mes_actual = nombres_meses[mes - 1]

    cursor.execute("SELECT dia_semana, hora_inicio, hora_fin FROM horarios WHERE usuario_id = ? AND trabaja = 1", (usuario_id,))
    horarios_trabajo = {h['dia_semana']: h for h in cursor.fetchall()}

    mes_actual_str = f"{ano}-{mes:02d}"
    cursor.execute("""
        SELECT t.fecha, t.hora, s.duracion 
        FROM turnos t JOIN servicios s ON t.servicio_id = s.id 
        WHERE t.usuario_id = ? AND strftime('%Y-%m', t.fecha) = ? ORDER BY t.hora
    """, (usuario_id, mes_actual_str))
    turnos_del_mes = cursor.fetchall()
    
    info_dias = {}
    for dia_num in range(1, 32):
        try:
            fecha = datetime(ano, mes, dia_num)
            dia_semana = fecha.weekday()
            
            if dia_semana in horarios_trabajo:
                horario_dia = horarios_trabajo[dia_semana]
                if horario_dia['hora_inicio'] and horario_dia['hora_fin']:
                    h_inicio_jornada = datetime.strptime(horario_dia['hora_inicio'], '%H:%M')
                    h_fin_jornada = datetime.strptime(horario_dia['hora_fin'], '%H:%M')
                    
                    turnos_ocupados_dt = []
                    turnos_del_dia_actual = [t for t in turnos_del_mes if t['fecha'] == fecha.strftime('%Y-%m-%d')]
                    for turno in turnos_del_dia_actual:
                        inicio = datetime.strptime(turno['hora'], '%H:%M')
                        fin = inicio + timedelta(minutes=turno['duracion'])
                        turnos_ocupados_dt.append((inicio, fin))

                    horarios_disponibles = []
                    slot_actual = h_inicio_jornada
                    slot_duracion = timedelta(minutes=60)

                    while slot_actual + slot_duracion <= h_fin_jornada:
                        slot_fin = slot_actual + slot_duracion
                        esta_ocupado = False
                        for turno_inicio, turno_fin in turnos_ocupados_dt:
                            if max(slot_actual, turno_inicio) < min(slot_fin, turno_fin):
                                esta_ocupado = True
                                break
                        
                        if not esta_ocupado:
                            horarios_disponibles.append(slot_actual.strftime('%H:%M'))
                        
                        slot_actual += slot_duracion

                    estado = "disponible"
                    if not horarios_disponibles: estado = "sin-disponibilidad"
                    elif len(turnos_del_dia_actual) > 3: estado = "poca-disponibilidad"

                    info_dias[dia_num] = {
                        "estado": estado,
                        "horarios_disponibles": horarios_disponibles,
                        "horarios_ocupados": [f"{t[0].strftime('%H:%M')} - {t[1].strftime('%H:%M')}" for t in turnos_ocupados_dt]
                    }
        except (ValueError, TypeError):
            continue

    conn.close()

    fecha_actual_dt = datetime(ano, mes, 1)
    mes_anterior_dt = fecha_actual_dt - timedelta(days=1)
    mes_siguiente_dt = (fecha_actual_dt + timedelta(days=31)).replace(day=1)

    return render_template('calendario_publico.html', 
                           nombre_salon=titulo_pagina,
                           dias_del_mes=dias_del_mes,
                           nombre_mes=nombre_mes_actual,
                           ano=ano, mes=mes,
                           info_dias=info_dias,
                           url_salon=url_salon,
                           mes_anterior=mes_anterior_dt,
                           mes_siguiente=mes_siguiente_dt)
if __name__ == '__main__':
    app.run(debug=True)