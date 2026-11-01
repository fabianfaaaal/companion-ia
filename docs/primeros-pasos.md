# Empezar con Companion IA en Windows

## 1. Descargar y abrir
1. Entrá a https://github.com/fabianfaaaal/companion-ia/releases y elegí la última **pre-release**.
2. Descargá `CompanionIA-windows-preview.zip` y extraé TODO el ZIP en una carpeta propia.
3. Abrí `CompanionIA/CompanionIA.exe`. Conservá `_internal` al lado del ejecutable. No hace falta instalar Python.

Es una alpha sin firma digital comercial. Si Windows muestra una advertencia, verificá origen y el SHA256 publicado antes de decidir ejecutarla. No desactives el antivirus. No es todavía un instalador universal.

## 2. Preparar el motor local, una sola vez
Instalá Ollama desde https://ollama.com/download/windows. Abrí una nueva PowerShell y ejecutá:

```powershell
ollama --version
ollama pull qwen2.5:7b
```

El modelo ocupa varios GB y requiere una PC con recursos suficientes. La RTX 3080 de 10 GB es nuestro equipo de referencia, no un requisito ni una garantía de velocidad. Si la app no conecta, abrí Ollama desde Inicio; como alternativa ejecutá `ollama serve` y dejá esa ventana abierta. Si informa que el puerto está ocupado, probablemente ya está funcionando.

## 3. Tu primera conversación
Abrí Companion IA, pulsá **Detectar Ollama**, elegí el modelo y escribí. Enter envía; Shift+Enter agrega una línea. La lista izquierda permite volver a charlas guardadas. El tiempo de respuesta aparece bajo el chat.

## 4. Elegir qué guardar
En un inicio normal el guardado viene activado. **Perfiles de privacidad** permite cambiarlo. **Mis datos → Abrir carpeta** lleva a la carpeta real del archivo de esta instalación; Copiar ubicación permite encontrarla después. Allí conviven SQLite y configuraciones: no todo es un archivo de texto legible. Leé y borrá conversaciones desde el programa. Fotos originales, modelos y motores de voz pueden vivir fuera de esa carpeta.

El archivo aún no está cifrado. No lo publiques ni lo subas a GitHub. Para respaldarlo, cerrá la app y copiá la carpeta completa. **Modo privado** desconecta memoria, percepción, voz y proveedores externos; deja solo chat local temporal. No borra el archivo anterior ni protege una carpeta que ya abriste con el Explorador.

## 5. Voz y otras funciones
La descarga no incluye Ollama, modelos ni motores de voz. Seguí [la guía de voz](https://github.com/fabianfaaaal/companion-ia/blob/main/docs/voice-ready-preview.md). Dictar funciona por botón, no como escucha permanente. La Memoteca se abre en otra ventana; Modo dispositivo abre la compañera flotante.

## 6. DeepSeek y otros proveedores
Son opcionales: el chat local funciona sin API paga. Una cuenta del sitio web no equivale a créditos API. En Proveedores agregá la cuenta, su clave y la autorización de uso. Consultar saldo ayuda a distinguir falta de fondos de conexión. No compartas claves por chat ni en reportes.

No hay integración automática con sesiones web de DeepSeek o ChatGPT en esta versión. Un navegador no convierte una suscripción web en API. Si falla el proveedor, el programa puede avisar y usar Ollama local según tus controles.

## 7. Actualizar y pedir ayuda
Cerrá la app anterior, extraé la nueva descarga en otra carpeta y abrí el nuevo ejecutable. La carpeta de datos se mantiene aparte. Hacé un respaldo previo: las versiones alpha pueden cambiar.

Al informar un problema, incluí versión, Windows y pasos para reproducirlo. Usá ejemplos inventados; omití tu carpeta de datos, claves, conversaciones y capturas personales. No hay soporte garantizado ni versión estable todavía.
