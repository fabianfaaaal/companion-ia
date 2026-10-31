# Prototipo de escritorio — primera prueba Windows

Esta entrega abre una ventana de chat con Ollama. Es una versión de prueba, no el instalador completo.

## Abrir
En el paquete descomprimido, abrir CompanionIA/CompanionIA.exe. Mantener _internal y las demás carpetas junto al ejecutable.
No hace falta Python. Ollama debe estar abierto y tener un modelo local. Modelo cotidiano sugerido: qwen2.5:7b (Apache-2.0, unos 4.7 GB de descarga). No se incluye el modelo en el ZIP.

1. Esperar la detección o pulsar Detectar Ollama.
2. Elegir modelo, nombre y estilo Charla/Trabajo.
3. Escribir mensaje y pulsar Enviar.
4. Probar Detener y Modo privado.
5. Cerrar/reabrir para verificar que no se conserva la conversación. Si cerraste en privado, usar Reactivar y luego Detectar Ollama.

## Alcance actual
- Conversación y contexto solo en RAM, con historial reciente y tamaño limitado.
- Sin recuerdos, sensores, avatar animado ni APIs externas.
- Privado cancela y espera al worker antes de confirmarse; fallo de detención mantiene bloqueo.
- Persistencia solo de estado técnico privado en el directorio de datos de la app, fuera del repo.
- Un solo proceso por cuenta/directorio de datos.
- Respuesta final ensamblada antes de mostrarla. No se reproduce el campo thinking.
- Contexto preparado inspeccionable; no se presenta como copia exacta de la solicitud HTTP.
- Cambio de nombre/estilo/modelo no borra por sí solo el historial: usar Nueva conversación.
- Configuración y nombre todavía no se conservan al cerrar.

## Límite del motor
Comprobar metadatos locales evita elegir modelos que se declaran remotos; no aísla la red de un Ollama modificado. La cancelación de la app no puede retirar datos que ya recibió el motor ni garantizar liberación inmediata de GPU.
Ollama puede administrar registros propios; «memoria apagada» describe la persistencia de Companion, no una garantía sobre software externo.

En la combinación probada Ollama 0.40.13 + qwen3:4b, /api/show declara thinking.values=[true]. Enviar think=false produjo razonamiento en content. La app consulta capacidades y usa think=true cuando es obligatorio para mantener separados esos campos. Qwen3 puede agotar el límite de generación sin entregar respuesta; se informa el error y no se muestra la traza. Qwen2.5 se prueba como perfil cotidiano alternativo, sin borrar Qwen3 ni cambiar a cloud.

## Medición
Se muestran tiempo total del trabajo, primer texto del motor y velocidad reportada de generación. La respuesta visible llega al completar y validar el texto; no confundir ese primer fragmento del motor con primer texto en pantalla.
Si hay razonamiento, los contadores del motor pueden incluirlo. Son observaciones de una solicitud, no un benchmark reproducible ni garantía de velocidad.

## Desarrollo y build
Python 3.12 en entorno virtual:
- pip install -r requirements-desktop.txt
- python -B -m unittest discover -s tests -v
- python apps/desktop/launcher.py (con PYTHONPATH=src), o python -m companion.desktop tras instalar el proyecto con extras desktop.
Windows build: pip install -r requirements-build-windows.txt y python scripts/build_windows.py.
El ZIP resultante es un paquete portable de prueba; DLLs Qt permanecen separadas y reemplazables. Ver THIRD_PARTY_NOTICES.md.

## Pendiente de aceptación
Prueba visual y de experiencia del usuario; instalación en Windows limpio sin herramientas de desarrollo; firma digital; instalador guiado del motor; benchmark repetido; memoria y Cinemateca.
