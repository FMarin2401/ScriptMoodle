"""Obtiene un token de Moodle y lo guarda en .env sin mostrarlo en pantalla."""
import getpass
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

ENV_PATH = Path(__file__).parent / ".env"


def main() -> int:
    load_dotenv(ENV_PATH)
    moodle_url = os.getenv("MOODLE_URL", "https://cursos.iberoleon.mx/online").rstrip("/")

    usuario = input("Usuario de Moodle: ")
    password = getpass.getpass("Contraseña: ")

    resp = requests.post(
        f"{moodle_url}/login/token.php",
        data={"username": usuario, "password": password, "service": "moodle_mobile_app"},
        timeout=15,
    )
    resp.raise_for_status()
    datos = resp.json()

    if "token" not in datos:
        print(f"❌ Moodle respondió: {datos.get('error')} ({datos.get('errorcode')})")
        return 1

    ENV_PATH.touch(exist_ok=True)
    set_key(ENV_PATH, "MOODLE_TOKEN", datos["token"])
    print(f"✅ Token guardado en {ENV_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
