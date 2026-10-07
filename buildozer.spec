[app]
title = Etiquetas Zebra
package.name = etiquetaszebra
package.domain = org.ejemplo
source.dir = .
source.include_exts = py,png,jpg,kv,csv
version = 0.1
requirements = python3,kivy==2.3.0,pyjnius,plyer,android
orientation = portrait
fullscreen = 0

android.permissions = BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_CONNECT,BLUETOOTH_SCAN,READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
