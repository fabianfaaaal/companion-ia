# Proveedores, preparación local e historiales
Preview 0.0.6a1. Implementación probada con datos sintéticos; conexión real a APIs pendiente de credenciales propias.

## Primer recorrido
1. Cerrá la versión anterior y abrí dist/providers-preview/CompanionIA/CompanionIA.exe. Ollama sigue siendo el proveedor inicial sin tarifa de API.
2. En Proveedores, Agregar cuenta permite DeepSeek u OpenAI. Usá alias separados para cada cuenta y el modelo correcto. DeepSeek sugiere deepseek-flash según documentación oficial revisada el 2026-10-31.
3. Pegá la clave únicamente en el campo del programa, nunca en un chat ni en Git. Por defecto vive en RAM; en Windows podés recordar una copia protegida por DPAPI del usuario. No es una contraseña del chat.
4. Seleccioná la cuenta y autorizá sus cargos/cuotas si querés hacer solicitudes. Agregar la cuenta no consulta la API ni compra saldo.
5. Elegí qué información adicional puede salir: conversación reciente, notas/fotos, historiales importados y contexto Web/Entorno. El mensaje actual y las instrucciones de personalidad forman parte de la solicitud.
6. Enviá un mensaje. Si está habilitado, Ollama prepara una guía estructurada y selecciona entre fuentes ya autorizadas. No modifica tu mensaje original ni concede permisos.
7. Ver contexto muestra en RAM los mensajes preparados; durante la solicitud se actualizan tras la preparación. La clave no se incluye. El inspector no es una copia completa del HTTP.
8. La respuesta identifica proveedor y cuenta. Voz lee la respuesta final con el motor configurado. La solicitud interna se inspecciona como texto; no se vocaliza automáticamente.
9. Si falla la API, el respaldo local está activado de inicio y avisa del cambio. Usa el mismo contexto filtrado, no agrega datos excluidos. Se puede desactivar.
10. Uso, cuotas y saldo se muestran solamente si el proveedor los informa. No informado significa desconocido, nunca saldo ilimitado ni cero cargos.

## Crear acceso DeepSeek
Abrí https://platform.deepseek.com/ y creá/iniciá tu cuenta. En API keys creá una clave y copiala al campo del programa.
La documentación oficial enlaza esa plataforma para obtener la clave: https://api-docs.deepseek.com/ .
La API tiene tarifas propias: https://api-docs.deepseek.com/quick_start/pricing/ . Chat web, API y suscripciones son accesos diferentes. No se garantiza crédito promocional ni API gratuita.
Si no querés contratar ni cargar saldo, dejá Ollama local. No hay recargas ni compras automáticas.
Saldo reportado DeepSeek: https://api-docs.deepseek.com/api/get-user-balance/ .
Una solicitud fallida puede haber consumido tokens o cargos; el respaldo local no revierte eso.

## Personalidad y profundidad
Personalidad permite nombre, compañera/compañero/mascota/asistente, estilo amistoso/afectivo/trabajo e instrucciones propias. Guardar aplica la configuración y reinicia la conversación derivada.
El vínculo afectivo es una simulación configurable. No se promete contenido sin límites ni eludir las reglas del proveedor. Archivo íntimo cifrado y modalidades adultas completas siguen fuera de este hito.
Profundidad Auto reconoce frases como pensá mucho, explicámelo a fondo, dame un resumen o sé breve. Breve/Profunda son elecciones explícitas que prevalecen sobre esa detección. La selección cambia instrucciones y presupuesto, no expone razonamiento interno.
La profundidad y el límite de salida se recuerdan. No se garantiza que todos los modelos interpreten igual las instrucciones.
Qwen TTS sigue experimental y lento; Piper es la opción de menor demora. Textos muy extensos pueden superar los límites de lectura del preview.

## Historiales y múltiples cuentas
La API no inicia sesión en los chats existentes. La primera integración importa archivos JSON que elegís y revisás.
Formatos: exportación ChatGPT en lista con mapping y rama activa; o JSON normalizado:
```json
{"conversations":[{"id":"synthetic-1","title":"Prueba","messages":[{"role":"user","content":"Mensaje de prueba"},{"role":"assistant","content":"Respuesta no verificada"}]}]}
```
No hay importador ZIP, sincronización automática ni soporte nativo universal para todos los servicios. Extraé el JSON localmente si tu exportación viene comprimida.
En Historiales, elegí archivo, proveedor, alias y conversaciones. La vista previa es temporal; guardar requiere confirmación y memoria habilitada. El archivo todavía no está cifrado: no importes intimidad en este preview.
Cada cuenta conserva procedencia, autor de mensajes y conversación. Las respuestas del asistente externo son registros no verificados, no hechos del usuario.
El filtro de cuenta también delimita el contexto; la casilla permite desactivarlo. Esa elección se recuerda al reabrir. Podés borrar una conversación o todas las copias de una cuenta sin modificar al proveedor original.
Importar no habilita su salida a una API: también debés seleccionar Historiales importados en Proveedores.

## Límites y pruebas
- Adaptadores DeepSeek y OpenAI Chat Completions, hosts HTTPS fijos, sin URLs arbitrarias, redirecciones ni cambio silencioso de cuenta.
- Primera entrega externa sin streaming: recibe respuesta completa, luego la muestra y vocaliza.
- Los conectores no dan acceso a cookies, cuentas web ni chats previos por poseer una clave.
- Ollama sigue necesario para preparar contexto y responder durante fallos. Si también falla, se muestra error.
- Privado cancela ambas rutas y consultas de saldo, limpia el prompt pendiente y las claves temporales; DPAPI y copias guardadas no se borran por desactivar funciones.
- Windows DPAPI protege claves para ese usuario del sistema; no protege frente a cualquier programa que ya actúe como ese usuario. Linux permite claves de sesión en este preview.
- Sin telemetría de conversaciones, tarifas estimadas inventadas ni promesa de disponibilidad del proveedor.
- Pruebas de transporte remoto usan mocks. La preparación local real se comprobó con respuesta remota simulada; no valida credenciales ni disponibilidad real del servicio.
