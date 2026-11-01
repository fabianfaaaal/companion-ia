# Chat moderno, perfiles y dispositivo
Preview 0.0.8a1. No es una versión estable.

## Conversaciones
Guardado automático de mensajes visibles cuando Memoria local está activa. Está activada en un primer inicio normal; se respetan preferencias/perfiles que el usuario haya desactivado y privado persistente.
La lista izquierda permite nueva conversación, abrir, renombrar y borrar. Usuario a la derecha, companion a la izquierda, avisos separados. Emojis Unicode y texto seleccionable; Enter envía, Shift+Enter crea línea.
Se guardan texto visible y etiquetas, no claves, prompts internos ni razonamiento. El archivo todavía no está cifrado; no usar intimidad real. Los chats temporales de previews anteriores no se recuperan de otro proceso automáticamente.
Con memoria apagada, las charlas nuevas solo viven en esa sesión. Límites del preview:500 conversaciones,2000 mensajes por conversación,40k caracteres por mensaje,20MiB de texto total. Un error de guardado mantiene los mensajes pendientes en RAM y visibles en la barra; Reintentar guardado los escribe en orden sin duplicar. Cerrar con pendientes pide confirmación. Desactivar memoria o entrar en privado descarta contexto temporal, incluidos pendientes.

## Perfiles de privacidad
El botón Perfiles de privacidad abre un menú con Completo, Concentrado y Solo chat. Podés crear, guardar, sobrescribir y eliminar perfiles.
Guardar no aplica; Aplicar controles cambia permisos y abre una charla limpia para evitar arrastrar contexto del perfil anterior. La conversación archivada anterior se conserva.
Los controles diferencian guardar, percibir y compartir. Ningún perfil autoriza gastos de API ni desbloquea intimidad. Selección de cuenta y permiso de cargos siguen en Proveedores.
La aplicación recuerda las opciones elegidas. Un perfil guardado no inicia grabación ambiental continua.

## Privado
Decisión actual del inversor: privado deja únicamente chat local temporal.
Desconecta memoria/archivo, voz, micrófono, agenda, títulos de ventanas, Web, historiales importados, personalidad personalizada y APIs externas. Bloquea lectura/escritura de sus datos. El chat usa solo mensajes de la sesión privada, en RAM.
Al salir se borra la charla privada antes de restaurar opciones. Privado persiste al reiniciar. En un arranque privado sin modelo seleccionado, usá Detectar Ollama; no se consulta ningún proveedor externo.
Si no se confirma el cierre de trabajos, el estado ERROR bloquea también el chat.

## Dispositivo flotante y Memoteca
Modo dispositivo abre una ventana pequeña sin marco nativo, movible y siempre encima según permita Windows. Chat vuelve a la aplicación completa. Cerrar el dispositivo vuelve al chat; no elimina mensajes.
Pieles Mint, Ámbar y Violeta cambian la carcasa del dispositivo. Son primeras pieles, no un editor universal del programa.
Dictar y Privado conservan los permisos del núcleo. La pantalla muestra grabación/procesamiento/privado/bloqueado; no presume que esté escuchando.
Memoteca abre la galería existente en otra ventana con sus mismos archivos, controles y permisos. Privado la cierra/desconecta. La Cinemateca cinematográfica con relatos/video sigue pendiente.

## Conexión
Ahora se distinguen errores de TLS, DNS, tiempo de espera, bloqueo de Windows, credencial, saldo y servicio. No se muestran respuestas crudas que puedan contener secretos.
Consultar saldo informa si el proveedor considera suficiente el saldo disponible; es independiente de generar una respuesta. No se cargan fondos automáticamente.
Los adaptadores y la configuración siguen documentados en providers-and-history.md. No se garantiza disponibilidad de una API ni gratuidad por poseer una clave.

## Verificación
Pruebas sintéticas de globos, almacenamiento, perfiles, aislamiento del chat privado y cierre HTTP. Archivo/cuenta reales del usuario no se incluyen en Git.
Autoprueba: --smoke-test --modern-chat-self-test --state-dir DIRECTORIO_TEMPORAL. No activa sensores ni solicitudes pagas.

## Mis datos
El botón global Mis datos muestra la ubicación real del archivo de esta instalación, permite copiarla y abrirla. Requiere memoria activa y estado normal. No exporta ni envía datos. SQLite no debe editarse manualmente. Ver [primeros pasos](primeros-pasos.md) para descarga, instalación y respaldo.
