[app]
title = Inventory HC
package.name = etiquetaszebra
package.domain = org.ejemplo
source.dir = .
source.include_exts = py,png,jpg,kv,csv
source.exclude_dirs = .venv,venv,__pycache__,bin,.buildozer,.vscode,.git
version = 0.7
requirements = hostpython3==3.11.5,python3==3.11.5,kivy==2.3.0,pyjnius,plyer,android
orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #0E0E0E

android.permissions = BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_CONNECT,BLUETOOTH_SCAN
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
