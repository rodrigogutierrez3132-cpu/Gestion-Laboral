import sqlite3
import os
import hashlib
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Form, File, UploadFile, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText

app = FastAPI()

# Si estás usando el disco persistente en Render (/data), usamos la ruta segura.
# Si estás probando en tu computador local, usa la carpeta local.
DB_NAME = "/data/gestion_laboral.db" if os.path.exists("/data") else "gestion_laboral.db"
UPLOAD_DIR = "/data/uploads" if os.path.exists("/data") else "uploads"

# ==========================================
# CONFIGURACIÓN DEL SERVIDOR DE CORREO (SMTP)
# ==========================================
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "gygrrhh35@gmail.com"
SMTP_PASSWORD = "sacxkwazjvdgdwgn"
EMAIL_SENDER_NAME = "Recursos Humanos / Sistema de Gestión Laboral"

def enviar_correo(destinatario: str, asunto: str, cuerpo_html: str):
    if not destinatario or "@" not in destinatario:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{EMAIL_SENDER_NAME} <{SMTP_USER}>"
        msg["To"] = destinatario
        msg["Subject"] = asunto
        msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, destinatario, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"[ERROR CORREO]: {e}")
        return False

# ==========================================
# BASE DE DATOS E INICIALIZACIÓN
# ==========================================
def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rut TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            email TEXT,
            clave TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rut_trabajador TEXT NOT NULL,
            tipo_documento TEXT NOT NULL,
            nombre_archivo TEXT NOT NULL,
            ruta_archivo TEXT NOT NULL,
            origen TEXT NOT NULL,
            fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            anulado INTEGER DEFAULT 0,
            motivo_anulacion TEXT,
            fecha_anulacion TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS firmas_documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER NOT NULL,
            rut_trabajador TEXT NOT NULL,
            fecha_firma TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_origen TEXT,
            codigo_verificacion TEXT,
            estado INTEGER DEFAULT 1,
            FOREIGN KEY(documento_id) REFERENCES documentos(id)
        )
    """)
    
    try:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN email TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE documentos ADD COLUMN anulado INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE documentos ADD COLUMN motivo_anulacion TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE documentos ADD COLUMN fecha_anulacion TIMESTAMP")
    except sqlite3.OperationalError:
        pass

    cursor.execute("SELECT * FROM usuarios WHERE rut = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO usuarios (rut, nombre, email, clave, rol) VALUES ('admin', 'Administrador RRHH', 'gygrrhh35@gmail.com', 'admin123', 'admin')")
    
    conn.commit()
    conn.close()

init_db()

def obtener_hora_chile():
    utc_now = datetime.now(timezone.utc)
    chile_time = utc_now - timedelta(hours=3)
    return chile_time.replace(tzinfo=None)

def agregar_sello_firma_y_anulado(ruta_pdf, motivo_anulado=None, fecha_anulacion=None, nombre_firmante=None, rut_firmante=None, fecha_firma=None, codigo_verificacion=None):
    reader = PdfReader(ruta_pdf)
    writer = PdfWriter()

    for index, page in enumerate(reader.pages):
        writer.add_page(page)
        page_num = len(writer.pages) - 1

        if nombre_firmante and rut_firmante and fecha_firma and codigo_verificacion:
            fecha_str = str(fecha_firma)[:19]
            texto_firma = (
                f"FIRMADO DIGITALMENTE POR: {nombre_firmante} (RUT: {rut_firmante})\n"
                f"FECHA DE FIRMA: {fecha_str}\n"
                f"CÓDIGO DE VERIFICACIÓN: FIRMA-{codigo_verificacion}"
            )
            anotacion_firma = FreeText(
                text=texto_firma,
                rect=(50, 50, 450, 110),
                font_size="8pt",
                font_color="1B4F72",
                border_color="2E86C1",
                background_color="EBF5FB"
            )
            writer.add_annotation(page_number=page_num, annotation=anotacion_firma)

        if motivo_anulado:
            fecha_anul_str = str(fecha_anulacion)[:19] if fecha_anulacion else ""
            texto_sello = f"DOCUMENTO ANULADO\nFECHA: {fecha_anul_str}\nMOTIVO: {motivo_anulado}"
            anotacion_anulado = FreeText(
                text=texto_sello,
                rect=(320, 250, 570, 600),
                font_size="24pt",
                font_color="D9534F",
                border_color="D9534F",
                background_color="FDF2F2"
            )
            writer.add_annotation(page_number=page_num, annotation=anotacion_anulado)

    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer

@app.get("/eliminar-documento-dt/{id_doc}", response_class=HTMLResponse)
def eliminar_documento_dt_get(id_doc: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT ruta_archivo FROM documentos WHERE id = ?", (id_doc,))
        doc = cursor.fetchone()
        if doc and os.path.exists(doc['ruta_archivo']):
            os.remove(doc['ruta_archivo'])
        cursor.execute("DELETE FROM firmas_documentos WHERE documento_id = ?", (id_doc,))
        cursor.execute("DELETE FROM documentos WHERE id = ?", (id_doc,))
        conn.commit()
        return render_admin_dashboard("Documento eliminado correctamente")
    except Exception as e:
        return render_admin_dashboard(f"Error al eliminar documento: {str(e)}")
    finally:
        conn.close()

@app.get("/eliminar-documento-trabajador/{id_doc}", response_class=HTMLResponse)
def eliminar_documento_trabajador_get(id_doc: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT ruta_archivo FROM documentos WHERE id = ?", (id_doc,))
        doc = cursor.fetchone()
        if doc and os.path.exists(doc['ruta_archivo']):
            os.remove(doc['ruta_archivo'])
        cursor.execute("DELETE FROM documentos WHERE id = ?", (id_doc,))
        conn.commit()
        return render_admin_dashboard("Documento subido por trabajador eliminado correctamente")
    except Exception as e:
        return render_admin_dashboard(f"Error al eliminar documento: {str(e)}")
    finally:
        conn.close()

def render_worker_dashboard(user, mensaje=""):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT d.*, f.fecha_firma, f.codigo_verificacion, f.estado 
            FROM documentos d 
            LEFT JOIN firmas_documentos f ON d.id = f.documento_id 
            WHERE d.rut_trabajador = ? AND d.origen = 'admin'
            ORDER BY d.id DESC
        """, (user['rut'],))
        docs_pendientes = cursor.fetchall()

        cursor.execute("""
            SELECT * FROM documentos 
            WHERE rut_trabajador = ? AND origen = 'trabajador'
            ORDER BY id DESC
        """, (user['rut'],))
        mis_subidas = cursor.fetchall()
    finally:
        conn.close()

    filas_por_firmar = ""
    for d in docs_pendientes:
        if d['anulado']:
            estado = f"<span class='badge bg-danger'>Anulado</span><br><small class='text-muted'>Motivo: {d['motivo_anulacion']}</small>"
            accion = f"<a href='/descargar/{d['id']}' class='btn btn-sm btn-outline-danger' target='_blank'>Ver Documento Anulado</a>"
        elif d['estado']:
            estado = f"<span class='badge bg-success'>Firmado ({str(d['fecha_firma'])[:16]})</span>"
            accion = f"<a href='/descargar/{d['id']}' class='btn btn-sm btn-outline-primary' target='_blank'>Ver Documento Firmado</a>"
        else:
            estado = "<span class='badge bg-warning text-dark'>Pendiente de Firma</span>"
            accion = f"""
                <form action='/firmar-documento/{d['id']}' method='post' class='d-inline'>
                    <input type='hidden' name='rut_trabajador' value='{user['rut']}'>
                    <button type='submit' class='btn btn-sm btn-success' onclick='return confirm("¿Declara haber leído y firmado electrónicamente este documento?")'>Firmar Documento</button>
                </form>
                <a href='/descargar/{d['id']}' class='btn btn-sm btn-outline-secondary ms-1' target='_blank'>Revisar PDF</a>
            """

        filas_por_firmar += f"""
        <tr>
            <td>{d['tipo_documento']}</td>
            <td>{d['nombre_archivo']}</td>
            <td>{estado}</td>
            <td>{accion}</td>
        </tr>
        """

    filas_subidas = ""
    for m in mis_subidas:
        fecha_f = str(m['fecha_subida'])[:16] if m['fecha_subida'] else ""
        filas_subidas += f"""
        <tr>
            <td>{m['tipo_documento']}</td>
            <td>{m['nombre_archivo']}</td>
            <td>{fecha_f}</td>
            <td><a href='/descargar/{m['id']}' class='btn btn-sm btn-outline-primary' target='_blank'>Ver Archivo</a></td>
        </tr>
        """

    alerta = f"<script>alert('{mensaje}');</script>" if mensaje else ""

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <title>Portal del Trabajador</title>
    </head>
    <body class="bg-light p-4">
        {alerta}
        <div class="container bg-white p-4 rounded shadow mb-4">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <div>
                    <h3 class="text-primary mb-0">Bienvenido(a), {user['nombre']}</h3>
                    <small class="text-muted">RUT: {user['rut']} | Correo: {user['email'] or 'Sin registrar'}</small>
                </div>
                <div>
                    <button class="btn btn-outline-secondary btn-sm me-2" data-bs-toggle="modal" data-bs-target="#modalClave">Cambiar Contraseña</button>
                    <a href="/" class="btn btn-outline-danger btn-sm">Cerrar Sesión</a>
                </div>
            </div>
            <hr>

            <div class="modal fade" id="modalClave" tabindex="-1" aria-hidden="true">
              <div class="modal-dialog">
                <div class="modal-content">
                  <div class="modal-header">
                    <h5 class="modal-title fw-bold">Actualizar Mi Contraseña</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                  </div>
                  <form action="/cambiar-clave-trabajador" method="post">
                      <div class="modal-body">
                        <input type="hidden" name="rut_trabajador" value="{user['rut']}">
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Nueva Contraseña</label>
                            <input type="password" name="nueva_clave" class="form-control" placeholder="Ingrese su nueva contraseña" required>
                        </div>
                      </div>
                      <div class="modal-footer">
                        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Cancelar</button>
                        <button type="submit" class="btn btn-primary btn-sm">Guardar Nueva Contraseña</button>
                      </div>
                  </form>
                </div>
              </div>
            </div>

            <div class="card mb-4 border-light shadow-sm">
                <div class="card-body">
                    <h5 class="fw-bold text-secondary mb-3">Cargar Nuevo Documento Personal</h5>
                    <form action="/subir-documento-trabajador" method="post" enctype="multipart/form-data" class="row g-2">
                        <input type="hidden" name="rut_trabajador" value="{user['rut']}">
                        <div class="col-md-5">
                            <label class="form-label small">Detalle / Tipo de Documento</label>
                            <select name="tipo_documento" class="form-select form-select-sm" required>
                                <option value="Carnet">Carnet</option>
                                <option value="Certificado de Antecedentes">Certificado de Antecedentes</option>
                                <option value="Certificado Médico">Certificado Médico</option>
                                <option value="Currículum">Currículum</option>
                                <option value="Certificado AFP">Certificado AFP</option>
                                <option value="Certificado Fonasa">Certificado Fonasa</option>
                                <option value="Certificado OS-10">Certificado OS-10</option>
                                <option value="Otro Documento">Otro Documento</option>
                            </select>
                        </div>
                        <div class="col-md-5">
                            <label class="form-label small">Archivo (PDF o Imagen)</label>
                            <input type="file" name="archivo" class="form-control form-control-sm" required>
                        </div>
                        <div class="col-md-2 d-flex align-items-end">
                            <button type="submit" class="btn btn-sm btn-primary w-100">Subir Archivo</button>
                        </div>
                    </form>
                </div>
            </div>

            <h5 class="fw-bold text-secondary mb-3">Documentos y Contratos Emitidos por RRHH</h5>
            <table class="table table-striped table-sm align-middle mb-4">
                <thead><tr><th>Tipo Documento</th><th>Archivo</th><th>Estado</th><th>Acción</th></tr></thead>
                <tbody>{filas_por_firmar if filas_por_firmar else '<tr><td colspan="4" class="text-center text-muted">No tienes documentos pendientes de firma.</td></tr>'}</tbody>
            </table>

            <h5 class="fw-bold text-secondary mb-3">Mis Documentos Cargados</h5>
            <table class="table table-striped table-sm align-middle">
                <thead><tr><th>Detalle</th><th>Archivo</th><th>Fecha Carga</th><th>Acción</th></tr></thead>
                <tbody>{filas_subidas if filas_subidas else '<tr><td colspan="4" class="text-center text-muted">No has subido documentos adicionales aún.</td></tr>'}</tbody>
            </table>
        </div>
    </body>
    </html>
    """

def render_admin_dashboard(mensaje=""):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM usuarios WHERE rol = 'trabajador' ORDER BY id DESC")
        trabajadores = cursor.fetchall()
        
        cursor.execute("""
            SELECT d.*, f.fecha_firma, f.ip_origen, f.estado, f.codigo_verificacion 
            FROM documentos d 
            LEFT JOIN firmas_documentos f ON d.id = f.documento_id 
            WHERE d.origen = 'admin'
            ORDER BY d.id DESC
        """)
        docs_admin = cursor.fetchall()

        cursor.execute("""
            SELECT * FROM documentos 
            WHERE origen = 'trabajador' 
            ORDER BY id DESC
        """)
        docs_trabajador = cursor.fetchall()
    finally:
        conn.close()

    filas_trabajadores = ""
    for t in trabajadores:
        email_val = t['email'] if t['email'] else ''
        rut_t = t['rut']
        nombre_t = t['nombre']
        
        filas_trabajadores += (
            "<tr>"
            f"<td>{rut_t}</td>"
            f"<td>{nombre_t}</td>"
            f"<td>{email_val}</td>"
            f"<td><code>{t['clave']}</code></td>"
            f"<td style='white-space: nowrap;'>"
            f"<button class='btn btn-sm btn-outline-warning me-1' data-bs-toggle='modal' data-bs-target='#editModal{t['id']}'>Editar</button>"
            f"<a href='/eliminar-trabajador/{t['id']}' class='btn btn-sm btn-outline-danger' onclick='return confirm(\"¿Eliminar este trabajador?\")'>Eliminar</a>"
            "</td>"
            "</tr>"
            f"""
            <div class="modal fade" id="editModal{t['id']}" tabindex="-1" aria-hidden="true">
              <div class="modal-dialog">
                <div class="modal-content">
                  <div class="modal-header">
                    <h5 class="modal-title fw-bold">Modificar Trabajador (RUT: {rut_t})</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                  </div>
                  <form action="/editar-trabajador/{t['id']}" method="post">
                      <div class="modal-body">
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Nombre Completo</label>
                            <input type="text" name="nombre" class="form-control" value="{nombre_t}" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Correo Electrónico</label>
                            <input type="email" name="email" class="form-control" value="{email_val}" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Nueva Contraseña (Restablecer)</label>
                            <input type="text" name="clave" class="form-control" value="{t['clave']}" required>
                        </div>
                      </div>
                      <div class="modal-footer">
                        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Cancelar</button>
                        <button type="submit" class="btn btn-primary btn-sm">Guardar Cambios</button>
                      </div>
                  </form>
                </div>
              </div>
            </div>
            """
        )

    filas_docs_admin = ""
    for d in docs_admin:
        doc_id = str(d['id'])
        if d['anulado']:
            estado_firma = f"<span class='badge bg-danger'>Anulado</span><br><small class='text-muted'>Motivo: {d['motivo_anulacion'] or ''}</small>"
            btn_ver = f"<a href='/descargar/{doc_id}' class='btn btn-sm btn-outline-danger' target='_blank'>Ver PDF Anulado</a>"
            btn_accion_anular = ""
        elif d['estado']:
            fecha_fmt = str(d['fecha_firma'])[:16] if d['fecha_firma'] else ""
            estado_firma = f"<span class='badge bg-success'>Firmado ({fecha_fmt})<br><small>Cód: {d['codigo_verificacion']}</small></span>"
            btn_ver = f"<a href='/descargar/{doc_id}' class='btn btn-sm btn-outline-primary' target='_blank'>Ver PDF</a>"
            btn_accion_anular = f"<button class='btn btn-sm btn-outline-danger ms-1' data-bs-toggle='modal' data-bs-target='#anularModal{doc_id}'>Anular</button>"
        else:
            estado_firma = "<span class='badge bg-warning text-dark'>Pendiente de Firma</span>"
            btn_ver = f"<a href='/descargar/{doc_id}' class='btn btn-sm btn-outline-primary' target='_blank'>Ver PDF</a>"
            btn_accion_anular = f"<button class='btn btn-sm btn-outline-danger ms-1' data-bs-toggle='modal' data-bs-target='#anularModal{doc_id}'>Anular</button>"

        btn_eliminar_dt = f"<a href='/eliminar-documento-dt/{doc_id}' class='btn btn-sm btn-danger ms-1' onclick='return confirm(\"¿Estás seguro de eliminar este documento?\")'>Eliminar</a>"

        filas_docs_admin += (
            "<tr>"
            f"<td>{d['rut_trabajador']}</td>"
            f"<td>{d['tipo_documento']}</td>"
            f"<td>{d['nombre_archivo']}</td>"
            f"<td>{estado_firma}</td>"
            f"<td>{btn_ver} {btn_accion_anular} {btn_eliminar_dt}</td>"
            "</tr>"
            f"""
            <div class="modal fade" id="anularModal{doc_id}" tabindex="-1" aria-hidden="true">
              <div class="modal-dialog">
                <div class="modal-content">
                  <div class="modal-header bg-danger text-white">
                    <h5 class="modal-title fw-bold">Anular Documento</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                  </div>
                  <form action="/anular-documento/{doc_id}" method="post">
                      <div class="modal-body">
                        <p class="small text-secondary">Indique la razón por la cual se anula este documento:</p>
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Motivo de Anulación</label>
                            <textarea name="motivo" class="form-control" rows="3" placeholder="Escriba el motivo..." required></textarea>
                        </div>
                      </div>
                      <div class="modal-footer">
                        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Cancelar</button>
                        <button type="submit" class="btn btn-danger btn-sm">Confirmar Anulación</button>
                      </div>
                  </form>
                </div>
              </div>
            </div>
            """
        )

    filas_docs_trabajador = ""
    for d in docs_trabajador:
        fecha_sub = str(d['fecha_subida'])[:16] if d['fecha_subida'] else ""
        filas_docs_trabajador += f"""
        <tr>
            <td>{d['rut_trabajador']}</td>
            <td>{d['tipo_documento']}</td>
            <td>{d['nombre_archivo']}</td>
            <td>{fecha_sub}</td>
            <td>
                <a href='/descargar/{d['id']}' class='btn btn-sm btn-outline-primary me-1' target='_blank'>Descargar / Ver</a>
                <a href='/eliminar-documento-trabajador/{d['id']}' class='btn btn-sm btn-danger' onclick='return confirm(\"¿Estás seguro de eliminar este documento?\")'>Eliminar</a>
            </td>
        </tr>
        """

    alerta = f"<script>alert('{mensaje}');</script>" if mensaje else ""

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
        <script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
        <title>Panel Administrador</title>
    </head>
    <body class="bg-light p-4">
        {alerta}
        <div class="container bg-white p-4 rounded shadow mb-4">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h2 class="text-primary mb-0">Panel Administrador (RRHH)</h2>
                <a href="/" class="btn btn-outline-danger">Cerrar Sesión</a>
            </div>
            <hr>
            <div class="row">
                <div class="col-md-6 border-end">
                    <h5 class="fw-bold mb-3 text-secondary">Registrar Nuevo Trabajador</h5>
                    <form action="/crear-trabajador" method="post" class="row g-2">
                        <div class="col-12">
                            <label class="form-label small">RUT</label>
                            <input type="text" name="rut" class="form-control form-control-sm" placeholder="12345678-k" required>
                        </div>
                        <div class="col-12">
                            <label class="form-label small">Nombre Completo</label>
                            <input type="text" name="nombre" class="form-control form-control-sm" placeholder="Juan Pérez" required>
                        </div>
                        <div class="col-12">
                            <label class="form-label small">Correo Electrónico</label>
                            <input type="email" name="email" class="form-control form-control-sm" placeholder="ejemplo@correo.com" required>
                        </div>
                        <div class="col-12">
                            <label class="form-label small">Contraseña Inicial</label>
                            <input type="password" name="clave" class="form-control form-control-sm" placeholder="......" required>
                        </div>
                        <div class="col-12 mt-3">
                            <button type="submit" class="btn btn-sm btn-primary w-100">Crear Trabajador</button>
                        </div>
                    </form>
                </div>
                <div class="col-md-6">
                    <h5 class="fw-bold mb-3 text-secondary">Enviar Documento a Firmar</h5>
                    <form action="/subir-documento-admin" method="post" enctype="multipart/form-data" class="row g-2">
                        <div class="col-12">
                            <label class="form-label small">RUT del Trabajador Destinatario</label>
                            <input type="text" name="rut_trabajador" class="form-control form-control-sm" placeholder="22222222-2" required>
                        </div>
                        <div class="col-12">
                            <label class="form-label small">Tipo de Documento</label>
                            <select name="tipo_documento" class="form-select form-select-sm" required>
                                <option value="Contrato de Trabajo">Contrato de Trabajo</option>
                                <option value="Anexo de Contrato">Anexo de Contrato</option>
                                <option value="Reglamento Interno">Reglamento Interno</option>
                                <option value="Charla DAS (Derecho a Saber)">Charla DAS (Derecho a Saber)</option>
                                <option value="Entrega de EPP">Entrega de EPP</option>
                                <option value="Liquidación de Sueldo">Liquidación de Sueldo</option>
                                <option value="Carnet">Carnet</option>
                                <option value="Certificado de Antecedentes">Certificado de Antecedentes</option>
                                <option value="Certificado Médico">Certificado Médico</option>
                                <option value="Currículum">Currículum</option>
                                <option value="Certificado AFP">Certificado AFP</option>
                                <option value="Certificado Fonasa">Certificado Fonasa</option>
                                <option value="Certificado OS-10">Certificado OS-10</option>
                                <option value="Otro Documento">Otro Documento</option>
                            </select>
                        </div>
                        <div class="col-12">
                            <label class="form-label small">Archivo PDF</label>
                            <input type="file" name="archivo" accept=".pdf" class="form-control form-control-sm" required>
                        </div>
                        <div class="col-12 mt-3">
                            <button type="submit" class="btn btn-sm btn-success w-100">Enviar Documento a Trabajador</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <div class="container bg-white p-4 rounded shadow mb-4">
            <h5 class="fw-bold text-secondary mb-3">Nómina de Trabajadores</h5>
            <table id="tablaTrabajadores" class="table table-striped table-sm align-middle">
                <thead><tr><th>RUT</th><th>Nombre</th><th>Correo</th><th>Clave Actual</th><th>Acciones</th></tr></thead>
                <tbody>{filas_trabajadores}</tbody>
            </table>
        </div>

        <div class="container bg-white p-4 rounded shadow mb-4">
            <h5 class="fw-bold text-secondary mb-3">Control de Documentos Enviados a Firmar (DT)</h5>
            <table id="tablaDocsAdmin" class="table table-striped table-sm align-middle">
                <thead><tr><th>RUT Trabajador</th><th>Tipo</th><th>Nombre Archivo</th><th>Estado Firma</th><th>Acciones</th></tr></thead>
                <tbody>{filas_docs_admin}</tbody>
            </table>
        </div>

        <div class="container bg-white p-4 rounded shadow">
            <h5 class="fw-bold text-secondary mb-3">Documentos Subidos Libremente por los Trabajadores</h5>
            <table class="table table-striped table-sm align-middle">
                <thead><tr><th>RUT Trabajador</th><th>Detalle / Tipo</th><th>Nombre Archivo</th><th>Fecha Subida</th><th>Acción</th></tr></thead>
                <tbody>{filas_docs_trabajador if filas_docs_trabajador else '<tr><td colspan="5" class="text-center">Ningún trabajador ha subido documentos aún.</td></tr>'}</tbody>
            </table>
        </div>
        <script>
            $(document).ready(function() {{
                $('#tablaTrabajadores').DataTable({{ language: {{ url: '//cdn.datatables.net/plug-ins/1.13.6/i18n/es-CL.json' }} }});
                $('#tablaDocsAdmin').DataTable({{ language: {{ url: '//cdn.datatables.net/plug-ins/1.13.6/i18n/es-CL.json' }} }});
            }});
        </script>
    </body>
    </html>
    """

@app.get("/", response_class=HTMLResponse)
def login_page():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <title>Ingreso al Sistema</title>
    </head>
    <body class="bg-light d-flex align-items-center justify-content-center vh-100">
        <div class="card p-4 shadow" style="width: 350px;">
            <h4 class="text-center mb-4 text-primary fw-bold">Gestión Laboral</h4>
            <form action="/login" method="post">
                <div class="mb-3">
                    <label class="form-label small">RUT</label>
                    <input type="text" name="rut" class="form-control" placeholder="admin o RUT trabajador" required>
                </div>
                <div class="mb-3">
                    <label class="form-label small">Contraseña</label>
                    <input type="password" name="clave" class="form-control" required>
                </div>
                <button type="submit" class="btn btn-primary w-100">Ingresar</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/login", response_class=HTMLResponse)
def login(rut: str = Form(...), clave: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM usuarios WHERE rut = ? AND clave = ?", (rut, clave))
        user = cursor.fetchone()
    finally:
        conn.close()

    if not user:
        return "<script>alert('Credenciales incorrectas'); window.location.href='/';</script>"
    if user['rol'] == 'admin':
        return render_admin_dashboard()
    else:
        return render_worker_dashboard(user)

@app.post("/crear-trabajador", response_class=HTMLResponse)
def crear_trabajador(rut: str = Form(...), nombre: str = Form(...), email: str = Form(...), clave: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO usuarios (rut, nombre, email, clave, rol) VALUES (?, ?, ?, ?, 'trabajador')", (rut, nombre, email, clave))
        conn.commit()
        return render_admin_dashboard("Trabajador creado exitosamente")
    except Exception as e:
        return render_admin_dashboard(f"Error al crear trabajador: {str(e)}")
    finally:
        conn.close()

@app.get("/eliminar-trabajador/{user_id}", response_class=HTMLResponse)
def eliminar_trabajador(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
        conn.commit()
        return render_admin_dashboard("Trabajador eliminado exitosamente")
    except Exception as e:
        return render_admin_dashboard(f"Error al eliminar trabajador: {str(e)}")
    finally:
        conn.close()

@app.post("/editar-trabajador/{user_id}", response_class=HTMLResponse)
def editar_trabajador(user_id: int, nombre: str = Form(...), email: str = Form(...), clave: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE usuarios SET nombre = ?, email = ?, clave = ? WHERE id = ?", (nombre, email, clave, user_id))
        conn.commit()
        return render_admin_dashboard("Datos y/o contraseña de trabajador actualizados")
    except Exception as e:
        return render_admin_dashboard(f"Error al actualizar trabajador: {str(e)}")
    finally:
        conn.close()

@app.post("/cambiar-clave-trabajador", response_class=HTMLResponse)
def cambiar_clave_trabajador(rut_trabajador: str = Form(...), nueva_clave: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE usuarios SET clave = ? WHERE rut = ?", (nueva_clave, rut_trabajador))
        conn.commit()
        cursor.execute("SELECT * FROM usuarios WHERE rut = ?", (rut_trabajador,))
        user = cursor.fetchone()
    finally:
        conn.close()
    return render_worker_dashboard(user, "Su contraseña ha sido actualizada con éxito")

@app.post("/subir-documento-admin", response_class=HTMLResponse)
async def subir_documento_admin(rut_trabajador: str = Form(...), tipo_documento: str = Form(...), archivo: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ruta_destino = os.path.join(UPLOAD_DIR, archivo.filename)
    contenido = await archivo.read()
    with open(ruta_destino, "wb") as f:
        f.write(contenido)
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO documentos (rut_trabajador, tipo_documento, nombre_archivo, ruta_archivo, origen)
            VALUES (?, ?, ?, ?, 'admin')
        """, (rut_trabajador, tipo_documento, archivo.filename, ruta_destino))
        conn.commit()
        
        cursor.execute("SELECT * FROM usuarios WHERE rut = ?", (rut_trabajador,))
        trabajador = cursor.fetchone()
    finally:
        conn.close()
    
    if trabajador and trabajador['email']:
        asunto = f"Nuevo documento pendiente de firma: {tipo_documento}"
        cuerpo = f"""
        <h2>Hola {trabajador['nombre']},</h2>
        <p>Se ha publicado un nuevo documento en tu portal de trabajador que requiere tu firma electrónica:</p>
        <ul>
            <li><b>Tipo de documento:</b> {tipo_documento}</li>
            <li><b>Nombre de archivo:</b> {archivo.filename}</li>
        </ul>
        <p>Por favor ingresa a tu portal laboral con tu RUT y contraseña para revisar y firmar el documento.</p>
        <hr>
        <small>Este es un correo automático, por favor no responder a esta dirección.</small>
        """
        enviar_correo(trabajador['email'], asunto, cuerpo)
        
    return render_admin_dashboard("Documento subido y notificación enviada al trabajador")

@app.post("/subir-documento-trabajador", response_class=HTMLResponse)
async def subir_documento_trabajador(rut_trabajador: str = Form(...), tipo_documento: str = Form(...), archivo: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ruta_destino = os.path.join(UPLOAD_DIR, archivo.filename)
    contenido = await archivo.read()
    with open(ruta_destino, "wb") as f:
        f.write(contenido)
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO documentos (rut_trabajador, tipo_documento, nombre_archivo, ruta_archivo, origen)
            VALUES (?, ?, ?, ?, 'trabajador')
        """, (rut_trabajador, tipo_documento, archivo.filename, ruta_destino))
        conn.commit()
        cursor.execute("SELECT * FROM usuarios WHERE rut = ?", (rut_trabajador,))
        user = cursor.fetchone()
    finally:
        conn.close()
    return render_worker_dashboard(user, "Documento subido con éxito")

@app.post("/firmar-documento/{doc_id}", response_class=HTMLResponse)
def firmar_documento(doc_id: int, rut_trabajador: str = Form(...), request: Request = None):
    ip_origen = request.client.host if request else "127.0.0.1"
    fecha_hora_actual = obtener_hora_chile()
    fecha_fmt = fecha_hora_actual.strftime("%d/%m/%Y %H:%M:%S")
    codigo_verificacion = hashlib.sha256(f"{doc_id}-{rut_trabajador}-{fecha_hora_actual}".encode()).hexdigest()[:12].upper()
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO firmas_documentos (documento_id, rut_trabajador, fecha_firma, ip_origen, codigo_verificacion, estado)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (doc_id, rut_trabajador, fecha_hora_actual, ip_origen, codigo_verificacion))
        conn.commit()
        
        cursor.execute("SELECT * FROM documentos WHERE id = ?", (doc_id,))
        doc = cursor.fetchone()
        cursor.execute("SELECT * FROM usuarios WHERE rut = ?", (rut_trabajador,))
        user = cursor.fetchone()
    finally:
        conn.close()
    
    if user and user['email']:
        asunto = f"Comprobante de Firma Electrónica - {doc['tipo_documento'] if doc else 'Documento'}"
        cuerpo = f"""
        <h2>Comprobante de Firma Electrónica</h2>
        <p>Estimado(a) <b>{user['nombre']}</b>,</p>
        <p>Confirmamos que has firmado electrónicamente de manera exitosa el siguiente documento laboral:</p>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
            <tr><td style="background-color: #f2f2f2;"><b>Documento:</b></td><td>{doc['tipo_documento'] if doc else 'N/A'}</td></tr>
            <tr><td style="background-color: #f2f2f2;"><b>Nombre Archivo:</b></td><td>{doc['nombre_archivo'] if doc else 'N/A'}</td></tr>
            <tr><td style="background-color: #f2f2f2;"><b>Fecha y Hora:</b></td><td>{fecha_fmt}</td></tr>
            <tr><td style="background-color: #f2f2f2;"><b>Dirección IP:</b></td><td>{ip_origen}</td></tr>
            <tr><td style="background-color: #f2f2f2;"><b>Código Único de Verificación:</b></td><td><b style="color: #0d6efd;">{codigo_verificacion}</b></td></tr>
        </table>
        <p><br>Puedes ingresar a tu portal de trabajador en cualquier momento para revisar o descargar una copia de este documento.</p>
        <hr>
        <small>Este es un correo generado automáticamente como respaldo de tu firma electrónica.</small>
        """
        enviar_correo(user['email'], asunto, cuerpo)
        
    return render_worker_dashboard(user, f"Documento firmado electrónicamente (Código: {codigo_verificacion}). Se ha enviado un comprobante a tu correo.")

@app.post("/anular-documento/{doc_id}", response_class=HTMLResponse)
def anular_documento(doc_id: int, motivo: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        fecha_anulacion = obtener_hora_chile()
        cursor.execute("""
            UPDATE documentos 
            SET anulado = 1, motivo_anulacion = ?, fecha_anulacion = ? 
            WHERE id = ?
        """, (motivo, fecha_anulacion, doc_id))
        conn.commit()
        return render_admin_dashboard("Documento anulado correctamente")
    except Exception as e:
        return render_admin_dashboard(f"Error al anular documento: {str(e)}")
    finally:
        conn.close()

@app.get("/descargar/{doc_id}")
def descargar_documento(doc_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT d.*, f.fecha_firma, f.codigo_verificacion, u.nombre as nombre_trabajador, u.rut as rut_trabajador_val
            FROM documentos d 
            LEFT JOIN firmas_documentos f ON d.id = f.documento_id 
            LEFT JOIN usuarios u ON d.rut_trabajador = u.rut
            WHERE d.id = ?
        """, (doc_id,))
        doc = cursor.fetchone()
    finally:
        conn.close()

    if doc and os.path.exists(doc['ruta_archivo']):
        buffer_pdf = agregar_sello_firma_y_anulado(
            doc['ruta_archivo'],
            motivo_anulado=doc['motivo_anulacion'] if doc['anulado'] else None,
            fecha_anulacion=doc['fecha_anulacion'] if doc['anulado'] else None,
            nombre_firmante=doc['nombre_trabajador'] if doc['codigo_verificacion'] else None,
            rut_firmante=doc['rut_trabajador_val'] if doc['codigo_verificacion'] else None,
            fecha_firma=doc['fecha_firma'] if doc['codigo_verificacion'] else None,
            codigo_verificacion=doc['codigo_verificacion'] if doc['codigo_verificacion'] else None
        )
        return StreamingResponse(
            buffer_pdf, 
            media_type="application/pdf", 
            headers={"Content-Disposition": f"inline; filename=firmado_{doc['nombre_archivo']}"}
        )
    return HTMLResponse("Archivo no encontrado", status_code=404)