"""Configuracion de la app. Edita este archivo ANTES de compilar el APK."""

# SHA-256 de la contrasena que protege el boton "Exportar nuevos".
# Contrasena por defecto: admin123   (CAMBIALA)
# Para generar el hash de la tuya ejecuta:  python generar_hash.py
PASSWORD_HASH = "de3d43caad2bd3c4f0622fc60deecd06b34a0f25a80e30b81fe051a3c54799bb"

# Modo con el que arranca la app la primera vez: "BIN" o "STORAGE LOCATION"
# (despues recuerda el ultimo modo usado)
MODO_INICIAL = "BIN"

# Cuantas etiquetas con QR se imprimen por captura (ademas de 1 con codigos de barras)
COPIAS_QR = 2

# Formato de la fecha impresa en las etiquetas
FORMATO_FECHA = "%d/%m/%Y %H:%M"

# Pausas (segundos) al enviar por Bluetooth. Si la etiqueta de codigos de barras
# no sale completa, sube estos valores (por ejemplo 1.5 y 3.0).
PAUSA_ENTRE_ETIQUETAS = 1.0
PAUSA_FINAL = 2.0
