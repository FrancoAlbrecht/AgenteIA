# AgenteIA — Estudio Rivarossa

Automatizaciones que leen Redmine y mandan avisos por correo. Tres scripts (laboral todavía en desarrollo) y sus workflows de GitHub Actions:

| Script | Workflow | Qué hace | Cuándo corre |
|---|---|---|---|
| `main_impuestos.py` | `.github/workflows/reporte-vencimientos.yml` | Avisa a cada responsable sus vencimientos tributarios próximos (II BB, DREI, CM, IVA, Sicore, Ag. Recaudación) | Todos los días, con 2 disparos de respaldo |
| `main_auditoria.py` | `.github/workflows/reporte-auditoria.yml` | Reporte mensual de auditoría (bloques en orden: CyA Balance, CyA Corte, CyA Auditoria) | Día 21 de cada mes, sea hábil o no (desde oct/2026). El workflow corre todos los días pero el script decide internamente si corresponde enviar |
| `main_laboral.py` | **todavía no tiene** | Avisos del sector laboral. **EN DESARROLLO, en modo prueba** (ver sección "Sector Laboral") | Sólo se corre a mano por ahora |

`feriados.py` tiene el calendario de feriados/no laborables usado para calcular días hábiles en `main_impuestos.py` (auditoría ya no lo usa: manda siempre el 21).

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

## Sector Laboral (`main_laboral.py`) — EN DESARROLLO

Tercer script, **independiente** de impuestos y auditoría (no importa nada de ellos; comparte sólo `feriados.py`). Se arma de a poco. Si el usuario dice "seguimos con lo de laboral", retomar desde acá.

**Regla de oro mientras esté en desarrollo:** `REDIRIGIR_A_ADMIN_LABORAL = True` → todos los correos a `francoalbrecht@rivarossa.com`. **Nunca mandar nada al sector laboral** hasta que el usuario lo pida explícitamente. Cada correo en modo prueba lleva un cartel amarillo con el destinatario real.

**Pruebas locales:**
- `SOLO_VISTA_PREVIA=1 FECHA_SIMULADA=2026-10-08 python main_laboral.py` → no manda nada, guarda los HTML en `vista_previa_laboral/` (en `.gitignore`) e imprime el resumen.
- Sin `SOLO_VISTA_PREVIA` manda de verdad (todo a admin por el modo prueba). `FECHA_SIMULADA` sirve también ahí.

**Destinatarios:** los trackers laborales usan `Liquidador 1`, `Control 1`, `Soporte 1` (no Auxiliar/Liquidador/Responsable como impuestos). Se avisa a los 3, un correo por persona con todos sus vencimientos (confirmado por el usuario).

**Reglas definidas por el sector (Excel del usuario, 28/09/2026) y estado de cada una:**

| Petición | Tracker en Redmine | Cuándo llega el aviso | Estado |
|---|---|---|---|
| Leyes sociales | `Laboral - Formulario 931` con asunto "Leyes Sociales" | 2 días hábiles antes del `Vencimiento DD. JJ.` | ✅ Hecho |
| Domésticas | `Laboral - Doméstica` (asunto "Domestica"/"Doméstica") | Último día hábil del mes; se avisa lo que vence en el mes siguiente (interpretación mía, validar con el usuario) | ✅ Hecho |
| 814 | `Laboral - 814` | Día hábil siguiente al vencimiento del **931** (el 814 no tiene vencimiento propio: tomar el `Vencimiento DD. JJ.` del 931 de la misma empresa y período — confirmado por el usuario) | ⏳ Próximo a hacer |
| SICORE | `Laboral - SICORE` | 4° día hábil del mes | ⏳ Pendiente |
| SIRADIG | `Laboral - SIRADIG` | El 20 de cada mes o el día hábil siguiente | ⏳ Pendiente |
| Asiento de sueldos | `Laboral - Asiento sueldo` | Wiltel: 2° día hábil del mes. Cortassa: después del vencimiento del 931 (el usuario anotó "VER — ¿usamos la fecha de presentación?": definir con él) | ⏳ Pendiente, regla a definir |
| Provisión vacaciones | (tracker a confirmar) | Según fecha asignada en el tablero (definir qué campo) | ⏳ Pendiente, regla a definir |

Al 28/09/2026 no había ninguna petición abierta de los trackers SICORE / SIRADIG / Asiento sueldo (y sólo 2 de `Laboral - 814`, período 08/2026, con campos `Control`, `Liquidador`, `Fecha Presentación:` sin "1" en el nombre). Revisar en Redmine qué campos tienen esos trackers antes de programarlos.

**Pendientes a resolver con el usuario / sector laboral:**
1. **Las 36 domésticas no tienen Liquidador 1 / Control 1 / Soporte 1 asignado** → hoy no le llegaría el aviso a nadie (sólo aparecen en el resumen de ejecución). ¿Las asignan en Redmine o va a una persona fija?
2. **931 sin responsables:** WILSON S.A (#187526), GLUBITS S.A (#187523), ETMA S.A. (#187520).
3. **15 peticiones 931 sin vencimiento cargado** (períodos 10, 11 y 12/2026) — probablemente normal (todavía no cargadas), no se avisan hasta que tengan fecha.
4. **Workflow de GitHub Actions para laboral:** no existe. Crearlo cuando el reporte esté validado (ojo: el `gh` local no tiene scope `workflow`, puede que haya que crearlo desde la web de GitHub o el usuario pushearlo con otro token). Conviene replicar los disparos de respaldo + `ya_hubo_envio_exitoso_hoy()` de impuestos.

**Resultado de la vista previa (28/09/2026):** 08/10 → 3 correos (Tschieder, Gallino, Tosello; vence 13/10 con el 12/10 feriado); 09/10 → 3 correos (Villagra, Gallino, Tosello); 30/09 → 0 correos (domésticas sin responsables).

## Pendientes / cosas a tener en cuenta

- **Gorreta, Valentina**: su email en Redmine (`valentinagorreta@rivarossa.com`) rebota (dirección inexistente). Mientras no se confirme/corrija la casilla real, su reporte se redirige a `francoalbrecht@rivarossa.com` (mismo mecanismo que ya existía para Previotto, Gisela — ver diccionario `EXCEPCIONES_DESTINO` en `main_impuestos.py`). Login de Redmine de Valentina es `vgorreta`, así que `vgorreta@rivarossa.com` es la dirección más probable si alguien la confirma.
- **Migración a Mailgun**: se evaluó y se llegó a crear una cuenta/dominio (`mg.rivarossa.com`) en Mailgun, pero **no se completó** por falta de acceso al DNS de `rivarossa.com` (lo administra un tercero, hosting BAEHOST). La cuenta de Mailgun queda creada y sin usar, por si en algún momento se consigue ese acceso.
- **Auditoría en producción desde el 24/09/2026** (`REDIRIGIR_A_ADMIN_AUDITORIA = False`): ese día hubo un envío puntual fuera de corte (`FECHAS_ENVIO_EXTRA`); a partir de ahí sale todos los 21, aunque caiga fin de semana o feriado. El envío del 21/09 fue todavía en modo prueba (todo a admin).
- **Ventana del reporte de auditoría (desde el 25/09/2026):** trae lo que vence desde el día del envío hasta el **último día del mes siguiente** (pedido del sector auditoría: antes llegaba sólo hasta el 21 del mes siguiente y los cierres de fin de mes quedaban afuera). Lo que vence entre el 21 y fin de mes sale en dos reportes seguidos, a propósito.
- **Auditoría no filtra por proyecto:** entran todas las peticiones CyA Balance/Corte/Auditoria en Pendiente, estén o no en el subproyecto de Auditoría. Las que están mal ubicadas se listan en el correo de resumen (`detectar_fuera_de_proyecto_auditoria`) para moverlas en Redmine.
- **Envío manual de auditoría fuera del 21:** correr localmente `FORZAR_ENVIO_AUDITORIA=1 python main_auditoria.py` (usa el `.env`). Manda a todos, y la corrida automática del día no duplica porque no fuerza. Se usó el 25/09/2026 para mandar el reporte completo con la ventana nueva. (No se pudo agregar como opción del workflow: el `gh` local no tiene scope `workflow` para pushear cambios en `.github/workflows/`.)

## Historial reciente (sesión del 15/09/2026)

1. El envío diario de impuestos falló por el bloqueo de Google (contraseña de aplicación + IP de GitHub Actions).
2. Se destrabó manualmente (login por navegador) y se reenvió el reporte del día.
3. Se corrigió el email de Gorreta, Valentina (excepción a admin) y se agregó la red de resiliencia (disparos de respaldo + chequeo de idempotencia).
4. Se migró la autenticación SMTP de ambos scripts (impuestos y auditoría) de contraseña de aplicación a OAuth2, para no depender más de ese tipo de bloqueo.
5. Se evaluó y descartó (por ahora) la migración a Mailgun.
6. Se borró el secret `SMTP_PASSWORD`, ya sin uso en el repo.

Todo el código y los workflows están pusheados a `main`. Último trabajo (28/09/2026): primera versión de `main_laboral.py` (Leyes Sociales + Domésticas, en modo prueba).
