# Preparación de Windows y Ollama

El objetivo final es un instalador guiado: instalar Companion, preparar el motor local, descargar un modelo con consentimiento y abrir la configuración inicial. Esta versión entrega el preparador del motor; todavía no instala ni abre una aplicación Companion completa.

## Qué se instala y dónde

- **Companion:** código fuente del repositorio durante desarrollo; más adelante instalador de escritorio con runtime incluido.
- **Ollama:** motor separado, instalado por su instalador oficial en el perfil del usuario. No va dentro del repositorio ni del baúl.
- **Modelo:** descarga aparte, administrada por Ollama; por defecto dentro del perfil del usuario. Más adelante el asistente ofrecerá elegir disco si falta espacio.
- **Recuerdos:** futuro directorio de datos de Companion fuera del repositorio y del baúl. Todavía no implementado.

No hace falta instalar Open WebUI, Docker, Node o Android Studio para ejecutar el motor. Python es necesario para desarrollar/probar este código, no será un requisito manual previsto para usuarios del instalador final.

## Pasos ahora

1. Abrí [Ollama para Windows](https://ollama.com/download/windows), descargá el instalador oficial y ejecutalo. Dejá la ubicación predeterminada.
2. Abrí Ollama desde Inicio. Queda funcionando en segundo plano; Companion se conectará a su API local.
3. En PowerShell ejecutá `ollama pull qwen2.5:7b` para obtener el modelo inicial sugerido. La ficha oficial indica aproximadamente 4.7 GB; la descarga requiere Internet. Si ya tenés un modelo apropiado, no lo descargues de nuevo.
4. Para una prueba manual opcional ejecutá `ollama run qwen2.5:7b`, escribí un saludo ficticio y salí con `/bye`. Esto prueba Ollama, no Companion. No usar datos íntimos para la prueba.
5. Desde la raíz del repositorio, `python src/companion/doctor.py` revisa la API y cuenta modelos; no realiza conversaciones ni descarga archivos.

Si `ollama` no aparece, cerrá y reabrí PowerShell después de instalar. No instales una segunda copia sin revisar la instalación existente.

## Preparador reutilizable incluido

Desde la raíz del repositorio, en PowerShell:

```powershell
.\scripts\setup-windows.ps1
```

Comprobación sin instalaciones, descargas ni preguntas:

```powershell
.\scripts\setup-windows.ps1 -CheckOnly
```

Detecta ejecutable y API por separado, no reemplaza instalaciones existentes y ofrece el instalador oficial si faltan ambos. La descarga exige HTTPS, valida Authenticode y pide revisar el editor antes de ejecutarla. Usa el instalador interactivo oficial y luego vuelve a verificar la API. Descarga de modelo opcional y explícita. Si Windows bloquea scripts, usar la instalación manual anterior; no desactivar políticas globales.

La instalación oficial real, las cancelaciones del instalador y el rendimiento del modelo requieren pruebas manuales. No se ejecutaron instalaciones ni descargas de modelos para verificar el preparador.

## Localidad y privacidad

Una API en 127.0.0.1 no demuestra que todos los modelos sean locales. No iniciar sesión cloud para el recorrido básico.
Ollama documenta `OLLAMA_NO_CLOUD=1` o `disable_ollama_cloud: true` y reinicio para desactivar funciones cloud. Esto cambia el comportamiento de otras aplicaciones que compartan Ollama: el preparador no lo modifica automáticamente.

Configuración manual si querés dedicar esta instalación al funcionamiento local:
1. Cerrá Ollama desde su icono en la bandeja.
2. Buscá en Windows «Editar las variables de entorno de esta cuenta».
3. Agregá una variable de usuario llamada `OLLAMA_NO_CLOUD` con valor `1`.
4. Volvé a abrir Ollama. La configuración afecta esa instalación compartida, no solo Companion.
La integración futura deberá validar la modalidad, elegir modelos locales y nunca cambiar silenciosamente a cloud. No se declara esa garantía implementada todavía.

## Qué falta para dos o tres botones

- Ventana de preparación con estados, progreso, reintento y cancelación.
- Comprobación real de hardware, disco, tamaño y licencia antes de recomendar modelo.
- Versión de motor probada y política de actualización/rollback; la URL actual entrega el instalador oficial vigente.
- Validación de editor esperado y paquete probado para distribución; hoy hay aprobación explícita del editor mostrado.
- Incorporar runtime de Companion y probar instalación limpia sin herramientas de desarrollo.
- Vincular motor y modelo seleccionados al chat y al tutorial.
- Separar instalación, funcionamiento e inicio automático; cada uno necesita control visible.
- No iniciar sensores ni habilitar memoria automáticamente al preparar el motor.

## Fuentes
- [Ollama Windows](https://docs.ollama.com/windows)
- [API de modelos](https://docs.ollama.com/api/tags)
- [Local-only y configuración](https://docs.ollama.com/faq)
- [Modelo qwen2.5:7b y licencia](https://ollama.com/library/qwen2.5:7b)
