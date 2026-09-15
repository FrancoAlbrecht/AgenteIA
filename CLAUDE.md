# AgenteIA — Estudio Rivarossa

Automatizaciones que leen Redmine y mandan avisos por correo. Dos scripts, dos workflows de GitHub Actions:

| Script | Workflow | Qué hace | Cuándo corre |
|---|---|---|---|
| `main_impuestos.py` | `.github/workflows/reporte-vencimientos.yml` | Avisa a cada responsable sus vencimientos tributarios próximos (II BB, DREI, CM, IVA, Sicore, Ag. Recaudación) | Todos los días, con 2 disparos de respaldo |
| `main_auditoria.py` | `.github/workflows/reporte-auditoria.yml` | Reporte mensual de auditoría | Día 20 de cada mes (o el hábil siguiente), el workflow corre todos los días pero el script decide internamente si corresponde enviar |

`feriados.py` tiene el calendario de feriados/no laborables usado para calcular días hábiles en ambos scripts.

Ambos scripts comparten el mismo patrón: descargan de Redmine → arman el HTML por persona → mandan por SMTP (Gmail) → siempre mandan un correo de resumen de ejecución a `francoalbrecht@rivarossa.com` (éxito o error), para que un fallo nunca sea silencioso.

## Autenticación de Gmail: OAuth2 (desde el 15/09/2026)

**Ya no se usa contraseña de aplicación.** El 15/09 Google empezó a bloquear el login SMTP con contraseña desde las IPs rotativas de GitHub Actions (error `534 5.7.9 WebLoginRequired`), cortando el envío diario sin aviso hasta que alguien lo notaba a mano.

Se migró a **OAuth2 (XOAUTH2)**:
- Proyecto de Google Cloud dedicado: `agente-impuestos-rivarossa` (dueño: `agenterivarossa@gmail.com`), **publicado en modo producción** (no "Prueba") para que el refresh token no venza cada 7 días.
- Scope: `https://mail.google.com/`.
- Homepage / Política de Privacidad de la app OAuth: página mínima publicada aparte (no es un servicio productivo, solo cumple el requisito de Google para publicar la app a producción).
- Credenciales en GitHub Secrets: `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN` (usadas por ambos workflows). El secret viejo `SMTP_PASSWORD` **ya no existe**, se borró el 15/09 tras confirmar que ningún script lo referenciaba.
- Funciones clave en ambos scripts: `obtener_access_token_oauth()` (cambia el refresh token por un access token de ~1h) y `autenticar_smtp_oauth()` (hace el `AUTH XOAUTH2` sobre la conexión SMTP ya abierta).
- La app queda con el cartel de "Google no verificó esta app" — es esperable, es de un solo desarrollador/usuario, no se pidió verificación formal.

**Riesgo residual:** aunque OAuth2 es mucho más robusto que la contraseña, Google podría en teoría volver a pedir alguna verificación. Si el correo de resumen avisa un error de auth, revisar `https://myaccount.google.com/notifications` en `agenterivarossa@gmail.com`.

## Resiliencia del envío diario (impuestos)

- `reporte-vencimientos.yml` tiene el cron original (~03:24 ART) más **dos disparos de respaldo** (~07:11 y ~10:37 ART), porque GitHub retrasa "schedule" 3-7hs en este repo bajo carga.
- `main_impuestos.py` chequea al arrancar (`ya_hubo_envio_exitoso_hoy()`, vía API de GitHub Actions) si ya hubo una corrida exitosa hoy; si la hay, no manda nada — así los disparos de respaldo no duplican avisos.

## Pendientes / cosas a tener en cuenta

- **Gorreta, Valentina**: su email en Redmine (`valentinagorreta@rivarossa.com`) rebota (dirección inexistente). Mientras no se confirme/corrija la casilla real, su reporte se redirige a `francoalbrecht@rivarossa.com` (mismo mecanismo que ya existía para Previotto, Gisela — ver diccionario `EXCEPCIONES_DESTINO` en `main_impuestos.py`). Login de Redmine de Valentina es `vgorreta`, así que `vgorreta@rivarossa.com` es la dirección más probable si alguien la confirma.
- **Migración a Mailgun**: se evaluó y se llegó a crear una cuenta/dominio (`mg.rivarossa.com`) en Mailgun, pero **no se completó** por falta de acceso al DNS de `rivarossa.com` (lo administra un tercero, hosting BAEHOST). La cuenta de Mailgun queda creada y sin usar, por si en algún momento se consigue ese acceso.
- **No confundir**: la fecha de corte de auditoría es el día 20 de cada mes; el 20/09/2026 cae domingo, así que el próximo envío real sería el 21/09.

## Historial reciente (sesión del 15/09/2026)

1. El envío diario de impuestos falló por el bloqueo de Google (contraseña de aplicación + IP de GitHub Actions).
2. Se destrabó manualmente (login por navegador) y se reenvió el reporte del día.
3. Se corrigió el email de Gorreta, Valentina (excepción a admin) y se agregó la red de resiliencia (disparos de respaldo + chequeo de idempotencia).
4. Se migró la autenticación SMTP de ambos scripts (impuestos y auditoría) de contraseña de aplicación a OAuth2, para no depender más de ese tipo de bloqueo.
5. Se evaluó y descartó (por ahora) la migración a Mailgun.
6. Se borró el secret `SMTP_PASSWORD`, ya sin uso en el repo.

Todo el código y los workflows están pusheados a `main` (último commit: `3cf8fd0`).
