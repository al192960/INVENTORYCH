"""
Etiquetas Zebra ZQ630 (4x2 in, 203 dpi) - Kivy + pyjnius
Flujo: escanear/teclear Material -> Cantidad -> Bin -> imprime solo.
"""
import csv
import hashlib
import os
import sqlite3
import threading

from kivy.app import App
from kivy.clock import mainthread
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

Window.softinput_mode = "below_target"

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"


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

    def agregar(self, material, descripcion, unidad):
        self.con.execute(
            "INSERT OR REPLACE INTO materiales_extra VALUES (?,?,?)",
            (material, descripcion, unidad),
        )
        self.con.commit()

    def cargar_incluido(self, ruta):
        """Carga el CSV empaquetado en el APK. Si cambió desde la última vez
        (otro APK con CSV nuevo), reemplaza todo el catálogo."""
        if not os.path.exists(ruta):
            return None
        with open(ruta, "rb") as f:
            huella = hashlib.md5(f.read()).hexdigest()
        fila = self.con.execute(
            "SELECT valor FROM meta WHERE clave='csv_hash'"
        ).fetchone()
        if fila and fila[0] == huella:
            return 0  # ya estaba cargado
        self.con.execute("DELETE FROM materiales")
        n = self.importar_csv(ruta)
        self.con.execute(
            "INSERT OR REPLACE INTO meta VALUES ('csv_hash', ?)", (huella,)
        )
        self.con.commit()
        return n

    def importar_csv(self, ruta):
        """CSV con encabezado: material,unidad[,descripcion]"""
        n = 0
        with open(ruta, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
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


# ----------------------------------------------------------------------- ZPL
def limpiar(s):
    return str(s).replace("^", " ").replace("~", " ").strip()


def generar_zpl(material, desc, qty, unidad, bin_):
    m, d, q, u, b = map(limpiar, (material, desc, qty, unidad, bin_))
    qr = f"{m}|{q}|{u}|{b}"
    return (
        "^XA\n^CI28\n^PW812\n^LL406\n^LH0,0\n"
        "^FO30,20^A0N,28,28^FDMATERIAL^FS\n"
        f"^FO30,48^A0N,70,70^FD{m}^FS\n"
        f"^FO30,125^A0N,28,28^FD{d[:32]}^FS\n"
        "^FO30,175^A0N,28,28^FDCANTIDAD^FS\n"
        f"^FO30,203^A0N,70,70^FD{q} {u}^FS\n"
        "^FO30,290^A0N,28,28^FDBIN^FS\n"
        f"^FO30,318^A0N,60,60^FD{b}^FS\n"
        f"^FO520,60^BQN,2,7^FDQA,{qr}^FS\n"
        "^XZ\n"
    )


# ------------------------------------------------------------------ Bluetooth
def pedir_permisos():
    if platform != "android":
        return
    from android.permissions import request_permissions

    request_permissions([
        "android.permission.BLUETOOTH_CONNECT",
        "android.permission.BLUETOOTH_SCAN",
        "android.permission.READ_EXTERNAL_STORAGE",
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


def enviar_bluetooth(mac, zpl):
    from jnius import autoclass

    BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
    UUID = autoclass("java.util.UUID")
    adapter = BluetoothAdapter.getDefaultAdapter()
    device = adapter.getRemoteDevice(mac)
    adapter.cancelDiscovery()
    sock = device.createRfcommSocketToServiceRecord(UUID.fromString(SPP_UUID))
    try:
        sock.connect()
        out = sock.getOutputStream()
        out.write(zpl.encode("utf-8"))
        out.flush()
    finally:
        sock.close()


# ------------------------------------------------------------------------ UI
class Campo(TextInput):
    def __init__(self, hint, **kw):
        super().__init__(
            hint_text=hint, multiline=False, size_hint_y=None, height="52dp",
            font_size="20sp", write_tab=False, **kw
        )


class Raiz(BoxLayout):
    def __init__(self, db, **kw):
        super().__init__(orientation="vertical", padding=12, spacing=8, **kw)
        self.db = db
        self.impresoras = {}

        self.spin = Spinner(text="Selecciona impresora", size_hint_y=None, height="48dp")
        self.btn_refrescar = Button(text="Actualizar impresoras", size_hint_y=None, height="44dp")
        self.btn_refrescar.bind(on_release=lambda *_: self.cargar_impresoras())
        self.btn_csv = Button(text="Importar CSV de materiales", size_hint_y=None, height="44dp")
        self.btn_csv.bind(on_release=lambda *_: self.elegir_csv())

        self.f_mat = Campo("Material (escanear)")
        self.info = Label(text="", size_hint_y=None, height="40dp")
        self.f_qty = Campo("Cantidad", input_filter="float")
        self.f_bin = Campo("Bin (escanear)")
        self.btn_print = Button(text="IMPRIMIR", size_hint_y=None, height="60dp")
        self.estado = Label(text="Listo")

        # Panel para dar de alta un material que no existe en la base
        self.f_uni = Campo("Unidad de medida (PZA, KG, M...)")
        self.f_desc = Campo("Descripcion (opcional)")
        self.btn_alta = Button(text="AGREGAR A LA BASE", size_hint_y=None, height="52dp")
        self.panel_alta = BoxLayout(orientation="vertical", spacing=6,
                                    size_hint_y=None, height=0, opacity=0, disabled=True)
        for w in (self.f_uni, self.f_desc, self.btn_alta):
            self.panel_alta.add_widget(w)

        self.f_mat.bind(on_text_validate=self.al_material)
        self.f_mat.bind(text=lambda *_: self.bloquear())  # al cambiar el material, revalidar
        self.f_uni.bind(on_text_validate=lambda *_: setattr(self.f_desc, "focus", True))
        self.f_desc.bind(on_text_validate=self.agregar_material)
        self.btn_alta.bind(on_release=self.agregar_material)
        self.f_qty.bind(on_text_validate=lambda *_: setattr(self.f_bin, "focus", True))
        self.f_bin.bind(on_text_validate=lambda *_: self.imprimir())
        self.btn_print.bind(on_release=lambda *_: self.imprimir())

        for w in (self.spin, self.btn_refrescar, self.btn_csv, self.f_mat,
                  self.info, self.panel_alta, self.f_qty, self.f_bin,
                  self.btn_print, self.estado):
            self.add_widget(w)

        self.bloquear()
        self.cargar_impresoras()
        self.f_mat.focus = True

    # --- control de estado: sin material valido no se captura ni se imprime
    def mostrar_alta(self, visible):
        self.panel_alta.height = dp(168) if visible else 0
        self.panel_alta.opacity = 1 if visible else 0
        self.panel_alta.disabled = not visible

    def bloquear(self):
        for w in (self.f_qty, self.f_bin, self.btn_print):
            w.disabled = True
        self.mostrar_alta(False)
        self.info.text = ""

    def desbloquear(self):
        for w in (self.f_qty, self.f_bin, self.btn_print):
            w.disabled = False

    def agregar_material(self, *_):
        mat = self.f_mat.text.strip()
        uni = self.f_uni.text.strip().upper()
        desc = self.f_desc.text.strip()
        if not mat:
            return
        if not uni:
            self.msg("La unidad de medida es obligatoria")
            self.f_uni.focus = True
            return
        self.db.agregar(mat, desc, uni)
        self.msg(f"Material {mat} agregado a la base")
        self.f_uni.text = self.f_desc.text = ""
        self.al_material()  # ahora ya existe: desbloquea y sigue a cantidad

    def msg(self, texto):
        self.estado.text = texto

    def cargar_impresoras(self):
        self.impresoras = impresoras_emparejadas()
        self.spin.values = list(self.impresoras)
        if self.impresoras and self.spin.text not in self.impresoras:
            # Prefiere una impresora Zebra si aparece
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

    def imprimir(self):
        mat = self.f_mat.text.strip()
        qty = self.f_qty.text.strip()
        bin_ = self.f_bin.text.strip()
        fila = self.db.buscar(mat)
        if not (mat and qty and bin_):
            return self.msg("Falta material, cantidad o bin")
        if not fila:
            return self.msg("Material no existe en la base")
        mac = self.impresoras.get(self.spin.text)
        if not mac:
            return self.msg("Selecciona una impresora")

        desc, unidad = fila
        zpl = generar_zpl(mat, desc, qty, unidad, bin_)
        self.msg("Imprimiendo...")
        threading.Thread(target=self._enviar, args=(mac, zpl), daemon=True).start()

    def _enviar(self, mac, zpl):
        try:
            enviar_bluetooth(mac, zpl)
            self.despues_de_imprimir(True, "Etiqueta enviada")
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
        # materiales.csv va empaquetado junto a main.py dentro del APK
        csv_incluido = os.path.join(os.path.dirname(os.path.abspath(__file__)), "materiales.csv")
        n = db.cargar_incluido(csv_incluido)
        raiz = Raiz(db)
        if n:
            raiz.msg(f"Base cargada: {n} materiales")
        return raiz


if __name__ == "__main__":
    EtiquetasApp().run()
