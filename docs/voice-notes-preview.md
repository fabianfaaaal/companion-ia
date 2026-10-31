> Documento del hito anterior. Para preferencias activadas de inicio y corrección de detección, consultar [voice-ready-preview](voice-ready-preview.md).

# Prueba de notas y voz local

Este preview suma notas manuales y audio al chat. El archivo ejecutable está en dist/memory-voice-preview/CompanionIA/CompanionIA.exe. Mantener _internal junto al programa; no sustituye la versión de escritorio anterior.

## Qué podés probar
1. Cerrá la versión anterior cuando hayas copiado lo que quieras conservar de su charla temporal.
2. Abrí el ejecutable nuevo con Ollama abierto. Si vuelve en privado, usá Reactivar y Detectar Ollama.
3. Marcá Leer respuestas para escuchar las respuestas finales con voz neural. La primera preparación puede demorar varios segundos.
4. Marcá Habilitar micrófono, aceptá el permiso y pulsá Dictar. Pulsá Terminar dictado o esperá el máximo de 30 segundos. Revisá/corregí el texto antes de Enviar.
5. Para conservar un dato, activá Memoria de notas y usá Guardar nota. Podés ver/borrar notas; desactivar memoria limpia el contexto de chat pero no borra las notas.

No se importa automáticamente ninguna conversación anterior. No se programa un aviso al guardar una nota.

## Voz
Piper genera voz local con Daniela es_AR. Whisper base transcribe en CPU/int8 para no ocupar VRAM del LLM. Los motores/modelos se prepararon como componente opcional separado, fuera del baúl y del repositorio.

La lectura comienza después de validar la respuesta final y genera audio por bloques. No se vocaliza el campo thinking. Este hito todavía no hace lectura token a token del LLM, detección continua de habla ni diálogo simultáneo de manos libres.
Las primeras cargas de voz tienen latencia; la calidad de voz y precisión del dictado requieren aceptación del usuario. La transcripción puede equivocarse; nunca se envía sola.

## Privacidad
Micrófono y reproducción tienen permisos propios, independientes de memoria. Micrófono apagado hasta habilitación y botón Dictar; señal visible y máximo 30 s. No se hicieron capturas reales de micrófono para las verificaciones automáticas.
Audio en RAM y pipes entre procesos, sin WAV ni logs de contenido. El motor solo usa modelos ya descargados al inferir; su preparación requiere Internet.
Privado cancela captura y reproducción, vacía colas, mata procesos del motor y verifica su salida antes de confirmar detención. Fallos de cierre mantienen ERROR y admisión bloqueada.
El texto dictado queda en el editor. Se guarda solamente si el usuario crea una nota manual. Una nota permanece sin cifrar en este preview; usar datos generales, no archivo íntimo.

## Memoria y capacidades
SQLite/FTS5 conserva únicamente notas elegidas manualmente. Memoria apagada por defecto al abrir; activar es consentimiento de uso de notas, no de sensores ni copia retroactiva del chat.
Las notas recuperadas se incluyen como datos no confiables con referencias/títulos en el contexto preparado. El modelo no obtiene permisos ni ejecuta acciones por afirmar que hizo algo.
Consultas de funciones, recordatorios y búsqueda web tienen respuestas verificadas del programa; recordatorios/agenda/web siguen sin integrar. No aceptar una promesa verbal del modelo como confirmación de una acción.

## Preparar en otra PC
El ZIP incluye runtime de la interfaz y audio, pero no el entorno separado de Piper/Whisper ni los modelos. En este equipo ya están preparados.
El instalador final que resuelva todos los componentes con pocos botones sigue pendiente.
Para desarrollo, Python 3.12 y scripts/setup_voice.py --install prepara el componente fuera del repo. Requiere las condiciones de licencia del motor y modelos; no ejecutar durante privado.
El motor usa solo localhost para el LLM; el audio no se manda a una API externa. Esto no certifica la red de un Ollama modificado.

## Licencias
Aplicación: Apache-2.0. Piper 1.8.0: GPL-3.0-or-later. faster-whisper 1.2.1: MIT. Daniela: la ficha declara dataset CC-BY-SA-4.0; se conserva MODEL_CARD, procedencia y atribución en el directorio del motor sin asumir que toda la voz sea MIT.
Modelos de voz y transcripción no se distribuyen dentro del ejecutable de Qt. Separación de procesos no constituye por sí sola una conclusión legal.
Fuentes: https://github.com/OHF-Voice/piper1-gpl ; https://github.com/SYSTRAN/faster-whisper ; https://huggingface.co/rhasspy/piper-voices/blob/main/es/es_AR/daniela/high/MODEL_CARD

## Validación del preview
95 pruebas automatizadas con datos sintéticos. Recorrido real de voz y transcripción sintéticas en RAM, nota recuperada por el LLM, privado y sesión limpia. Ejecutable empaquetado verificado con SQLite/FTS5 y TTS del motor externo.
Salida de audio verificada con muestras silenciosas; sin escucha ni captura de micrófono reales automatizadas. Escucha, micrófono elegido y experiencia quedan para prueba manual.
