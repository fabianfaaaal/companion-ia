# Voz preparada y preferencias iniciales

## Esta versión
El problema observado era que el ejecutable no encontraba el entorno de voz preparado desde otra aplicación Windows. Ahora busca una ubicación registrada en ~/.companion-ia/voice-location.json y rutas de respaldo. Los instaladores nuevos usan ~/.companion-ia, fuera de AppData virtualizado.

En el primer inicio normal, memoria, lectura, permiso de micrófono, avisos, observación de títulos de ventanas y contexto web/entorno están activados. Podés desmarcarlos y se recuerda esa elección. Esto modifica el criterio inicial opt-in por decisión del inversor del 2026-10-31.
El aviso de inicio explica los permisos. La memoria sigue guardando solo notas, fotos y avisos confirmados; no archiva automáticamente conversaciones. No está cifrada: usá información general.
Micrófono habilitado significa permiso para Dictar: no escucha hasta pulsar ese botón. No hay cámara. Observar ventanas solo lee el título en RAM. Web envía la consulta al pulsar Buscar; no se agregan búsquedas automáticas.
Privado mantiene prioridad, también después de reiniciar. Reactivar es una acción local explícita.

## Probar
1. Cerrá manualmente el programa anterior y abrí voice-ready-preview/CompanionIA/CompanionIA.exe.
2. Comprobá el estado del motor. Si falta, pulsá Revisar voz: reintenta la detección y permite seleccionar una instalación preparada.
3. Seleccioná voz Natural (Qwen si está instalado) o Ligera (Piper). Si Qwen falta, Natural usa Piper y el estado lo identifica.
4. Detectá Ollama, elegí qwen2.5:7b y enviá un saludo sintético. El modelo de conversación genera el texto; el motor TTS lee esa respuesta, no el razonamiento interno.
5. Pulsá Dictar, hablá y pulsá Terminar dictado. Revisá el texto antes de enviarlo. Para elegir micrófono, desmarcá y volvé a marcar Habilitar micrófono: consulta los dispositivos disponibles. Si Windows deniega acceso, revisá Configuración > Privacidad y seguridad > Micrófono.
6. Desmarcá las funciones que no quieras. Al reabrir deben conservarse apagadas.

## Motores gratuitos
- Piper/Daniela es_AR: alternativa CPU liviana. Licencias y límites en voice-notes-preview.md.
- Qwen3-TTS-12Hz-0.6B-CustomVoice: opción experimental, español con voz Serena, código/pesos Apache-2.0 según fuentes registradas por el preparador. No clona a ninguna persona. Usa GPU CUDA y necesita evaluación auditiva en español.
- Whisper base: transcripción local CPU/int8, audio solo RAM.

No se afirma que Qwen sea universalmente la voz más natural. Selección propuesta para comparar en el equipo real. Su API de este preview devuelve segmentos completos; no se promete streaming real ni primer audio en 97 ms. La primera carga puede demorar y compite por VRAM con Ollama.

## Preparación para desarrollo u otra PC
Python 3.12 y scripts/setup_voice.py --install preparan Piper+Whisper.
scripts/setup_qwen_voice.py --install prepara Qwen en otro entorno independiente y descarga paquetes/modelo. Es una instalación grande, incluida la rueda CUDA de PyTorch; consultar el plan del script antes de instalar.
Los motores/modelos quedan fuera del repo y del ZIP. El instalador final unificado sigue pendiente.
La inferencia no descarga modelos ni envía audio a terceros.

## Proveedor del texto
El adaptador actual envía prompts a Ollama local. Una API externa como OpenAI todavía no está integrada: requiere configuración de credenciales, permiso de envío y evaluación de costo. No se usa una cuenta ChatGPT automáticamente ni se prometen llamadas gratuitas.

## Medición local del motor natural
Ensayo sintético del 2026-10-31 en RTX 3080 de 10GB con qwen2.5:7b cargado: Qwen TTS tardó 46.094s en la primera frase para 6.88s de audio; otra frase en el mismo proceso tardó 7.996s para 2.4s de audio. Pico global observado 9025MiB de VRAM usada, sin OOM.
Son observaciones puntuales, no garantía. La aplicación de este preview reinicia el worker por respuesta, por lo que vuelve a pagar el costo de carga. No es voz conversacional en tiempo real. Elegí Ligera/Piper para menor demora; Qwen queda como opción experimental de calidad para evaluar escuchando.
El indicador Preparando Qwen/Piper muestra segundos mientras se genera audio. No se probó el micrófono real automáticamente.
