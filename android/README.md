# Companion Android alpha

Cliente de chat local para Ollama en Windows. Conectá ambos equipos a la misma Wi-Fi.

1. Instalá las dependencias de requirements-build-windows.txt en la PC y abrí Companion desde el código.
2. Elegí un modelo Ollama. En Android, ingresá la IPv4 local de la PC y habilitá el chat.
3. Desde la APK escaneá el QR y enviá un mensaje. Si Windows pide permiso de red, habilitá la red privada.
4. Deshabilitá Android o entrá en modo privado para cerrar la conexión.

El QR vence a los dos minutos y sirve para un equipo. Android verifica la identidad TLS de la PC con la huella del QR. Los eventos se agregan a CompanionIA/android-connections.jsonl en LOCALAPPDATA. El chat permanece en RAM.

Compilación: Gradle 8.9, SDK 35, JDK 17 o 21. Ejecutá :app:assembleDebug desde la carpeta android. El artefacto queda en app/build/outputs/apk/debug/app-debug.apk.

Pendientes: túnel por Internet, equipos recordados, avisos por correo, voz y sensores de contexto.
