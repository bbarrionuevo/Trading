# Datos de entrenamiento de TRADING (rama `data`)

Los escribe el recolector (`.github/workflows/recoleccion.yml`) cada 15 minutos. Solo datos públicos.

- `eventos/AAAA-MM.jsonl`: una noticia por línea: titular, fragmento, fuente y nivel, horas de
  publicación y llegada, activos detectados, si es la original o una copia (`dedup`, `cluster`) y si
  el filtro la marcó como ruido.
- `reacciones/AAAA-MM.jsonl`: la reacción del precio a cada noticia original, por par (velas públicas
  de 1 minuto): retornos a 1, 5, 15 y 60 minutos desde la publicación, extremos de la hora y lo que
  quedaba por capturar desde la llegada (`r_recv_*`).
- `estado.json`: lo ya visto por fuente, lo pendiente de etiquetar, la salud de las fuentes y los
  conteos por día.

No se edita a mano: el recolector agrega líneas y reescribe el estado en cada corrida.
