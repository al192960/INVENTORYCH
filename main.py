"""
Etiquetas Zebra ZQ630 (4x2 in, 203 dpi) - Kivy + pyjnius

Modos:
  BIN               QR = material TAB um TAB bin TAB qty TAB TAB TAB TAB
  STORAGE LOCATION  QR = material TAB um TAB qty TAB TAB TAB TAB   (sin bin)
"""
import csv
import hashlib
import hmac
import io
import os
import sqlite3
import threading
from datetime import datetime

from kivy.app import App
from kivy.clock import mainthread
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

import config

Window.softinput_mode = "below_target"

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"
MODO_BIN = "BIN"
MODO_SL = "STORAGE LOCATION"


# ----------------------------------------------------------------- Base de datos
class DB:
    def __init__(self, path):
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS materiales ("
            "material TEXT PRIMARY KEY, descripcion TEXT, unidad TEXT)"
        )
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)"
        )
        # Materiales dados de alta desde la app (no se borran al recargar el CSV)
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS materiales_extra ("
            "material TEXT PRIMARY KEY, descripcion TEXT, unidad TEXT)"
        )
        self.con.commit()

    # --- meta
    def get_meta(self, clave, defecto=None):
        fila = self.con.execute(
            "SELECT valor FROM meta WHERE clave=?", (clave,)
        ).fetchone()
        return fila[0] if fila else defecto

    def set_meta(self, clave, valor):
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (clave, valor))
        self.con.commit()

    # --- catalogo
    def cargar_incluido(self, ruta):
        """Carga el CSV empaquetado en el APK. Si cambio desde la ultima vez
        (APK nuevo con CSV nuevo), reemplaza el catalogo."""
        if not os.path.exists(ruta):
            return None
        with open(ruta, "rb") as f:
            huella = hashlib.md5(f.read()).hexdigest()
        if self.get_meta("csv_hash") == huella:
            return 0
        try:
            self.con.execute("DELETE FROM materiales")
            n = self.importar_csv(ruta)
        except Exception:
            self.con.rollback()  # conserva el catalogo anterior
            raise
        self.set_meta("csv_hash", huella)
        return n

    def importar_csv(self, ruta):
        """CSV con encabezado: material,unidad[,descripcion]"""
        with open(ruta, "rb") as f:
            crudo = f.read()
        # UTF-8 (con o sin BOM) y, si no, Windows-1252 (Excel "CSV delimitado por comas")
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texto = crudo.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        # Excel en espanol suele separar con ";" en vez de ","
        primera = texto.splitlines()[0] if texto.strip() else ""
        delim = max((",", ";", "\t"), key=primera.count)

        n = 0
        for r in csv.DictReader(io.StringIO(texto, newline=""), delimiter=delim):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if not r.get("material"):
                continue
            self.con.execute(
                "INSERT OR REPLACE INTO materiales VALUES (?,?,?)",
                (r["material"], r.get("descripcion", ""), r.get("unidad", "")),
            )
            n += 1
        self.con.commit()
        return n

    def buscar(self, material):
        """Devuelve (descripcion, unidad) o None si no existe."""
        for tabla in ("materiales", "materiales_extra"):
            fila = self.con.execute(
                f"SELECT descripcion, unidad FROM {tabla} WHERE material=?",
                (material,),
            ).fetchone()
            if fila:
                return fila
        return None

    def agregar(self, material, descripcion, unidad):
        self.con.execute(
            "INSERT OR REPLACE INTO materiales_extra VALUES (?,?,?)",
            (material, descripcion, unidad),
        )
        self.con.commit()

    def exportar_extra_csv(self):
        """Materiales agregados desde la app, como texto CSV (o None si no hay)."""
        filas = self.con.execute(
            "SELECT material, unidad, descripcion FROM materiales_extra ORDER BY material"
        ).fetchall()
        if not filas:
            return None, 0
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["material", "unidad", "descripcion"])
        w.writerows(filas)
        return buf.getvalue(), len(filas)


# ----------------------------------------------------------------------- ZPL
def limpiar(s):
    s = str(s)
    for c in ("^", "~", "\t", "\r", "\n"):
        s = s.replace(c, " ")
    return s.strip()


def _hex(s):
    """Escapa un campo para ^FH (el indicador '_' se escribe como _5F)."""
    return str(s).replace("_", "_5F")


def datos_qr(modo, m, u, b, q):
    """Campos separados por TAB y cuatro TAB finales."""
    partes = [m, u, b, q] if modo == MODO_BIN else [m, u, q]
    return "_09".join(_hex(p) for p in partes) + "_09" * 4


def _fuente(texto):
    """Altura de fuente según la longitud del dato."""
    n = len(texto)
    return 48 if n <= 14 else 36 if n <= 18 else 28


def zpl_etiqueta_qr(modo, m, d, q, u, b, fecha, copias):
    """
    Etiqueta 4x2: datos a la izquierda y QR a la derecha.
    Fuente 0: CG Triumvirate Bold Condensed.
    Descripción de 26 puntos de impresora, aproximadamente 9.2 pt.
    """
    fm, fq, fb = _fuente(m), _fuente(f"{q} {u}"), min(34, _fuente(b))

    z = [
        "^XA",
        "^CI28",
        "^MNM",
        "^MMT",
        "^PW812",
        "^LL406",
        "^LH0,0",
        "^LS0",
        "^LT0",
        "^PON",
        "^PMN",

        "^FO28,35^A0N,18,18^FDMATERIAL^FS",
        (
            f"^FO28,60^A0N,{fm},{min(fm, max(12, 454 // max(1, len(m))))}"
            f"^FH_^FD{_hex(m)}^FS"
        ),
        f"^FO28,110^A0N,26,26^FB454,2,4,L,0^FH_^FD{_hex(d)}^FS",

        (
            f"^FO28,{173 if modo == MODO_BIN else 187}"
            "^A0N,18,18^FDCANTIDAD^FS"
        ),
        (
            f"^FO28,{197 if modo == MODO_BIN else 217}"
            f"^A0N,{fq},{min(fq, max(12, 454 // max(1, len(f'{q} {u}'))))}"
            f"^FH_^FD{_hex(f'{q} {u}')}^FS"
        ),
    ]

    if modo == MODO_BIN:
        z += [
            "^FO28,255^A0N,18,18^FDBIN CODE^FS",
            (
                f"^FO28,283^A0N,{fb},{min(fb, max(12, 454 // max(1, len(b))))}"
                f"^FH_^FD{_hex(b)}^FS"
            ),
        ]

    z += [
        f"^FO28,361^A0N,18,18^FH_^FDFecha: {_hex(fecha)}^FS",
        (
            "^FO374,361^A0N,18,18^FDModo: "
            f"{'STORAGE BIN' if modo == MODO_BIN else 'STORAGE LOCATION'}^FS"
        ),
        f"^FO526,65^BQN,2,8^FH_^FDQA,{datos_qr(modo, m, u, b, q)}^FS",
        f"^PQ{copias},0,1,N",
        "^XZ",
    ]
    return "\n".join(z) + "\n"


def zpl_etiqueta_barcode(modo, m, d, q, u, b, fecha):
    """
    Etiqueta 4x2 con códigos Code 128 simples.
    By Storage Bin: MATERIAL/UOM arriba, BIN/QTY abajo.
    By SLOC: MATERIAL/UOM arriba, QTY abajo.
    """
    items = [("MATERIAL", m), ("UOM", u)]
    if modo == MODO_BIN:
        items += [("BIN", b), ("QTY", q)]
    else:
        items.append(("QTY", q))

    y0 = 34
    bloque = 139
    alto = 62

    z = [
        "^XA",
        "^CI28",
        "^MNM",
        "^MMT",
        "^PW812",
        "^LL406",
        "^LH0,0",
        "^LS0",
        "^LT0",
        "^PON",
        "^PMN",
    ]

    for i, (titulo, valor) in enumerate(items):
        y = y0 + (i // 2) * bloque
        x = 28 if i % 2 == 0 else 430
        ancho = 366 if i % 2 == 0 else 354

        if modo != MODO_BIN and titulo == "QTY":
            x = 28
            ancho = 756

        tamano = (
            36 if titulo == "MATERIAL"
            else 38 if titulo == "QTY"
            else 34 if titulo == "UOM"
            else 32
        )

        # Cota conservadora de ancho, incluyendo zonas de silencio.
        modulo = min(3, ancho // (11 * (len(valor) + 3) + 22))
        if modulo < 1:
            raise ValueError(
                f"{titulo}: el dato es demasiado largo para esta etiqueta."
            )

        z += [
            f"^FO{x},{y + 5}^A0N,17,17^FD{titulo}^FS",
            (
                f"^FO{x},{y + 27}"
                f"^A0N,{tamano},{min(tamano, max(10, ancho // max(1, len(valor))))}"
                f"^FH_^FD{_hex(valor)}^FS"
            ),
            (
                f"^FO{x + 10 * modulo},{y + 63}"
                f"^BY{modulo},3,{alto}"
                f"^BCN,{alto},N,N,N,A"
                f"^FH_^FD{_hex(valor)}^FS"
            ),
        ]

    z += [
        f"^FO28,309^A0N,26,26^FB756,1,0,L,0^FH_^FD{_hex(d)}^FS",
        f"^FO28,361^A0N,18,18^FH_^FDFecha: {_hex(fecha)}^FS",
        (
            "^FO374,361^A0N,18,18^FDModo: "
            f"{'STORAGE BIN' if modo == MODO_BIN else 'STORAGE LOCATION'}^FS"
        ),
        "^PQ1,0,1,N",
        "^XZ",
    ]
    return "\n".join(z) + "\n"


def generar_zpl(modo, material, desc, qty, unidad, bin_):
    """Genera dos etiquetas QR y una etiqueta de códigos de barras."""
    m, d, q, u, b = map(limpiar, (material, desc, qty, unidad, bin_))
    fecha = datetime.now().strftime(
        getattr(config, "FORMATO_FECHA", "%d/%m/%Y %H:%M")
    )
    copias_qr = int(getattr(config, "COPIAS_QR", 2))
    return (
        zpl_etiqueta_qr(modo, m, d, q, u, b, fecha, copias_qr)
        + zpl_etiqueta_barcode(modo, m, d, q, u, b, fecha)
    )
# ------------------------------------------------------------------ Android
def pedir_permisos():
    if platform != "android":
        return
    from android.permissions import request_permissions

    request_permissions([
        "android.permission.BLUETOOTH_CONNECT",
        "android.permission.BLUETOOTH_SCAN",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.WRITE_EXTERNAL_STORAGE",
    ])


def impresoras_emparejadas():
    """Devuelve {nombre: direccion MAC} de dispositivos ya emparejados."""
    if platform != "android":
        return {}
    from jnius import autoclass

    adapter = autoclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
    if adapter is None or not adapter.isEnabled():
        return {}
    return {
        d.getName(): d.getAddress()
        for d in adapter.getBondedDevices().toArray()
    }


from kivy.utils import platform
import time

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"

# Conexión Bluetooth actual
bluetooth_socket = None
bluetooth_output = None
bluetooth_mac_actual = None


def pedir_permisos():
    if platform != "android":
        return

    from android.permissions import request_permissions

    request_permissions([
        "android.permission.BLUETOOTH_CONNECT",
        "android.permission.BLUETOOTH_SCAN",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.WRITE_EXTERNAL_STORAGE",
    ])


def impresoras_emparejadas():
    """Devuelve {nombre: direccion MAC} de dispositivos ya emparejados."""

    if platform != "android":
        return {}

    from jnius import autoclass

    adapter = autoclass(
        "android.bluetooth.BluetoothAdapter"
    ).getDefaultAdapter()

    if adapter is None or not adapter.isEnabled():
        return {}

    return {
        d.getName(): d.getAddress()
        for d in adapter.getBondedDevices().toArray()
    }


def conectar_impresora(mac):
    """Conecta con la impresora Bluetooth y mantiene la conexión abierta."""

    global bluetooth_socket
    global bluetooth_output
    global bluetooth_mac_actual

    if platform != "android":
        return False

    from jnius import autoclass

    BluetoothAdapter = autoclass(
        "android.bluetooth.BluetoothAdapter"
    )

    UUID = autoclass("java.util.UUID")

    adapter = BluetoothAdapter.getDefaultAdapter()

    if adapter is None:
        raise Exception("Bluetooth no disponible")

    if not adapter.isEnabled():
        raise Exception("Bluetooth está apagado")

    # Si ya estamos conectados a la misma impresora,
    # no necesitamos crear otra conexión.
    if (
        bluetooth_socket is not None
        and bluetooth_output is not None
        and bluetooth_mac_actual == mac
    ):
        return True

    # Cerrar conexión anterior si existe
    desconectar_impresora()

    device = adapter.getRemoteDevice(mac)

    # IMPORTANTE:
    # No debe estar haciendo discovery mientras conectamos.
    adapter.cancelDiscovery()

    uuid = UUID.fromString(SPP_UUID)

    bluetooth_socket = device.createRfcommSocketToServiceRecord(uuid)

    try:
        bluetooth_socket.connect()

        bluetooth_output = bluetooth_socket.getOutputStream()

        bluetooth_mac_actual = mac

        return True

    except Exception:
        desconectar_impresora()
        raise


def enviar_bluetooth(mac, zpl):
    """
    Envía ZPL a la impresora.
    Mantiene la conexión abierta para poder imprimir
    varias etiquetas consecutivamente.
    """

    global bluetooth_socket
    global bluetooth_output
    global bluetooth_mac_actual

    if platform != "android":
        return False

    # Si no estamos conectados, conectar
    if (
        bluetooth_socket is None
        or bluetooth_output is None
        or bluetooth_mac_actual != mac
    ):
        conectar_impresora(mac)

    try:

        # Enviar etiqueta
        bluetooth_output.write(
            zpl.encode("utf-8")
        )

        bluetooth_output.flush()

        # Pequeña pausa para darle tiempo
        # a la impresora de procesar los datos.
        time.sleep(0.1)

        return True

    except Exception as e:

        print("Error enviando a impresora:", e)

        # La conexión probablemente se perdió.
        desconectar_impresora()

        # Intentar reconectar automáticamente
        try:

            conectar_impresora(mac)

            bluetooth_output.write(
                zpl.encode("utf-8")
            )

            bluetooth_output.flush()

            time.sleep(0.1)

            return True

        except Exception as e2:

            print(
                "Error al reconectar impresora:",
                e2
            )

            desconectar_impresora()

            return False


def desconectar_impresora():
    """Cierra completamente la conexión Bluetooth."""

    global bluetooth_socket
    global bluetooth_output
    global bluetooth_mac_actual

    try:
        if bluetooth_output is not None:
            bluetooth_output.close()
    except Exception:
        pass

    try:
        if bluetooth_socket is not None:
            bluetooth_socket.close()
    except Exception:
        pass

    bluetooth_output = None
    bluetooth_socket = None
    bluetooth_mac_actual = None
    
def guardar_en_descargas(nombre, texto):
    """Guarda un CSV en la carpeta Descargas. Devuelve la ubicacion donde quedo."""
    datos = texto.encode("utf-8-sig")  # BOM: Excel respeta los acentos

    if platform != "android":
        ruta = os.path.join(os.path.expanduser("~"), nombre)
        with open(ruta, "wb") as f:
            f.write(datos)
        return ruta

    from jnius import autoclass

    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    sdk = autoclass("android.os.Build$VERSION").SDK_INT

    # 1) Android 10+: MediaStore (no necesita permiso de almacenamiento)
    if sdk >= 29:
        try:
            ContentValues = autoclass("android.content.ContentValues")
            Downloads = autoclass("android.provider.MediaStore$Downloads")
            v = ContentValues()
            v.put("_display_name", nombre)
            v.put("mime_type", "text/csv")
            v.put("relative_path", "Download")
            resolver = activity.getContentResolver()
            uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, v)
            salida = resolver.openOutputStream(uri)
            salida.write(datos)
            salida.flush()
            salida.close()
            return "Descargas/" + nombre
        except Exception:
            pass

    # 2) Android 9 o menor: escritura directa
    try:
        ruta = "/sdcard/Download/" + nombre
        with open(ruta, "wb") as f:
            f.write(datos)
        return ruta
    except Exception:
        pass

    # 3) Ultimo recurso: carpeta de archivos propia de la app
    ext = activity.getExternalFilesDir(None).getAbsolutePath()
    ruta = os.path.join(ext, nombre)
    with open(ruta, "wb") as f:
        f.write(datos)
    return ruta


# ------------------------------------------------------------------------ UI
class Campo(TextInput):
    def __init__(self, hint, **kw):
        super().__init__(
            hint_text=hint, multiline=False, size_hint_y=None, height=dp(52),
            font_size="20sp", write_tab=False, **kw
        )


class Raiz(BoxLayout):
    ALTO_ALTA = dp(168)

    def __init__(self, db, **kw):
        super().__init__(orientation="vertical", padding=10, spacing=6, **kw)
        self.db = db
        self.impresoras = {}
        self.material_ok = False
        modo = db.get_meta("modo", config.MODO_INICIAL)
        self.modo = modo if modo in (MODO_BIN, MODO_SL) else MODO_BIN

        # --- barra superior
        self.spin = Spinner(text="Selecciona impresora", size_hint_y=None, height=dp(44))
        fila = BoxLayout(size_hint_y=None, height=dp(40), spacing=6)
        b_imp = Button(text="Impresoras", font_size="13sp")
        b_csv = Button(text="Importar CSV", font_size="13sp")
        b_exp = Button(text="Exportar nuevos", font_size="13sp")
        b_imp.bind(on_release=lambda *_: self.cargar_impresoras())
        b_csv.bind(on_release=lambda *_: self.elegir_csv())
        b_exp.bind(on_release=lambda *_: self.pedir_password(self.exportar))
        for b in (b_imp, b_csv, b_exp):
            fila.add_widget(b)
        self.btn_modo = Button(size_hint_y=None, height=dp(44), bold=True)
        self.btn_modo.bind(on_release=lambda *_: self.cambiar_modo())

        # --- captura
        self.f_mat = Campo("Material (escanear)")
        self.info = Label(text="", size_hint_y=None, height=dp(36))
        self.f_uni = Campo("Unidad de medida (PZA, KG, M...)")
        self.f_desc = Campo("Descripcion (opcional)")
        self.btn_alta = Button(text="AGREGAR A LA BASE", size_hint_y=None, height=dp(52))
        self.panel_alta = BoxLayout(orientation="vertical", spacing=6,
                                    size_hint_y=None, height=0, opacity=0, disabled=True)
        for w in (self.f_uni, self.f_desc, self.btn_alta):
            self.panel_alta.add_widget(w)
        self.f_qty = Campo("QTY", input_filter="float")
        self.f_bin = Campo("Bin (escanear)")
        self.btn_print = Button(text="IMPRIMIR", size_hint_y=None, height=dp(60))
        self.estado = Label(text="Listo")

        self.f_mat.bind(on_text_validate=self.al_material)
        self.f_mat.bind(text=lambda *_: self.bloquear())  # al cambiar el material, revalidar
        self.f_uni.bind(on_text_validate=lambda *_: setattr(self.f_desc, "focus", True))
        self.f_desc.bind(on_text_validate=self.agregar_material)
        self.btn_alta.bind(on_release=self.agregar_material)
        self.f_qty.bind(on_text_validate=self.al_qty)
        self.f_bin.bind(on_text_validate=lambda *_: self.imprimir())
        self.btn_print.bind(on_release=lambda *_: self.imprimir())

        for w in (self.spin, fila, self.btn_modo, self.f_mat, self.info,
                  self.panel_alta, self.f_qty, self.f_bin, self.btn_print, self.estado):
            self.add_widget(w)

        self.aplicar_modo()
        self.bloquear()
        self.cargar_impresoras()
        self.f_mat.focus = True

    def msg(self, texto):
        self.estado.text = texto

    # --- modo BIN / STORAGE LOCATION
    def aplicar_modo(self):
        es_bin = self.modo == MODO_BIN
        self.btn_modo.text = f"MODO: {self.modo}  (toca para cambiar)"
        self.f_bin.height = dp(52) if es_bin else 0
        self.f_bin.opacity = 1 if es_bin else 0
        self.aplicar_estado()

    def cambiar_modo(self):
        self.modo = MODO_SL if self.modo == MODO_BIN else MODO_BIN
        self.db.set_meta("modo", self.modo)
        self.f_bin.text = ""
        self.aplicar_modo()
        self.msg(f"Modo {self.modo}")
        self.f_mat.focus = True

    # --- control de estado: sin material valido no se captura ni se imprime
    def aplicar_estado(self):
        ok = self.material_ok
        self.f_qty.disabled = not ok
        self.f_bin.disabled = not (ok and self.modo == MODO_BIN)
        self.btn_print.disabled = not ok

    def mostrar_alta(self, visible):
        self.panel_alta.height = self.ALTO_ALTA if visible else 0
        self.panel_alta.opacity = 1 if visible else 0
        self.panel_alta.disabled = not visible

    def bloquear(self):
        self.material_ok = False
        self.mostrar_alta(False)
        self.info.text = ""
        self.aplicar_estado()

    def desbloquear(self):
        self.material_ok = True
        self.aplicar_estado()

    # --- impresoras / CSV
    def cargar_impresoras(self):
        self.impresoras = impresoras_emparejadas()
        self.spin.values = list(self.impresoras)
        if self.impresoras and self.spin.text not in self.impresoras:
            zebra = [n for n in self.impresoras if "zq" in n.lower() or "zebra" in n.lower()]
            self.spin.text = (zebra or list(self.impresoras))[0]
        self.msg(f"{len(self.impresoras)} impresora(s) emparejada(s)")

    def elegir_csv(self):
        try:
            from plyer import filechooser
            filechooser.open_file(on_selection=self.csv_elegido, filters=["*.csv"])
        except Exception as e:
            self.msg(f"No se pudo abrir selector: {e}")

    @mainthread
    def csv_elegido(self, seleccion):
        if not seleccion:
            return
        try:
            n = self.db.importar_csv(seleccion[0])
            self.msg(f"Importados {n} materiales")
        except Exception as e:
            self.msg(f"Error CSV: {e}")

    # --- exportar materiales nuevos (con contrasena)
    def pedir_password(self, accion):
        caja = BoxLayout(orientation="vertical", spacing=8, padding=8)
        campo = TextInput(password=True, multiline=False, hint_text="Contrasena",
                          size_hint_y=None, height=dp(48), font_size="18sp")
        aviso = Label(text="", size_hint_y=None, height=dp(24))
        botones = BoxLayout(size_hint_y=None, height=dp(48), spacing=8)
        b_ok = Button(text="Aceptar")
        b_no = Button(text="Cancelar")
        botones.add_widget(b_no)
        botones.add_widget(b_ok)
        for w in (campo, aviso, botones):
            caja.add_widget(w)
        pop = Popup(title="Contrasena requerida", content=caja,
                    size_hint=(0.92, None), height=dp(230), auto_dismiss=False)

        def verificar(*_):
            huella = hashlib.sha256(campo.text.encode("utf-8")).hexdigest()
            if hmac.compare_digest(huella, config.PASSWORD_HASH):
                pop.dismiss()
                accion()
            else:
                aviso.text = "Contrasena incorrecta"
                campo.text = ""
                campo.focus = True

        b_ok.bind(on_release=verificar)
        b_no.bind(on_release=lambda *_: pop.dismiss())
        campo.bind(on_text_validate=verificar)
        pop.open()
        campo.focus = True

    def exportar(self):
        texto, n = self.db.exportar_extra_csv()
        if not texto:
            return self.msg("No hay materiales agregados para exportar")
        nombre = f"materiales_nuevos_{datetime.now():%Y%m%d_%H%M%S}.csv"
        try:
            ubicacion = guardar_en_descargas(nombre, texto)
            self.msg(f"{n} materiales exportados: {ubicacion}")
        except Exception as e:
            self.msg(f"Error al exportar: {e}")

    # --- flujo de captura
    def al_material(self, *_):
        mat = self.f_mat.text.strip()
        if not mat:
            return
        fila = self.db.buscar(mat)
        if fila:
            self.mostrar_alta(False)
            self.info.text = f"{fila[0]}  [{fila[1]}]"
            self.desbloquear()
            self.f_qty.focus = True
        else:
            self.info.text = "NO EXISTE en la base. Captura la unidad y agregalo"
            self.mostrar_alta(True)
            self.f_uni.focus = True

    def agregar_material(self, *_):
        """Valida los datos y pide contrasena antes de guardar."""
        if not self.f_mat.text.strip():
            return
        if not self.f_uni.text.strip():
            self.msg("La unidad de medida es obligatoria")
            self.f_uni.focus = True
            return
        self.pedir_password(self._guardar_material)

    def _guardar_material(self):
        mat = self.f_mat.text.strip()
        uni = self.f_uni.text.strip().upper()
        desc = self.f_desc.text.strip()
        self.db.agregar(mat, desc, uni)
        self.msg(f"Material {mat} agregado a la base")
        self.f_uni.text = self.f_desc.text = ""
        self.al_material()  # ya existe: desbloquea y sigue a cantidad

    def al_qty(self, *_):
        if self.modo == MODO_BIN:
            self.f_bin.focus = True
        else:
            self.imprimir()

    def imprimir(self):
        mat = self.f_mat.text.strip()
        qty = self.f_qty.text.strip()
        bin_ = self.f_bin.text.strip() if self.modo == MODO_BIN else ""
        fila = self.db.buscar(mat) if mat else None
        if not fila:
            return self.msg("Material no existe en la base")
        try:
            if float(qty) <= 0:
                raise ValueError
        except ValueError:
            return self.msg("Captura una cantidad valida")
        if self.modo == MODO_BIN and not bin_:
            return self.msg("Falta el bin")
        mac = self.impresoras.get(self.spin.text)
        if not mac:
            return self.msg("Selecciona una impresora")

        desc, unidad = fila
        zpl = generar_zpl(self.modo, mat, desc, qty, unidad, bin_)
        self.msg("Imprimiendo...")
        threading.Thread(target=self._enviar, args=(mac, zpl), daemon=True).start()

    def _enviar(self, mac, zpl):
        try:
            enviar_bluetooth(mac, zpl)
            self.despues_de_imprimir(True, "3 etiquetas enviadas")
        except Exception as e:
            self.despues_de_imprimir(False, f"Error: {e}")

    @mainthread
    def despues_de_imprimir(self, ok, texto):
        self.msg(texto)
        if ok:
            self.f_mat.text = self.f_qty.text = self.f_bin.text = ""
            self.bloquear()
            self.f_mat.focus = True


class EtiquetasApp(App):
    def build(self):
        pedir_permisos()
        db = DB(os.path.join(self.user_data_dir, "materiales.db"))
        # materiales.csv va empaquetado junto a main.py dentro del APK (opcional)
        csv_incluido = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "materiales.csv"
        )
        error = None
        n = None
        try:
            n = db.cargar_incluido(csv_incluido)
        except Exception as e:
            error = f"Error al cargar materiales.csv: {e}"
        raiz = Raiz(db)
        if error:
            raiz.msg(error)
        elif n:
            raiz.msg(f"Base cargada: {n} materiales")
        return raiz


if __name__ == "__main__":
    EtiquetasApp().run()
