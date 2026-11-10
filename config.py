"""Configuracion de la app."""

# SHA-256 de la contraseña que protege el boton "Exportar nuevos".
# Para generar el hash ejecuta:  python generar_hash.py
PASSWORD_HASH = "de3d43caad2bd3c4f0622fc60deecd06b34a0f25a80e30b81fe051a3c54799bb"

# Modo con el que arranca la app la primera vez: "BIN" o "STORAGE LOCATION"
# (despues recuerda el ultimo modo usado)
MODO_INICIAL = "BIN"

# Cuantas etiquetas con QR se imprimen por captura (ademas de 1 con codigos de barras)
COPIAS_QR = 2

# Formato de la fecha impresa en las etiquetas
FORMATO_FECHA = "%d/%m/%Y %H:%M"

# Pausas (segundos) al enviar por Bluetooth. Si la etiqueta de codigos de barras tarda en imprimirse, aumenta estos valores.
PAUSA_ENTRE_ETIQUETAS = 1.0
PAUSA_FINAL = 2.0
#Idioma
IDIOMA_ACTIVO = False
IDIOMA_FIJO = "es"
