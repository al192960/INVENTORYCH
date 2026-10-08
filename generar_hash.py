"""Genera el hash SHA-256 para pegarlo en config.py (PASSWORD_HASH)."""
import getpass
import hashlib

p1 = getpass.getpass("Nueva contrasena: ")
p2 = getpass.getpass("Repite la contrasena: ")
if p1 != p2 or not p1:
    raise SystemExit("No coinciden o esta vacia.")
print("\nPega esto en config.py:\n")
print(f'PASSWORD_HASH = "{hashlib.sha256(p1.encode()).hexdigest()}"')
