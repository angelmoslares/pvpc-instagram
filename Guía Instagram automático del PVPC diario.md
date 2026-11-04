# Guía: Instagram automático del PVPC diario

Oct 29, 2026 · @Ángel Moslares

## ¿Se puede hacer gratis?

Sí: 0 € al mes. Un post diario cabe holgadamente en la capa gratuita de todas las piezas, siempre que se cumplan cuatro condiciones:

1. **Repositorio de GitHub público.** En repos públicos, GitHub Actions es gratis y sin límite de minutos en runners estándar. En privado tendrías 2.000 min/mes gratis, que también sobrarían (el job tarda 1–2 min), pero el repo público te da además la URL pública de la imagen sin montar nada más.
2. **App de Meta en modo desarrollo, con tu cuenta como tester.** Para publicar solo en tu propia cuenta no hace falta App Review ni pasar la app a Live.
3. **Credenciales en GitHub Secrets**, nunca en el código: el repo es público y cualquiera lo puede leer.
4. **Nada de recursos de la empresa.** Ni el tenant M365 de ENCE ni licencias premium de Power Automate: es un proyecto personal y debe vivir en cuentas personales.

El único gasto posible es opcional y no necesario: un dominio propio si algún día quieres una web asociada.

## Stack tecnológico

Python + GitHub Actions + API de Instagram: todo en la nube, sin depender de que tu PC esté encendido.

| Pieza | Para qué | Coste |
| --- | --- | --- |
| Python 3.12 (requests, pandas, Jinja2, Playwright) | Descargar el PVPC, validar y generar la imagen | 0 € |
| GitHub (repo público) | Código, histórico de imágenes y URL pública del JPEG | 0 € |
| GitHub Actions | Ejecución diaria programada y renovación del token | 0 € (ilimitado en repo público) |
| GitHub Secrets | Guardar token de ESIOS, token de Instagram e ID de cuenta | 0 € |
| API ESIOS de REE (indicador 1001, PVPC 2.0TD) | Fuente oficial del precio del día siguiente | 0 € (token personal gratuito) |
| Instagram API with Instagram Login (graph.instagram.com) | Publicar el post en tu cuenta profesional | 0 € |
| Meta for Developers | App que da acceso a la API | 0 € |
| Bot de Telegram (opcional) | Avisarte si algo falla | 0 € |
| Claude Design (Design System + Design) | Definir el estilo visual y diseñar las plantillas | 0 € (incluido en tu cuenta de Claude) |
| VS Code + Anaconda (ya los tienes) | Desarrollo y pruebas en local | 0 € |

**Alternativas descartadas.** n8n autoalojado exige un servidor o tu PC encendido a diario; n8n Cloud es de pago. Power Automate necesita conector HTTP premium. Una VM gratuita (p. ej. Oracle Cloud) funciona, pero añade mantenimiento sin aportar nada a este caso.

## Arquitectura y flujo diario

&#91;embedded content: flujo diario · 8 pasos, 2 decisiones\]

Cada disparo ejecuta el mismo script. El estado guardado hace que solo publique el primero que encuentra el día completo; la renovación del token corre aparte, dos veces al mes.

## Fase 0 · Preparación de cuentas (2–3 h)

Objetivo: tener todas las credenciales guardadas en GitHub Secrets antes de escribir código. El panel de Meta es la parte con más fricción.

1. **Cuenta de Instagram nueva** para el proyecto. Pásala a cuenta profesional (Empresa) y déjala **pública**: Meta no permite renovar tokens de cuentas privadas.
2. **App en Meta for Developers** (tipo Business). Añade el producto Instagram y entra en *API setup with Instagram login*. Activa los permisos `instagram_business_basic` e `instagram_business_content_publish`.
3. **Tu cuenta como Instagram Tester**: en *App roles → Roles* invita a tu usuario y acepta la invitación desde Instagram (*Configuración → Apps y sitios web → Invitaciones de tester*).
   - Si la cuenta de IG pertenece al mismo portfolio de empresa que la app, Meta no deja añadirla como tester. Solución: no vincules la cuenta a ningún portfolio, o pasa la app a Live (Standard Access, sin App Review para tu propia cuenta).
4. **Generar el token**: en *Generate access tokens → Add account → Generate token*. Cópialo al momento (solo se muestra una vez); dura unos 60 días. Anota también el ID de la cuenta que aparece al lado.
5. **Token de ESIOS**: solicítalo a REE (consultasios@ree.es). Es personal y gratuito.
6. **Repositorio público en GitHub** (p. ej. `pvpc-instagram`) y estos secretos en *Settings → Secrets and variables → Actions*:
   - `ESIOS_TOKEN`
   - `IG_TOKEN`
   - `IG_USER_ID`
   - `GH_PAT`: token *fine-grained* con permiso de escritura en *Secrets* de ese repo, para que el workflow pueda guardar el token de Instagram renovado (Fase 5).

**Entregable:** cuatro secretos creados y una llamada de prueba a `https://graph.instagram.com/me?fields=user_id,username` que devuelve tu usuario.

## Fase 1 · Datos del PVPC (2–3 h)

Objetivo: un script `fetch_pvpc.py` que devuelve los precios horarios de D+1 validados, o dice claramente que aún no están publicados.

1. Llamada a `https://api.esios.ree.es/indicators/1001` con `start_date` y `end_date` de D+1 en hora de Madrid, y cabecera `x-api-key` con tu token.
2. Filtrar la zona **Península** (`geo_id` 8741) y pasar de €/MWh a €/kWh.
3. Convertir las marcas de tiempo a `Europe/Madrid` con `zoneinfo`; nunca trabajar en hora local "naive".
4. **Validar** antes de seguir:
   - Número de valores = horas del día (24; 23 o 25 en los días de cambio de hora).
   - Si la API devuelve cuartos de hora, agregar a la media horaria, que es como se factura al doméstico.
   - Sin datos o incompletos → salir con estado "pendiente", no con error: el siguiente intento lo recogerá.
5. Guardar el día en `data/pvpc_YYYY-MM-DD.csv`. El histórico te servirá para comparar con ayer y con la media del mes.

**Plan B sin token:** la API pública REData (`apidatos.ree.es`) publica precios de mercado sin autenticación. Compruébala en la primera prueba antes de depender de ella.

**Entregable:** ejecutas el script en local a partir de las 20:30 y obtienes una tabla de 24 precios que cuadra con la web de ESIOS.

## Identidad visual · pixel art cozy cyberpunk

Todas las publicaciones salen con el mismo estilo: retro pixel art con colores cozy cyberpunk. El estilo se define una vez en Claude Design y se congela en una plantilla HTML que el script rellena cada día con los datos. No se usa un modelo de imagen: no garantiza coherencia entre días y tiene coste.

**Reparto de trabajo**

| Pieza | Dónde | Cuándo | Resultado |
| --- | --- | --- | --- |
| Design System | Claude Design | Una vez (Fase 2a) | Paleta, tipografías, rejilla y componentes como referencia |
| Plantillas | Claude Design | Una vez, iterando hasta cerrar (Fase 2a) | Post 1080 × 1350 y story 1080 × 1920 aprobados |
| Plantilla parametrizada | Repo: `templates/post.html` + `post.css` | Una vez (Fase 2b) | HTML/CSS con variables Jinja2 (fecha, precios, mínimo, máximo, media, mejor franja) |
| Render diario | `render.py` con Playwright (Chromium headless) | Cada día en GitHub Actions | JPEG idéntico en estilo, solo cambian los datos |

**Punto de partida del estilo** (se ajusta en Claude Design)

- Paleta: fondo morado noche `#1B1530`, panel `#2A2145`, magenta neón suave `#FF5FA2`, cian `#5EE6EB`, ámbar cálido `#FFB454`, texto crema `#F3E9D2`. Neón apagado y tonos cálidos: "cozy", no agresivo.
- Semáforo del precio dentro de la paleta: barato = cian, medio = ámbar, caro = magenta.
- Tipografías pixel (Google Fonts, licencia OFL): Press Start 2P o Silkscreen para titulares, VT323 para cifras.
- Componentes: marco de "ventana" retro con barra de título, tarjeta de precio medio, gráfico de 24 barras pixeladas, sello "mejor franja" y pie con la unidad.
- Detalles: rejilla de 8 px, sin degradados suaves ni sombras difusas, bordes de 1 bloque.

**Limitaciones a tener en cuenta**

- Claude Design no publica ni se ejecuta solo: sirve para diseñar, no para producir el post del día.
- Las fuentes van dentro del repo (`assets/fonts/`), no cargadas desde Google Fonts en tiempo de render: sin red no hay fallo ni cambio de fuente.
- Fuentes pixel solo a tamaños múltiplos de su rejilla (8, 16, 24, 32 px) y con `-webkit-font-smoothing: none`; si no, se ven borrosas.
- Playwright en GitHub Actions necesita `playwright install --with-deps chromium` (\~1 min por ejecución, sigue siendo 0 € en repo público). Conviene cachearlo.
- El texto con acentos y "€" debe existir en la fuente elegida; Press Start 2P no tiene todos los glifos, comprobarlo antes de cerrar la plantilla.

**Repos de referencia en GitHub** (consulta del 03/11/2026)

| Repo | ⭐ | Qué aporta |
| --- | --- | --- |
| [Hainrixz/open-carrusel](https://github.com/Hainrixz/open-carrusel) | 506 | Carruseles diseñados con Claude y exportados a PNG a medida de Instagram (Fase 6) |
| [sergebulaev/instagram-skills](https://github.com/sergebulaev/instagram-skills) | 720 | Skills para captions, hooks y hashtags |
| [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 137.955 | Base para Reels en la Fase 6 |
| [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) | 40.767 | Programador autoalojado, solo si algún día se publica en varias redes |

## Fase 2 · Imagen del post (3–5 h)

Objetivo: `render.py` genera un JPEG de 1080 × 1350 px (formato vertical 4:5 del feed) que se entiende en dos segundos.

1. **2a · Diseño (Claude Design): **Design System + plantillas de post y story en el estilo pixel art cozy cyberpunk, hasta dejarlas aprobadas. 2–3 h.
2. **2b · Plantilla en código: **la plantilla aprobada pasa a templates/post.html con variables Jinja2; render.py la rellena y Playwright la captura a 1080 × 1350. Sustituye a matplotlib para la imagen.
3. **Contenido mínimo:** fecha, precio medio, hora más barata y más cara, y gráfico de barras de las 24 horas.
4. **Color por nivel de precio** (barato / medio / caro respecto a la media del día), no por periodo valle-llano-punta: fines de semana y festivos son valle entero y el color dejaría de informar.
5. **El dato útil**: la mejor franja de 3 horas seguidas ("pon la lavadora de 14 a 17 h"). Es lo que hace que alguien guarde o comparta el post.
6. **Aclarar la unidad:** término de energía del PVPC en €/kWh, sin IVA ni impuesto eléctrico. Ponlo en letra pequeña en la imagen.
7. Capturar con Playwright como **JPEG** (`type='jpeg'`, calidad \~90): la API solo acepta JPEG.
8. Texto del pie (`caption`) generado desde los mismos datos: resumen en 3 líneas + 5–10 hashtags fijos (#preciodelaluz #PVPC #ahorroenergetico…).

**Entregable:** una carpeta `out/` con imágenes de varios días reales que te parezcan publicables. Itera aquí el diseño: es lo único que verá el público.

## Fase 3 · Publicación en Instagram (2–3 h)

Objetivo: `publish.py` publica la imagen del día y deja constancia para no publicar dos veces.

1. **URL pública de la imagen.** Meta descarga el JPEG desde una URL; no admite subir el archivo. Haz commit de `out/YYYY-MM-DD.jpg` y usa `https://raw.githubusercontent.com/<usuario>/<repo>/main/out/YYYY-MM-DD.jpg`. Un nombre distinto cada día evita problemas de caché.
2. **Crear el contenedor:** `POST https://graph.instagram.com/<versión>/<IG_USER_ID>/media` con `image_url` y `caption`. Usa `graph.instagram.com`: con token de Instagram Login, `graph.facebook.com` falla con un error de token engañoso.
3. **Esperar a que esté listo:** consulta `GET /<container_id>?fields=status_code` cada pocos segundos hasta `FINISHED`. `ERROR` o `EXPIRED` → fallo.
4. **Publicar:** `POST /<IG_USER_ID>/media_publish` con `creation_id` = el ID del contenedor.
5. **Idempotencia:** guarda fecha e ID del post en `state/published.json`. Si el día ya figura, el script termina sin hacer nada.

Límite de la API: 50 publicaciones en 24 h (un carrusel cuenta como una). Con un post diario no es un problema.

**Entregable:** primer post real publicado desde tu PC con el script. Hazlo con la cuenta aún sin seguidores.

## Fase 4 · Ejecución diaria con GitHub Actions (2 h)

Objetivo: el workflow `.github/workflows/daily.yml` ejecuta `main.py` (fetch → render → publish) cada tarde sin intervención.

1. **Varios disparos programados** en UTC, todos después de las 20:15 de Madrid tanto en invierno como en verano: `35 19 * * *`, `5 20 * * *` y `35 20 * * *`. Gracias a la idempotencia de la Fase 3, solo el primero que encuentra datos publica; el resto no hace nada.
2. **Evita el minuto :00**: los disparos programados de GitHub se retrasan más a las horas en punto.
3. Añade `workflow_dispatch` para lanzarlo a mano desde la web o el móvil.
4. `permissions: contents: write` para poder hacer commit de la imagen y del estado.
5. `concurrency` con un grupo fijo para que dos ejecuciones no se solapen.
6. Pasos: `actions/checkout` → `actions/setup-python` (con caché de pip) → `pip install -r requirements.txt` → `python main.py` → commit y push de `out/`, `data/` y `state/`.

**Entregable:** tres días seguidos publicados sin tocar nada.

## Fase 5 · Robustez (2–3 h)

Objetivo: que el sistema aguante meses solo y te avise cuando no. El fallo silencioso típico es el token caducado: el canal deja de publicar y nadie se entera.

1. **Renovación del token.** Workflow aparte (`refresh.yml`) los días 1 y 15 de cada mes:
   - `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<token>`. Funciona con tokens de más de 24 h que no hayan caducado, y da otros 60 días.
   - Guarda el token nuevo con `gh secret set IG_TOKEN`, autenticado con `GH_PAT`.
   - Si el token llega a caducar, no hay renovación posible: hay que generarlo de nuevo a mano en el panel de Meta.
2. **Alertas.** GitHub ya envía un correo cuando falla una ejecución programada. Añade un mensaje de Telegram (bot gratuito) en dos casos: error, o último disparo del día sin datos publicados.
3. **Workflows dormidos.** GitHub desactiva los programados de un repo público tras 60 días sin actividad. El commit diario de la imagen lo evita; si un día deja de publicar, revisa esto primero.
4. **Comprobaciones de cordura** antes de publicar: ningún valor nulo, número de horas correcto y precios dentro de un rango razonable. Mejor no publicar que publicar un dato erróneo.

**Entregable:** una renovación de token ejecutada con éxito y una alerta de prueba recibida en Telegram.

## Fase 6 · Contenido y crecimiento (opcional, continuo)

Con el MVP en marcha, cada mejora es un mini proyecto nuevo de automatización. En orden de impacto:

1. **Carrusel** (2–4 h): portada con el resumen, segunda imagen con la tabla de 24 h, tercera con la comparación con ayer y la media del mes. Los carruseles suelen guardarse más que una imagen suelta.
2. **Resumen semanal y mensual** (2–3 h): aprovecha el histórico de `data/` para publicar los domingos y el día 1.
3. **Historias** (1–2 h): misma imagen en formato 1080 × 1920 como story diaria. La publicación de stories por API exige cuenta de Empresa.
4. **Reel corto** (4–8 h): vídeo de 10–15 s con las barras animadas, generado con matplotlib + ffmpeg. Requiere subir vídeo, algo más complejo.
5. **Texto con IA** (2 h): un pie de foto distinto cada día, con un comentario sobre por qué sube o baja el precio. Ojo: la API de un LLM no es gratis; si quieres coste cero, usa plantillas de texto con variaciones.

Crecer en seguidores dependerá más de la constancia y del dato útil (la franja para la lavadora) que de la técnica.

## Cronograma: tiempo y coste por fase

El MVP (Fases 0–5) son unas 13–20 horas y 0 €. A un ritmo de 4–5 horas por semana, el canal publica solo en 3–4 semanas.

| Fase | Tiempo estimado | Coste | Hito |
| --- | --- | --- | --- |
| 0 · Cuentas y credenciales | 2–3 h | 0 € | Secretos creados y llamada de prueba OK |
| 1 · Datos del PVPC | 2–3 h | 0 € | 24 precios validados en local |
| 2 · Imagen | 3–5 h | 0 € | JPEG publicable de varios días reales |
| 3 · Publicación | 2–3 h | 0 € | Primer post publicado desde el PC |
| 4 · GitHub Actions | 2 h | 0 € | Tres días seguidos sin intervención |
| 5 · Robustez | 2–3 h | 0 € | Token renovado y alerta de prueba recibida |
| **Total MVP** | **13–20 h** | **0 €** | **Canal autónomo** |
| 6 · Mejoras (opcional) | 2–8 h cada una | 0 € (salvo texto con LLM de pago) | Carrusel, resúmenes, stories, reels |

Las estimaciones suponen soltura con Python y algo de experiencia con APIs REST. La Fase 0 es la más impredecible: el panel de Meta cambia a menudo y puede llevar más tiempo del previsto.

## Riesgos y limitaciones conocidas

| Riesgo | Qué pasa | Mitigación |
| --- | --- | --- |
| Token de Instagram caducado | Deja de publicar sin avisar | Renovación quincenal automática + alerta de Telegram |
| Retraso de REE o de GitHub | El primer disparo no encuentra datos | Tres disparos escalonados e idempotencia |
| Cambio de hora | Días de 23 o 25 h; el cron en UTC no se ajusta | `zoneinfo` + validación del nº de horas; disparos válidos en invierno y verano |
| Cambios de API de Meta | Versión de la API retirada o panel distinto | Versión de la API en una variable; revisar avisos de Meta cada pocos meses |
| Workflow desactivado por inactividad | GitHub pausa los programados tras 60 días | El commit diario mantiene el repo activo |
| Secretos expuestos | Repo público visible para todos | Todo en GitHub Secrets; nunca imprimir tokens en logs |
| Dato erróneo publicado | Pérdida de credibilidad | Comprobaciones de cordura; mejor saltarse un día que publicar mal |
| Cambios de precio de GitHub Actions | Hoy gratis en repo público | Coste por ejecución mínimo; si cambiara, mover a otro runner gratuito |

Si algún día lo conviertes en un proyecto con ingresos, revisa antes la cláusula de no competencia de tu contrato: el contenido es del sector energético.

## Checklist de puesta en marcha

- [ ] Cuenta de Instagram profesional y pública
- [ ] App de Meta con los dos permisos y tu cuenta como tester
- [ ] Token de Instagram e ID de cuenta generados
- [ ] Token de ESIOS recibido
- [ ] Repo público con los cuatro secretos
- [ ] `fetch_pvpc.py` cuadra con ESIOS en dos días distintos, uno laborable y uno de fin de semana
- [ ] `render.py` genera imágenes que te parecen publicables
- [ ] Primer post publicado desde el PC
- [ ] `daily.yml` publica tres días seguidos solo
- [ ] `refresh.yml` renueva el token y actualiza el secreto
- [ ] Alerta de Telegram probada
- [ ] Probado el día de cambio de hora (próximo: finales de marzo de 2027)

## Fuentes

- [Instagram Platform – Content Publishing (Meta)](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
- [Instagram API with Instagram Login – Content Publishing](https://developers.secure.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing)
- [Credenciales de Instagram sin App Review para cuenta propia (keryx)](https://keryx-a0a6a5.gitlab.io/how-to/instagram-credentials/)
- [Renovación del token y modo desarrollo (omnipost)](https://packagist.org/packages/omnipost/instagram)
- [GitHub Actions: precios 2026, gratis en repos públicos](https://resources.github.com/actions/2026-pricing-changes-for-github-actions/)
- [GitHub Changelog: actualización de precios de Actions](https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/)
- [REE: publicación diaria del PVPC a las 20:15](https://www.ree.es/en/press-office/news/press-release/2021/05/red-electrica-publishes-electricity-prices-new-tariff-for-small-consumers)
- [Tarifasgasluz: PVPC horario como media de cuartos de hora](https://tarifasgasluz.com/comparador/pvpc)
