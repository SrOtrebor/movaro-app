import time
import sqlite3
import calendar
import os
from flask import Flask, render_template, request, redirect, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
from collections import Counter
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'mi_clave_secreta_super_dificil_v2'
DATABASE = 'agenda.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
TOLERANCIA_MINUTOS = 10 

# --- CONFIGURACIÓN DE ADMIN ---
ADMIN_EMAIL = "nailflow.ad@gmail.com"

# --- DECORADOR PARA PROTEGER RUTAS DE ADMIN ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_email' not in session or session.get('usuario_email') != ADMIN_EMAIL:
            flash("No tenés permiso para acceder a esta página.")
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function

# --- RUTAS PRINCIPALES Y DE AUTENTICACIÓN ---
@app.route('/')
def inicio_v2():
    if 'usuario_id' in session:
        if session.get('usuario_email') == ADMIN_EMAIL:
            return redirect('/superadmin')
        return redirect('/panel')
    return redirect('/login')

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
            # Verificación de estado (pendiente, suspendido)
            if usuario['estado'] != 'activo' and usuario['email'] != ADMIN_EMAIL:
                flash(f"Tu cuenta se encuentra en estado '{usuario['estado']}'. Contactá al administrador.", "error")
                return redirect('/login')

            # --- NUEVA VERIFICACIÓN DE VENCIMIENTO ---
            if usuario['fecha_vencimiento']:
                fecha_vencimiento = datetime.strptime(usuario['fecha_vencimiento'], '%Y-%m-%d').date()
                if fecha_vencimiento < datetime.now().date():
                    flash("Tu suscripción ha vencido. Por favor, contactá al administrador para renovarla.", "error")
                    return redirect('/login')
            # --- FIN DE LA VERIFICACIÓN ---

            session['usuario_id'] = usuario['id']
            session['nombre_salon'] = usuario['nombre_salon']
            session['usuario_email'] = usuario['email']
            return redirect('/')
        else:
            flash("Email o contraseña incorrectos.", "error")
            return redirect('/login')
            
    return render_template('login.html')

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre_salon = request.form['nombre_salon']
        email = request.form['email']
        password = request.form['password']
        if len(password) < 8 or not any(c.isupper() for c in password) or not any(c.isdigit() for c in password):
            flash("La contraseña debe tener al menos 8 caracteres, una mayúscula y un número.")
            return redirect('/registro')
        password_hash = generate_password_hash(password)
        try:
            conn = sqlite3.connect(DATABASE)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO usuarios (nombre_salon, email, password_hash) VALUES (?, ?, ?)", (nombre_salon, email, password_hash))
            conn.commit()
        except sqlite3.IntegrityError:
            flash("El email ya está en uso.")
            return redirect('/registro')
        finally:
            conn.close()
        flash("¡Registro exitoso! Tu cuenta será revisada por un administrador.")
        return redirect('/login')
    return render_template('registro.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- RUTAS DE PANELES Y CONFIGURACIÓN ---
@app.route('/panel', methods=['GET', 'POST'])
def panel_control():
    if 'usuario_id' not in session: return redirect('/login')

    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        nueva_meta = request.form['meta_fidelizacion']
        cursor.execute("UPDATE usuarios SET meta_fidelizacion = ? WHERE id = ?", 
                       (nueva_meta, usuario_id_actual))
        conn.commit()
        flash("¡Configuración de fidelización guardada!")
        conn.close()
        return redirect('/panel')

    # --- LÓGICA GET: Mostrar todo ---
    
    cursor.execute("SELECT meta_fidelizacion FROM usuarios WHERE id = ?", (usuario_id_actual,))
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
    
    # Alerta de Cumpleaños
    hoy_str_mmdd = hoy.strftime('%m-%d')
    cumpleaneras = []
    for clienta in todos_mis_clientes:
        if clienta['cumpleanos'] and clienta['cumpleanos'][5:] == hoy_str_mmdd:
            clienta_dict = dict(clienta)
            telefono_local = (clienta['telefono'] or '').strip()
            clienta_dict['telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
            cumpleaneras.append(clienta_dict)

    # Alerta de Turnos para Mañana
    manana = hoy + timedelta(days=1)
    manana_str = manana.strftime('%Y-%m-%d')
    turnos_manana_db = cursor.execute("SELECT t.*, c.nombre, c.apellido, c.telefono FROM turnos t JOIN clientes c ON t.cliente_id = c.id WHERE t.usuario_id = ? AND t.fecha = ?", (usuario_id_actual, manana_str)).fetchall()
    turnos_manana = [dict(t) for t in turnos_manana_db]
    for turno in turnos_manana:
        turno['cliente_nombre'] = f"{turno['nombre']} {turno['apellido']}"
        telefono_local = (turno['telefono'] or '').strip()
        turno['cliente_telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
    
    # Alerta de Fidelización
    conteo_turnos_por_cliente = {row['cliente_id']: row['total_turnos'] for row in cursor.execute("SELECT cliente_id, COUNT(*) as total_turnos FROM turnos WHERE usuario_id = ? GROUP BY cliente_id", (usuario_id_actual,)).fetchall()}
    clientes_a_premiar = []
    for clienta in todos_mis_clientes:
        total_turnos = conteo_turnos_por_cliente.get(clienta['id'], 0)
        premios_recibidos = clienta['premios_recibidos']
        if total_turnos > 0 and meta_fidelizacion > 0:
            premios_merecidos = total_turnos // meta_fidelizacion
            if premios_merecidos > premios_recibidos:
                clienta_dict = dict(clienta)
                clienta_dict['total_turnos'] = total_turnos
                telefono_local = (clienta['telefono'] or '').strip()
                clienta_dict['telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
                clientes_a_premiar.append(clienta_dict)

    # Alerta de Clientes Inactivos (CON LÓGICA CORREGIDA)
    clientes_inactivos = []
    fecha_limite = hoy - timedelta(days=30)
    ultimo_turno_por_cliente = {row['cliente_id']: row['ultima_fecha'] for row in cursor.execute("SELECT cliente_id, MAX(fecha) as ultima_fecha FROM turnos WHERE usuario_id = ? GROUP BY cliente_id", (usuario_id_actual,)).fetchall()}
    
    for clienta in todos_mis_clientes:
        ultima_fecha_str = ultimo_turno_por_cliente.get(clienta['id'])
        # ---- LA CONDICIÓN CORREGIDA ----
        # Solo si el cliente TIENE un último turno y ese turno es antiguo
        if ultima_fecha_str and (datetime.strptime(ultima_fecha_str, '%Y-%m-%d') < fecha_limite):
            clienta_dict = dict(clienta)
            telefono_local = (clienta['telefono'] or '').strip()
            clienta_dict['telefono_wa'] = "549" + telefono_local if len(telefono_local) >= 10 else telefono_local
            clientes_inactivos.append(clienta_dict)
    
    conn.close()
            
    return render_template('panel.html', 
                           meta_actual=meta_fidelizacion,
                           dias_del_mes=dias_del_mes,
                           nombre_mes=nombre_mes_actual,
                           ano=ano,
                           dias_con_turnos=dias_con_turnos,
                           cumpleaneras=cumpleaneras,
                           turnos_manana=turnos_manana,
                           clientes_a_premiar=clientes_a_premiar,
                           clientes_inactivos=clientes_inactivos)

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
    flash("¡Cliente marcado como premiado!")
    return redirect('/panel')

@app.route('/superadmin')
@admin_required
def panel_superadmin():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Ahora también pedimos la fecha_vencimiento
    cursor.execute("SELECT id, nombre_salon, email, estado, fecha_vencimiento FROM usuarios")
    todos_los_usuarios = cursor.fetchall()
    conn.close()
    return render_template('superadmin.html', usuarios=todos_los_usuarios)

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

@app.route('/superadmin/cambiar_estado', methods=['POST'])
@admin_required
def cambiar_estado_usuario():
    usuario_id = request.form['usuario_id']
    nuevo_estado = request.form['nuevo_estado']

    # Validamos que el nuevo estado sea uno de los permitidos
    if nuevo_estado not in ['activo', 'suspendido', 'pendiente']:
        flash("Estado no válido.", "error")
        return redirect('/superadmin')

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET estado = ? WHERE id = ?", (nuevo_estado, usuario_id))
    conn.commit()
    conn.close()
    
    flash(f"Usuario actualizado al estado '{nuevo_estado}' con éxito.", "success")
    return redirect('/superadmin')


@app.route('/superadmin/borrar_usuario', methods=['POST'])
@admin_required
def borrar_usuario():
    usuario_id_a_borrar = request.form['usuario_id']
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # IMPORTANTE: Borramos en cascada para no dejar datos huérfanos
    # 1. Borramos las fotos de los turnos (opcional, pero buena práctica)
    cursor.execute("SELECT foto_path FROM turnos WHERE usuario_id = ? AND foto_path IS NOT NULL", (usuario_id_a_borrar,))
    fotos_a_borrar = cursor.fetchall()
    for foto in fotos_a_borrar:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], foto[0]))
        except OSError:
            pass # Ignorar si el archivo no se encuentra

    # 2. Borramos los turnos, clientes, servicios y finalmente el usuario
    cursor.execute("DELETE FROM turnos WHERE usuario_id = ?", (usuario_id_a_borrar,))
    cursor.execute("DELETE FROM servicios WHERE usuario_id = ?", (usuario_id_a_borrar,))
    cursor.execute("DELETE FROM clientes WHERE usuario_id = ?", (usuario_id_a_borrar,))
    cursor.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id_a_borrar,))
    
    conn.commit()
    conn.close()

    flash("Usuario y todos sus datos han sido eliminados permanentemente.", "success")
    return redirect('/superadmin')

@app.route('/superadmin/activar', methods=['POST'])
@admin_required
def activar_usuario():
    usuario_id_a_activar = request.form['usuario_id']
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET estado = 'activo' WHERE id = ?", (usuario_id_a_activar,))
    conn.commit()
    conn.close()
    flash("¡Usuario activado con éxito!")
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
    if usuario:
        return render_template('resetear_password.html', usuario=usuario)
    return redirect('/superadmin')

@app.route('/superadmin/resetear', methods=['POST'])
@admin_required
def procesar_reseteo():
    usuario_id = request.form['usuario_id']
    nueva_password = request.form['nueva_password']
    if len(nueva_password) < 8 or not any(c.isupper() for c in nueva_password) or not any(c.isdigit() for c in nueva_password):
        flash("La nueva contraseña debe tener al menos 8 caracteres, una mayúscula y un número.")
        return redirect(f'/superadmin/resetear/{usuario_id}')
    nuevo_password_hash = generate_password_hash(nueva_password)
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (nuevo_password_hash, usuario_id))
    conn.commit()
    conn.close()
    flash("¡Contraseña actualizada con éxito!")
    return redirect('/superadmin')

# --- RUTAS DE GESTIÓN (Clientes, Servicios, Agenda) ---
@app.route('/clientes')
def ver_clientes():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clientes WHERE usuario_id = ? ORDER BY apellido, nombre", (usuario_id_actual,))
    clientes_del_usuario = cursor.fetchall()
    conn.close()
    return render_template('clientes.html', clientes=clientes_del_usuario)

@app.route('/agregar_cliente', methods=['POST'])
def agregar_cliente():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO clientes (usuario_id, nombre, apellido, telefono, cumpleanos, avatar_path) VALUES (?, ?, ?, ?, ?, NULL)", (usuario_id_actual, request.form['nombre'], request.form['apellido'], request.form['telefono'], request.form['cumpleanos']))
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

@app.route('/subir_foto', methods=['POST'])
def subir_foto():
    if 'usuario_id' not in session: return redirect('/login')
    turno_id = request.form['turno_id']
    cliente_id = request.form['cliente_id']
    foto = request.files.get('foto')
    if foto and foto.filename != '':
        nombre_archivo = secure_filename(foto.filename)
        nombre_unico = f"{turno_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nombre_archivo}"
        ruta_guardado = os.path.join(app.config['UPLOAD_FOLDER'], nombre_unico)
        foto.save(ruta_guardado)
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("UPDATE turnos SET foto_path = ? WHERE id = ? AND usuario_id = ?", (nombre_unico, turno_id, session['usuario_id']))
        conn.commit()
        conn.close()
        flash("¡Foto subida con éxito!")
    else:
        flash("No se seleccionó ningún archivo.")
    return redirect(f'/cliente/{cliente_id}')

@app.route('/subir_avatar', methods=['POST'])
def subir_avatar():
    if 'usuario_id' not in session: return redirect('/login')

    cliente_id = request.form['cliente_id']
    avatar = request.files.get('avatar')

    if avatar and avatar.filename != '':
        # Creamos un nombre de archivo seguro y único
        nombre_archivo = secure_filename(avatar.filename)
        nombre_unico = f"avatar_{cliente_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nombre_archivo}"
        
        # Guardamos el archivo
        ruta_guardado = os.path.join(app.config['UPLOAD_FOLDER'], nombre_unico)
        avatar.save(ruta_guardado)

        # Actualizamos la base de datos para el cliente
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("UPDATE clientes SET avatar_path = ? WHERE id = ? AND usuario_id = ?",
                       (nombre_unico, cliente_id, session['usuario_id']))
        conn.commit()
        conn.close()
        flash("¡Foto de perfil actualizada!")
    else:
        flash("No se seleccionó ningún archivo.")

    # Redirigimos de vuelta a la ficha del cliente
    return redirect(f'/cliente/{cliente_id}')

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

@app.route('/calendario/<int:ano>/<int:mes>')
def ver_calendario(ano, mes):
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
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
    turnos_por_dia = {dia: [] for dia in range(1, 32)}
    for t in turnos_del_mes:
        turnos_por_dia[datetime.strptime(t['fecha'], '%Y-%m-%d').day].append(t['hora'])
    fecha_actual = datetime(ano, mes, 1)
    mes_anterior_dt = fecha_actual - timedelta(days=1)
    mes_siguiente_dt = (fecha_actual + timedelta(days=31)).replace(day=1)
    cursor.execute("SELECT * FROM clientes WHERE usuario_id = ?", (usuario_id_actual,))
    clientes = cursor.fetchall()
    cursor.execute("SELECT * FROM servicios WHERE usuario_id = ?", (usuario_id_actual,))
    servicios_propios = cursor.fetchall()
    conn.close()
    return render_template('calendario.html', dias_del_mes=dias_del_mes, nombre_mes=nombre_mes_actual, ano=ano, mes=mes, turnos_por_dia=turnos_por_dia, clientes=clientes, servicios=servicios_propios, mes_anterior=mes_anterior_dt, mes_siguiente=mes_siguiente_dt)

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

    # --- LA CONSULTA CORREGIDA ---
    # Ahora sí le pedimos el ID del turno (t.id)
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
@app.route('/agregar_turno', methods=['POST'])
def agregar_turno():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    cliente_id = int(request.form['cliente_id'])
    servicio_id = int(request.form['servicio_id'])
    fecha_str = request.form['fecha']
    hora_str = request.form['hora']

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    servicio_seleccionado = cursor.execute("SELECT nombre, duracion FROM servicios WHERE id = ? AND usuario_id = ?", (servicio_id, usuario_id_actual)).fetchone()
    if not servicio_seleccionado:
        flash("Error: Servicio no válido.")
        conn.close()
        return redirect(f'/calendario/{fecha_str[:4]}/{int(fecha_str[5:7])}')

    duracion_servicio = servicio_seleccionado['duracion']
    nombre_servicio = servicio_seleccionado['nombre']

    inicio_nuevo_turno = datetime.strptime(f"{fecha_str} {hora_str}", '%Y-%m-%d %H:%M')
    fin_nuevo_turno = inicio_nuevo_turno + timedelta(minutes=duracion_servicio + TOLERANCIA_MINUTOS)

    turnos_existentes = cursor.execute("SELECT t.hora, s.duracion FROM turnos t JOIN servicios s ON t.servicio_id = s.id WHERE t.usuario_id = ? AND t.fecha = ?", (usuario_id_actual, fecha_str)).fetchall()

    for turno_existente in turnos_existentes:
        inicio_existente = datetime.strptime(f"{fecha_str} {turno_existente['hora']}", '%Y-%m-%d %H:%M')
        fin_existente = inicio_existente + timedelta(minutes=turno_existente['duracion'] + TOLERANCIA_MINUTOS)
        
        if max(inicio_nuevo_turno, inicio_existente) < min(fin_nuevo_turno, fin_existente):
            flash(f"Error: El horario se pisa con el turno de las {turno_existente['hora']}.")
            conn.close()
            return redirect(f'/calendario/{fecha_str[:4]}/{int(fecha_str[5:7])}')

    cursor.execute(
        "INSERT INTO turnos (usuario_id, cliente_id, servicio_id, servicio_nombre, fecha, hora) VALUES (?, ?, ?, ?, ?, ?)",
        (usuario_id_actual, cliente_id, servicio_id, nombre_servicio, fecha_str, hora_str)
    )
    conn.commit()
    conn.close()
    flash("¡Turno agendado con éxito!")
    return redirect(f'/calendario/{fecha_str[:4]}/{int(fecha_str[5:7])}')

# --- RUTA DE ESTADÍSTICAS ---
@app.route('/estadisticas')
def ver_estadisticas():
    if 'usuario_id' not in session: return redirect('/login')
    usuario_id_actual = session['usuario_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT fecha, servicio_nombre FROM turnos WHERE usuario_id = ?", (usuario_id_actual,))
    todos_los_turnos = cursor.fetchall()
    conn.close()
    turnos_por_mes = Counter(turno['fecha'][:7] for turno in todos_los_turnos)
    turnos_por_mes_ordenado = sorted(turnos_por_mes.items())
    contador_servicios = Counter(turno['servicio_nombre'] for turno in todos_los_turnos)
    servicios_populares = contador_servicios.most_common()
    return render_template('estadisticas.html', turnos_mes=turnos_por_mes_ordenado, servicios_pop=servicios_populares)

@app.route('/turno/borrar', methods=['POST'])
def borrar_turno():
    if 'usuario_id' not in session: return redirect('/login')

    turno_id = request.form['turno_id']
    fecha_turno = request.form['fecha_turno']
    usuario_id_actual = session['usuario_id']

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM turnos WHERE id = ? AND usuario_id = ?", (turno_id, usuario_id_actual))
    conn.commit()
    conn.close()

    flash("Turno cancelado con éxito.", "success")
    
    # --- LA LÍNEA CORREGIDA ---
    # Le agregamos un parámetro inútil pero único para engañar a la caché
    cache_buster = int(time.time())
    fecha_dt = datetime.strptime(fecha_turno, '%Y-%m-%d')
    return redirect(f'/calendario/{fecha_dt.year}/{fecha_dt.month}?v={cache_buster}')

if __name__ == '__main__':
    # init_db() # Este archivo no debe existir en el app.py final
    app.run(debug=True)