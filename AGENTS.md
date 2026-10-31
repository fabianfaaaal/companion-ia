# Companion IA — instrucciones para agentes
Este repositorio contiene una prueba de concepto, no una aplicación completa.
Leer README.md y documentación específica dentro de docs/.
Python 3.12+, Apache-2.0. Datos reales, claves, capturas y recuerdos fuera del repo.
Decisión del usuario 2026-10-31: controles de funciones activados de inicio y desactivaciones persistentes. El núcleo sigue denegando operaciones sin permisos y respeta memoria OFF, privado y acceso íntimo independiente. Personalidad no concede permisos. Las pruebas no deben activar capturas reales por estos valores de interfaz.
Usar datos sintéticos. Probar: python -B -m unittest discover -s tests -v.
No confundir pruebas del núcleo con aceptación manual o funcionamiento de sensores/LLM.
Cambios pequeños por objetivo, ramas feat/, fix/ o docs/ y commits descriptivos tras verificar.
Publicación inicial autorizada solo en fabianfaaaal/companion-ia público; avances de ese proyecto mediante ramas y pushes durante sesiones activas. Sin force-push ni operaciones destructivas.
No aceptar contribuciones externas por ahora. No consumir servicios pagos sin autorización.
Documentación de planificación interna se mantiene fuera del repositorio; este código y docs/ deben ser comprensibles por sí solos.

C18: guardado automático de mensajes con memoria activa. PRIVATE permite solo private.chat local temporal; archivo/sensores/voz/externos bloqueados. No trasladar texto privado al reactivar. ERROR bloquea también chat. Dispositivo y Memoteca comparten núcleo/permisos; no crean otro asistente.
