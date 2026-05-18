import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import backtranslate_check
import qa_semantic


class SemanticQaModuleTests(unittest.TestCase):
    def test_backtranslate_check_reexports_semantic_qa_api(self):
        self.assertIs(backtranslate_check.normalize_english, qa_semantic.normalize_english)
        self.assertIs(backtranslate_check.make_prompt, qa_semantic.make_prompt)
        self.assertIs(backtranslate_check.run_backtranslation, qa_semantic.run_backtranslation)
        self.assertIs(backtranslate_check.build_report, qa_semantic.build_report)
        self.assertIs(backtranslate_check.classify_block, qa_semantic.classify_block)
        self.assertIs(backtranslate_check.choose_items_for_qa, qa_semantic.choose_items_for_qa)
        self.assertIs(backtranslate_check.write_markdown, qa_semantic.write_markdown)

    def test_qa_semantic_does_not_import_translation_pipeline(self):
        script = (
            "import backtranslate_check, qa_semantic, sys; "
            "raise SystemExit(1 if 'translate_pdf_via_codex' in sys.modules else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_qa_semantic_import_does_not_mutate_sys_path(self):
        script = (
            "import sys; "
            "before = list(sys.path); "
            "import qa_semantic; "
            "raise SystemExit(0 if sys.path == before else 1)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_normalize_english_canonicalizes_case_quotes_and_spacing(self):
        self.assertEqual(
            qa_semantic.normalize_english("  Hello , WORLD !\nThis  is fine .  "),
            "hello,world!this is fine.",
        )
        self.assertIn('"quoted"', qa_semantic.normalize_english("“Quoted”"))
        self.assertIn("'apostrophe'", qa_semantic.normalize_english("’Apostrophe’"))

    def test_make_prompt_includes_items_and_expected_json_shape(self):
        prompt = qa_semantic.make_prompt(
            [
                {"id": "p001b0001", "translation": "这是一个中文段落。"},
                {"id": "p002b0003", "translation": "另一个中文段落。"},
            ]
        )

        self.assertIn("back-translation quality check", prompt)
        self.assertIn('"back_translation"', prompt)
        self.assertIn('"p001b0001"', prompt)
        self.assertIn('"p002b0003"', prompt)
        self.assertIn("这是一个中文段落。", prompt)

    def test_choose_items_for_qa_is_deterministic_and_prioritizes_high_value_blocks(self):
        original_map = {
            "p003b0001": "A later body paragraph with ordinary discussion.",
            "p001b0001": "Abstract This paper studies wait-free synchronization.",
            "p002b0001": "1. Introduction:",
            "p010b0001": "A much later paragraph.",
        }
        translations = {block_id: f"译文 {block_id}" for block_id in original_map}

        all_items = qa_semantic.choose_items_for_qa(original_map, translations, "all", 2)
        self.assertEqual([item["id"] for item in all_items], list(translations))

        sampled = qa_semantic.choose_items_for_qa(original_map, translations, "sample", 2)
        self.assertEqual([item["id"] for item in sampled], ["p001b0001", "p002b0001"])

    def test_build_report_scores_and_sorts_lowest_first(self):
        original_map = {
            "bad": "This sentence describes a lock-free queue.",
            "good": "Distributed consensus requires agreement.",
        }
        translations = {
            "bad": "错误译文",
            "good": "分布式共识需要达成一致。",
        }
        backtranslations = {
            "bad": "A banana describes a calendar.",
            "good": "Distributed consensus requires agreement.",
        }

        report = qa_semantic.build_report(original_map, translations, backtranslations)

        self.assertEqual([item["id"] for item in report], ["bad", "good"])
        self.assertEqual(report[1]["score"], 1.0)
        self.assertEqual(report[0]["translation"], "错误译文")

    def test_write_markdown_is_deterministic_and_limits_to_top_twenty_items(self):
        report = [
            {
                "id": f"p001b{i:04d}",
                "score": round(i / 100, 4),
                "original": f"original {i}",
                "translation": f"translation {i}",
                "back_translation": f"back {i}",
            }
            for i in range(25)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backtranslate_report.md"
            qa_semantic.write_markdown(report, path)
            text = path.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("# Back-Translation QA Report"))
        self.assertIn("## p001b0000  score=0.0", text)
        self.assertIn("## p001b0019  score=0.19", text)
        self.assertNotIn("## p001b0020", text)

    def test_run_backtranslation_uses_injected_runner_and_validates_output_ids(self):
        items = [
            {"id": "p001b0001", "translation": "甲" * 8},
            {"id": "p001b0002", "translation": "乙" * 8},
            {"id": "p001b0003", "translation": "丙" * 8},
        ]
        prompts = []

        def fake_runner(cmd, *, input_text=None, check=True):
            prompts.append(input_text)
            out_path = Path(cmd[cmd.index("-o") + 1])
            payload = json.loads(input_text.split("Items:\n", 1)[1])
            out_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {"id": item["id"], "back_translation": f"back {item['id']}"}
                            for item in payload["items"]
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            result = qa_semantic.run_backtranslation(
                items,
                16,
                job_dir,
                retries=1,
                runner=fake_runner,
            )

            self.assertTrue((job_dir / "backtranslate_schema.json").exists())
            self.assertTrue((job_dir / "backtranslate-01.log.txt").exists())
            self.assertGreater(len(prompts), 1)

        self.assertEqual(
            result,
            {
                "p001b0001": "back p001b0001",
                "p001b0002": "back p001b0002",
                "p001b0003": "back p001b0003",
            },
        )

    def test_run_backtranslation_reports_failed_batch_log_path(self):
        def failing_runner(_cmd, *, input_text=None, check=True):
            return SimpleNamespace(returncode=1, stdout="bad", stderr="stderr")

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "back-translation batch 1 failed"):
                qa_semantic.run_backtranslation(
                    [{"id": "p001b0001", "translation": "译文"}],
                    7000,
                    job_dir,
                    retries=1,
                    runner=failing_runner,
                )
            self.assertTrue((job_dir / "backtranslate-01.log.txt").exists())

    def test_backtranslate_check_main_keeps_compatible_report_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "work"
            job_dir = work_dir / "jobs" / "paper"
            job_dir.mkdir(parents=True)
            (job_dir / "source_pages.json").write_text(
                json.dumps([[{"id": "p001b0001", "text": "Original English text."}]]),
                encoding="utf-8",
            )
            (job_dir / "translations.json").write_text(
                json.dumps({"p001b0001": "中文译文"}, ensure_ascii=False),
                encoding="utf-8",
            )
            original_tmp_dir = backtranslate_check.TMP_DIR
            argv = [
                "backtranslate_check.py",
                "--job-name",
                "paper",
                "--mode",
                "all",
                "--retries",
                "1",
            ]

            try:
                backtranslate_check.TMP_DIR = work_dir
                with (
                    patch.object(sys, "argv", argv),
                    patch.object(
                        backtranslate_check,
                        "run_backtranslation",
                        return_value={"p001b0001": "Original English text."},
                    ) as run_mock,
                ):
                    backtranslate_check.main()
            finally:
                backtranslate_check.TMP_DIR = original_tmp_dir

            run_mock.assert_called_once()
            report_json = json.loads((job_dir / "backtranslate_report.json").read_text(encoding="utf-8"))
            report_md = (job_dir / "backtranslate_report.md").read_text(encoding="utf-8")
            self.assertEqual(report_json[0]["id"], "p001b0001")
            self.assertIn("Back-Translation QA Report", report_md)


if __name__ == "__main__":
    unittest.main()
