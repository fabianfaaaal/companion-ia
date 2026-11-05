# Companion IA — fichas, copias y preparación guiada

Este preview Windows agrega tres recorridos a la aplicación. No hace falta usar terminal ni editar configuraciones para prepararlos.

## Programa y archivo personal separados

En el primer inicio elegís la carpeta personal: aceptá la recomendada o pulsá **Elegir otra carpeta**. Allí quedan conversaciones, fichas, fotos y configuraciones del usuario; los programas, Python, voces y modelos se preparan en otras ubicaciones. La app conserva solo un localizador técnico hacia el archivo elegido. Las instalaciones anteriores mantienen su ubicación sin mover datos.

**Mis datos → Elegir otro archivo personal** permite cambiar la ubicación y reabrir. Esto no mueve ni borra el archivo anterior. Para trasladar información, creá una copia y restaurala en el nuevo archivo. Una unidad desconectada o una ubicación inválida produce un aviso; no se abre un archivo vacío silenciosamente.

La separación facilita actualización, respaldo y reinstalación, pero no es cifrado ni aislamiento del sistema operativo frente a otros programas con permisos sobre esa carpeta. No se admiten carpetas de programa/motores ni carpetas ajenas no vacías.

## Preparar la aplicación y sus motores

1. Abrí `CompanionIA.exe` conservando toda su carpeta, incluido `_internal`.
2. En **Preparar motores**, pulsá **Instalar aplicación y acceso en Inicio** si querés un acceso permanente para tu cuenta de Windows. Conserva tus datos aparte.
3. Pulsá **Preparar todo (recomendado)**, leé el resumen de descargas, destinos, espacio y licencias, y elegí **Aceptar** o **Cancelar**.
4. La aplicación comprueba lo instalado. Si falta, prepara Ollama y un modelo local pequeño; después prepara Python independiente para Piper/Whisper y descarga voz y dictado. El modelo se detecta al terminar.

No requiere cuenta, suscripción ni clave API para el recorrido local. No activa por sí solo micrófono ni memoria. **Dictar** sigue siendo por botón. Las funciones cloud propias de otros programas no se configuran globalmente desde Companion.

El instalador muestra una barra de 0 a 100% cuando conoce el tamaño del paso actual. En instalaciones, verificaciones y esperas sin duración medible, muestra una animación y el tiempo transcurrido; si no hay novedades durante diez segundos, muestra cuánto hace desde la última actualización. El porcentaje es del paso actual, no una estimación del tiempo total restante. Al terminar, fallar o cancelar, detiene la actividad y explica el resultado. Permite cancelar/reintentar. Cancelar no desinstala lo que ya terminó ni borra motores existentes. Windows puede solicitar confirmación para Microsoft Visual C++ cuando falta ese requisito del sistema. Las descargas necesitan Internet y espacio libre; las cifras de tamaño son estimaciones. Qwen TTS experimental no es una descarga obligatoria. El recorrido recomendado usa Piper.

Una instalación limpia real sigue pendiente de aceptación: las pruebas de desarrollo simulan red/instaladores y no prueban tu conexión, drivers ni hardware de audio.

## Fichas de contexto

En **Fichas y aprendizajes → Nueva ficha** elegí una de estas categorías: Identidad, Preferencias, Personas, Mascotas, Proyectos, Rutinas, Lugares o Temas. Son plantillas vacías: no contienen información inferida sobre vos.

Completá título, contenido que querés confirmar, fuente y claves/alias separados por comas. Ejemplo ficticio: una ficha Proyecto demo con claves `proyecto demo, mi demo`. Al conversar sobre `mi demo`, se recupera esa ficha. No se activa `gato` dentro de `gatopardo`; una frase más específica tiene prioridad sobre una coincidencia superpuesta. Si una clave apunta a más de una ficha, elegís cuál; cancelar conserva el mensaje sin enviarlo.

Podés editar, desactivar o borrar fichas. El inspector del último envío muestra los fragmentos incluidos dentro del límite de contexto, con claves, versión y procedencia. Que una ficha coincida no implica que todo su contenido se envíe: se respetan el presupuesto y los controles del proveedor. **Compartir memoria** controla su incorporación a proveedores externos.

Con memoria apagada, las fichas nuevas son temporales en RAM; no consultan el archivo anterior. Se descartan al activar memoria, cerrar o entrar en privado. El modo privado no usa fichas.

## Aprendizajes que se pueden revisar

Seleccioná un fragmento de un mensaje, abrí su menú con clic derecho y elegí **Proponer como aprendizaje**. También podés proponer texto directamente desde la pestaña de fichas. Elegís una ficha y revisás texto/fuente. Las respuestas del asistente se identifican como afirmaciones no verificadas.

**Revisar propuesta** permite editar y aceptar, o rechazar. Aceptar reemplaza el contenido de la ficha y conserva la revisión anterior. Si la ficha cambió desde la propuesta, se pide volver a revisar para no sobrescribir cambios nuevos. Rechazar evita que esa misma propuesta reaparezca. No hay extracción automática de hechos de cada mensaje ni entrenamiento de un modelo.

## Exportar: crear y copiar una carpeta

En **Copias y restauración**, aceptá **Crear copia**. Abrí **Copias creadas** y copiá la carpeta completa a donde quieras conservarla. No copies archivos SQLite sueltos ni edites el manifiesto.

La ubicación definida es `transfer/outgoing` dentro del directorio de datos de esta instalación. La copia incluye SQLite consistente, conversaciones, notas, agenda, historiales, fichas/propuestas/revisiones, fotos originales, miniaturas y preferencias compatibles. No incluye cuentas/claves API, motores, modelos descargados ni el estado privado.

## Restaurar: pegar, aceptar y volver a abrir

1. Abrí **Entrada de restauración** y pegá allí la carpeta completa de una copia creada por Companion.
2. La app detecta la copia terminada, revisa formato, versión, hashes, base de datos y referencias de fotos.
3. Elegí **Aceptar** para preparar la restauración o **Cancelar** para seguir con tus datos actuales. **Revisar carpetas pegadas** permite volver a ofrecer una copia cancelada.
4. Companion se cierra y vuelve a abrir para incorporar el archivo coherentemente. Si Windows no puede reabrirlo, abrilo normalmente: no hay pasos manuales sobre los datos.

La ubicación fija es `transfer/incoming`. La restauración **reemplaza** el archivo; no mezcla silenciosamente dos historias. Guarda una copia de recuperación previa y un registro técnico para recuperar una operación interrumpida. Una copia antigua puede contener recuerdos que borraste después: el aviso lo informa antes de aceptar.

Las claves/cuentas API locales no se reemplazan ni viajan. Para otra PC, prepará motores y cuentas propias por separado. El modo privado se conserva. Con memoria apagada o privado no se escanean ni exportan datos; recuperar una transacción de restauración ya aceptada es una operación de integridad independiente.

El archivo y sus copias siguen sin cifrar; el cifrado queda para un hito final.


## Iniciar un Ollama que ya está instalado

En Conversación, pulsá **Iniciar Ollama** y aceptá. Se inicia el servicio local y después se detectan los modelos, sin descargar ni instalar componentes. Si no está instalado, usá Preparar motores.

Como alternativa, abrí PowerShell, ejecutá `ollama serve` y pulsá **Detectar Ollama** en Companion. Si informa que el puerto ya está ocupado, el servicio puede estar iniciado: probá Detectar Ollama. La preparación de voz y la conexión del chat son componentes distintos.

El paquete conserva los recursos de certificados obligatorios del Python que lo construye. No se desactiva HTTPS ni la validación de certificados para corregir problemas de conexión. Las autopruebas del ejecutable comprueban también que se pueda crear un contexto TLS válido.
