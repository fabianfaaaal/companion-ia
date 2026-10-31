# Companion IA

**Un companion configurable, con memoria opcional y una historia que puedas explorar.**

Companion IA es un proyecto gratuito y de código abierto que explora cómo construir una experiencia de compañía digital con modelos de lenguaje intercambiables, contexto autorizado y control del usuario sobre sus recuerdos.

Queremos que puedas conversar con una compañera o mascota con personalidad propia, compartir lo que elijas y, si habilitás la memoria, volver a esos momentos desde una **Cinemateca de Recuerdos**.

> **Estado: prototipo de escritorio en desarrollo temprano.** Ventana Windows con chat Ollama, notas, voz opcional, agenda, Cinemateca inicial de fotos y controles de web y entorno. Paquete portable para pruebas; todavía sin instalador completo ni archivo íntimo cifrado.

## Qué queremos demostrar

Que un companion puede responder con más continuidad si recibe el contexto adecuado, sin necesitar todo tu archivo personal en cada conversación.

La cadena que queremos validar es:

**Entrada autorizada → contexto relevante → modelo seleccionado → respuesta → recuerdo navegable, si elegiste conservarlo.**

La percepción propuesta consiste en procesar fuentes que vos habilitás. La continuidad y personalidad son comportamientos del software; no implican conciencia o comprensión infalible.

## La experiencia que estamos diseñando

Estas funciones son objetivos del producto; la tabla de estado más abajo distingue lo que ya existe.

- **Compañía configurable.** Nombre, estilo, humor y forma de hablar. El primer perfil será una compañera amistosa y risueña, capaz de acompañar una charla, ayudar a trabajar y revisar experiencias con vos.
- **Tutorial inicial y claves de contexto.** Asociar palabras a fichas: por ejemplo, «mi mascota» recupera la información que decidiste guardar sobre ella. Las ambigüedades se aclaran.
- **Memoria opcional.** Sin activarla, el contenido de la conversación deberá permanecer en la sesión. Activarla permite conservar información dentro del alcance autorizado.
- **Cinemateca de Recuerdos.** Explorar notas, fotos y conversaciones por momentos, fechas y relaciones.
- **Contexto inspeccionable.** Consultar qué fuentes se utilizaron y qué contenido se envió realmente al modelo. Un relato generado queda identificado como interpretación.
- **Intimidad controlada.** Separar temas generales y restringidos. Frases como «estamos solos» pueden sugerir reserva, pero no autentican ni desbloquean el archivo íntimo.
- **Agenda y retrospectiva.** Proponer recordatorios y reconstruir acontecimientos a partir de fuentes, distinguiendo planes tentativos de compromisos confirmados.
- **Percepción autorizada.** Más adelante, incorporar voz, imágenes y otras señales con permisos específicos. Inactividad no equivale a sueño confirmado.

## Qué existe hoy

| Componente | Estado |
| --- | --- |
| Núcleo de autorización y revocación de permisos | Implementado y probado con acciones ficticias |
| Memoria y preferencias | Núcleo deniega sin permisos; interfaz inicia funciones activadas y recuerda lo que desmarques |
| Modo privado y rechazo de resultados tardíos | Implementado al nivel del núcleo |
| Persistencia del estado privado | Probada cuando se configura una ruta de estado |
| Control básico de acceso restringido | Implementado; autenticación local pendiente |
| Pruebas automatizadas | 183 casos locales aprobados (políticas, transporte, notas, audio, motores e interfaz) |
| Chat, interfaz y conexión Ollama | Prototipo Windows verificado con saludo sintético; aceptación del usuario pendiente |
| Notas manuales, búsqueda y recuperación | Integradas con memoria opcional; conversación sigue temporal |
| Voz y dictado | Piper o Qwen3-TTS opcional; Whisper local por botón. Motor registrado entre contextos Windows |
| Fotos y Cinemateca inicial | Importación manual, miniaturas, notas y búsqueda textual; sin análisis visual |
| Calendario, recordatorios y alarmas | Avisos dentro de la app, con memoria y avisos activos; sonido opt-in |
| Búsqueda web | Bing/Wikipedia con autorización por consulta, fragmentos y enlaces |
| Contexto ambiental inicial | Estado autoinformado y título de ventana autorizado en Windows, solo RAM |
| Proveedores externos y respaldo local | DeepSeek/OpenAI implementados con pruebas simuladas; conexión real pendiente de claves |
| Historiales multicuenta | Importación seleccionada ChatGPT JSON/normalizado, contexto filtrable y borrado |
| Personalidad y profundidad | Pestaña de perfil y selección Auto/Breve/Profunda |
| Archivo íntimo cifrado, cámara y escucha permanente | Pendientes |
| Android, distribución Linux e instaladores | Pendientes |

Las pruebas usan datos sintéticos. Se comprobó un recorrido real de ventana y modelo local, además de controles automatizados; no constituye una auditoría de seguridad ni aceptación de experiencia. El archivo íntimo con datos reales requiere protección de contenido y claves antes de habilitarse.

## Proveedores e historiales

Ver [guía de proveedores, cuentas e importación](docs/providers-and-history.md). Ollama es la ruta inicial sin tarifa de API; servicios externos requieren credenciales y autorización de cargos del usuario. Los conectores no dan acceso automático a chats previos.

## Probar notas y voz

Ver [voz preparada y preferencias iniciales](docs/voice-ready-preview.md). En el primer inicio, los controles de funciones disponibles vienen activados; desmarcarlos recuerda tu elección. Micrófono habilitado permite Dictar, sin escucha continua.

Ver [herramientas integradas](docs/integrated-tools-preview.md) y [notas y voz](docs/voice-notes-preview.md). Guardar una nota no programa recordatorios. El audio requiere permisos propios; el motor neural opcional y sus modelos se preparan fuera del repositorio.

## Desarrollo asistido por IA

Este proyecto nace de una mezcla de exploración rápida —a veces llamada *vibe coding*— y trabajo deliberado sobre requisitos, arquitectura y pruebas.

Usamos Codex y agentes de IA para proponer diseños, escribir código, revisar cambios y documentar avances. La dirección del proyecto mantiene la responsabilidad sobre las decisiones, el alcance y lo que se publica.

Nuestro método:

1. Convertir una idea en un incremento con un criterio de aceptación.
2. Definir responsabilidades y contratos antes de conectar componentes.
3. Implementar cambios pequeños y revisar sus límites.
4. Ejecutar pruebas pertinentes y registrar su resultado.
5. Separar lo propuesto, lo implementado, lo probado y lo aceptado por una persona.

La generación de código y la revisión por otra IA pueden ayudar a encontrar problemas; no garantizan que un cambio sea correcto. Preferimos mostrar evidencia y limitaciones antes que anunciar funcionalidades que todavía no funcionan.

## Preparar el motor local

Incluimos un [preparador Windows y guía paso a paso](docs/setup-windows.md) y un diagnóstico local de la API de Ollama. El chat integrado está en prueba; todavía no hay instalador completo de Companion. Ver [uso del prototipo ejecutable](docs/desktop-prototype.md).

## Arquitectura

El diseño adoptado parte de un núcleo modular y una aplicación de escritorio. El alcance actual del código es más pequeño:

| Capa | Elección | Situación |
| --- | --- | --- |
| Núcleo | Python 3.12+ | Base actual |
| Escritorio | PySide6 / Qt Widgets | Primera ventana implementada; Qt Quick sigue como opción posterior |
| Modelo local | Ollama mediante adaptador | Integrado en prueba con modelos locales verificados |
| Memoria y búsqueda | SQLite / FTS5 | Integrado para notas, agenda y fotos |
| Cliente Android | Kotlin / Jetpack Compose | Planificado |

Windows es la primera plataforma. Android será una extensión de la PC, que conservará la autoridad del archivo; Linux tendrá su propia validación y empaquetado. La portabilidad prevista no equivale a compatibilidad ya probada.

Estructura actual:

```text
companion-ia/
├── src/companion/
│   ├── policy.py
│   ├── desktop.py
│   └── ...
├── tests/
│   ├── test_policy.py
│   └── ...
├── AGENTS.md
├── LICENSE
├── README.md
└── pyproject.toml
```

## Probar el núcleo

Requisito: Python 3.12 o posterior.

Desde la raíz del repositorio:

```shell
python -B -m unittest discover -s tests -v
```

Las pruebas básicas usan la biblioteca estándar; las de interfaz y fotos requieren requirements-desktop.txt. No requieren Ollama ni GPU. Los casos de persistencia crean estado técnico ficticio en directorios temporales.

Este comando ejecuta pruebas. Para abrir o construir la ventana ver [prototipo de escritorio](docs/desktop-prototype.md). Instalar requirements-desktop.txt para incluir las pruebas de interfaz; sin Qt se omiten esas pruebas.

## Privacidad como requisito

El diseño separa tres controles:

- **Memoria:** decide si se conserva información personal y se recupera el archivo histórico.
- **Acceso íntimo:** decide si el contexto restringido puede utilizarse en la sesión.
- **Modo privado:** detiene el procesamiento y comunicaciones del companion bajo control de la aplicación.

Los permisos deben aplicarse en el núcleo, también al recuperar, enviar o guardar información. Personalidad, recuerdos y documentos no pueden concederlos.

El funcionamiento local será la ruta básica prevista, sin API paga obligatoria. Un adaptador local tendrá que comprobar que el modelo realmente se ejecuta localmente y evitar sustituciones silenciosas por servicios externos.

No incorporar datos personales, claves, capturas reales ni exportaciones de usuarios al repositorio.

## Próximos hitos

- [x] Definir la visión y los contratos iniciales.
- [x] Crear el núcleo inicial de permisos y sus pruebas.
- [ ] Completar el entorno reproducible y las verificaciones automáticas.
- [x] Conversar con un modelo local desde una ventana Windows de prueba.
- [ ] Configurar personalidad y completar el tutorial.
- [ ] Guardar y recuperar el primer recuerdo con una fotografía.
- [ ] Inspeccionar el contexto, exportar y borrar datos.
- [ ] Completar la protección del archivo íntimo.
- [ ] Incorporar agenda, voz y percepción por incrementos.
- [ ] Validar Android y Linux.

Sin fechas de entrega comprometidas por ahora. Cada hito necesita evidencia propia.

## Contribuciones

Por ahora no aceptamos aportes externos ni pull requests. El código sigue abierto: podés estudiarlo, copiarlo, modificarlo y redistribuir tu versión bajo las condiciones de Apache-2.0. Ver [política de contribuciones](CONTRIBUTING.md).

## Licencia y costos

Código propio bajo [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0); el repositorio incluye el texto en LICENSE.

El objetivo es ofrecer una aplicación gratuita con funcionamiento básico local. Hardware, electricidad y servicios externos opcionales pueden tener costos. Modelos, voces, recursos y dependencias mantienen sus respectivas licencias.

---

**La memoria pertenece al usuario.** Nuestra prueba de concepto busca dar continuidad al companion conservando la posibilidad de revisar, corregir, exportar y borrar lo que se decidió guardar.
