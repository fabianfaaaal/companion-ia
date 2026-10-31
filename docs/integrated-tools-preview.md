# Preview de herramientas integradas

Versión 0.0.4a1. Prototipo: no es una entrega estable ni un instalador completo.

## Probar en Windows

1. Abrí CompanionIA.exe desde la carpeta tools-preview/CompanionIA. Conservá _internal junto al ejecutable. La otra versión debe cerrarse manualmente antes: ambas usan el mismo archivo local.
2. En Conversación, activá Memoria local si querés guardar notas, avisos o fotos. Este prototipo NO cifra esos datos: usá contenido de prueba.
3. En Agenda, creá un aviso con fecha futura y confirmá guardarlo. Activá los avisos y mantené la app abierta. El calendario muestra los del día seleccionado; el sonido requiere permiso por aviso.
4. En Cinemateca, importá una foto sintética JPEG/PNG/WebP con título y descripción. Se guarda una copia local y miniatura. La búsqueda del chat usa esos textos: no analiza los píxeles ni inventa lo que muestra la foto.
5. En Web, revisá la consulta, elegí Bing o Wikipedia, autorizá su envío y pulsá Buscar. Podés abrir una fuente y habilitar sus fragmentos para la próxima respuesta.
6. En Entorno, indicá tu estado. Observar títulos de ventanas es otro permiso, inicialmente apagado; compartir ese contexto con el chat también requiere marcar su casilla.
7. Activá Modo privado desde cualquier pestaña: debe detener trabajos y borrar contexto temporal. Detener audio también está disponible en la barra superior.

El micrófono graba únicamente por botón; la transcripción se revisa antes de enviar. La voz lee la respuesta final, no cada token. Requiere el motor opcional preparado según [notas y voz](voice-notes-preview.md).

## Límites comprobables

- Avisos locales dentro de la app, sin notificaciones de Windows ni ejecución con la app cerrada. Sin garantía con suspensión o corte de energía.
- Cinemateca inicial de fotos y descripciones; no conserva automáticamente conversaciones ni genera películas.
- Web recibe fragmentos del proveedor; no navega autónomamente ni verifica que una fuente sea correcta. La consulta sale de la PC.
- Entorno: estado autoinformado y título de ventana en Windows, solo RAM. Sin cámara, lectura de pantallas, identificación de personas ni inferencia automática del sueño.
- Memoria apagada impide acceder al archivo conservado; no borra los datos guardados anteriormente.
- Una petición escrita prepara la herramienta; solo los controles de confirmación crean avisos o autorizan búsquedas.

## Desarrollo y verificación

Instalar requirements-desktop.txt y ejecutar python -B -m unittest discover -s tests -v.
Construir en Windows con python scripts/build_windows.py --variant tools-preview.
El paquete incluye Pillow para validar fotos y generar miniaturas.
Los autochequeos --smoke-test --memory-self-test --tools-self-test requieren --state-dir temporal explícito y usan datos sintéticos.
Pruebas automáticas no equivalen a aceptación manual de sonido, micrófono o experiencia.
