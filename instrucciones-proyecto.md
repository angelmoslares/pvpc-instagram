# Proyecto: Instagram automático del PVPC diario

## Objetivo
Proyecto personal de aprendizaje de automatización: una cuenta de Instagram que publica cada día, sin intervención, el precio PVPC del día siguiente. Coste objetivo: 0 €. La guía completa del proyecto está en el archivo "Guía Instagram automático del PVPC diario.md" del conocimiento del proyecto; úsala como referencia de fases, stack y decisiones ya tomadas.

## Quién soy y qué domino
- Domino el mercado eléctrico español a nivel experto (PVPC, ESIOS, OMIE, cuartohorario). No me expliques conceptos básicos del sector.
- Programo en Python (Anaconda) sobre Windows con VS Code. Nivel intermedio con APIs REST y GitHub Actions.

## Stack decidido (no proponer alternativas salvo que lo pida)
- Python 3.12: requests, pandas, matplotlib, zoneinfo.
- GitHub, repositorio público, con GitHub Actions para la ejecución programada y GitHub Secrets para credenciales.
- Fuente de datos: API ESIOS, indicador 1001 (PVPC 2.0TD), zona Península (geo_id 8741), cabecera x-api-key. Plan B: apidatos.ree.es.
- Publicación: Instagram API with Instagram Login (graph.instagram.com), app de Meta en modo desarrollo con mi cuenta como tester.
- Alertas: bot de Telegram.

## Estructura del repositorio
```
pvpc-instagram/
├── main.py                 # orquesta fetch → render → publish
├── src/
│   ├── fetch_pvpc.py
│   ├── render.py
│   ├── publish.py
│   ├── notify.py
│   └── state.py
├── data/                   # pvpc_YYYY-MM-DD.csv
├── out/                    # YYYY-MM-DD.jpg
├── state/published.json
├── requirements.txt
├── README.md
└── .github/workflows/
    ├── daily.yml
    └── refresh.yml
```

## Restricciones que siempre hay que respetar
- Coste 0 €: nada de servicios de pago (incluidas APIs de LLM) salvo que yo lo pida.
- Ningún recurso de la empresa: nada del tenant M365 corporativo ni licencias del trabajo.
- Credenciales solo en GitHub Secrets o en un .env local ignorado por git. Nunca en el código ni impresas en logs.
- Zona horaria: toda la lógica de fechas en Europe/Madrid con zoneinfo. El cron de GitHub va en UTC y no cambia con el horario de verano; disparos a 19:35, 20:05 y 20:35 UTC. Tener en cuenta días de 23 y 25 horas.
- REE publica el PVPC de D+1 hacia las 20:15. Datos incompletos = estado "pendiente", no error.
- Idempotencia: nunca publicar dos veces el mismo día (state/published.json).
- Instagram: solo JPEG, la imagen debe estar en una URL pública (raw.githubusercontent.com), formato 1080 × 1350. Usar graph.instagram.com, no graph.facebook.com. El token dura 60 días y se renueva los días 1 y 15.
- Precio mostrado: término de energía del PVPC en €/kWh sin impuestos; indicarlo en la imagen.

## Cómo quiero las respuestas
- En español, directas, sin rodeos.
- Código completo y copiable de cada archivo, nunca fragmentos ni diffs sueltos.
- Si hay varias formas de hacerlo, dime cuál recomiendas y por qué.
- Anticipa limitaciones técnicas conocidas antes de que fallen.
- Verifica datos que puedan haber cambiado (APIs de Meta, límites, precios de servicios) en vez de responder de memoria.
- Indica siempre cómo probar en local en Windows antes de subirlo a GitHub.

## Estado actual
- Guía del proyecto terminada.
- Siguiente paso: Fase 0 (cuentas y credenciales) y Fase 1 (fetch_pvpc.py).
