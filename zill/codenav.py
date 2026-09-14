"""Code navigation: symbols, definitions, references and signatures, without a server.

Concept: grep finds text, but a model editing code asks structural questions:
what is defined in this file, where is this function defined, who calls it,
what does it take. code_nav answers them from the source itself.

Design rules:
  * Python is parsed with ast, so its answers are exact: classes, functions,
    methods (as Class.method), signatures and first docstring lines.
  * Other languages use declaration patterns (def, class, function, fn, func,
    struct, interface, type, const, let, var...), and say they are heuristic.
  * Read-only, confined to the working directory, and bounded like grep.
"""

import ast
import os
import re

from .tools import IGNORED_DIRS, tool

CODE = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc",
        ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala", ".lua", ".sh"}
DECLARATION = re.compile(r"^\s*(?:(?:export|default|public|private|protected|static|async|pub"
                         r"(?:\([\w:]+\))?|abstract|final|inline|unsafe)\s+)*"
                         r"(class|def|function|fn|func|struct|interface|enum|trait|type|impl|"
                         r"module|const|let|var)\s+\*?\s*([A-Za-z_]\w*)")
MAX_HITS = 100


def _python_symbols(text):
    """Yield (line, kind, qualified name, signature, docstring line) for Python source."""
    def visit(nodes, prefix):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{node.name}"
                is_class = isinstance(node, ast.ClassDef)
                signature = "" if is_class else f"({ast.unparse(node.args)})"
                doc = (ast.get_docstring(node) or "").strip().split("\n")[0]
                yield node.lineno, "class" if is_class else "def", name, signature, doc
                yield from visit(node.body, f"{name}.")
    yield from visit(ast.parse(text).body, "")


def symbols_of(path, text):
    """Return [(line, kind, name, signature, doc)] for one file's source text."""
    if path.endswith(".py"):
        try:
            return list(_python_symbols(text))
        except SyntaxError:
            pass
    return [(n, m.group(1), m.group(2), "", "") for n, line in enumerate(text.splitlines(), 1)
            for m in [DECLARATION.match(line)] if m]


def code_nav_tool(workdir):
    """Return the code_nav tool for workdir."""
    root = os.path.realpath(workdir)

    def sources(path=""):
        """Yield (relative path, text) for the code files under path."""
        base = os.path.realpath(os.path.join(root, path))
        try:
            inside = os.path.commonpath([root, base]) == root
        except ValueError:  # another drive on Windows
            inside = False
        if not inside:
            raise PermissionError(f"{path!r} escapes the working directory")
        walked = ([(os.path.dirname(base), [], [os.path.basename(base)])]
                  if os.path.isfile(base) else os.walk(base))
        for dirpath, dirnames, filenames in walked:
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and d != ".zill")
            for name in sorted(filenames):
                if os.path.splitext(name)[1] in CODE:
                    full = os.path.join(dirpath, name)
                    try:
                        with open(full, encoding="utf-8") as f:
                            yield os.path.relpath(full, root).replace(os.sep, "/"), f.read()
                    except (UnicodeDecodeError, OSError):
                        continue

    def describe(rel, symbol):
        line, kind, name, signature, doc = symbol
        guess = "" if rel.endswith(".py") else "  (heuristic)"
        return f"{rel}:{line}: {kind} {name}{signature}{f'  # {doc}' if doc else ''}{guess}"

    @tool("Navigate code structurally. action is one of: symbols (what a file or folder "
          "defines), definition (where name is defined), references (where name is used), "
          "hover (a definition's signature, docstring and first lines).", risk="read",
          action="symbols, definition, references or hover",
          name="Symbol name for definition, references and hover (e.g. greet or Cart.total)",
          path="File or folder to look in (default: the whole working directory)")
    def code_nav(action, name="", path=""):
        if action not in ("symbols", "definition", "references", "hover"):
            return "ERROR: action must be symbols, definition, references or hover"
        if action != "symbols" and not name:
            return f"ERROR: {action} needs a name"
        hits = []
        for rel, text in sources(path):
            if action == "references":
                word = re.compile(rf"(?<![\w.]){re.escape(name.rsplit('.', 1)[-1])}\b")
                hits += [f"{rel}:{n}: {line.strip()[:200]}"
                         for n, line in enumerate(text.splitlines(), 1) if word.search(line)]
            else:
                for symbol in symbols_of(rel, text):
                    if action == "symbols" or name in (symbol[2], symbol[2].rsplit(".", 1)[-1]):
                        hits.append(describe(rel, symbol))
                        if action == "hover":
                            lines = text.splitlines()[symbol[0] - 1:symbol[0] + 7]
                            hits.append("\n".join(f"    {line}" for line in lines))
            if len(hits) >= MAX_HITS:
                return "\n".join(hits[:MAX_HITS]) + f"\n... stopped at {MAX_HITS} results"
        return "\n".join(hits) or f"(nothing found: {action} {name})"

    return code_nav
