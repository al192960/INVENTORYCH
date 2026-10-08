"""
Inventory HC - Etiquetas Zebra ZQ630 (4x2 in, 203 dpi) - Kivy + pyjnius

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
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner, SpinnerOption
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
        # BINs validos (bines.csv)
        self.con.execute("CREATE TABLE IF NOT EXISTS bines (bin TEXT PRIMARY KEY)")
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

    # --- BINs validos (bines.csv)
    def cargar_bines(self, ruta):
        """Carga bines.csv empaquetado. Una columna con los BINs validos; el
        encabezado es opcional (bin, storage bin, ubicacion...). Si el archivo
        cambio desde la ultima vez, reemplaza la lista."""
        if not os.path.exists(ruta):
            return None
        with open(ruta, "rb") as f:
            crudo = f.read()
        huella = hashlib.md5(crudo).hexdigest()
        if self.get_meta("bines_hash") == huella:
            return 0
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texto = crudo.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        primera = texto.splitlines()[0] if texto.strip() else ""
        delim = max((",", ";", "\t"), key=primera.count)
        encabezados = {"bin", "bines", "storage bin", "storage_bin", "storagebin",
                       "ubicacion", "ubicación", "storage location"}
        filas = list(csv.reader(io.StringIO(texto, newline=""), delimiter=delim))
        col, inicio = 0, 0
        if filas:
            nombres = [c.strip().lower() for c in filas[0]]
            idx = next((i for i, c in enumerate(nombres) if c in encabezados), None)
            if idx is not None:
                col, inicio = idx, 1
        try:
            self.con.execute("DELETE FROM bines")
            n = 0
            for fila in filas[inicio:]:
                if len(fila) <= col:
                    continue
                b = fila[col].strip().upper()
                if not b:
                    continue
                self.con.execute("INSERT OR IGNORE INTO bines VALUES (?)", (b,))
                n += 1
            self.con.commit()
        except Exception:
            self.con.rollback()  # conserva la lista anterior
            raise
        self.set_meta("bines_hash", huella)
        return n

    def hay_bines(self):
        return self.con.execute("SELECT 1 FROM bines LIMIT 1").fetchone() is not None

    def bin_existe(self, b):
        return self.con.execute(
            "SELECT 1 FROM bines WHERE bin=?", (b.strip().upper(),)
        ).fetchone() is not None


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
    copias_qr = int(getattr(config, "COPIAS_QR", 1))
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

# ------------------------------------------------------------------------ UI
# Paleta: blanco, amarillo y negro
NEGRO = (0.055, 0.055, 0.055, 1)
BLANCO = (1, 1, 1, 1)
AMARILLO = (1.0, 0.80, 0.0, 1)
AMARILLO_OSC = (0.88, 0.66, 0.0, 1)
AMARILLO_CLARO = (1.0, 0.97, 0.80, 1)
AMARILLO_FOCO = (1.0, 0.92, 0.45, 1)
GRIS = (0.84, 0.84, 0.84, 1)
GRIS_TEXTO = (0.48, 0.48, 0.48, 1)
GRIS_OSC = (0.24, 0.24, 0.24, 1)

DIR_APP = os.path.dirname(os.path.abspath(__file__))

Window.clearcolor = BLANCO


def _con_borde(widget, color, ancho, radio=8):
    """Dibuja un borde redondeado sobre el widget. Devuelve (Color, Line)."""
    with widget.canvas.after:
        c = Color(*color)
        ln = Line(rounded_rectangle=(0, 0, 10, 10, dp(radio)), width=dp(ancho))

    def actualizar(*_):
        if widget.width < 2 or widget.height < 2:
            return
        ln.rounded_rectangle = (widget.x, widget.y, widget.width, widget.height, dp(radio))

    widget.bind(pos=actualizar, size=actualizar)
    actualizar()
    return c, ln


class Boton(Button):
    """Boton redondeado: fondo y tinta configurables, gris cuando esta deshabilitado."""

    def __init__(self, texto, fondo=AMARILLO, tinta=NEGRO, **kw):
        kw.setdefault("font_size", "17sp")
        super().__init__(
            text=texto, bold=True, color=tinta, disabled_color=GRIS_TEXTO,
            background_normal="", background_down="",
            background_disabled_normal="", background_disabled_down="",
            background_color=(0, 0, 0, 0), halign="center", **kw
        )
        self._fondo = fondo
        with self.canvas.before:
            self._c = Color(*fondo)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
        self.bind(pos=self._mover, size=self._mover, state=self._pintar, disabled=self._pintar)
        self._pintar()

    def _mover(self, *_):
        self._r.pos = self.pos
        self._r.size = self.size

    def _pintar(self, *_):
        if self.disabled:
            self._c.rgba = GRIS
        elif self.state == "down":
            self._c.rgba = tuple(max(0, v * 0.8) for v in self._fondo[:3]) + (1,)
        else:
            self._c.rgba = self._fondo


class Campo(TextInput):
    def __init__(self, hint, **kw):
        super().__init__(
            hint_text=hint, multiline=False, size_hint_y=None, height=dp(52),
            font_size="20sp", write_tab=False,
            background_normal="", background_active="", background_disabled_normal="",
            background_color=AMARILLO_CLARO, foreground_color=NEGRO,
            disabled_foreground_color=GRIS_TEXTO, hint_text_color=(0.45, 0.45, 0.45, 1),
            cursor_color=NEGRO, selection_color=(1, 0.8, 0, 0.45),
            padding=[dp(12), dp(13), dp(12), dp(8)], **kw
        )
        self._cb, self._ln = _con_borde(self, NEGRO, 1.3)
        self.bind(focus=self._estilo, disabled=self._estilo)
        self._estilo()

    def _estilo(self, *_):
        if self.disabled:
            self.background_color = (0.93, 0.93, 0.93, 1)
            self._cb.rgba, ancho = (0.70, 0.70, 0.70, 1), 1.0
        elif self.focus:
            self.background_color = AMARILLO_FOCO
            self._cb.rgba, ancho = (0.88, 0.62, 0.0, 1), 2.6
        else:
            self.background_color = AMARILLO_CLARO
            self._cb.rgba, ancho = NEGRO, 1.3
        self._ln.width = dp(ancho)


class OpcionSelector(SpinnerOption):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = AMARILLO_CLARO
        self.color = NEGRO
        self.font_size = "16sp"


class Selector(Spinner):
    def __init__(self, **kw):
        super().__init__(
            background_normal="", background_down="", background_color=AMARILLO_CLARO,
            color=NEGRO, bold=True, font_size="15sp", option_cls=OpcionSelector, **kw
        )
        _con_borde(self, NEGRO, 1.3)


class Encabezado(BoxLayout):
    """Franja negra con el logo y el nombre de la app."""

    def __init__(self, **kw):
        super().__init__(
            orientation="horizontal", size_hint_y=None, height=dp(60),
            padding=[dp(10), dp(6), dp(10), dp(8)], spacing=dp(10), **kw
        )
        with self.canvas.before:
            Color(*NEGRO)
            self._fondo = Rectangle(pos=self.pos, size=self.size)
        with self.canvas.after:
            Color(*AMARILLO)
            self._linea = Rectangle(pos=self.pos, size=(self.width, dp(4)))
        self.bind(pos=self._mover, size=self._mover)

        self.add_widget(Image(source=os.path.join(DIR_APP, "icon.png"),
                              size_hint=(None, 1), width=dp(46)))
        titulo = Label(text="INVENTORY [color=ffffff]HC[/color]", markup=True, bold=True,
                       font_size="24sp", color=AMARILLO, halign="left", valign="middle")
        titulo.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(titulo)

    def _mover(self, *_):
        self._fondo.pos = self.pos
        self._fondo.size = self.size
        self._linea.pos = self.pos
        self._linea.size = (self.width, dp(4))


class BarraEstado(BoxLayout):
    """Barra negra inferior con el mensaje de estado en amarillo."""

    def __init__(self, **kw):
        super().__init__(size_hint_y=None, height=dp(56),
                         padding=[dp(12), dp(6), dp(12), dp(4)], **kw)
        with self.canvas.before:
            Color(*NEGRO)
            self._fondo = Rectangle(pos=self.pos, size=self.size)
            Color(*AMARILLO)
            self._linea = Rectangle(pos=(self.x, self.top - dp(3)), size=(self.width, dp(3)))
        self.bind(pos=self._mover, size=self._mover)
        self.label = Label(text="Listo", color=AMARILLO, font_size="15sp",
                           halign="left", valign="middle", max_lines=2)
        self.label.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(self.label)

    def _mover(self, *_):
        self._fondo.pos = self.pos
        self._fondo.size = self.size
        self._linea.pos = (self.x, self.top - dp(3))
        self._linea.size = (self.width, dp(3))


class PanelAlta(BoxLayout):
    """Recuadro amarillo para dar de alta un material que no existe."""

    def __init__(self, **kw):
        super().__init__(orientation="vertical", spacing=dp(6), padding=dp(8), **kw)
        with self.canvas.before:
            Color(*AMARILLO)
            self._fondo = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
        self.bind(pos=self._mover, size=self._mover)

    def _mover(self, *_):
        self._fondo.pos = self.pos
        self._fondo.size = self.size


class Raiz(BoxLayout):
    ALTO_ALTA = dp(190)

    def __init__(self, db, **kw):
        super().__init__(orientation="vertical", spacing=0, padding=0, **kw)
        self.db = db
        self.impresoras = {}
        self.material_ok = False
        modo = db.get_meta("modo", config.MODO_INICIAL)
        self.modo = modo if modo in (MODO_BIN, MODO_SL) else MODO_BIN

        # --- impresora y modo
        self.spin = Selector(text="Selecciona impresora")
        b_act = Boton("Actualizar", fondo=NEGRO, tinta=AMARILLO, font_size="13sp",
                      size_hint_x=0.3)
        b_act.bind(on_release=lambda *_: self.cargar_impresoras())
        fila_imp = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.spin.size_hint_x = 0.7
        fila_imp.add_widget(self.spin)
        fila_imp.add_widget(b_act)

        self.btn_modo = Boton("", fondo=NEGRO, tinta=AMARILLO, font_size="16sp",
                              size_hint_y=None, height=dp(46))
        self.btn_modo.bind(on_release=lambda *_: self.cambiar_modo())

        # --- captura
        self.f_mat = Campo("Material (escanear)")
        self.info = Label(text="", size_hint_y=None, height=dp(34), color=NEGRO, bold=True,
                          font_size="16sp", halign="left", valign="middle",
                          shorten=True, shorten_from="center", max_lines=1)
        self.info.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.f_uni = Campo("Unidad de medida (PZA, KG, M...)")
        self.f_desc = Campo("Descripción (opcional)")
        self.btn_alta = Boton("AGREGAR A LA BASE", fondo=NEGRO, tinta=AMARILLO,
                              size_hint_y=None, height=dp(52))
        self.panel_alta = PanelAlta(size_hint_y=None, height=0, opacity=0, disabled=True)
        for w in (self.f_uni, self.f_desc, self.btn_alta):
            self.panel_alta.add_widget(w)
        self.f_qty = Campo("QTY", input_filter="float")
        self.f_bin = Campo("Bin (escanear)")

        # --- imprimir / reimprimir
        self.btn_print = Boton("IMPRIMIR", font_size="22sp", size_hint_x=0.62)
        self.btn_reimp = Boton("REIMPRIMIR\nÚLTIMO", fondo=NEGRO, tinta=AMARILLO,
                               font_size="14sp", size_hint_x=0.38)
        fila_print = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(8))
        fila_print.add_widget(self.btn_print)
        fila_print.add_widget(self.btn_reimp)

        self.f_mat.bind(on_text_validate=self.al_material)
        self.f_mat.bind(text=lambda *_: self.bloquear())  # al cambiar el material, revalidar
        self.f_uni.bind(on_text_validate=lambda *_: setattr(self.f_desc, "focus", True))
        self.f_desc.bind(on_text_validate=self.agregar_material)
        self.btn_alta.bind(on_release=self.agregar_material)
        self.f_qty.bind(on_text_validate=self.al_qty)
        self.f_bin.bind(on_text_validate=self.al_bin)
        self.btn_print.bind(on_release=lambda *_: self.imprimir())
        self.btn_reimp.bind(on_release=lambda *_: self.reimprimir())

        # --- armado de la pantalla: encabezado, contenido con scroll, barra de estado
        contenido = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None,
                              padding=[dp(12), dp(10), dp(12), dp(10)])
        contenido.bind(minimum_height=contenido.setter("height"))
        for w in (fila_imp, self.btn_modo, self.f_mat, self.info, self.panel_alta,
                  self.f_qty, self.f_bin, fila_print):
            contenido.add_widget(w)
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3), bar_color=AMARILLO_OSC)
        scroll.add_widget(contenido)

        barra = BarraEstado()
        self.estado = barra.label

        self.add_widget(Encabezado())
        self.add_widget(scroll)
        self.add_widget(barra)

        self.aplicar_modo()
        self.bloquear()
        self.actualizar_reimprimir()
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

    def actualizar_reimprimir(self):
        self.btn_reimp.disabled = not self.db.get_meta("ultimo_zpl")

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

    # --- impresoras
    def cargar_impresoras(self):
        self.impresoras = impresoras_emparejadas()
        self.spin.values = list(self.impresoras)
        if self.impresoras and self.spin.text not in self.impresoras:
            zebra = [n for n in self.impresoras if "zq" in n.lower() or "zebra" in n.lower()]
            self.spin.text = (zebra or list(self.impresoras))[0]
        self.msg(f"{len(self.impresoras)} impresora(s) emparejada(s)")

    # --- contrasena (para dar de alta materiales)
    def pedir_password(self, accion):
        caja = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        campo = Campo("Contraseña", password=True)
        aviso = Label(text="", size_hint_y=None, height=dp(24), color=AMARILLO)
        botones = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        b_ok = Boton("Aceptar")
        b_no = Boton("Cancelar", fondo=GRIS_OSC, tinta=BLANCO)
        botones.add_widget(b_no)
        botones.add_widget(b_ok)
        for w in (campo, aviso, botones):
            caja.add_widget(w)
        pop = Popup(title="Contraseña requerida", content=caja, title_color=AMARILLO,
                    separator_color=AMARILLO, background="", background_color=NEGRO,
                    size_hint=(0.92, None), height=dp(240), auto_dismiss=False)

        def verificar(*_):
            huella = hashlib.sha256(campo.text.encode("utf-8")).hexdigest()
            if hmac.compare_digest(huella, config.PASSWORD_HASH):
                pop.dismiss()
                accion()
            else:
                aviso.text = "Contraseña incorrecta"
                campo.text = ""
                Clock.schedule_once(lambda dt: setattr(campo, "focus", True), 0)

        b_ok.bind(on_release=verificar)
        b_no.bind(on_release=lambda *_: pop.dismiss())
        campo.bind(on_text_validate=verificar)
        pop.open()
        Clock.schedule_once(lambda dt: setattr(campo, "focus", True), 0.1)

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
            self.info.text = "NO EXISTE en la base. Agrégalo abajo"
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

    # --- validacion de BIN contra bines.csv
    def bin_valido(self, b):
        if not self.db.hay_bines():
            self.msg("La base de BINs está vacía (falta bines.csv). No se imprime.")
            return False
        if not self.db.bin_existe(b):
            self.msg(f"El BIN {b} NO existe. No se imprime.")
            return False
        return True

    def al_bin(self, *_):
        b = self.f_bin.text.strip()
        if not b:
            return
        if not self.bin_valido(b):
            self.f_bin.text = ""
            Clock.schedule_once(lambda dt: setattr(self.f_bin, "focus", True), 0)
            return
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
        if self.modo == MODO_BIN:
            if not bin_:
                return self.msg("Falta el bin")
            if not self.bin_valido(bin_):
                return
        mac = self.impresoras.get(self.spin.text)
        if not mac:
            return self.msg("Selecciona una impresora")

        desc, unidad = fila
        zpl = generar_zpl(self.modo, mat, desc, qty, unidad, bin_)
        resumen = f"{mat} | {qty} {unidad}" + (f" | BIN {bin_}" if bin_ else "")
        self.msg("Imprimiendo...")
        threading.Thread(target=self._enviar, args=(mac, zpl, resumen), daemon=True).start()

    def _enviar(self, mac, zpl, resumen):
        try:
            if not enviar_bluetooth(mac, zpl):
                raise RuntimeError("no se pudo enviar a la impresora")
            self.despues_de_imprimir(True, "3 etiquetas enviadas", zpl, resumen)
        except Exception as e:
            self.despues_de_imprimir(False, f"Error: {e}")

    @mainthread
    def despues_de_imprimir(self, ok, texto, zpl=None, resumen=""):
        self.msg(texto)
        if ok:
            if zpl:  # guarda la ultima impresion para poder reimprimirla
                self.db.set_meta("ultimo_zpl", zpl)
                self.db.set_meta("ultimo_resumen", resumen)
                self.actualizar_reimprimir()
            self.f_mat.text = self.f_qty.text = self.f_bin.text = ""
            self.bloquear()
            self.f_mat.focus = True

    # --- reimprimir la ultima captura
    def reimprimir(self):
        zpl = self.db.get_meta("ultimo_zpl")
        if not zpl:
            return self.msg("Aún no hay una etiqueta para reimprimir")
        mac = self.impresoras.get(self.spin.text)
        if not mac:
            return self.msg("Selecciona una impresora")
        resumen = self.db.get_meta("ultimo_resumen", "")
        self.msg(f"Reimprimiendo: {resumen}")
        threading.Thread(target=self._reenviar, args=(mac, zpl, resumen), daemon=True).start()

    def _reenviar(self, mac, zpl, resumen):
        try:
            if not enviar_bluetooth(mac, zpl):
                raise RuntimeError("no se pudo enviar a la impresora")
            self.fin_reimpresion(f"Reimpresas 3 etiquetas: {resumen}")
        except Exception as e:
            self.fin_reimpresion(f"Error: {e}")

    @mainthread
    def fin_reimpresion(self, texto):
        self.msg(texto)


class EtiquetasApp(App):
    title = "Inventory HC"
    icon = os.path.join(DIR_APP, "icon.png")

    def build(self):
        pedir_permisos()
        db = DB(os.path.join(self.user_data_dir, "materiales.db"))
        # materiales.csv y bines.csv van empaquetados junto a main.py dentro del APK
        errores = []
        n = nb = None
        try:
            n = db.cargar_incluido(os.path.join(DIR_APP, "materiales.csv"))
        except Exception as e:
            errores.append(f"materiales.csv: {e}")
        try:
            nb = db.cargar_bines(os.path.join(DIR_APP, "bines.csv"))
        except Exception as e:
            errores.append(f"bines.csv: {e}")
        raiz = Raiz(db)
        if errores:
            raiz.msg("Error al cargar " + " | ".join(errores))
        else:
            partes = []
            if n:
                partes.append(f"{n} materiales")
            if nb:
                partes.append(f"{nb} BINs")
            if partes:
                raiz.msg("Base cargada: " + ", ".join(partes))
        return raiz


if __name__ == "__main__":
    EtiquetasApp().run()
