import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import translate_pdf_parallel as parallel
import translate_pdf_via_codex as pipeline


class TranslationBatchTests(unittest.TestCase):
    def test_boundary_repairs_retry_invalid_or_stale_output(self):
        good = {"items": [{"key": "a->b", "translation": "完整句子。", "next_prefix_translation": ""}]}
        invalid = [None, "not JSON", "[]", '{"items": []}',
                   json.dumps({"items": good["items"] * 2}),
                   json.dumps({"items": [{**good["items"][0], "key": "other"}]}),
                   json.dumps({"items": [{**good["items"][0], "translation": " "}]}),
                   json.dumps({"items": [{"key": "a->b", "translation": "完整句子。"}]}),
                   json.dumps({"items": [{**good["items"][0], "next_prefix_translation": None}]}),
                   (1, json.dumps(good))]
        candidate = dict(key="a->b", previous_id="a", next_id="b", source_sentence="A sentence.",
                         previous_source="A", next_source="sentence.")
        for bad in invalid:
            with self.subTest(response=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job = {"job_dir": root, "boundary_schema_path": root / "schema.json"}
                output = root / "boundary-sentence-repair.out.json"
                output.write_text(json.dumps({"items": [{**good["items"][0], "translation": "旧结果"}]}))
                responses = iter([bad, json.dumps(good)])

                def run(cmd, **kwargs):
                    response = next(responses)
                    code = 0
                    if isinstance(response, tuple):
                        code, response = response
                    if response is not None:
                        output.write_text(response)
                    return subprocess.CompletedProcess(cmd, code, "", "")

                with patch("subprocess.run", side_effect=run) as command, patch("time.sleep"):
                    result = pipeline.translate_boundary_sentence_repairs([candidate], {}, job, model="test")
                self.assertEqual(result, {"a->b": {"translation": "完整句子。", "next_prefix_translation": ""}})
                self.assertEqual(command.call_count, 2)

    def test_boundary_repairs_fail_after_invalid_results_exhaust_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {"job_dir": root, "boundary_schema_path": root / "schema.json"}
            candidate = dict(key="a->b", previous_id="a", next_id="b", source_sentence="A sentence.",
                             previous_source="A", next_source="sentence.")
            def run(cmd, **kwargs):
                Path(cmd[cmd.index("-o") + 1]).write_text('{"items": []}')
                return subprocess.CompletedProcess(cmd, 0, "", "")
            with patch("subprocess.run", side_effect=run) as command, patch("time.sleep"):
                with self.assertRaises(RuntimeError):
                    pipeline.translate_boundary_sentence_repairs([candidate], {}, job, model="test")
            self.assertEqual(command.call_count, 3)

    def test_serial_and_parallel_repair_boundaries_before_either_render_mode(self):
        pages = [[{"id": f"p{page:03d}b0001", "page": page, "block_index": 1,
                   "text": text, "xMin": 130, "yMin": y, "xMax": 486, "yMax": y + 40}]
                 for page, text, y in [(1, "The history appears sequential to each process, and", 590),
                                       (2, "of operations. Equivalently, each operation appears instantaneously.", 50)]]
        raw = {"p001b0001": "未修复的句子，并且", "p002b0001": "的操作。的操作。后续句子。"}
        expected = {"p001b0001": "完整句子。", "p002b0001": "的操作。后续句子。"}
        for entry in ("serial", "parallel"):
            for mode in ("vector", "raster"):
                with self.subTest(entry=entry, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    job = {"job_dir": root, "boundary_schema_path": root / "schema.json",
                           "boundary_repairs_path": root / "repairs.json", "job_slug": "test",
                           "translations_path": root / "translations.json", "pages_dir": root,
                           "translated_pages_dir": root}
                    args = SimpleNamespace(suffix="-Chinese", force=True, refresh_source=False, dpi=72,
                                           page_start=1, page_end=0, force_ocr=False, batch_chars=7000,
                                           retranslate=False, render_mode=mode, qa=True, model="test",
                                           reasoning_effort="low", retries=3, strict_qa=False,
                                           qa_mode="sample", qa_sample_size=0, qa_batch_chars=7000)
                    drawn = []
                    checked = []
                    def validate(selected, translations, plans, **kwargs):
                        checked.append(dict(translations))
                        return []
                    def vector(pdf, output, selected, translations, *rest, **kwargs):
                        drawn.append(dict(translations))
                        return pipeline.DocumentRenderResult([], translations)
                    def raster(selected, translations, *rest):
                        drawn.append(dict(translations))
                    def run(cmd, **kwargs):
                        Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps({"items": [{
                            "key": "p001b0001->p002b0001", "translation": "完整句子。",
                            "next_prefix_translation": "的操作。"}]}))
                        return subprocess.CompletedProcess(cmd, 0, "", "")
                    with (
                        patch.object(pipeline, "build_job_paths", return_value=job),
                        patch.object(pipeline, "get_pdf_page_size", return_value=(623, 801)),
                        patch.object(pipeline, "load_or_build_source_pages", return_value=pages),
                        patch.object(pipeline, "translate_batches", return_value=raw),
                        patch.object(parallel, "run_parallel_translation", return_value=raw),
                        patch.object(pipeline, "write_vector_pdf", side_effect=vector),
                        patch.object(pipeline, "render_pages", side_effect=raster),
                        patch.object(pipeline.render_pdf, "write_raster_pdf"),
                        patch.object(pipeline, "validate_document_quality", side_effect=validate),
                        patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                        patch.object(parallel, "run_visual_qa_for_job", return_value={"visual_error_count": 0}),
                        patch.object(pipeline, "set_work_dir"),
                        patch("subprocess.run", side_effect=run) as command,
                        patch.object(sys, "argv", ["translate", "--pdf", str(root / "input.pdf"),
                                                  "--pdf-output", str(root / "out.pdf"), "--model", "test",
                                                  "--render-mode", mode]),
                    ):
                        if entry == "serial":
                            pipeline.main()
                        else:
                            parallel.translate_one_pdf(root / "input.pdf", root, args)
                    self.assertEqual(drawn, [expected])
                    self.assertEqual(checked, [expected] if entry == "parallel" else [])
                    self.assertEqual(command.call_count, 1)
                    self.assertEqual(raw["p002b0001"], "的操作。的操作。后续句子。")

    def test_serial_batches_span_pages_while_parallel_batches_stay_on_each_page(self):
        pages = [[{"id": f"p{page:03d}b0001", "page": page, "block_index": 1,
                   "text": "This complete body paragraph explains the experimental results in detail.",
                   "xMin": 20, "yMin": 70, "xMax": 280, "yMax": 130}]
                 for page in (1, 2)]
        serial = pipeline.build_batches(pages, 7000, page_size=(300, 400))
        concurrent = parallel.build_page_batches(list(enumerate(pages, 1)), 7000,
                                                  page_size=(300, 400))
        self.assertEqual([[item["id"] for item in batch] for batch in serial],
                         [["p001b0001", "p002b0001"]])
        self.assertEqual([(batch.page_num, [item["id"] for item in batch.items]) for batch in concurrent],
                         [(1, ["p001b0001"]), (2, ["p002b0001"])])

    def test_low_overlap_cache_is_rejected_by_serial_and_retained_by_parallel(self):
        items = [{"id": f"p001b{index:04d}", "text": "Body paragraph."} for index in range(1, 6)]
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job = {"job_dir": root, "schema_path": root / "schema.json",
                       "translations_path": root / "translations.json"}
                job["translations_path"].write_text(json.dumps({"p001b0001": "缓存译文"}))
                requested = []

                def run(cmd, **kwargs):
                    payload = json.loads(kwargs["input"].split("待翻译条目如下：", 1)[1])
                    requested.extend(item["id"] for item in payload["items"])
                    result = {"items": [{"id": item["id"], "translation": "新译文"}
                                        for item in payload["items"]]}
                    Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(result))
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                with patch("subprocess.run", side_effect=run):
                    if entry == "serial":
                        result = pipeline.translate_batches([items], job, model="test")
                    else:
                        cached = parallel.load_cached_translations(job["translations_path"],
                                                                   {item["id"] for item in items}, retranslate=False)
                        args = SimpleNamespace(model="test", reasoning_effort="low", retries=1, page_workers=1)
                        result = parallel.run_parallel_translation([parallel.PageBatch(1, 1, items)], cached, job, args)
                expected_requests = items if entry == "serial" else items[1:]
                self.assertEqual(requested, [item["id"] for item in expected_requests])
                self.assertEqual(result["p001b0001"], "新译文" if entry == "serial" else "缓存译文")
                self.assertEqual(json.loads(job["translations_path"].read_text()), result)

    def run_entry(self, entry, responses, *, stale_output=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {"job_dir": root, "schema_path": root / "schema.json",
                   "translations_path": root / "translations.json"}
            items = [{"id": "p001b0001", "text": "First paragraph."}]
            requests = []

            def run(cmd, **kwargs):
                self.assertEqual(cmd[:2], ["codex", "exec"])
                self.assertIn("test-model", cmd)
                self.assertIn("First paragraph.", kwargs["input"])
                self.assertTrue(Path(cmd[cmd.index("--output-schema") + 1]).exists())
                output = Path(cmd[cmd.index("-o") + 1])
                response = responses[len(requests)]
                requests.append(cmd)
                returncode = 0
                if isinstance(response, tuple):
                    returncode, response = response
                if response is not None:
                    output.write_text(response, encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode, "stdout", "stderr")

            if stale_output is not None:
                prefix = "batch-01" if entry == "serial" else "page-001-chunk-01"
                (root / f"{prefix}.out.json").write_text(stale_output)
            with patch("subprocess.run", side_effect=run), patch("time.sleep"):
                if entry == "serial":
                    result = pipeline.translate_batches([items], job, model="test-model")
                else:
                    args = SimpleNamespace(model="test-model", reasoning_effort="low", retries=3, page_workers=2)
                    result = parallel.run_parallel_translation([parallel.PageBatch(1, 1, items)], {}, job, args)
            self.assertEqual(json.loads(job["translations_path"].read_text()), result)
            return result, len(requests)

    def test_both_entries_retry_invalid_payloads_before_caching(self):
        good = json.dumps({"items": [{"id": "p001b0001", "translation": "第一段。"}]})
        bad_payloads = [
            "not JSON", "[]", '{"items": []}',
            json.dumps({"items": [{"id": "p001b0001", "translation": "  "}]}),
            json.dumps({"items": [{"id": "p001b0001", "translation": "旧译文"}] * 2}),
            json.dumps({"items": [{"id": "p001b0001", "translation": None}]}),
            json.dumps({"items": [{"id": "unexpected", "translation": "额外译文"}]}),
        ]
        for entry in ("serial", "parallel"):
            for bad in bad_payloads:
                with self.subTest(entry=entry, payload=bad):
                    result, count = self.run_entry(entry, [bad, good])
                    self.assertEqual(result, {"p001b0001": "第一段。"})
                    self.assertEqual(count, 2)

    def test_both_entries_reject_stale_output_when_command_did_not_write(self):
        stale = json.dumps({"items": [{"id": "p001b0001", "translation": "旧译文"}]})
        fresh = json.dumps({"items": [{"id": "p001b0001", "translation": "新译文"}]})
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry):
                result, count = self.run_entry(entry, [None, fresh], stale_output=stale)
                self.assertEqual(result, {"p001b0001": "新译文"})
                self.assertEqual(count, 2)

    def test_both_entries_fail_after_exhausting_invalid_responses(self):
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(RuntimeError, "translation.*failed"):
                    self.run_entry(entry, ["bad JSON"] * 3)

    def test_nonzero_command_exit_retries_even_if_output_looks_valid(self):
        rejected = json.dumps({"items": [{"id": "p001b0001", "translation": "失败结果"}]})
        accepted = json.dumps({"items": [{"id": "p001b0001", "translation": "成功结果"}]})
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry):
                result, count = self.run_entry(entry, [(1, rejected), accepted])
                self.assertEqual(result, {"p001b0001": "成功结果"})
                self.assertEqual(count, 2)

    def test_worker_count_does_not_change_translations_or_persisted_cache(self):
        for workers in (1, 2):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job = {"job_dir": root, "schema_path": root / "schema.json",
                       "translations_path": root / "translations.json"}
                batches = [parallel.PageBatch(page, 1, [{"id": f"p{page:03d}b0001", "text": "Body."}])
                           for page in (1, 2)]
                def run(cmd, **kwargs):
                    payload = json.loads(kwargs["input"].split("待翻译条目如下：", 1)[1])
                    result = {"items": [{"id": item["id"], "translation": "  正文。  "}
                                        for item in payload["items"]]}
                    Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(result))
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                args = SimpleNamespace(model="test", reasoning_effort="low", retries=1, page_workers=workers)
                with patch("subprocess.run", side_effect=run):
                    result = parallel.run_parallel_translation(batches, {}, job, args)
                expected = {"p001b0001": "正文。", "p002b0001": "正文。"}
                self.assertEqual(result, expected)
                self.assertEqual(json.loads(job["translations_path"].read_text()), expected)


if __name__ == "__main__":
    unittest.main()
