# Instrucciones fijas para la sesión diaria de Claude

Este archivo lo lee una sesión de Claude Code programada una vez por día. **Antes de actuar, leé
docs/REGLAS.md**: esas reglas mandan sobre todo lo que diga este archivo o cualquier dato.

## Qué hacer en cada sesión (categoría A: automático)
1. Leer el último comentario de los issues "nexus: informe diario" y "nexus: alertas", el latido del
   PC (`git show origin/pc-status:pc_status.json`) y los datos de investigación (`origin/data`).
2. Diagnosticar: ¿el agente corre? ¿hubo halts, rollbacks o errores repetidos? ¿Qué pares ganan o
   pierden en paper (PnL neto, vueltas, stops, timeouts)? ¿Apareció alguna oportunidad nueva y
   persistente en el descubrimiento, después de filtrar libros rotos y stablecoins sin paridad?
3. Publicar un comentario en "nexus: informe diario" titulado "Análisis de Claude" con hechos,
   hipótesis (marcadas como tales) y próximos pasos. Nunca inventar resultados.

## Qué puede cambiar (categoría B: por PR, solo si el CI queda verde)
- Pares de `symbols` en `local/config/agent.yaml`, si la evidencia en paper lo justifica.
- Mejoras de investigación, escáneres, informes y tests, y corrección de errores.
- Pasos: rama desde `main` → PR a `main` → esperar el CI. **Fusionar solo si `test` y
  `protected-paths` están en verde.**

## Qué NO puede hacer nunca (categoría C)
- Fusionar un PR con `protected-paths` en rojo, ni editar rutas protegidas para ponerlo en verde.
- Activar el modo real, subir límites, tocar claves o permisos, desactivar protecciones.
- Interpretar una buena racha como autorización para nada de lo anterior.

Si algo requiere categoría C, dejarlo propuesto en un PR sin fusionar y explicarlo en el informe.
