"""Code parsing stage."""

from pathlib import Path

from rag_ingestion.models.file import FileRecord
from rag_ingestion.models.parsed import ParsedFile, ParsedSymbol
from rag_ingestion.utils.counters import PipelineCounters
from rag_ingestion.utils.logger import log_skip


def parse_file(file: FileRecord, counters: PipelineCounters) -> ParsedFile:
    """Parse a source file and extract imports and symbols."""
    try:
        source = Path(file.path).read_bytes()
        if file.language in {"python", "javascript", "typescript"}:
            parser, language = _build_parser(file.extension)
            tree = parser.parse(source)
            root = tree.root_node

            imports = _extract_imports(root, source, file.language)
            symbols = _extract_symbols(root, source, file.language)
        else:
            language = file.language
            imports = []
            symbols = []
        counters.files_parsed_ok += 1
        return ParsedFile(
            relative_path=file.relative_path,
            language=file.language,
            parse_status="ok",
            imports=imports,
            symbols=symbols,
        )
    except Exception:
        log_skip(file.relative_path, "ast_parse_failed", "file_level_fallback")
        counters.files_parse_failed += 1
        return ParsedFile(
            relative_path=file.relative_path,
            language=file.language,
            parse_status="failed",
            imports=[],
            symbols=[],
        )


def _build_parser(extension: str):
    from tree_sitter import Language, Parser

    parser = Parser()
    language = _load_language(extension, Language)
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language
    return parser, language


def _load_language(extension: str, language_class):
    if extension == ".py":
        import tree_sitter_python

        return language_class(tree_sitter_python.language())

    if extension in {".js", ".jsx"}:
        import tree_sitter_javascript

        return language_class(tree_sitter_javascript.language())

    if extension in {".ts", ".tsx"}:
        import tree_sitter_typescript

        if extension == ".tsx":
            return language_class(tree_sitter_typescript.language_tsx())
        return language_class(tree_sitter_typescript.language_typescript())

    raise ValueError(f"Unsupported parser extension: {extension}")


def _extract_imports(root, source: bytes, language: str) -> list[str]:
    import_types = {
        "python": {"import_statement", "import_from_statement"},
        "javascript": {"import_statement"},
        "typescript": {"import_statement"},
    }.get(language, set())
    imports: list[str] = []

    def visit(node) -> None:
        if node.type in import_types:
            imports.append(_node_text(node, source).strip())
        for child in node.children:
            visit(child)

    visit(root)
    return imports


def _extract_symbols(root, source: bytes, language: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []

    def visit(node, parent_symbol: str = "") -> None:
        if _is_class_node(node):
            class_name = _node_name(node, source)
            if class_name:
                methods = _class_methods(node, source)
                symbols.append(
                    ParsedSymbol(
                        symbol_name=class_name,
                        symbol_type="class",
                        parent_symbol="",
                        signature=_signature(node, source),
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        methods=methods,
                        docstring=_docstring(node, source, language),
                        calls=_calls(node, source),
                    )
                )
                for child in node.children:
                    visit(child, class_name)
                return

        if _is_function_node(node):
            name = _node_name(node, source)
            if name:
                symbols.append(
                    ParsedSymbol(
                        symbol_name=name,
                        symbol_type="method" if parent_symbol else "function",
                        parent_symbol=parent_symbol,
                        signature=_signature(node, source),
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parameters=_parameters(node, source),
                        docstring=_docstring(node, source, language),
                        calls=_calls(node, source),
                    )
                )
                return

        for child in node.children:
            visit(child, parent_symbol)

    visit(root)
    return symbols


def _is_class_node(node) -> bool:
    return node.type in {"class_definition", "class_declaration"}


def _is_function_node(node) -> bool:
    return node.type in {
        "function_definition",
        "function_declaration",
        "method_definition",
        "generator_function_declaration",
    }


def _node_name(node, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source).strip()

    for child in node.children:
        if child.type in {"identifier", "property_identifier"}:
            return _node_text(child, source).strip()
    return ""


def _parameters(node, source: bytes) -> list[str]:
    parameters_node = node.child_by_field_name("parameters")
    if parameters_node is None:
        return []

    parameters: list[str] = []
    for child in parameters_node.children:
        if child.type in {"identifier", "typed_parameter"}:
            text = _node_text(child, source).strip()
            if text and text not in {"self", ","}:
                parameters.append(text.split(":", 1)[0].strip())
    return parameters


def _class_methods(node, source: bytes) -> list[str]:
    methods: list[str] = []

    def visit(child) -> None:
        if _is_function_node(child):
            name = _node_name(child, source)
            if name:
                methods.append(name)
            return
        for grandchild in child.children:
            visit(grandchild)

    visit(node)
    return methods


def _docstring(node, source: bytes, language: str) -> str:
    if language != "python":
        return ""

    body = node.child_by_field_name("body")
    if body is None:
        return ""

    for child in body.children:
        if child.type == "expression_statement" and child.children:
            first = child.children[0]
            if first.type == "string":
                return _node_text(first, source).strip().strip("\"'")
    return ""


def _calls(node, source: bytes) -> list[str]:
    calls: list[str] = []

    def visit(child) -> None:
        if child.type in {"call", "call_expression"}:
            function_node = child.child_by_field_name("function") or (
                child.children[0] if child.children else None
            )
            if function_node is not None:
                name = _node_text(function_node, source).strip()
                if name:
                    calls.append(name)
        for grandchild in child.children:
            visit(grandchild)

    visit(node)
    return calls


def _signature(node, source: bytes) -> str:
    text = _node_text(node, source).strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    return first_line.rstrip("{").strip()


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
