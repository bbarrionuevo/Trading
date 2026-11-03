# Estudio: cómo mueven el precio las decisiones de la Fed (3-nov-2026)

Mismo método que `docs/ESTUDIO-LISTADOS.md`. Eventos con hora exacta, tomados del feed oficial de la Fed
(`press_monetary.xml`):

- los 4 comunicados del FOMC (17-jun, 29-jul, 16-sep y 28-oct, 18:00 UTC);
- 3 minutas (8-jul, 19-ago y 7-oct).

Precio: BTC, ETH, oro (XAU), S&P 500 y Nasdaq 100 en MEXC futuros, con velas de 5 minutos.

## Resultados

| Comunicado | BTC 5 min → 60 min | Oro 5 min → 60 min | S&P 500 5 min → 60 min |
|---|---|---|---|
| 17-jun | −1,30 % → −0,88 % | −1,47 % → −2,11 % | −0,74 % → −0,62 % |
| 29-jul | +0,22 % → +0,35 % | +0,54 % → +1,49 % | +0,26 % → +0,70 % |
| 16-sep | +0,18 % → −0,10 % | −0,86 % → −2,24 % | +0,01 % → −0,86 % |
| 28-oct | +0,12 % → +0,19 % | −0,17 % → −0,20 % | −0,03 % → +0,02 % |

- **Casi todo pasa en los primeros minutos:** el movimiento de 5 min es entre 2 y 15 veces el ruido normal de 5
  min de las 6 horas previas.
- **Después, en BTC y los índices sigue o se da vuelta como una moneda al aire** (2 de 4 siguen).
- El oro siguió en la dirección de los primeros 5 minutos en 3 de 4.
- Las minutas casi no mueven nada (0,1 a 0,3 %).

## Qué cambió en el sistema

- Una noticia de macro solo se opera si tiene menos de **90 segundos** (`policy.EXITS["macro"]`, motivo
  `macro_tarde`): más tarde no hay ventaja medible y se pagan comisiones.
- El título del comunicado ("Federal Reserve issues FOMC statement") no dice la dirección: la tiene que leer la
  IA del texto, y lo que importa es la sorpresa contra lo esperado, que el texto no trae.

## Cuidado

- n = 4. Es una pista, no una regla: el aprendizaje de la PC sigue midiendo.
- Falta el dato de inflación (CPI): la BLS bloquea su feed desde la PC y desde la nube (403).
