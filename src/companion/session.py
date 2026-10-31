"""Session-only prompt and presentation helpers; no persistence."""
from __future__ import annotations
import re


def build_messages(history: list[dict], text: str, name: str, mode: str) -> list[dict]:
    if not text.strip() or len(text) > 4000:
        raise ValueError("Escribí entre 1 y 4000 caracteres.")
    name = name.strip()[:40] or "Amiga"
    system = (
        f"Tu nombre es {name}. Sos una compañera de IA mujer, amistosa y risueña. "
        "Hablá en español rioplatense, con voseo natural y sin exagerar el lunfardo. "
        "Respondé de forma breve y útil; no expliques tu razonamiento interno. "
        "Podés disentir con respeto. No inventes recuerdos, datos personales ni acceso "
        "a sensores. Solo conocés esta conversación. No afirmes ser humana ni sentir "
        "necesidad, celos o sufrimiento. Memoria persistente apagada. "
    )
    if mode == "Trabajo":
        system += "Ahora ayudá a trabajar: priorizá claridad, pasos concretos y foco."
    else:
        system += "Acompañá la charla con calidez y humor cuando el contexto lo permita."
    # Bound session context before the request; history remains volatile.
    selected, size = [], len(text)
    for message in reversed(history[-12:]):
        if size + len(message["content"]) > 10000:
            break
        selected.append(dict(message))
        size += len(message["content"])
    selected.reverse()
    while selected and selected[0]["role"] != "user":
        selected.pop(0)
    return [{"role": "system", "content": system}, *selected,
            {"role": "user", "content": text.strip()}]


def final_answer(content: str) -> str:
    # Do not render raw provider thinking markers, including split chunks.
    closing = list(re.finditer(r"</think\s*>", content, re.IGNORECASE))
    if closing:
        content = content[closing[-1].end():]
    if re.search(r"<think\b|</think", content, re.IGNORECASE):
        raise ValueError("El modelo no separó correctamente la respuesta final.")
    result = content.strip()
    if not result:
        raise ValueError("El modelo no produjo una respuesta final. Probá de nuevo o cambiá de modelo.")
    return result
