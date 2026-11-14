"""The prebuilt deploy helper must preserve physical .env lines."""
import ast
from pathlib import Path
import tempfile
import unittest

class DeploymentEnvironmentFormatTests(unittest.TestCase):
    def test_environment_write_uses_real_newlines_and_preserves_literal_values(self):
        source=Path(__file__).resolve().parents[1]/'deploy-prebuilt-backend.py'
        tree=ast.parse(source.read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.With) and any(isinstance(i.context_expr,ast.Call) and isinstance(i.context_expr.func,ast.Attribute) and i.context_expr.func.attr=='open' and isinstance(i.context_expr.func.value,ast.Name) and i.context_expr.func.value.id=='envfile' for i in n.items))
        lines=['PUBLIC_BASE_URL=https://example.invalid','LITERAL=value\\ntext','COMPOSE_FILE=base.yml:override.json']
        with tempfile.TemporaryDirectory() as directory:
            envfile=Path(directory)/'.env'
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),dict(envfile=envfile,lines=lines))
            self.assertEqual(envfile.read_text().splitlines(),lines)
            self.assertTrue(envfile.read_bytes().endswith(b'\n'))
