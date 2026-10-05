"""AST Linter for AI-generated rule files (PRD section 4).

Whitelist-first: anything not explicitly allowed is rejected (CODE009),
then targeted checks produce specific CODExxx diagnostics.
"""

from __future__ import annotations

import ast

from .diagnostics import Diagnostic

# Approved methods (CODE014). No regex. split/join and list methods are
# allowed because list variables and list operations are supported.
APPROVED_METHODS = frozenset({
    "strip", "lstrip", "rstrip", "lower", "upper", "casefold",
    "capitalize", "title", "startswith", "endswith", "replace",
    "split", "join",
    "append", "extend", "insert", "remove", "pop", "count", "index", "copy",
})

# Approved plain function calls (CODE009): len() for list/string length.
APPROVED_FUNCTIONS = frozenset({"len"})

# Attractions that indicate forbidden operations; reported with specific codes
# for better repair prompts (whitelist would catch them anyway).
DANGEROUS_ATTRS = {
    "open": "CODE011", "read": "CODE011", "write": "CODE011",
    "system": "CODE011", "popen": "CODE011", "socket": "CODE011",
    "getenv": "CODE011", "environ": "CODE011",
    "eval": "CODE012", "exec": "CODE012", "compile": "CODE012",
    "import_module": "CODE012", "getattr": "CODE012", "setattr": "CODE012",
    "delattr": "CODE012", "__import__": "CODE012", "__dict__": "CODE012",
    "__class__": "CODE012", "__globals__": "CODE012", "__subclasses__": "CODE012",
}
DANGEROUS_NAMES = {
    "eval": "CODE012", "exec": "CODE012", "compile": "CODE012",
    "open": "CODE011", "input": "CODE011", "print": "CODE011",
    "breakpoint": "CODE012", "vars": "CODE012", "dir": "CODE012",
    "globals": "CODE012", "locals": "CODE012", "__import__": "CODE012",
}

# Syntax whitelist (CODE009). PRD 4: if/elif/else, local assignment,
# str/bool/int constants and literal collections, and/or/not, equality &
# membership, approved string/list methods, subscripts & slices,
# len(), return str or False, plus string/list concatenation.
WHITELIST: set[type] = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg,
    ast.If, ast.Assign, ast.AnnAssign, ast.Return,
    ast.Expr,                       # docstring only (checked separately)
    ast.Constant, ast.Name, ast.Load, ast.Store,
    ast.Compare, ast.Eq, ast.NotEq, ast.In, ast.NotIn,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub,  # USub: negative indices like lst[-1]
    ast.Attribute, ast.Call,
    ast.BinOp, ast.Add,             # string/list concatenation only
    ast.Tuple, ast.List,            # literal collections, constant elements only
    ast.Subscript, ast.Slice,       # list/str indexing and slicing
    ast.Pass,
}

SIGNATURE_SRC = "def handle(text: str) -> str | bool:"


def _is_blank_str(s: str) -> bool:
    return s.strip() == ""


def lint_rule_source(source: str, file_name: str, max_lines: int) -> Diagnostic:
    """Lint a single rule file. Returns a Diagnostic with issues (empty = pass)."""
    diag = Diagnostic(stage="lint")

    # CODE004: line count (counted before parsing; parse errors reported separately)
    lines = source.splitlines()
    if len(lines) > max_lines:
        diag.add("CODE004", f"文件超过 {max_lines} 行（当前 {len(lines)} 行）", file_name, 1)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        diag.add("CODE009", f"Python 语法错误：{exc.msg}", file_name, exc.lineno or 1)
        return diag

    # ---- top-level structure ----
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    other_top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Expr, ast.Pass))]

    # CODE001: exactly one handle function
    if len(funcs) != 1 or not isinstance(funcs[0], ast.FunctionDef) or funcs[0].name != "handle":
        diag.add(
            "CODE001",
            f"每个文件必须恰好一个名为 handle 的同步函数（发现 {len(funcs)} 个函数定义）",
            file_name, 1,
        )
    for node in tree.body:
        if isinstance(node, ast.Expr) and not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            diag.add("CODE009", "模块顶层只允许 handle 函数定义和 docstring", file_name, node.lineno)
    for node in other_top:
        diag.add("CODE009", f"不允许的顶层语句：{type(node).__name__}", file_name, node.lineno)

    handle_func = funcs[0] if len(funcs) == 1 and isinstance(funcs[0], ast.FunctionDef) else None

    # CODE002: fixed signature
    if handle_func is not None:
        args = handle_func.args
        ok_sig = (
            len(args.posonlyargs) == 0
            and len(args.args) == 1
            and args.vararg is None
            and args.kwarg is None
            and not args.kwonlyargs
            and args.defaults == []
            and args.args[0].arg == "text"
            and args.args[0].annotation is not None
            and ast.unparse(args.args[0].annotation) == "str"
            and handle_func.returns is not None
            and ast.unparse(handle_func.returns) == "str | bool"
            and not handle_func.decorator_list
        )
        if not ok_sig:
            diag.add(
                "CODE002",
                f"函数签名必须固定为 {SIGNATURE_SRC}",
                file_name, handle_func.lineno,
            )

        # CODE003: no nested defs / classes / lambdas (also enforced via whitelist)
        # CODE005: last statement must be `return False`
        body = handle_func.body
        if not body or not (isinstance(body[-1], ast.Return) and _is_const_false(body[-1].value)):
            last = ast.unparse(body[-1]) if body else "(空)"
            diag.add(
                "CODE005",
                f"最后一条语句必须为 `return False`（当前为 `{last}`）",
                file_name, body[-1].lineno if body else handle_func.lineno,
            )

    # ---- walk: whitelist + targeted codes ----
    # Signature annotations (`text: str`, `-> str | bool`) are validated by the
    # CODE002 signature check; their subtrees (incl. BitOr) are exempt from the
    # generic walk so the `|` in `str | bool` is not treated as an expression.
    annotation_nodes: set[int] = set()
    if handle_func is not None:
        arg = handle_func.args.args[0] if handle_func.args.args else None
        for ann in (arg.annotation if arg else None, handle_func.returns):
            if ann is not None:
                for sub in ast.walk(ann):
                    annotation_nodes.add(id(sub))

    for node in ast.walk(tree):
        if id(node) in annotation_nodes:
            continue
        t = type(node)

        # specific, high-signal codes first
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            diag.add("CODE013", "规则文件不得包含 import", file_name, node.lineno)
            continue
        if isinstance(node, ast.ClassDef):
            diag.add("CODE003", "禁止定义类", file_name, node.lineno)
            continue
        if isinstance(node, (ast.Lambda,)):
            diag.add("CODE003", "禁止 lambda", file_name, node.lineno)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and handle_func is not None and node is not handle_func:
            diag.add("CODE003", "禁止辅助函数、嵌套函数或异步函数", file_name, node.lineno)
            continue
        if isinstance(node, ast.AsyncFunctionDef):
            diag.add("CODE003", "禁止异步函数", file_name, node.lineno)
            continue
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
            diag.add("CODE010", "禁止循环", file_name, node.lineno)
            continue
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            diag.add("CODE010", "禁止推导式", file_name, node.lineno)
            continue
        if isinstance(node, ast.Try) or (hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)):
            diag.add("CODE010", "禁止异常处理", file_name, node.lineno)
            continue
        if isinstance(node, (ast.With, ast.AsyncWith)):
            diag.add("CODE010", "禁止上下文管理器", file_name, node.lineno)
            continue
        if isinstance(node, ast.JoinedStr):
            diag.add("CODE009", "禁止 f-string，请使用普通字符串或 `+` 拼接", file_name, node.lineno)
            continue

        # constants: str / bool / int (ints for indices, slices, len() checks)
        if isinstance(node, ast.Constant) and not isinstance(node.value, (str, bool, int)):
            diag.add("CODE009", f"只允许字符串、布尔和整数字面量（发现 {type(node.value).__name__}）", file_name, node.lineno)
            continue

        # attribute access
        if isinstance(node, ast.Attribute):
            if node.attr in DANGEROUS_ATTRS:
                diag.add(DANGEROUS_ATTRS[node.attr], f"禁止属性访问 `.{node.attr}`", file_name, node.lineno)
            elif node.attr not in APPROVED_METHODS:
                diag.add("CODE014", f"未批准的方法 `.{node.attr}`，允许：{sorted(APPROVED_METHODS)}", file_name, node.lineno)
            continue

        # calls
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in DANGEROUS_NAMES:
                    diag.add(DANGEROUS_NAMES[func.id], f"禁止调用 `{func.id}`", file_name, node.lineno)
                elif func.id in APPROVED_FUNCTIONS:
                    for kw in node.keywords:
                        diag.add("CODE009", "禁止关键字参数", file_name, node.lineno)
                    if len(node.args) != 1:
                        diag.add("CODE009", f"`{func.id}()` 只接受 1 个参数", file_name, node.lineno)
                else:
                    diag.add("CODE009", "只允许调用批准的函数（len）和批准的字符串/列表方法", file_name, node.lineno)
                continue
            if isinstance(func, ast.Attribute):
                if func.attr not in APPROVED_METHODS:
                    # already reported via Attribute branch
                    pass
                for kw in node.keywords:
                    diag.add("CODE009", "禁止关键字参数", file_name, node.lineno)
                if len(node.args) > 2:
                    diag.add("CODE014", "字符串方法最多接受 2 个参数", file_name, node.lineno)
            else:
                diag.add("CODE009", f"不允许的调用形式：{ast.unparse(node)}", file_name, node.lineno)
            continue

        # bare dangerous names
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in DANGEROUS_NAMES:
            diag.add(DANGEROUS_NAMES[node.id], f"禁止使用 `{node.id}`", file_name, node.lineno)
            continue

        # binary ops: only +
        if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Add):
            diag.add("CODE009", f"只允许 `+`（字符串/列表拼接），发现 `{ast.unparse(node.op)}`", file_name, node.lineno)
            continue
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, (ast.Not, ast.USub)):
            diag.add("CODE009", "只允许 `not` 一元运算和负整数（如 -1）", file_name, node.lineno)
            continue

        # comparison ops: equality & membership only
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if not isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                    diag.add("CODE009", f"只允许相等比较和成员判断，发现 `{ast.unparse(op)}`", file_name, node.lineno)
            continue

        # literal collections: constant elements only
        if isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                if not (isinstance(elt, ast.Constant) and isinstance(elt.value, (str, bool, int))):
                    diag.add("CODE009", "集合字面量只允许包含字符串/布尔/整数常量", file_name, node.lineno)
            continue

        if t not in WHITELIST:
            diag.add("CODE009", f"不允许的语法结构：{t.__name__}", file_name, getattr(node, "lineno", 1))

    # ---- return statements ----
    if handle_func is not None:
        _check_returns(handle_func, file_name, diag)

    return diag


def _is_const_false(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _check_returns(func: ast.FunctionDef, file_name: str, diag: Diagnostic) -> None:
    """CODE006/CODE007 static checks + CODE008 unconditional-fallback detection."""
    # Map each Return node to whether it is nested inside an If.
    conditional: set[int] = set()

    class _Marker(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_If(self, node: ast.If) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_Return(self, node: ast.Return) -> None:
            if self.depth > 0:
                conditional.add(id(node))
            self.generic_visit(node)

    _Marker().visit(func)

    for node in ast.walk(func):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        inside_if = id(node) in conditional
        if value is None:
            diag.add("CODE006", "禁止 `return`（无值），必须 return 字符串或 False", file_name, node.lineno)
        elif isinstance(value, ast.Constant):
            if value.value is True:
                diag.add("CODE006", "禁止 `return True`，未命中必须 `return False`", file_name, node.lineno)
            elif value.value is False:
                pass
            elif isinstance(value.value, str):
                if _is_blank_str(value.value):
                    diag.add("CODE007", "返回的回复不能为空或纯空白", file_name, node.lineno)
                if not inside_if:
                    diag.add("CODE008", "无条件返回字符串兜底：回复必须放在条件分支内", file_name, node.lineno)
            else:
                diag.add("CODE006", f"禁止返回 {type(value.value).__name__} 类型", file_name, node.lineno)
        elif isinstance(value, ast.Name) and value.id == "text" and not inside_if:
            diag.add("CODE008", "无条件回显输入（return text）不允许：回复必须放在条件分支内", file_name, node.lineno)
