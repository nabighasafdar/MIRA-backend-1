import ast
import os
import glob

def clean_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
    except Exception as e:
        print(f"Skipping {filepath} (could not read): {e}")
        return

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"Skipping {filepath} (SyntaxError): {e}")
        return

    # Remove docstrings
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr) and
                isinstance(node.body[0].value, ast.Constant) and
                isinstance(node.body[0].value.value, str)):
                node.body.pop(0)

    # We need a custom NodeTransformer to remove @observe decorators
    class DecoratorRemover(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            self.generic_visit(node)
            new_decorator_list = []
            for dec in node.decorator_list:
                # Check for @observe
                if isinstance(dec, ast.Name) and dec.id == 'observe':
                    continue
                # Check for @observe()
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == 'observe':
                    continue
                # Check for @observe_debug
                elif isinstance(dec, ast.Name) and dec.id == 'observe_debug':
                    continue
                # Check for @observe_debug()
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == 'observe_debug':
                    continue
                else:
                    new_decorator_list.append(dec)
            node.decorator_list = new_decorator_list
            return node

        def visit_AsyncFunctionDef(self, node):
            self.generic_visit(node)
            new_decorator_list = []
            for dec in node.decorator_list:
                # Check for @observe
                if isinstance(dec, ast.Name) and dec.id == 'observe':
                    continue
                # Check for @observe()
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == 'observe':
                    continue
                # Check for @observe_debug
                elif isinstance(dec, ast.Name) and dec.id == 'observe_debug':
                    continue
                # Check for @observe_debug()
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == 'observe_debug':
                    continue
                else:
                    new_decorator_list.append(dec)
            node.decorator_list = new_decorator_list
            return node
            
        def visit_ImportFrom(self, node):
            if node.module == 'MIRA.system.observability':
                # Check if we are importing observe or observe_debug
                names = [n for n in node.names if n.name not in ('observe', 'observe_debug')]
                if len(names) == 0:
                    return None # Remove the import completely
                node.names = names
            return node

    remover = DecoratorRemover()
    tree = remover.visit(tree)
    ast.fix_missing_locations(tree)

    cleaned_source = ast.unparse(tree)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(cleaned_source)
    print(f"Cleaned {filepath}")

if __name__ == "__main__":
    system_dir = os.path.join(os.path.dirname(__file__), 'system')
    python_files = glob.glob(os.path.join(system_dir, '**', '*.py'), recursive=True)
    for filepath in python_files:
        clean_file(filepath)
    print("Done!")
