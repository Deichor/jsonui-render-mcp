"""JSON UI templates: `@` inheritance and `$variable` substitution."""

import copy
import json
import re
from pathlib import Path


def load_jsonc(path):
    """Mojang's UI files carry both comment styles and trailing commas."""
    s = Path(path).read_text(encoding="utf-8")
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            j = i + 1
            while j < n:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == '"':
                    break
                j += 1
            out.append(s[i:j + 1])
            i = j + 1
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


# --- conditions ---------------------------------------------------------------------------------

_TOKENS = re.compile(r"\(|\)|<>|!=|=|-|'[^']*'|\$[\w.]+|[\w.#]+")


def _tokenize(text):
    return _TOKENS.findall(text)


class _Condition:
    """`ignored` / `visible` expressions: not, and, or, =. An unset variable reads as ''."""

    def __init__(self, tokens, scope):
        self.tokens, self.at, self.scope = tokens, 0, scope

    def peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.at += 1
        return token

    def parse(self):
        value = self.disjunction()
        return value

    def disjunction(self):
        value = self.conjunction()
        while self.peek() == "or":
            self.take()
            value = bool(self.conjunction()) or bool(value)
        return value

    def conjunction(self):
        value = self.negation()
        while self.peek() == "and":
            self.take()
            value = bool(self.negation()) and bool(value)
        return value

    def negation(self):
        if self.peek() == "not":
            self.take()
            return not bool(self.negation())
        return self.comparison()

    def comparison(self):
        left = self.difference()
        if self.peek() in ("=", "<>", "!="):
            operator = self.take()
            right = self.difference()
            return (left == right) if operator == "=" else (left != right)
        return left

    def difference(self):
        """`a - b` on strings: b taken out of a, once.

        It is the only test JSON UI has. A screen marker and a settings-row token are both invisible
        formatting codes on the front of a caption, and `(#text - 'token') = #text` is how a control
        asks whether its caption carries one — false when it does, which is why every gate is
        wrapped in `not`. First occurrence rather than all of them, which is what the client does;
        the difference cannot show on a marker, since a caption carries at most one.
        """
        value = self.atom()
        while self.peek() == "-":
            self.take()
            right = self.atom()
            if isinstance(value, str) and isinstance(right, str):
                value = value.replace(right, "", 1)
            else:
                # Numbers subtract; anything else is left alone rather than guessed at.
                try:
                    value = float(value) - float(right)
                except (TypeError, ValueError):
                    pass
        return value

    def atom(self):
        token = self.take()
        if token == "(":
            value = self.disjunction()
            if self.peek() == ")":
                self.take()
            return value
        if token is None:
            return ""
        if token.startswith("$") or token.startswith("#"):
            # Unset is normal: Mojang gates whole platforms this way.
            return self.scope.get(token, "")
        if token.startswith("'"):
            return token[1:-1]
        if token in ("true", "false"):
            return token == "true"
        return token


def truth(value, scope):
    """Whether a condition holds, for the three shapes it arrives in."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return bool(_Condition(_tokenize(value), scope).parse())
    return bool(value)


class Index:
    """Every control Mojang ships, plus our own, addressable as `namespace.name`."""

    def __init__(self, vanilla_ui_dir, own_namespace=None, own_controls=None, problems=None):
        self.defs = {}
        self.problems = problems if problems is not None else []
        # The palette lives outside any namespace; without it every templated colour is lost.
        globals_file = Path(vanilla_ui_dir) / "_global_variables.json"
        self.globals = load_jsonc(globals_file) if globals_file.exists() else {}
        for file in sorted(Path(vanilla_ui_dir).glob("*.json")):
            try:
                doc = load_jsonc(file)
            except Exception as error:
                self.problems.append(f"could not read {file.name}: {error}")
                continue
            namespace = doc.get("namespace")
            if not namespace:
                continue
            for key, value in doc.items():
                if key == "namespace" or not isinstance(value, dict):
                    continue
                name, _, base = key.partition("@")
                # A bare base means "in this file".
                if base and "." not in base and not base.startswith("$"):
                    base = f"{namespace}.{base}"
                self.defs[f"{namespace}.{name}"] = (value, base or None, namespace)
        for name, control in (own_controls or {}).items():
            self.defs[f"{own_namespace}.{name}"] = (control, None, own_namespace)

    def resolve(self, ref, overrides=None, env=None, depth=0, gate=True):
        """Expands [ref] with [overrides] on top. None if `ignored`/`visible` rules it out."""
        if depth > 24:
            self.problems.append(f"'{ref}' nests deeper than this resolves")
            return dict(overrides or {})
        entry = self.defs.get(ref)
        if entry is None:
            self.problems.append(f"'{ref}' is not defined in any pack this reads")
            return dict(overrides or {})

        definition, base, namespace = entry
        merged = (self.resolve(base, None, env, depth + 1, gate=False) or {}) if base else {}
        merged.update(copy.deepcopy(definition))
        merged.update(copy.deepcopy(overrides or {}))

        # `$x|default` only fills a gap, so defaults go in first.
        scope = dict(self.globals)
        scope.update(env or {})
        for key, value in merged.items():
            if key.startswith("$") and key.endswith("|default"):
                scope.setdefault(key[:-len("|default")], value)
        for key, value in merged.items():
            if key.startswith("$") and not key.endswith("|default"):
                scope[key] = value

        merged = self.substitute(merged, scope)
        if gate and (truth(merged.get("ignored"), scope) or not truth(merged.get("visible", True), scope)):
            return None
        # Flattened here, while the template's variables are still in scope.
        merged["controls"] = [
            flat
            for entry in merged.get("controls", []) or []
            for flat in [self.flatten(entry, scope, depth, namespace)]
            if flat
        ]
        if not merged["controls"]:
            merged.pop("controls")
        return merged

    def flatten(self, entry, scope, depth, namespace):
        out = {}
        for name, child in entry.items():
            if not isinstance(child, dict):
                out[name] = child
                continue
            base = name.partition("@")[2]
            if base:
                if base.startswith("$"):
                    base = scope.get(base, base)
                # A bare base means "in the file this control came from".
                if isinstance(base, str) and "." not in base and not base.startswith("$"):
                    base = f"{namespace}.{base}"
                expanded = self.resolve(base, child, scope, depth + 1)
                if expanded is not None:
                    out[name.partition("@")[0]] = expanded
            else:
                if truth(child.get("ignored"), scope) or not truth(child.get("visible", True), scope):
                    continue
                nested = dict(child)
                nested["controls"] = [
                    flat
                    for e in nested.get("controls", []) or []
                    for flat in [self.flatten(e, scope, depth + 1, namespace)]
                    if flat
                ]
                if not nested["controls"]:
                    nested.pop("controls")
                out[name] = nested
        return out

    def substitute(self, node, scope, depth=0):
        if depth > 24:
            return node
        if isinstance(node, str):
            if node.startswith("$") and node in scope:
                # A variable may name another variable; follow it rather than drawing "$foo".
                return self.substitute(scope[node], scope, depth + 1)
            return node
        if isinstance(node, list):
            return [self.substitute(v, scope, depth + 1) for v in node]
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                name, _, base = key.partition("@")
                if base:
                    # `child@$some_control` is how a template is told what to wrap.
                    target = scope.get(base, base) if base.startswith("$") else base
                    if isinstance(target, str):
                        out[f"{name}@{target}"] = self.substitute(value, scope, depth + 1)
                        continue
                out[key] = self.substitute(value, scope, depth + 1)
            return out
        return node

    def expand(self, name, control, env=None):
        """A child written as `name@ref`, flattened. None if the client would not create it."""
        base = name.partition("@")[2]
        if not base:
            return control
        return self.resolve(base, control, env)
