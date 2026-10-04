import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import translation_batch
import pipeline
import translate_pdf_via_codex as serial
import translate_pdf_parallel as parallel


class SharedBatchExecutionTests(unittest.TestCase):
    def test_cached_translation_requires_the_same_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {"job_dir": root, "schema_path": root / "schema.json",
                   "translations_path": root / "translations.json"}
            requests = []

            def run(cmd, **kwargs):
                requests.append(kwargs["input"])
                output = Path(cmd[cmd.index("-o") + 1])
                output.write_text(json.dumps({"items": [{"id": "a", "translation": f"译文{len(requests)}"}]}))
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch("subprocess.run", side_effect=run):
                original = [translation_batch.TranslationBatch("batch-01", [{"id": "a", "text": "First."}])]
                changed = [translation_batch.TranslationBatch("batch-01", [{"id": "a", "text": "Changed."}])]
                self.assertEqual(translation_batch.run_batches(original, job, model="model-a"), {"a": "译文1"})
                self.assertEqual(translation_batch.run_batches(original, job, model="model-a", workers=3),
                                 {"a": "译文1"})
                self.assertEqual(translation_batch.run_batches(changed, job, model="model-a"), {"a": "译文2"})
                self.assertEqual(translation_batch.run_batches(changed, job, model="model-b"), {"a": "译文3"})
            self.assertEqual(len(requests), 3)

    def test_worker_count_preserves_batches_and_rejects_unverified_legacy_cache(self):
        for workers in (1, 3):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cache = root / "translations.json"
                cache.write_text(json.dumps({"a": "缓存译文", "unselected": "其他页"}))
                batches = [
                    translation_batch.TranslationBatch("batch-01", [
                        {"id": "a", "text": "First."}, {"id": "b", "text": "Second."}]),
                    translation_batch.TranslationBatch("batch-02", [{"id": "c", "text": "Third."}]),
                ]
                requests = []

                def run(cmd, **kwargs):
                    items = json.loads(kwargs["input"].split("待翻译条目如下：", 1)[1])["items"]
                    output = Path(cmd[cmd.index("-o") + 1])
                    requests.append((output.name, [item["id"] for item in items]))
                    output.write_text(json.dumps({"items": [
                        {"id": item["id"], "translation": "新译文"} for item in items
                    ]}))
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                with patch("subprocess.run", side_effect=run):
                    result = translation_batch.run_batches(
                        batches, {"job_dir": root, "schema_path": root / "schema.json",
                                  "translations_path": cache},
                        model="test", workers=workers,
                    )
                self.assertEqual(sorted(requests), [
                    ("batch-01.out.json", ["a", "b"]), ("batch-02.out.json", ["c"]),
                ])
                self.assertEqual(result, {"a": "新译文", "b": "新译文", "c": "新译文"})
                self.assertEqual(json.loads(cache.read_text()), result)

    def test_failed_later_batch_leaves_completed_batch_for_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "translations.json"
            batches = [translation_batch.TranslationBatch(f"batch-{index:02d}", [
                {"id": block_id, "text": "Body."}
            ]) for index, block_id in enumerate(("a", "b"), 1)]

            def run(cmd, **kwargs):
                output = Path(cmd[cmd.index("-o") + 1])
                if output.name == "batch-01.out.json":
                    output.write_text('{"items": [{"id": "a", "translation": "完成"}]}')
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return subprocess.CompletedProcess(cmd, 1, "", "failed")

            with patch("subprocess.run", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "batch-02"):
                    translation_batch.run_batches(
                        batches, {"job_dir": root, "schema_path": root / "schema.json",
                                  "translations_path": cache},
                        model="test", workers=1, retries=1,
                    )
            self.assertEqual(json.loads(cache.read_text()), {"a": "完成"})

    def test_retranslate_backs_up_existing_cache_before_model_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "translations.json"
            cache.write_text('{"a": "原译文"}', encoding="utf-8")
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "failed")):
                with self.assertRaises(RuntimeError):
                    translation_batch.run_batches(
                        [translation_batch.TranslationBatch("batch-01", [{"id": "a", "text": "Body."}])],
                        {"job_dir": root, "schema_path": root / "schema.json", "translations_path": cache},
                        model="test", retries=1, retranslate=True,
                    )
            backups = list(root.glob("translations.backup-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text()), {"a": "原译文"})


class DocumentExecutionTests(unittest.TestCase):
    def test_source_cache_rebuilds_when_pdf_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "paper.pdf"
            source.write_bytes(b"first PDF bytes")
            with patch.object(pipeline, "TMP_ROOT", root):
                job = pipeline.build_job_paths(source, "paper")
            job["job_dir"].mkdir(parents=True)
            job["source_pages_path"].write_text('[[{"id": "stale", "text": "old"}]]')
            fresh = [[{"id": "fresh", "page": 1, "block_index": 1,
                      "text": "Fresh source paragraph.", "xMin": 10, "yMin": 20,
                      "xMax": 100, "yMax": 40}]]
            with (
                patch.object(pipeline, "ensure_assets") as assets,
                patch.object(pipeline, "parse_bbox", return_value=fresh),
                patch.object(pipeline, "should_use_ocr", return_value=False),
                patch.object(pipeline, "text_extraction_looks_garbled", return_value=False),
            ):
                first = pipeline.load_or_build_source_pages(source, 200, job, (200, 220))
                self.assertEqual(first[0][0]["id"], "fresh")
                self.assertEqual(assets.call_count, 1)
                pipeline.load_or_build_source_pages(source, 200, job, (200, 220))
                self.assertEqual(assets.call_count, 1)
                source.write_bytes(b"changed PDF bytes")
                pipeline.load_or_build_source_pages(source, 200, job, (200, 220))
                self.assertEqual(assets.call_count, 2)

    def test_failed_sentence_repair_preserves_output_and_completed_translation_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "paper.pdf"
            with pipeline.load_fitz().open() as source_doc:
                source_doc.new_page(width=623, height=801)
                source_doc.new_page(width=623, height=801)
                source_doc.save(source)
            output = root / "output.pdf"
            output.write_bytes(b"previous PDF")
            job = root / "work/jobs/paper"
            job.mkdir(parents=True)
            pages = [[{
                "id": f"p{page:03d}b0001", "page": page, "block_index": 1,
                "text": text, "xMin": 130, "yMin": y, "xMax": 486, "yMax": y + 40,
            }] for page, text, y in [
                (1, "The history appears sequential to each process, and", 590),
                (2, "of operations. Equivalently, each operation appears instantaneously.", 50),
            ]]
            (job / "source_pages.json").write_text(json.dumps(pages))
            (job / "source_cache_key.txt").write_text(
                pipeline.source_cache_key(source, 200, 1, 0, False)
            )
            raw = {"p001b0001": "未完成，并且", "p002b0001": "的操作。后续句子。"}

            def run(cmd, **kwargs):
                self.assertEqual(cmd[:2], ["codex", "exec"])
                out = Path(cmd[cmd.index("-o") + 1])
                if out.name.startswith("page-"):
                    items = json.loads(kwargs["input"].split("待翻译条目如下：", 1)[1])["items"]
                    out.write_text(json.dumps({"items": [
                        {"id": item["id"], "translation": raw[item["id"]]} for item in items
                    ]}))
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return subprocess.CompletedProcess(cmd, 1, "", "sentence repair failed")

            with patch.object(pipeline, "TMP_ROOT", root / "work"), \
                 patch.object(pipeline, "get_pdf_page_size", return_value=(623, 801)), \
                 patch("subprocess.run", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "boundary-sentence-repair"):
                    pipeline.translate_document(source, output, pipeline.DocumentOptions(model="test", retries=1))
            self.assertEqual(output.read_bytes(), b"previous PDF")
            self.assertEqual(json.loads((job / "translations.json").read_text()), raw)
            self.assertEqual(list((job / "plans").glob("*.json")), [])

    def test_refresh_source_keeps_backup_even_when_rebuild_selects_no_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "jobs/paper"
            job.mkdir(parents=True)
            cached = job / "source_pages.json"
            cached.write_text('[[]]', encoding="utf-8")
            present_during_load = []

            def load(*args, **kwargs):
                present_during_load.append(cached.exists())
                return []

            with patch.object(pipeline, "TMP_ROOT", root), \
                 patch.object(pipeline, "get_pdf_page_size", return_value=(300, 400)), \
                 patch.object(pipeline, "load_or_build_source_pages", side_effect=load):
                with self.assertRaisesRegex(RuntimeError, "no pages selected"):
                    pipeline.translate_document(root / "paper.pdf", root / "out.pdf",
                                                pipeline.DocumentOptions(refresh_source=True))
            self.assertEqual(present_during_load, [False])
            backups = list(job.glob("source_pages.source-backup-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text()), [[]])

    def test_batch_scope_preserves_empty_page_numbers_and_character_boundaries(self):
        text = "This complete body paragraph explains the experimental results in detail."

        def block(block_id, page, y):
            return {"id": block_id, "page": page, "text": text,
                    "xMin": 40, "yMin": y, "xMax": 350, "yMax": y + 40}

        selected = [(1, [block("a", 1, 100)]), (3, []),
                    (5, [block("b", 5, 100), block("c", 5, 200)])]
        for scope, expected in (
            ("document", [("batch-01", ["a", "b"]), ("batch-02", ["c"])]),
            ("page", [("page-001-chunk-01", ["a"]), ("page-005-chunk-01", ["b", "c"])]),
        ):
            with self.subTest(scope=scope):
                batches = pipeline.build_translation_batches(selected, len(text) * 2, batch_scope=scope)
                self.assertEqual([(batch.prefix, [item["id"] for item in batch.items]) for batch in batches], expected)
                oversized = pipeline.build_translation_batches(selected, len(text) - 1, batch_scope=scope)
                self.assertEqual([[item["id"] for item in batch.items] for batch in oversized], [["a"], ["b"], ["c"]])

    def test_empty_page_selection_fails_before_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(pipeline, "TMP_ROOT", root), \
                 patch.object(pipeline, "get_pdf_page_size", return_value=(300, 400)), \
                 patch.object(pipeline, "load_or_build_source_pages", return_value=[[]]), \
                 patch("subprocess.run", side_effect=AssertionError("empty selection must not invoke a model")):
                with self.assertRaisesRegex(RuntimeError, "no pages selected"):
                    pipeline.translate_document(root / "source.pdf", root / "out.pdf",
                                                pipeline.DocumentOptions(page_start=2))
            self.assertFalse((root / "out.pdf").exists())

    def test_existing_output_skips_source_and_preserves_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out.pdf"
            output.write_bytes(b"previous output")
            with patch.object(pipeline, "get_pdf_page_size", side_effect=AssertionError("source should not be opened")):
                result = pipeline.translate_document(root / "missing.pdf", output,
                                                     pipeline.DocumentOptions(overwrite=False))
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(output.read_bytes(), b"previous output")

    def test_both_clis_share_batching_and_preserve_qa_defaults_and_selected_pages(self):
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "paper.pdf"
                fitz = pipeline.load_fitz()
                with fitz.open() as pdf:
                    for _ in range(3):
                        pdf.new_page(width=400, height=500)
                    pdf.save(source)
                work = root / "work"
                job = work / "jobs/paper"
                job.mkdir(parents=True)
                pages = [[{
                    "id": f"p{page:03d}b0001", "page": page, "block_index": 1,
                    "text": "This complete body paragraph explains the experimental results in detail.",
                    "xMin": 40, "yMin": 100, "xMax": 350, "yMax": 160,
                }] for page in (1, 2, 3)]
                (job / "source_pages.json").write_text(json.dumps(pages))
                (job / "source_cache_key.txt").write_text(
                    pipeline.source_cache_key(source, 200, 2, 3, False)
                )
                requested_ids, backtranslation_ids = [], []

                def run(cmd, **kwargs):
                    self.assertEqual(cmd[:2], ["codex", "exec"])
                    prompt = kwargs["input"]
                    if "Items:\n" in prompt:
                        items = json.loads(prompt.split("Items:\n", 1)[1])["items"]
                        backtranslation_ids.extend(item["id"] for item in items)
                        response = {"items": [{"id": item["id"], "back_translation": pages[0][0]["text"]}
                                              for item in items]}
                    else:
                        items = json.loads(prompt.split("待翻译条目如下：", 1)[1])["items"]
                        requested_ids.append([item["id"] for item in items])
                        response = {"items": [{"id": item["id"], "translation": "这段完整正文详细解释了实验结果。"}
                                              for item in items]}
                    Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(response))
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                common = ["--work-dir", str(work), "--page-start", "2", "--page-end", "3", "--model", "test"]
                if entry == "serial":
                    output = root / "single.pdf"
                    argv = ["translate_pdf_via_codex.py", "--pdf", str(source), "--pdf-output", str(output)]
                else:
                    output = root / "output/paper-Chinese.pdf"
                    argv = ["translate_pdf_parallel.py", "--source-dir", str(root), "--target-dir", str(output.parent)]
                with patch.object(sys, "argv", argv + common), \
                     patch.object(pipeline, "TMP_ROOT", work), \
                     patch.object(pipeline, "get_pdf_page_size", return_value=(400, 500)), \
                     patch("subprocess.run", side_effect=run):
                    (serial.main if entry == "serial" else parallel.main)()

                self.assertEqual(sorted(requested_ids), [["p002b0001"], ["p003b0001"]])
                self.assertEqual(sorted(backtranslation_ids), [] if entry == "serial" else ["p002b0001", "p003b0001"])
                with fitz.open(output) as pdf:
                    self.assertEqual(len(pdf), 2)
                plans = [json.loads(path.read_text()) for path in sorted((job / "plans").glob("*.json"))]
                self.assertEqual([plan["page_num"] for plan in plans], [2, 3])
                self.assertEqual([plan["output_page_num"] for plan in plans], [1, 2])
                if entry == "parallel":
                    result = json.loads((work / "parallel_translation_summary.json").read_text())[0]
                    self.assertEqual(result["qa"]["checked_blocks"], 2)
                    self.assertEqual(result["qa"]["visual_checked_pages"], [2, 3])
                else:
                    self.assertFalse((job / "backtranslate_report.json").exists())


if __name__ == "__main__":
    unittest.main()
