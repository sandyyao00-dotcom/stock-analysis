import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class AppSummaryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))

    def test_news_summary_does_not_overwrite_stock_summary(self):
        summary_assignments = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "summary" for target in node.targets)
        ]

        self.assertEqual(len(summary_assignments), 1)
        value = summary_assignments[0].value
        self.assertIsInstance(value, ast.Call)
        self.assertIsInstance(value.func, ast.Name)
        self.assertEqual(value.func.id, "summarize_stock")

        agreement_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "agreement_analysis"
        ]
        self.assertEqual(len(agreement_calls), 1)
        technical_score = agreement_calls[0].args[0]
        self.assertIsInstance(technical_score, ast.Attribute)
        self.assertIsInstance(technical_score.value, ast.Name)
        self.assertEqual(technical_score.value.id, "summary")
        self.assertEqual(technical_score.attr, "technical_score")

    def test_news_summary_keeps_stock_summary_and_original_truncation_rule(self):
        news_loop = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.For)
            and isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "enumerate"
            and ast.unparse(node.iter.args[0]) == "news_result.articles"
        )
        agreement_statement = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value).startswith("st.info(agreement_analysis(")
        )
        executable = ast.fix_missing_locations(ast.Module(body=[news_loop, agreement_statement], type_ignores=[]))

        class FakeContainer:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        writes = []
        info_messages = []
        agreement_arguments = []
        fake_st = SimpleNamespace(
            container=lambda **_kwargs: FakeContainer(),
            markdown=lambda *_args, **_kwargs: None,
            write=lambda value: writes.append(value),
            caption=lambda *_args, **_kwargs: None,
            link_button=lambda *_args, **_kwargs: None,
            info=lambda value: info_messages.append(value),
        )
        article = SimpleNamespace(
            category="中性/等待确认",
            event_label="一般新闻",
            title="测试新闻",
            summary_or_content="新" * 301,
            explanation="测试说明",
            published_at=None,
            publisher="测试来源",
            freshness="最新",
            url=None,
        )
        stock_summary = SimpleNamespace(technical_score=73)

        def fake_agreement(technical_score, fundamental_score):
            agreement_arguments.append((technical_score, fundamental_score))
            return "测试共振说明"

        namespace = {
            "agreement_analysis": fake_agreement,
            "fundamental_score": 61,
            "news_result": SimpleNamespace(articles=(article,)),
            "relative_age": lambda _published_at: "时间未知",
            "st": fake_st,
            "summary": stock_summary,
        }
        exec(compile(executable, str(APP_PATH), "exec"), namespace)

        self.assertIs(namespace["summary"], stock_summary)
        self.assertEqual(agreement_arguments, [(73, 61)])
        self.assertEqual(info_messages, ["测试共振说明"])
        self.assertIn("新" * 297 + "...", writes)


if __name__ == "__main__":
    unittest.main()
