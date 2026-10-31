"""Technical UI preferences only; never conversation or observations."""
import json
from pathlib import Path

DEFAULTS = dict(memory=True, voice=True, microphone=True, notices=True,
                foreground=True, environment_context=True, web=True, web_context=True,
                natural_voice=True)


class Preferences:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return DEFAULTS.copy()
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or saved.get("version") != 1:
                raise ValueError()
            values = saved["options"]
            if not isinstance(values, dict) or any(type(values.get(key)) is not bool for key in DEFAULTS):
                raise ValueError()
            return {key: values[key] for key in DEFAULTS}
        except (OSError, ValueError, TypeError, KeyError):
            # A broken file must not silently reactivate previously disabled features.
            return dict.fromkeys(DEFAULTS, False)

    def save(self, options):
        if any(type(options.get(key)) is not bool for key in DEFAULTS):
            raise ValueError("Invalid preferences")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "options": {key: options[key] for key in DEFAULTS}}), encoding="utf-8")
        temporary.replace(self.path)
