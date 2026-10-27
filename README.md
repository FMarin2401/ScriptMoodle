# Avisos de Moodle 📚

Te manda notificaciones al celular y a la PC cuando aparece una actividad nueva en Moodle (Ibero León) y te recuerda antes de cada fecha de entrega (24 h y 2 h antes, configurable).

Funciona con la API oficial de Moodle (la misma que usa su app) y con [ntfy](https://ntfy.sh) para las notificaciones. Corre gratis cada hora en GitHub Actions.

## Cómo funciona

1. Consulta a Moodle tus actividades pendientes con fecha de entrega.
2. Las compara con `avisados.json`, la lista de avisos que ya te mandó, para no repetirlos.
3. Te notifica por ntfy lo que sea nuevo o esté por vencer.

La primera ejecución manda **un solo resumen** en lugar de un aviso por cada actividad que ya tenías.

## Configuración

### 1. Crea tu copia

En esta página: **Use this template → Create a new repository**, y márcalo como **Private**. Cada persona necesita su propia copia: ahí van tu token y tu historial de avisos.

### 2. Configura ntfy

1. Instala **ntfy** en tu celular (Play Store / App Store).
2. Genera un nombre de tema difícil de adivinar (en ntfy.sh cualquiera que lo conozca puede leer tus mensajes):
   ```
   uv run python -c "import secrets; print('moodle-' + secrets.token_urlsafe(12))"
   ```
3. En la app toca **+** y suscríbete a ese tema. En la PC puedes abrir `https://ntfy.sh/<tu-tema>` y permitir notificaciones.

### 3. Obtén tu token de Moodle

Necesitas [uv](https://docs.astral.sh/uv/) instalado.

```
git clone <url-de-tu-copia>
cd <carpeta>
uv sync
copy .env.example .env        # en Linux/Mac: cp .env.example .env
uv run obtener_token.py
```

Te pide tu usuario y contraseña de Moodle una sola vez y guarda el token en `.env`. Tu contraseña no se guarda en ningún lado.

Pon tu tema de ntfy en `NTFY_TOPIC=` dentro de `.env` y prueba:

```
uv run moodle_avisos.py
```

Debería llegarte el resumen.

### 4. Actívalo en GitHub Actions

En tu repo: **Settings → Secrets and variables → Actions → New repository secret**, y crea:

| Secret | Valor |
|---|---|
| `MOODLE_TOKEN` | el valor de `MOODLE_TOKEN` en tu `.env` |
| `NTFY_TOPIC` | tu tema de ntfy |

Después ve a la pestaña **Actions → Avisos de Moodle → Run workflow** para probarlo. A partir de ahí corre solo cada hora.

## Configuración opcional

Variables en `.env` (local) o en el workflow:

| Variable | Default | Para qué |
|---|---|---|
| `RECORDATORIOS_HORAS` | `24,2` | Horas antes de la entrega para recordar |
| `ZONA_HORARIA` | `America/Mexico_City` | Zona en la que se muestran las fechas |
| `MOODLE_URL` | Moodle de Ibero León | Para usarlo con otro Moodle |

## Si deja de funcionar

Si Moodle rechaza el token (por ejemplo, porque venció o cambiaste tu contraseña), te llega una alerta **⚠️ Avisos de Moodle detenidos**. Corre `uv run obtener_token.py` otra vez y actualiza el secret `MOODLE_TOKEN`.

## Seguridad

- **Nunca subas `.env`**: el token da acceso a tu cuenta de Moodle. Ya está en `.gitignore`.
- Si crees que tu token se filtró, cierra sesión en todos los dispositivos desde tu perfil de Moodle (o cambia tu contraseña) y genera uno nuevo.
- GitHub puede retrasar unos minutos las ejecuciones programadas; no esperes precisión al minuto.
