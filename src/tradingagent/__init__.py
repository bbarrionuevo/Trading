"""Agente de trading autónomo guiado por eventos: ingesta → inteligencia → riesgo → ejecución.

La ejecución está desacoplada de la inteligencia (solo se comunican por intenciones de orden en una
cola) y las alertas corren en su propio hilo: nada de lo que pase con Telegram o con una fuente de
noticias puede demorar al motor. Por defecto todo opera en paper (docs/REGLAS.md).
"""
__version__ = "0.1.0"
